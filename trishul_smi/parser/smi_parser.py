"""Public parser API: parse(text) -> MibModule.

Usage::

    # Singleton pattern: create once, reuse. The grammar is compiled on
    # first use and cached inside the instance. Creating a new SmiParser
    # per call re-compiles the grammar on each call, which is expensive.
    # Use a module-level or application-level singleton.

    _parser = SmiParser()                    # module-level singleton
    mib = _parser.parse(raw_asn1_text)

    # Force a specific dialect:
    parser = SmiParser(dialect="smiv1")

    # From async code (parser.parse is synchronous/CPU-bound):
    mib = await asyncio.to_thread(_parser.parse, raw_text)
"""
from __future__ import annotations

import importlib.resources
from typing import Literal

from lark import Lark, UnexpectedInput

from trishul_smi.errors import ParseError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.parser._constants import SMIv2_MARKERS
from trishul_smi.parser.transformer import MibTransformer

_DIALECT = Literal["smiv2", "smiv1", "auto"]


def _load_grammar(name: str) -> str:
    """Load a .lark grammar file from the grammar/ package directory.

    Requires trishul_smi/parser/grammar/ to be a package (has __init__.py)
    and listed under [tool.hatch.build.targets.wheel.force-include] in
    pyproject.toml so it is included in wheel builds.
    """
    pkg = importlib.resources.files("trishul_smi.parser.grammar")
    return (pkg / name).read_text(encoding="utf-8")


def _detect_dialect(text: str) -> Literal["smiv2", "smiv1"]:
    """Heuristic: scan text for SMIv2-specific IMPORTS module names.

    Uses SMIv2_MARKERS from _constants.py — the same set used by
    MibTransformer.module_definition to set MibModule.language.
    """
    for marker in SMIv2_MARKERS:
        if marker in text:
            return "smiv2"
    return "smiv1"


class SmiParser:
    """Parses raw ASN.1 MIB text into a MibModule dataclass.

    Args:
        dialect: ``"smiv2"`` (default), ``"smiv1"``, or ``"auto"``
                 (auto-detects from IMPORTS section).

    Performance note:
        Grammar compilation happens on first ``parse()`` call per
        ``(dialect, algorithm)`` combination and is cached in
        ``self._parsers``. Use a single instance per process.
    """

    def __init__(self, dialect: _DIALECT = "auto") -> None:
        self._dialect = dialect
        self._parsers: dict[str, Lark] = {}

    def _get_parser(self, dialect: Literal["smiv2", "smiv1"], earley: bool = False) -> Lark:
        key = f"{dialect}:{'earley' if earley else 'lalr'}"
        if key not in self._parsers:
            grammar = _load_grammar(f"{dialect}.lark")
            self._parsers[key] = Lark(
                grammar,
                parser="earley" if earley else "lalr",
                propagate_positions=False,
                maybe_placeholder=False,
            )
        return self._parsers[key]

    def parse(self, text: str) -> MibModule:
        """Parse raw ASN.1 text. Raises ParseError on invalid input.

        Strategy:
        1. Detect dialect (if ``auto``).
        2. Try LALR(1) — fast path.
        3. On parse failure, retry with Earley — handles vendor dialect quirks.
        4. On Earley failure, raise ParseError with location info.
        """
        dialect: Literal["smiv2", "smiv1"] = (
            _detect_dialect(text) if self._dialect == "auto"
            else self._dialect  # type: ignore[assignment]
        )

        transformer = MibTransformer()

        try:
            tree = self._get_parser(dialect, earley=False).parse(text)
            return transformer.transform(tree)
        except UnexpectedInput:
            pass
        except Exception as exc:
            raise ParseError(f"Unexpected error in LALR parse: {exc}") from exc

        try:
            tree = self._get_parser(dialect, earley=True).parse(text)
            return transformer.transform(tree)
        except UnexpectedInput as exc:
            context = getattr(exc, "get_context", lambda t: "")(text)
            raise ParseError(
                f"Failed to parse MIB ({dialect}). "
                f"Line {getattr(exc, 'line', '?')}, "
                f"col {getattr(exc, 'column', '?')}.\n{context}"
            ) from exc
        except Exception as exc:
            raise ParseError(f"Unexpected error in Earley parse: {exc}") from exc
