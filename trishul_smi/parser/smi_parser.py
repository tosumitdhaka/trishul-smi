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

# Dialect detection pattern: SMIv2 markers recognised only as IMPORTS targets
# ("FROM SNMPv2-SMI"). The word-boundary lookarounds keep e.g. "SNMPv2-SMI-v1"
# (the SMIv1 compatibility shim) from matching — the transformer records
# language the same way ("SMIv2" iff an imported module is an SMIv2 marker).
_FROM_SMIV2_PATTERN = re.compile(
    r"\bFROM\b\s*(?<![A-Za-z0-9\-])("
    + "|".join(re.escape(m) for m in SMIv2_MARKERS)
    + r")(?![A-Za-z0-9\-])"
)

# Fallback for root SMIv2 modules (SNMPv2-SMI itself) that have no IMPORTS
# clause at all: SMIv2-only construct keywords. An SMIv1 module never
# contains these as grammar tokens, and detection runs on the masked copy,
# so mentions inside strings/comments cannot trigger the fallback.
_SMIV2_CONSTRUCT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9\-])("
    "MODULE-IDENTITY|OBJECT-IDENTITY|NOTIFICATION-TYPE|MODULE-COMPLIANCE"
    "|AGENT-CAPABILITIES|MAX-ACCESS"
    r")(?![A-Za-z0-9\-])"
)

# Strip MACRO body content before LALR parsing.
# MACRO..END blocks contain free-form ASN.1 notation that is not valid grammar input.
# We reduce each to "MACRO-NAME MACRO ::= BEGIN END" (preserving newlines for line numbers).
# Matching runs on a quote/comment-masked copy of the text (see
# _mask_quotes_and_comments) so the words MACRO/END inside DESCRIPTION strings
# or -- comments can neither start nor end a match (issue #11). The "::= BEGIN"
# anchor additionally stops a module name containing "MACRO" (e.g. X-MACRO-MIB)
# from being mistaken for a macro assignment.
_MACRO_BODY_RE = re.compile(r"\bMACRO\b\s*::=\s*BEGIN(.*?)\bEND\b", re.DOTALL)
_WRAPPED_COMMENT_TEXT_RE = re.compile(r"[a-z][A-Za-z0-9\-]*(?:[ \t]+[A-Za-z0-9][A-Za-z0-9\-]*)*")


def _mask_quotes_and_comments(text: str) -> str:
    """Blank out string-literal and comment contents while keeping length.

    Every character inside a double-quoted string or a ``--`` comment is
    replaced by a space; newlines are kept so line numbers (and macro-body
    newline preservation) stay correct. The result has the same length as
    *text*, so match spans found on it index directly into the original.

    Mirrors the grammar tokenization (smiv1.lark / smiv2.lark):
      QUOTED_STRING : /"(?:[^\\"]|\\[\\s\\S])*"/   double-quoted, may span
                                                    lines, backslash escapes any
                                                    following character
      COMMENT       : /--[^\n]*/                   runs to end of line
    """
    chars = list(text)
    length = len(text)
    index = 0
    in_quote = False
    while index < length:
        char = text[index]
        if in_quote:
            if char == "\\" and index + 1 < length:
                chars[index] = " "
                chars[index + 1] = " "
                index += 2
                continue
            if char == '"':
                in_quote = False
                chars[index] = " "
                index += 1
                continue
            if char not in "\r\n":
                chars[index] = " "
            index += 1
            continue
        if char == '"':
            in_quote = True
            chars[index] = " "
            index += 1
            continue
        if char == "-" and index + 1 < length and text[index + 1] == "-":
            while index < length and text[index] not in "\r\n":
                chars[index] = " "
                index += 1
            continue
        index += 1
    return "".join(chars)


def _strip_macro_bodies(text: str) -> str:
    masked = _mask_quotes_and_comments(text)

    def _keep_newlines(m: re.Match[str]) -> str:
        return "MACRO ::= BEGIN" + "".join(c for c in m.group(1) if c in "\r\n") + "END"

    parts: list[str] = []
    last = 0
    for match in _MACRO_BODY_RE.finditer(masked):
        parts.append(text[last : match.start()])
        parts.append(_keep_newlines(match))
        last = match.end()
    parts.append(text[last:])
    return "".join(parts)


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
    """Heuristic: SMIv2 iff an IMPORTS clause references an SMIv2 module.

    Root SMIv2 modules (SNMPv2-SMI itself) import nothing, so when a module
    has no IMPORTS clause at all we fall back to SMIv2-only construct
    keywords — an SMIv1 module never contains them as grammar tokens.

    Detection runs on a quote/comment-masked copy of *text* and only
    recognises SMIv2 marker modules as ``FROM <module>`` import targets
    (issue #24). This mirrors the transformer's language decision — "SMIv2"
    iff any imported module is an SMIv2 marker (transformer.py) — so a
    comment or DESCRIPTION merely mentioning "SNMPv2-SMI" can no longer
    force the v2 grammar onto an SMIv1 module.
    """
    masked = _mask_quotes_and_comments(text)
    if _FROM_SMIV2_PATTERN.search(masked):
        return "smiv2"
    if not re.search(r"\bFROM\b", masked) and _SMIV2_CONSTRUCT_PATTERN.search(masked):
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
