"""MibCompiler: top-level orchestrator.

Wires together readers, parser, resolver, cache, and formatters into
a single async ``compile()`` call.

Typical usage::

    from trishul_smi import MibCompiler, CompilerConfig
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
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.output.pysnmp_fmt import PysnmpFormatter
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import FetchProtocol
from trishul_smi.reader.chain import ReaderChain
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.resolver import MibResolver

logger = logging.getLogger(__name__)

_FORMATTER_CLASSES: dict[str, type[FormatterProtocol]] = {
    "json": JsonFormatter,
    "pysnmp": PysnmpFormatter,
}


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
            fmt: _FORMATTER_CLASSES[fmt]()
            for fmt in self._config.formats
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
        - ``'failed'``   — fetch or parse error; see ``.error``.

        Formatter errors (e.g. a buggy Jinja2 template) are non-fatal:
        they are appended to ``result.warnings`` and logged at WARNING
        level so they surface in the CLI without aborting the entire run.

        Raises:
            RuntimeError: if no readers were registered via add_reader().
            WriterError: if the output directory cannot be created (e.g.
                permission denied).
        """
        if not self._readers:
            raise RuntimeError(
                "No readers registered. Call add_reader() before compile()."
            )

        chain = ReaderChain(*self._readers)
        resolver = MibResolver(chain, self._parser, self._cache)
        resolve_result = await resolver.resolve(list(mib_names))

        results: list[CompileResult] = []
        out_dir = self._config.output_dir
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as _mkdir_exc:
            from trishul_smi.errors import WriterError
            raise WriterError(
                f"Cannot create output directory {out_dir}: {_mkdir_exc}"
            ) from _mkdir_exc

        # --- Successfully resolved modules ---
        for module in resolve_result.modules:
            output_paths: list[Path] = []
            warnings: list[str] = []

            for fmt_name, formatter in self._formatters.items():
                out_path = out_dir / f"{module.name}{formatter.FILE_SUFFIX}"
                try:
                    content = formatter.format(module)
                    if isinstance(content, bytes):
                        out_path.write_bytes(content)
                    else:
                        out_path.write_text(content, encoding="utf-8")
                    output_paths.append(out_path)
                except Exception as _fmt_exc:  # noqa: BLE001
                    msg = f"[{fmt_name}] formatter error for {module.name}: {_fmt_exc}"
                    warnings.append(msg)
                    logger.warning(msg)  # visible in CLI without aborting the run

            results.append(CompileResult(
                name=module.name,
                status="compiled",
                output_paths=output_paths,
                warnings=warnings,
            ))

        # --- Failed modules ---
        for name, exc in resolve_result.errors.items():
            results.append(CompileResult(
                name=name,
                status="failed",
                error=str(exc),
            ))

        return results
