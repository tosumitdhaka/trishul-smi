"""MibCompiler: top-level orchestrator.

Wires together readers, parser, resolver, cache, and formatters into
a single async ``compile()`` call.

Typical usage::

    from trishul_smi.compiler import MibCompiler
    from trishul_smi.config import CompilerConfig
    from trishul_smi.reader import FileReader, HttpReader

    config = CompilerConfig(output_dir=Path("./out"))
    async with HttpReader(*config.sources) as http:
        compiler = (
            MibCompiler(config)
            .add_reader(FileReader("/usr/share/snmp/mibs"))
            .add_reader(http)
        )
        results = await compiler.compile("IF-MIB", "IP-MIB")
        for r in results:
            print(r.name, r.status, r.output_paths)
"""

from __future__ import annotations

import logging
from pathlib import Path

from trishul_smi.config import VALID_FORMATS, CompilerConfig
from trishul_smi.models import CompileResult
from trishul_smi.output.base import FormatterProtocol
from trishul_smi.output.json_bundle import (
    MANIFEST_FILENAME,
    OID_INDEX_FILENAME,
    JsonModuleArtifact,
    build_manifest_bytes,
    build_oid_index_bytes,
)
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.output.json_ir import JsonArtifactMetadata, make_json_artifact_metadata
from trishul_smi.output.pysnmp_fmt import PysnmpFormatter
from trishul_smi.parser._constants import BASE_MIBS
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import FetchProtocol
from trishul_smi.reader.chain import ReaderChain
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.oid_resolver import resolve_oids
from trishul_smi.resolver.resolver import MibResolver

logger = logging.getLogger(__name__)


def _make_formatter(fmt: str, config: CompilerConfig) -> FormatterProtocol:
    if fmt == "pysnmp":
        return PysnmpFormatter(no_texts=config.no_texts)
    if fmt == "json":
        return JsonFormatter(no_texts=config.no_texts)
    raise ValueError(f"Unknown output format: {fmt!r}")


