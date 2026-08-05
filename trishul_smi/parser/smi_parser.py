"""Public parser API: parse(text) -> MibModule.

Usage::

    # Create parsers freely. Grammar text is cached process-wide, while
    # compiled Lark parser instances are cached per thread so concurrent
    # asyncio.to_thread(...) calls do not share mutable parser state.

    mib = SmiParser().parse(raw_asn1_text)

    # Force a specific dialect:
    parser = SmiParser(dialect="smiv1")

    # From async code (parser.parse is synchronous/CPU-bound):
    mib = await asyncio.to_thread(SmiParser().parse, raw_text)
"""

from __future__ import annotations

import importlib.resources
import re
import threading
from typing import ClassVar, Literal, cast

from lark import Lark, UnexpectedInput

from trishul_smi.errors import ParseError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.parser._constants import SMIv2_MARKERS
from trishul_smi.parser.transformer import MibTransformer

_DIALECT = Literal["smiv2", "smiv1", "auto"]

# Pre-compiled pattern for word-boundary dialect detection.
# Matches any SMIv2 marker as a whole token (not as a substring of e.g. SNMPv2-TC-v1).
_SMIv2_PATTERN = re.compile(
    r"(?<![A-Za-z0-9\-])(" + "|".join(re.escape(m) for m in SMIv2_MARKERS) + r")(?![A-Za-z0-9\-])"
)

# Strip MACRO body content before LALR parsing.
# MACRO..END blocks contain free-form ASN.1 notation that is not valid grammar input.
# We reduce each to "MACRO-NAME MACRO ::= BEGIN END" (preserving newlines for line numbers).
_MACRO_BODY_RE = re.compile(r"\bMACRO\b(.*?)\bEND\b", re.DOTALL)
_WRAPPED_COMMENT_TEXT_RE = re.compile(r"[a-z][A-Za-z0-9\-]*(?:[ \t]+[A-Za-z0-9][A-Za-z0-9\-]*)*")


def _strip_macro_bodies(text: str) -> str:
    def _keep_newlines(m: re.Match[str]) -> str:
        return "MACRO ::= BEGIN" + "".join(c for c in m.group(1) if c in "\r\n") + "END"

    return _MACRO_BODY_RE.sub(_keep_newlines, text)


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def _find_comment_start(line: str, in_quote: bool) -> tuple[int, bool]:
    """Return the first ``--`` comment column outside quoted strings."""
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"' and (index == 0 or line[index - 1] != "\\"):
            in_quote = not in_quote
            index += 1
            continue
        if not in_quote and char == "-" and index + 1 < len(line) and line[index + 1] == "-":
            return index, in_quote
        index += 1
    return -1, in_quote


def _normalize_wrapped_comments(text: str) -> str:
    """Promote wrapped inline comment continuations to full comment lines.

    Some real-world MIBs wrap the trailing text of an inline ``--`` comment onto
    a later, deeply-indented line without repeating the comment marker. Lark
    then sees the continuation as bare ASN.1 text and fails to parse the file.
    Convert only these narrow continuation lines into explicit comment lines
    while preserving original line counts for parse error reporting.
    """
    lines = text.splitlines(keepends=True)
    in_quote = False
    index = 0
    while index < len(lines):
        comment_col, in_quote = _find_comment_start(lines[index], in_quote)
        if comment_col <= 0:
            index += 1
            continue

        line_content, _ = _split_line_ending(lines[index])
        # Normalize only trailing inline comments. Indented standalone comment
        # lines are real ASN.1 comments and must not absorb the following code.
        if not line_content[:comment_col].strip():
            index += 1
            continue

        look_ahead = index + 1
        while look_ahead < len(lines):
            content, line_ending = _split_line_ending(lines[look_ahead])
            stripped = content.strip()
            if not stripped:
                break
            leading = content[: len(content) - len(content.lstrip(" \t"))]
            if len(leading) < comment_col or not _WRAPPED_COMMENT_TEXT_RE.fullmatch(stripped):
                break
            lines[look_ahead] = f"{leading}-- {stripped}{line_ending}"
            look_ahead += 1

        index = look_ahead

    return "".join(lines)


def _load_grammar(name: str) -> str:
    """Load a .lark grammar file from the grammar/ package directory."""
    pkg = importlib.resources.files("trishul_smi.parser.grammar")
    return (pkg / name).read_text(encoding="utf-8")


def _detect_dialect(text: str) -> Literal["smiv2", "smiv1"]:
    """Heuristic: scan text for SMIv2-specific IMPORTS module names."""
    if _SMIv2_PATTERN.search(text):
        return "smiv2"
    return "smiv1"


class SmiParser:
    """Parses raw ASN.1 MIB text into a MibModule dataclass.

    Args:
        dialect: ``"smiv2"`` (default), ``"smiv1"``, or ``"auto"``
                 (auto-detects from IMPORTS section).

    Performance:
        Lark grammar compilation is expensive (~50–200 ms per
        ``(dialect, algorithm)`` combination). Grammar source text is
        cached process-wide, while compiled ``Lark`` instances are cached
        per thread so concurrent parser use does not share mutable parser
        state across worker threads.
    """

    _grammar_text_cache: ClassVar[dict[str, str]] = {}
    _thread_local: ClassVar[threading.local] = threading.local()

    def __init__(self, dialect: _DIALECT = "auto") -> None:
        self._dialect = dialect

    @classmethod
    def _get_thread_parser_cache(cls) -> dict[str, Lark]:
        cache = getattr(cls._thread_local, "parser_cache", None)
        if cache is None:
            cache = {}
            cls._thread_local.parser_cache = cache
        return cast(dict[str, Lark], cache)

    @classmethod
    def _get_grammar(cls, dialect: Literal["smiv2", "smiv1"]) -> str:
        name = f"{dialect}.lark"
        if name not in cls._grammar_text_cache:
            cls._grammar_text_cache[name] = _load_grammar(name)
        return cls._grammar_text_cache[name]

    def _get_parser(self, dialect: Literal["smiv2", "smiv1"], earley: bool = False) -> Lark:
        key = f"{dialect}:{'earley' if earley else 'lalr'}"
        parser_cache = self._get_thread_parser_cache()
        if key not in parser_cache:
            grammar = self._get_grammar(dialect)
            parser_cache[key] = Lark(
                grammar,
                parser="earley" if earley else "lalr",
                propagate_positions=True,
                # maybe_placeholder removed in Lark >= 1.2 — do not add back.
            )
        return parser_cache[key]

    def parse(self, text: str) -> MibModule:
        """Parse raw ASN.1 text. Raises ParseError on invalid input."""
        text = _normalize_wrapped_comments(text)
        text = _strip_macro_bodies(text)
        dialect: Literal["smiv2", "smiv1"] = (
            _detect_dialect(text) if self._dialect == "auto" else self._dialect
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
