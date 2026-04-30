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

from pathlib import Path
from typing import Literal

from trishul_smi.config import CompilerConfig
from trishul_smi.errors import MibNotFoundError
from trishul_smi.models import CompileResult
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.output.pysnmp_fmt import PysnmpFormatter
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import FetchProtocol
from trishul_smi.reader.chain import ReaderChain
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.resolver import MibResolver

_FORMATTERS = {
    "json":   JsonFormatter,
    "pysnmp": PysnmpFormatter,
}


class MibCompiler:
    """Compiles one or more MIB names to structured output files.

    Build with a fluent ``add_reader()`` chain, then call ``compile()``.

    Args:
        config: CompilerConfig controlling output location, formats, cache,
                HTTP settings, and size limits.
    """

    def __init__(self, config: CompilerConfig | None = None) -> None:
        self._config = config or CompilerConfig()
        self._readers: list[FetchProtocol] = []
        # Parser is a singleton per compiler — grammar compiled on first parse.
        self._parser = SmiParser()
        # Compiled-module cache (None when cache_dir is None).
        self._cache: MibCache | None = (
            MibCache(self._config.cache_dir, self._config.cache_ttl_days)
            if self._config.cache_dir is not None
            else None
        )
        # Formatter instances (stateless, reusable)
        self._formatters = {fmt: _FORMATTERS[fmt]() for fmt in self._config.formats}

    # ------------------------------------------------------------------
    # Fluent reader registration
    # ------------------------------------------------------------------

    def add_reader(self, reader: FetchProtocol) -> "MibCompiler":
        """Append a reader to the fallback chain. Returns self for chaining."""
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
        - ``'cached'``   — not yet used here (reserved for future skip logic).
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
        out_dir.mkdir(parents=True, exist_ok=True)

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
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"[{fmt_name}] formatter error: {exc}")

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
