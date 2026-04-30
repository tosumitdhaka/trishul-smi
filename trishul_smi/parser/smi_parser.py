"""Public parser API: parse(text) -> MibModule.

Usage:
    parser = SmiParser()                     # auto-detect dialect
    parser = SmiParser(dialect="smiv2")      # force SMIv2
    mib = parser.parse(raw_asn1_text)

The parser is CPU-bound and synchronous. Call from async contexts via:
    mib = await asyncio.to_thread(parser.parse, raw_text)
"""
from __future__ import annotations

import importlib.resources
from typing import Literal

from lark import Lark, UnexpectedInput

from trishul_smi.errors import ParseError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.parser.transformer import MibTransformer

_DIALECT = Literal["smiv2", "smiv1", "auto"]

# SMIv2 markers in IMPORTS that confirm dialect
_SMIv2_MARKERS = frozenset({"SNMPv2-SMI", "SNMPv2-TC", "SNMPv2-CONF", "SNMPv2-MIB"})


def _load_grammar(name: str) -> str:
    """Load a .lark grammar file from the grammar/ package directory."""
    pkg = importlib.resources.files("trishul_smi.parser.grammar")
    return (pkg / name).read_text(encoding="utf-8")


def _detect_dialect(text: str) -> Literal["smiv2", "smiv1"]:
    """Heuristic: scan IMPORTS block for SMIv2-specific module names."""
    for marker in _SMIv2_MARKERS:
        if marker in text:
            return "smiv2"
    return "smiv1"


class SmiParser:
    """Parses raw ASN.1 MIB text into a MibModule dataclass.

    Args:
        dialect: "smiv2" (default), "smiv1", or "auto" (detect from source).
    """

    def __init__(self, dialect: _DIALECT = "auto") -> None:
        self._dialect = dialect
        self._parsers: dict[str, Lark] = {}

    def _get_parser(self, dialect: Literal["smiv2", "smiv1"], earley: bool = False) -> Lark:
        key = f"{dialect}:{'earley' if earley else 'lalr'}"
        if key not in self._parsers:
            grammar = _load_grammar(f"{dialect}.lark")
            algo = "earley" if earley else "lalr"
            self._parsers[key] = Lark(
                grammar,
                parser=algo,
                propagate_positions=False,
                maybe_placeholder=False,
            )
        return self._parsers[key]

    def parse(self, text: str) -> MibModule:
        """Parse raw ASN.1 text. Raises ParseError on invalid input.

        Strategy:
        1. Detect dialect if set to 'auto'.
        2. Try LALR(1) — fast path.
        3. On parse failure, retry with Earley — handles vendor dialect ambiguity.
        4. On Earley failure, raise ParseError with location info.
        """
        dialect: Literal["smiv2", "smiv1"] = (
            _detect_dialect(text) if self._dialect == "auto" else self._dialect  # type: ignore[assignment]
        )

        transformer = MibTransformer()

        # --- LALR fast path ---
        try:
            parser = self._get_parser(dialect, earley=False)
            tree = parser.parse(text)
            return transformer.transform(tree)
        except UnexpectedInput:
            pass  # fall through to Earley
        except Exception as exc:
            raise ParseError(f"Unexpected error in LALR parse: {exc}") from exc

        # --- Earley fallback ---
        try:
            parser = self._get_parser(dialect, earley=True)
            tree = parser.parse(text)
            return transformer.transform(tree)
        except UnexpectedInput as exc:
            context = getattr(exc, "get_context", lambda t: "")(text)
            raise ParseError(
                f"Failed to parse MIB ({dialect}). "
                f"Error at line {getattr(exc, 'line', '?')}, "
                f"column {getattr(exc, 'column', '?')}.\n{context}"
            ) from exc
        except Exception as exc:
            raise ParseError(f"Unexpected error in Earley parse: {exc}") from exc