class MibCompiler:
    """Compiles one or more MIB names to structured output files.

    Build with a fluent ``add_reader()`` chain, then call ``compile()``.

    Args:
        config: CompilerConfig controlling output location, formats, cache,
                HTTP settings, and size limits.

    Raises:
        ValueError: if ``config.formats`` contains an unrecognised format name.
            Raised at construction time so the CLI surfaces the error before
            any I/O begins.
    """

    def __init__(self, config: CompilerConfig | None = None) -> None:
        self._config = config or CompilerConfig()

        # Validate formats eagerly — a KeyError in compile() deep inside an
        # async gather would be opaque. Surface it here instead.
        unknown = set(self._config.formats) - VALID_FORMATS
        if unknown:
            raise ValueError(
                f"Unknown output format(s): {sorted(unknown)}. "
                f"Valid formats: {sorted(VALID_FORMATS)}"
            )

        self._readers: list[FetchProtocol] = []
        # Parser is a singleton per compiler — grammar compiled on first parse.
        self._parser = SmiParser()
        # Compiled-module cache (None when cache_dir is None).
        self._cache: MibCache | None = (
            MibCache(self._config.cache_dir, self._config.cache_ttl_days)
            if self._config.cache_dir is not None
            else None
        )
        # Formatter instances keyed by format name — typed against FormatterProtocol
        # so mypy can verify FILE_SUFFIX and format() calls in compile().
        self._formatters: dict[str, FormatterProtocol] = {
            fmt: _make_formatter(fmt, self._config) for fmt in self._config.formats
        }

    # ------------------------------------------------------------------
    # Fluent reader registration
    # ------------------------------------------------------------------

    def add_reader(self, reader: FetchProtocol) -> MibCompiler:
        """Append a reader to the fallback chain. Returns self for chaining.

        Raises:
            RuntimeError: if called after compile() has already been invoked
                (readers are snapshotted into a ReaderChain at compile time).
        """
        self._readers.append(reader)
        return self

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def compile(self, *mib_names: str) -> list[CompileResult]:
        """Fetch, parse, and write output files for *mib_names* and all
        transitive dependencies.

        Returns a list of CompileResult, one per module (including deps).
        Status values:
        - ``'compiled'`` — successfully parsed and written to disk.
        - ``'failed'``   — fetch, parse, or dependency-blocking error; see ``.error``.
        - ``'missing'``  — the module could not be found in any configured reader.

        Formatter errors (e.g. a buggy Jinja2 template) are non-fatal:
        they are appended to ``result.warnings`` and logged at WARNING
        level so they surface in the CLI without aborting the entire run.

        Raises:
            RuntimeError: if no readers were registered via add_reader().
            WriterError: if the output directory cannot be created (e.g.
                permission denied).
        """
        if not self._readers:
            raise RuntimeError("No readers registered. Call add_reader() before compile().")

        chain = ReaderChain(*self._readers)
        resolver = MibResolver(chain, self._parser, self._cache)
        resolve_result = await resolver.resolve(list(mib_names))
        resolve_oids(resolve_result.modules)

        json_artifact_metadata: JsonArtifactMetadata | None = None
        if any(isinstance(formatter, JsonFormatter) for formatter in self._formatters.values()):
            json_artifact_metadata = make_json_artifact_metadata()
            for formatter in self._formatters.values():
                if isinstance(formatter, JsonFormatter):
                    formatter.set_artifact_metadata(json_artifact_metadata)

        requested_set = set(mib_names)
        resolved_names = {module.name for module in resolve_result.modules}
        blocked: dict[str, list[str]] = {}

        results: list[CompileResult] = []
        emitted_json_modules: list[JsonModuleArtifact] = []
        out_dir = self._config.output_dir
        if not self._config.dry_run:
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except OSError as _mkdir_exc:
                from trishul_smi.errors import WriterError

                raise WriterError(
                    f"Cannot create output directory {out_dir}: {_mkdir_exc}"
                ) from _mkdir_exc

        for module in resolve_result.modules:
            unresolved = sorted(
                dep
                for dep in module.all_imports()
                if dep not in BASE_MIBS
                and (dep in resolve_result.errors or dep in blocked or dep not in resolved_names)
            )
            if unresolved:
                blocked[module.name] = unresolved

        # --- Successfully resolved modules ---
        for module in resolve_result.modules:
            if module.name in blocked:
                continue
            output_paths: list[Path] = []
            # Seed with parser-level warnings (non-standard syntax accepted leniently).
            # Surfaced in the CLI via CompileResult.warnings; not logged here to avoid
            # noisy duplicate output for modules with many warnings.
            warnings: list[str] = list(module.warnings)

            for fmt_name, formatter in self._formatters.items():
                out_path = out_dir / f"{module.name}{formatter.FILE_SUFFIX}"
                try:
                    content = formatter.format(module)
                    if not self._config.dry_run:
                        if isinstance(content, bytes):
                            out_path.write_bytes(content)
                        else:
                            out_path.write_text(content, encoding="utf-8")
                        output_paths.append(out_path)
                    if fmt_name == "json":
                        emitted_json_modules.append(
                            JsonModuleArtifact(
                                module=module.name,
                                file=out_path.name,
                                module_data=module,
                            )
                        )
                except Exception as _fmt_exc:  # noqa: BLE001
                    msg = f"[{fmt_name}] formatter error for {module.name}: {_fmt_exc}"
                    warnings.append(msg)
                    logger.warning(msg)  # visible in CLI without aborting the run

            results.append(
                CompileResult(
                    name=module.name,
                    status="compiled",
                    output_paths=output_paths,
                    warnings=warnings,
                    is_dependency=module.name not in requested_set,
                )
            )

        for module in resolve_result.modules:
            if module.name not in blocked:
                continue
            results.append(
                CompileResult(
                    name=module.name,
                    status="failed",
                    error=(
                        f"Unresolved non-base imports for {module.name}: "
                        f"{', '.join(blocked[module.name])}"
                    ),
                    is_dependency=module.name not in requested_set,
                    missing_dependencies=blocked[module.name],
                )
            )

        # --- Failed / missing modules ---
        from trishul_smi.errors import MibNotFoundError

        for name, exc in resolve_result.errors.items():
            results.append(
                CompileResult(
                    name=name,
                    status="missing" if isinstance(exc, MibNotFoundError) else "failed",
                    error=str(exc),
                    is_dependency=name not in requested_set,
                    missing_dependencies=[name] if isinstance(exc, MibNotFoundError) else [],
                )
            )

        oid_index_written = False
        if not self._config.dry_run and self._config.emit_oid_index and emitted_json_modules:
            if json_artifact_metadata is None:
                raise RuntimeError("emit_oid_index requires an active JSON formatter")
            oid_index_path = out_dir / OID_INDEX_FILENAME
            try:
                oid_index_path.write_bytes(
                    build_oid_index_bytes(json_artifact_metadata, emitted_json_modules)
                )
                oid_index_written = True
            except OSError as _oid_index_exc:
                from trishul_smi.errors import WriterError

                raise WriterError(
                    f"Cannot write OID index {oid_index_path}: {_oid_index_exc}"
                ) from _oid_index_exc

        if not self._config.dry_run and self._config.emit_manifest and emitted_json_modules:
            if json_artifact_metadata is None:
                raise RuntimeError("emit_manifest requires an active JSON formatter")
            manifest_path = out_dir / MANIFEST_FILENAME
            try:
                manifest_path.write_bytes(
                    build_manifest_bytes(
                        json_artifact_metadata,
                        emitted_json_modules,
                        oid_index_filename=OID_INDEX_FILENAME if oid_index_written else None,
                    )
                )
            except OSError as _manifest_exc:
                from trishul_smi.errors import WriterError

                raise WriterError(
                    f"Cannot write bundle manifest {manifest_path}: {_manifest_exc}"
                ) from _manifest_exc

        return results
