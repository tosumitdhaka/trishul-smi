"""MIB lint engine — validation mode plus ``--fix`` remediation (v0.5.2, #31/#34).

This module is the library core of ``tsmi lint``. It reuses the existing
resolve pipeline (fetch → parse → cache → topological sort → ``resolve_oids``)
and inspects the resulting module closure for a small, bounded set of
defects. There is deliberately no new parse path and no grammar change.

Wiring a CLI command is a single call::

    report = asyncio.run(run_lint(names, config, mib_dirs=[...]))

then render :class:`LintReport`. Two stable renderings live here so the CLI
stays a thin wrapper: :func:`lint_report_to_dict` (the machine-readable JSON
document, stable across versions for CI consumers) and
:func:`format_lint_report_text` (the human-readable text mode). This module
carries no terminal/presentation concern beyond those two string/dict
renderings.

Remediation (``--fix``, v0.5.2 plan item 1)
-------------------------------------------
When ``run_lint(..., fix=True)`` is used, the two fixable check kinds are
applied to local ``--mib-dir`` source files:

- ``missing-import`` (TYPE-role only): the missing ``symbol FROM provider``
  import is added when the symbol resolves to exactly one provider module in
  the loaded closure. Ambiguous or unresolvable symbols are reported, not
  fixed.
- ``unused-import``: the unused symbol is removed from the IMPORTS clause;
  the clause is removed entirely when it empties.

Member/OID-role ``missing-import`` findings are NOT fixable (they are legal
unimported OID references). All other checks are report-only. Fixes edit only
the IMPORTS block of the raw source text (other lines are preserved
byte-for-byte), are validated by a re-parse (rollback on failure), and are
restricted to local ``--mib-dir`` files — HTTP/ZIP-sourced modules are
report-only. With ``diff=True`` nothing is written; the caller receives
unified diffs in ``LintReport.diffs`` instead.

Checks (v1 set plus the two v0.5.1 additions; all check ids stable)
------------------------------------------------------------------
``missing-import`` (error/warning)  A symbol is referenced structurally but
                                    is neither imported by the module nor
                                    defined in-module, and is not a built-in
                                    base type or well-known OID root.
                                    Severity is role-dependent: a reference
                                    in a TYPE position (SYNTAX / base type)
                                    is an ``error``; a reference in a
                                    MEMBER/OID position (notification OBJECTS
                                    members, INDEX, AUGMENTS, OID parent,
                                    TRAP-TYPE ENTERPRISE) is a ``warning``.
``undefined-type`` (error)      A SYNTAX / TEXTUAL-CONVENTION / type
                                assignment reference is imported or defined
                                in-module, but walking the type chain finds
                                no concrete base type anywhere in the
                                closure (the provider module does not define
                                it, the chain cycles, or it bottoms out in a
                                non-type).
``unresolvable-oid`` (error)    An object/notification's OID parent chain
                                dead-ends: after ``resolve_oids`` the parent
                                name is still unresolved (it is not a
                                well-known root, not an OID-bearing object
                                in the closure, and not an absolute numeric
                                value such as a numeric TRAP-TYPE enterprise).
``unused-import`` (warning)     An IMPORTS symbol is never referenced by
                                the module.
``duplicate-oid-arc`` (warning) Two or more objects in the closure resolve
                                to the exact same absolute OID (same
                                sub-identifier under the same parent).
``missing-status`` (warning)    A construct whose SMI macro mandates a
                                STATUS clause lacks one. Applies to the
                                SMIv2 macros that carry STATUS (OBJECT-TYPE,
                                OBJECT-IDENTITY, NOTIFICATION-TYPE,
                                OBJECT-GROUP, NOTIFICATION-GROUP,
                                MODULE-COMPLIANCE, AGENT-CAPABILITIES) and
                                to TEXTUAL-CONVENTION definitions.
``missing-description`` (warning)
                                A construct whose SMI macro carries a
                                DESCRIPTION clause lacks one. Applies to the
                                same SMIv2 macros plus TRAP-TYPE, and to the
                                module-level description of an *object-bearing*
                                SMIv2 module — a module that declares objects
                                or notifications but has no MODULE-IDENTITY
                                DESCRIPTION. TC-only modules (``SNMPv2-TC``,
                                ``SNMPv2-CONF``, ``IPV6-TC``) conventionally
                                carry no MODULE-IDENTITY and are not flagged
                                (issue #34).

Severity rationale
------------------
``undefined-type`` and ``unresolvable-oid`` are ``error``: each produces a
module whose compiled output is unusable (an undefined type produces no
meaningful SYNTAX; an unresolvable OID produces no absolute path for the OID
index). ``missing-import`` severity is role-dependent: a reference in a TYPE
position (SYNTAX / base type) is an ``error`` — the module's output is
unusable without the type — while a reference in a MEMBER/OID position
(notification OBJECTS members, INDEX, AUGMENTS, OID parent, TRAP-TYPE
ENTERPRISE) is a ``warning``: those are OID references that resolve by name
across the closure rather than by import, so failing to import them is legal
SMI and common in real vendor trap MIBs. ``unused-import``,
``duplicate-oid-arc``, ``missing-status``, and ``missing-description`` are
``warning``: they never block parsing or output. A missing STATUS/DESCRIPTION
clause is common in older vendor MIBs and does not break compilation — the
field is simply null in the model and the compiled output — so these two
checks are advisory, not blocking.

Scoping notes
-------------
- Macro/keyword usage (the ``OBJECT-TYPE`` in ``foo OBJECT-TYPE ...``) is not
  treated as a reference for ``missing-import``: the grammar guarantees the
  macro exists, so a missing macro import would be noise. It *is* counted as
  a "use" for ``unused-import`` so normal modules are not flagged.
- Built-in SMI/ASN.1 base types (``Integer32``, ``OCTET STRING``, ``Counter``,
  ...) and well-known OID roots (``iso``, ``mib-2``, ``enterprises``, ...)
  are always available without an import.
- Symbols imported FROM ``BASE_MIBS`` modules (e.g. ``DisplayString`` from
  SNMPv2-TC) are trusted as defined types: those infrastructure modules are
  never fetched into the closure, so their definitions cannot be inspected.
- ``undefined-type`` only fires when the immediate symbol is imported or
  defined in-module. A reference that is neither is reported by
  ``missing-import`` instead, so the two checks never double-report the same
  reference.
- ``missing-status`` only fires on constructs whose SMI macro actually
  defines a STATUS clause. MODULE-IDENTITY (no STATUS per RFC 2578),
  TRAP-TYPE (no STATUS per RFC 1215), and OBJECT IDENTIFIER value
  assignments are never flagged. The module-level ``missing-description``
  check applies only to SMIv2 modules (SMIv1 has no module-level DESCRIPTION
  clause) that declare objects or notifications: a TC-only module without a
  MODULE-IDENTITY is legal SMI and is not flagged (issue #34).
- The model does not distinguish a TEXTUAL-CONVENTION from a plain type
  assignment (``Foo ::= OCTET STRING``). Per RFC 2578 a type assignment
  legitimately carries no STATUS/DESCRIPTION, so to avoid false positives the
  type-level checks fire only on definitions carrying TC-only markers — a
  DISPLAY-HINT or a DESCRIPTION (the plain type assignments in the v0.5.0
  corpus carry neither).
- The five v1 checks are otherwise independent: a genuinely broken module can
  legitimately trigger several (e.g. an OID parent that is neither imported
  nor defined fires both ``missing-import`` and ``unresolvable-oid``).
"""

from __future__ import annotations

import asyncio
import difflib
import os
import re
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from trishul_smi.config import CompilerConfig
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_type import MibType
from trishul_smi.parser._constants import BASE_MIBS
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import FetchProtocol
from trishul_smi.reader.chain import ReaderChain
from trishul_smi.reader.localfile import _EXTENSIONS
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.oid_resolver import WELL_KNOWN_OIDS, resolve_oids
from trishul_smi.resolver.resolver import MibResolver

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Finding severity. Values are stable strings (safe for JSON output)."""

    ERROR = "error"
    WARNING = "warning"


class CheckId(str, Enum):
    """Stable identifiers for the v1 lint check set (plus v0.5.1 additions)."""

    MISSING_IMPORT = "missing-import"
    UNDEFINED_TYPE = "undefined-type"
    UNRESOLVABLE_OID = "unresolvable-oid"
    UNUSED_IMPORT = "unused-import"
    DUPLICATE_OID_ARC = "duplicate-oid-arc"
    MISSING_STATUS = "missing-status"
    MISSING_DESCRIPTION = "missing-description"


@dataclass(frozen=True)
class LintFinding:
    """A single defect reported by the lint engine.

    Attributes:
        check: Stable check id (see :class:`CheckId`).
        severity: ``error`` or ``warning``.
        module: Declared name of the module the finding belongs to.
        symbol: The symbol/object most directly involved (imported symbol,
            type reference, object name), or None when not applicable.
        message: Human-readable description.
    """

    check: CheckId
    severity: Severity
    module: str
    symbol: str | None
    message: str


class FixStatus(str, Enum):
    """Outcome of a fixable finding under ``--fix`` (stable strings for JSON)."""

    FIXED = "fixed"
    LEFT = "left"


@dataclass(frozen=True)
class FixedFinding:
    """One fixable finding's outcome under ``--fix``.

    Attributes:
        module: Declared name of the module the finding belongs to.
        check: The fixable check id (``missing-import`` type-role or
            ``unused-import``).
        severity: The original finding's severity.
        symbol: The symbol involved, or None when not applicable.
        file: Local source file path when a local source exists; None when
            the module was not sourced from a local ``--mib-dir`` file.
        status: ``fixed`` or ``left``.
        message: Human description of the fix applied, or the reason the
            finding was left unfixed.
    """

    module: str
    check: CheckId
    severity: Severity
    symbol: str | None
    file: str | None
    status: FixStatus
    message: str


@dataclass(frozen=True)
class LintSummary:
    """Aggregate counts for a lint run."""

    modules_checked: int
    """Number of modules in the resolved closure that were inspected."""
    errors: int
    """Number of findings with severity ``error``."""
    warnings: int
    """Number of findings with severity ``warning``."""


@dataclass(frozen=True)
class LintReport:
    """Result of a lint run.

    Attributes:
        findings: All findings that remain after any ``--fix`` pass,
            deterministically ordered (errors before warnings, then by
            module, check, symbol). Fixed findings are moved out of here into
            ``fixed``.
        summary: Aggregate counts derived from ``findings``.
        resolve_errors: Modules that failed to fetch or parse
            (``{requested name: error message}``). These modules could not
            be inspected and are not included in ``summary.modules_checked``;
            the CLI should surface them separately from findings.
        fixed: Per-finding outcomes of the ``--fix`` pass (only populated
            when ``run_lint(..., fix=True)``): one entry per fixable
            finding, marked ``fixed`` or ``left`` with a reason.
        diffs: ``{file path: unified diff}`` for modules the fixer would
            change. Only populated in dry-run mode (``run_lint(...,
            diff=True)``), which writes nothing.
    """

    findings: list[LintFinding]
    summary: LintSummary
    resolve_errors: dict[str, str]
    fixed: list[FixedFinding] = field(default_factory=list)
    diffs: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Built-in reference vocabulary
# ---------------------------------------------------------------------------

# Base ASN.1 / SMI types that are valid SYNTAX / base_type terminals and need
# no import. A type reference not in this set must be imported or defined
# in-module.
_BASE_TYPES: frozenset[str] = frozenset(
    {
        # ASN.1 built-ins
        "INTEGER",
        "OCTET STRING",
        "OBJECT IDENTIFIER",
        "NULL",
        "SEQUENCE",
        "CHOICE",
        "BIT STRING",
        "BOOLEAN",
        # SMIv2 application types (RFC 2578)
        "Integer32",
        "Counter32",
        "Counter64",
        "Gauge32",
        "Unsigned32",
        "TimeTicks",
        "Opaque",
        "IpAddress",
        "BITS",
        # SMIv1 application types (RFC 1155)
        "Counter",
        "Gauge",
        "NetworkAddress",
    }
)

# Well-known OID roots are usable as OID parents without an import.
_WELL_KNOWN_NAMES: frozenset[str] = frozenset(WELL_KNOWN_OIDS)

# A purely numeric parent (e.g. a TRAP-TYPE ENTERPRISE written as a number)
# is an absolute value, not a symbol reference.
_NUMERIC_PARENT_RE = re.compile(r"^\d+(?:\.\d+)*$")

# Human labels for each reference role, used in finding messages.
_ROLE_LABELS: dict[str, str] = {
    "type": "as a SYNTAX/base type",
    "oid-parent": "as an OID parent",
    "index": "in an INDEX clause",
    "augments": "in an AUGMENTS clause",
    "member": "as a group/notification member",
    "enterprise": "in an ENTERPRISE clause",
    "macro": "as an assignment macro",
}

# Roles that are OID references, not symbol imports: referencing a symbol in
# these positions without importing it is legal SMI (the name resolves across
# the closure, not via an import), so an unresolved-by-import reference here
# is suspicious but not broken — a warning, not an error.
_MEMBER_OID_ROLES: frozenset[str] = frozenset(
    {"oid-parent", "index", "augments", "member", "enterprise"}
)

# Macros whose SMI definition mandates a STATUS clause (RFC 2578 / 2580).
# MODULE-IDENTITY (no STATUS), TRAP-TYPE (RFC 1215, no STATUS), and OBJECT
# IDENTIFIER value assignments are deliberately absent.
_STATUS_MACROS: frozenset[str] = frozenset(
    {
        "OBJECT-TYPE",
        "OBJECT-IDENTITY",
        "NOTIFICATION-TYPE",
        "OBJECT-GROUP",
        "NOTIFICATION-GROUP",
        "MODULE-COMPLIANCE",
        "AGENT-CAPABILITIES",
    }
)

# Macros whose SMI definition carries a DESCRIPTION clause. Adds TRAP-TYPE
# (RFC 1215 DESCRIPTION is optional but applicable); MODULE-IDENTITY is
# covered by the module-level check (MibModule.description), not here, so a
# module is never double-reported.
_DESCRIPTION_MACROS: frozenset[str] = frozenset(
    {
        "OBJECT-TYPE",
        "OBJECT-IDENTITY",
        "NOTIFICATION-TYPE",
        "OBJECT-GROUP",
        "NOTIFICATION-GROUP",
        "MODULE-COMPLIANCE",
        "AGENT-CAPABILITIES",
        "TRAP-TYPE",
    }
)


def _syntax_symbols(syntax: str | None) -> list[str]:
    """Extract the symbol reference(s) from a SYNTAX/base-type string.

    Base types are returned as-is (callers decide whether a base type counts
    as "needs importing"). ``SEQUENCE OF X`` yields the member type ``X``.
    """
    if not syntax:
        return []
    stripped = syntax.strip()
    if stripped.startswith("SEQUENCE OF "):
        member = stripped[len("SEQUENCE OF ") :].strip()
        return [member] if member else []
    return [stripped]


def _iter_references(module: MibModule) -> Iterator[tuple[str, str]]:
    """Yield every structural symbol reference in *module* as ``(symbol, role)``.

    Roles: ``type``, ``oid-parent``, ``index``, ``augments``, ``member``,
    ``enterprise``, ``macro``. Base-type references are included (a module
    that imports ``Integer32`` and uses ``SYNTAX Integer32`` has used it).
    """
    for obj in (*module.objects.values(), *module.notifications.values()):
        for symbol in _syntax_symbols(obj.syntax):
            yield symbol, "type"
        if obj.oid_parent is not None:
            yield obj.oid_parent, "oid-parent"
        for name in obj.index or []:
            yield name, "index"
        if obj.augments is not None:
            yield obj.augments, "augments"
        for name in obj.members or []:
            yield name, "member"
        if obj.enterprise is not None:
            yield obj.enterprise, "enterprise"
        if obj.object_type:
            yield obj.object_type, "macro"
    for typ in module.types.values():
        for symbol in _syntax_symbols(typ.base_type):
            yield symbol, "type"


def _defined_in_module(symbol: str, module: MibModule) -> bool:
    """True if *symbol* names an object, type, or notification in *module*."""
    return symbol in module.objects or symbol in module.types or symbol in module.notifications


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_missing_imports(module: MibModule, findings: list[LintFinding]) -> None:
    """Check (a): symbol referenced but never imported and not defined in-module.

    Severity is role-dependent: a TYPE-position reference (SYNTAX / base
    type) is an ``error`` — the module's output is unusable without the type.
    A MEMBER/OID-position reference (notification OBJECTS member, INDEX,
    AUGMENTS, OID parent, TRAP-TYPE ENTERPRISE) is a ``warning`` — those are
    OID references, legal in SMI without an import.
    """
    imported = set(module.import_reverse_map())
    reported: set[str] = set()
    for symbol, role in _iter_references(module):
        if role == "macro":
            continue  # grammar-validated keyword; not reported (see module docstring)
        if symbol in reported:
            continue
        if _defined_in_module(symbol, module) or symbol in imported:
            continue
        if symbol in _BASE_TYPES or symbol in _WELL_KNOWN_NAMES:
            continue
        if _NUMERIC_PARENT_RE.fullmatch(symbol):
            continue  # absolute numeric value, not a name
        reported.add(symbol)
        findings.append(
            LintFinding(
                check=CheckId.MISSING_IMPORT,
                severity=(Severity.WARNING if role in _MEMBER_OID_ROLES else Severity.ERROR),
                module=module.name,
                symbol=symbol,
                message=(
                    f"{symbol!r} is referenced {_ROLE_LABELS[role]} but is neither "
                    f"imported nor defined in module {module.name!r}"
                ),
            )
        )


def _resolve_base_type(
    symbol: str,
    ctx: MibModule,
    modules_by_name: dict[str, MibModule],
    visited: set[str],
) -> bool:
    """True if *symbol* (referenced from module *ctx*) bottoms out in a base type.

    Walks TEXTUAL-CONVENTION / type-assignment chains through the closure.
    A symbol imported from a BASE_MIBS module is trusted as defined (those
    modules are never fetched into the closure). A cycle or a dead-end
    (provider module absent, provider lacking the symbol, or a non-type
    definition) yields False.
    """
    if symbol in _BASE_TYPES or symbol in _WELL_KNOWN_NAMES:
        return True
    if symbol in visited:
        return False  # circular chain — no base type anywhere
    visited.add(symbol)

    defining = ctx.types.get(symbol)
    if defining is None:
        provider = ctx.import_reverse_map().get(symbol)
        if provider is None:
            return False  # neither imported nor defined — missing-import owns this
        if provider in BASE_MIBS:
            return True  # trusted infrastructure module
        source = modules_by_name.get(provider)
        if source is None:
            return False  # provider failed to resolve — no definition in the closure
        defining = source.types.get(symbol)
        if defining is None:
            return False  # imported from a provider that does not define it
        ctx = source

    base_symbols = _syntax_symbols(defining.base_type)
    if not base_symbols:
        return False  # empty base_type — no base at all
    return all(_resolve_base_type(b, ctx, modules_by_name, visited) for b in base_symbols)


def _check_undefined_type_ref(
    module: MibModule,
    kind: str,
    referent: str,
    symbol: str,
    modules_by_name: dict[str, MibModule],
    findings: list[LintFinding],
) -> None:
    """Report one SYNTAX/base-type reference if its closure walk dead-ends."""
    if symbol in _BASE_TYPES or symbol in _WELL_KNOWN_NAMES:
        return
    # A reference that is neither imported nor defined in-module is
    # reported by missing-import, not here (see module docstring).
    if not (_defined_in_module(symbol, module) or symbol in module.import_reverse_map()):
        return
    if _resolve_base_type(symbol, module, modules_by_name, set()):
        return
    findings.append(
        LintFinding(
            check=CheckId.UNDEFINED_TYPE,
            severity=Severity.ERROR,
            module=module.name,
            symbol=symbol,
            message=(
                f"type reference {symbol!r} (used by {kind} {referent!r}) resolves to "
                f"no TEXTUAL-CONVENTION or base type anywhere in the module closure"
            ),
        )
    )


def _check_undefined_types(
    modules: list[MibModule],
    modules_by_name: dict[str, MibModule],
    findings: list[LintFinding],
) -> None:
    """Check (b): a SYNTAX/import reference with no TC or base type in the closure."""
    for module in modules:
        reported: set[str] = set()
        for obj in (*module.objects.values(), *module.notifications.values()):
            for symbol in _syntax_symbols(obj.syntax):
                if symbol not in reported:
                    reported.add(symbol)
                    _check_undefined_type_ref(
                        module, "object", obj.name, symbol, modules_by_name, findings
                    )
        for typ in module.types.values():
            for symbol in _syntax_symbols(typ.base_type):
                if symbol not in reported:
                    reported.add(symbol)
                    _check_undefined_type_ref(
                        module, "type", typ.name, symbol, modules_by_name, findings
                    )


def _check_unresolvable_oids(modules: list[MibModule], findings: list[LintFinding]) -> None:
    """Check (c): OID parent chain dead-ends (run after ``resolve_oids``)."""
    for module in modules:
        for obj in (*module.objects.values(), *module.notifications.values()):
            if obj.oid_parent is None:
                continue
            if _NUMERIC_PARENT_RE.fullmatch(obj.oid_parent):
                continue  # numeric enterprise (TRAP-TYPE) — absolute, not a name
            findings.append(
                LintFinding(
                    check=CheckId.UNRESOLVABLE_OID,
                    severity=Severity.ERROR,
                    module=module.name,
                    symbol=obj.name,
                    message=(
                        f"OID parent {obj.oid_parent!r} of {obj.name!r} does not resolve "
                        f"to a known OID (parent chain dead-ends)"
                    ),
                )
            )


def _check_unused_imports(module: MibModule, findings: list[LintFinding]) -> None:
    """Check (d): IMPORTS declared but never referenced by the module."""
    used: set[str] = {symbol for symbol, _ in _iter_references(module)}
    for source_module, symbols in module.imports.items():
        for symbol in symbols:
            if symbol in used:
                continue
            findings.append(
                LintFinding(
                    check=CheckId.UNUSED_IMPORT,
                    severity=Severity.WARNING,
                    module=module.name,
                    symbol=symbol,
                    message=(
                        f"imported symbol {symbol!r} from {source_module!r} is never "
                        f"referenced by module {module.name!r}"
                    ),
                )
            )


def _is_tc_shaped(typ: MibType) -> bool:
    """True if *typ* carries a marker only a TEXTUAL-CONVENTION can have.

    The model does not distinguish a TEXTUAL-CONVENTION from a plain type
    assignment (``Foo ::= OCTET STRING``). A plain type assignment never has
    a DISPLAY-HINT or a DESCRIPTION, so either marker identifies a TC; a
    definition with neither is treated as a legal type assignment and is
    exempt from the STATUS/DESCRIPTION clause checks (see module docstring).
    """
    return typ.description is not None or typ.display_hint is not None


def _check_missing_status(module: MibModule, findings: list[LintFinding]) -> None:
    """Check (f): a STATUS-bearing construct has no STATUS clause."""
    for obj in (*module.objects.values(), *module.notifications.values()):
        if obj.object_type not in _STATUS_MACROS:
            continue
        if obj.status is not None:
            continue
        findings.append(
            LintFinding(
                check=CheckId.MISSING_STATUS,
                severity=Severity.WARNING,
                module=module.name,
                symbol=obj.name,
                message=(
                    f"{obj.object_type} {obj.name!r} has no STATUS clause "
                    f"(required by SMI for {obj.object_type})"
                ),
            )
        )
    for typ in module.types.values():
        if typ.status is not None or not _is_tc_shaped(typ):
            continue
        findings.append(
            LintFinding(
                check=CheckId.MISSING_STATUS,
                severity=Severity.WARNING,
                module=module.name,
                symbol=typ.name,
                message=(
                    f"TEXTUAL-CONVENTION {typ.name!r} has no STATUS clause "
                    f"(required by SMI for TEXTUAL-CONVENTION)"
                ),
            )
        )


def _check_missing_description(module: MibModule, findings: list[LintFinding]) -> None:
    """Check (g): a DESCRIPTION-bearing construct has no DESCRIPTION clause."""
    for obj in (*module.objects.values(), *module.notifications.values()):
        if obj.object_type not in _DESCRIPTION_MACROS:
            continue
        if obj.description is not None:
            continue
        findings.append(
            LintFinding(
                check=CheckId.MISSING_DESCRIPTION,
                severity=Severity.WARNING,
                module=module.name,
                symbol=obj.name,
                message=f"{obj.object_type} {obj.name!r} has no DESCRIPTION clause",
            )
        )
    for typ in module.types.values():
        if typ.description is not None or not _is_tc_shaped(typ):
            continue
        findings.append(
            LintFinding(
                check=CheckId.MISSING_DESCRIPTION,
                severity=Severity.WARNING,
                module=module.name,
                symbol=typ.name,
                message=f"TEXTUAL-CONVENTION {typ.name!r} has no DESCRIPTION clause",
            )
        )
    # Module-level check (issue #34): fires only on OBJECT-BEARING SMIv2
    # modules — those that declare objects or notifications — that have no
    # MODULE-IDENTITY DESCRIPTION. A TC-only module (e.g. SNMPv2-TC,
    # SNMPv2-CONF, IPV6-TC) conventionally carries no MODULE-IDENTITY at
    # all, and is legal SMI, so it is never flagged.
    if (
        module.language == "SMIv2"
        and module.description is None
        and (module.objects or module.notifications)
    ):
        findings.append(
            LintFinding(
                check=CheckId.MISSING_DESCRIPTION,
                severity=Severity.WARNING,
                module=module.name,
                symbol=None,
                message=(
                    f"module {module.name!r} has no module-level DESCRIPTION "
                    f"(no MODULE-IDENTITY with a DESCRIPTION clause)"
                ),
            )
        )


def _check_duplicate_oid_arcs(modules: list[MibModule], findings: list[LintFinding]) -> None:
    """Check (e): same absolute OID claimed by more than one object.

    Only objects whose parent chain fully resolved are considered; objects
    with a dead-end parent are already reported by ``unresolvable-oid``.
    The first holder (deterministic: module then name) is treated as the
    canonical claim; each later holder is reported as a duplicate.
    """
    by_path: dict[tuple[int, ...], list[tuple[str, str]]] = {}
    for module in modules:
        for obj in (*module.objects.values(), *module.notifications.values()):
            if obj.oid_parent is None and obj.oid_path:
                by_path.setdefault(tuple(obj.oid_path), []).append((module.name, obj.name))

    for path, holders in by_path.items():
        if len(holders) < 2:
            continue
        holders_sorted = sorted(holders)
        canonical_module, canonical_name = holders_sorted[0]
        parent = ".".join(str(n) for n in path[:-1])
        for holder_module, name in holders_sorted[1:]:
            findings.append(
                LintFinding(
                    check=CheckId.DUPLICATE_OID_ARC,
                    severity=Severity.WARNING,
                    module=holder_module,
                    symbol=name,
                    message=(
                        f"duplicate OID arc {path[-1]} under parent {parent!r}: "
                        f"{'.'.join(str(n) for n in path)} is also claimed by "
                        f"{canonical_name!r} in module {canonical_module!r}"
                    ),
                )
            )


def _run_reference_checks(
    modules: list[MibModule], modules_by_name: dict[str, MibModule]
) -> list[LintFinding]:
    """Run the reference-based checks (a), (b), (d) over the closure.

    MUST run before ``resolve_oids``: that pass mutates objects in place and
    clears ``oid_parent`` on every resolved object, which would otherwise
    erase OID-parent references before the missing-import and unused-import
    checks could see them.
    """
    findings: list[LintFinding] = []
    for module in modules:
        _check_missing_imports(module, findings)
        _check_unused_imports(module, findings)
    _check_undefined_types(modules, modules_by_name, findings)
    return findings


def _run_oid_checks(modules: list[MibModule]) -> list[LintFinding]:
    """Run the OID-state checks (c), (e) over the closure.

    MUST run after ``resolve_oids``: both need the resolved absolute paths
    (resolved parents are marked by ``oid_parent`` becoming None).
    """
    findings: list[LintFinding] = []
    _check_unresolvable_oids(modules, findings)
    _check_duplicate_oid_arcs(modules, findings)
    return findings


def _run_clause_checks(modules: list[MibModule]) -> list[LintFinding]:
    """Run the STATUS/DESCRIPTION clause-presence checks (f), (g).

    Reads model fields only (``status`` / ``description`` / module-level
    ``description``) and is independent of both the reference-based and the
    OID-state checks; it may run in any position relative to
    ``resolve_oids``.
    """
    findings: list[LintFinding] = []
    for module in modules:
        _check_missing_status(module, findings)
        _check_missing_description(module, findings)
    return findings


def _order_findings(findings: list[LintFinding]) -> list[LintFinding]:
    """Order findings deterministically: errors first, then module/check/symbol."""
    findings.sort(
        key=lambda f: (
            0 if f.severity is Severity.ERROR else 1,
            f.module,
            f.check.value,
            f.symbol or "",
        )
    )
    return findings


# ---------------------------------------------------------------------------
# --fix remediation (v0.5.2, plan item 1)
# ---------------------------------------------------------------------------
#
# Only two check kinds are ever allowed to modify a file (plan safety rules):
#   - missing-import in the TYPE role  → add the missing FROM import.
#   - unused-import                    → remove the symbol from the IMPORTS
#                                        clause (dropping the clause entirely
#                                        when it empties).
# Everything else is report-only. Fixes edit ONLY the IMPORTS block of the
# raw source text (span edits on the affected clause lines), are re-parsed
# after editing (rollback on failure), and apply only to local --mib-dir
# files — HTTP/ZIP-sourced modules are report-only.
#
# Token regex for locating and parsing the IMPORTS clause. Comments (``--``
# ...) and quoted strings are consumed as single tokens so the words
# BEGIN/IMPORTS and ``;``/``,`` inside them can never be misread as syntax.
_FIX_TOKEN_RE = re.compile(r'--[^\n]*|"(?:[^"\\]|\\[\s\S])*"|[A-Za-z][A-Za-z0-9\-]*|[,;]')


def _tokenize(text: str) -> list[tuple[str, int, int]]:
    """Yield ``(value, start, end)`` tokens, skipping whitespace/comments/strings."""
    tokens: list[tuple[str, int, int]] = []
    pos = 0
    length = len(text)
    while pos < length:
        match = _FIX_TOKEN_RE.match(text, pos)
        if match is None:
            pos += 1
            continue
        value = match.group(0)
        if value.startswith("--") or value.startswith('"'):
            pos = match.end()
            continue
        tokens.append((value, match.start(), match.end()))
        pos = match.end()
    return tokens


@dataclass
class _ImportClause:
    """One ``symbols FROM module`` clause inside the IMPORTS block."""

    module: str
    symbols: list[tuple[str, int, int]]  # (name, start offset, end offset)
    start: int  # offset of the first symbol
    end: int  # offset just past the last symbol / module reference


@dataclass
class _ImportsBlock:
    """Structural view of the module's IMPORTS clause in the raw source."""

    keyword_start: int
    semicolon: int | None
    clauses: list[_ImportClause]
    end: int  # offset just past the last clause (block content end)


def _parse_imports_block(text: str) -> _ImportsBlock | None:
    """Locate and structurally parse the module IMPORTS clause, if present."""
    tokens = _tokenize(text)
    begin_idx = next((i for i, (value, _, _) in enumerate(tokens) if value == "BEGIN"), None)
    if begin_idx is None or begin_idx + 1 >= len(tokens):
        return None
    index = begin_idx + 1
    if tokens[index][0] == "EXPORTS":
        # SMIv1 allows an ``EXPORTS ... ;`` section before the IMPORTS
        # clause (smiv1.lark module_definition); skip it so IMPORTS is
        # located wherever it sits after BEGIN.
        while index < len(tokens) and tokens[index][0] != ";":
            index += 1
        index += 1
    if index >= len(tokens) or tokens[index][0] != "IMPORTS":
        return None
    keyword_start = tokens[index][1]

    clauses: list[_ImportClause] = []
    current: _ImportClause | None = None
    semicolon: int | None = None
    last_was_comma = False
    index += 1
    while index < len(tokens):
        value, start, end = tokens[index]
        if value == ";":
            semicolon = start
            break
        if value == "FROM":
            if current is not None and index + 1 < len(tokens):
                current.module = tokens[index + 1][0]
                current.end = tokens[index + 1][2]
                clauses.append(current)
                current = None
            index += 2
            last_was_comma = False
            continue
        if value == ",":
            last_was_comma = True
            index += 1
            continue
        if current is None:
            current = _ImportClause(module="", symbols=[(value, start, end)], start=start, end=end)
        elif last_was_comma:
            current.symbols.append((value, start, end))
        else:
            # Multi-word symbol (e.g. "OCTET STRING", "OBJECT IDENTIFIER").
            name, symbol_start, _ = current.symbols[-1]
            current.symbols[-1] = (name + " " + value, symbol_start, end)
        last_was_comma = False
        index += 1

    block_end = clauses[-1].end if clauses else keyword_start
    return _ImportsBlock(
        keyword_start=keyword_start, semicolon=semicolon, clauses=clauses, end=block_end
    )


def _clause_line_span(text: str, clause: _ImportClause) -> tuple[int, int]:
    """Character span of the clause's line(s), including the trailing newline."""
    start = text.rfind("\n", 0, clause.start) + 1
    end = text.find("\n", clause.end)
    if end == -1:
        end = len(text)
    else:
        end += 1
    return start, end


def _clause_is_sole_on_line(text: str, block: _ImportsBlock, clause: _ImportClause) -> bool:
    """True if *clause* is the only token-run on its physical line.

    The grammar permits several import clauses (and even the ``IMPORTS``
    keyword or the terminating ``;``) to share one line, so deleting a
    clause's whole line is only safe when no sibling clause's tokens, the
    keyword, or the ``;`` overlap that line. Otherwise the drop must be a
    span edit over the clause's own tokens only.
    """
    line_start, line_end = _clause_line_span(text, clause)
    if line_start <= block.keyword_start < line_end:
        return False
    if block.semicolon is not None and line_start <= block.semicolon < line_end:
        return False
    for other in block.clauses:
        if other is clause:
            continue
        if other.start < line_end and other.end > line_start:
            return False
    return True


def _block_span(text: str, block: _ImportsBlock) -> tuple[int, int]:
    """Character span of the whole IMPORTS clause (keyword line through ``;``)."""
    start = text.rfind("\n", 0, block.keyword_start) + 1
    marker = block.semicolon if block.semicolon is not None else block.end
    end = text.find("\n", marker)
    if end == -1:
        end = len(text)
    else:
        end += 1
    return start, end


def _block_indent(text: str, block: _ImportsBlock) -> str:
    """Leading whitespace of the first clause line (defaults to four spaces)."""
    if not block.clauses:
        return "    "
    line_start = text.rfind("\n", 0, block.clauses[0].start) + 1
    return text[line_start : block.clauses[0].start] or "    "


def _block_line_ending(text: str, block: _ImportsBlock) -> str:
    """Dominant line ending inside the IMPORTS block (``\\r\\n`` or ``\\n``)."""
    for clause in block.clauses:
        newline = text.find("\n", clause.end)
        if newline > 0 and text[newline - 1] == "\r":
            return "\r\n"
    return "\n"


def _apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping ``(start, end, replacement)`` edits back-to-front.

    Raises:
        ValueError: if any two edits overlap — an overlapping edit set would
            corrupt the text because offsets shift after the first splice.
    """
    ordered = sorted(edits, key=lambda edit: edit[0])
    for i in range(len(ordered) - 1):
        start, end, _ = ordered[i]
        next_start, next_end, _ = ordered[i + 1]
        if next_start < end:
            raise ValueError(
                f"overlapping fix edits: [{start}, {end}) and [{next_start}, {next_end})"
            )
    for start, end, replacement in reversed(ordered):
        text = text[:start] + replacement + text[end:]
    return text


def _is_fixable(finding: LintFinding, module: MibModule) -> bool:
    """True for the two fixable check kinds (plan safety rule 1).

    ``unused-import`` findings are always fixable. ``missing-import``
    findings are fixable only in the TYPE role (SYNTAX / base type): a
    MEMBER/OID-role reference is a legal unimported OID reference, not a
    defect, so those findings are report-only.
    """
    if finding.check is CheckId.UNUSED_IMPORT:
        return True
    if finding.check is CheckId.MISSING_IMPORT:
        return any(
            symbol == finding.symbol and role == "type" for symbol, role in _iter_references(module)
        )
    return False


def _resolve_import_provider(
    symbol: str, module: MibModule, modules_by_name: dict[str, MibModule]
) -> str | None:
    """The single closure module defining *symbol* as a type, or None.

    Only modules other than *module* count; a TYPE-position reference must
    resolve to exactly one provider module in the loaded closure for the
    missing-import fix to apply (plan item 1). Ambiguous (two or more
    providers) and unresolvable (none) symbols are reported, not fixed.
    """
    providers = [
        candidate.name
        for candidate in modules_by_name.values()
        if candidate.name != module.name and symbol in candidate.types
    ]
    return providers[0] if len(providers) == 1 else None


@dataclass
class _FixPlan:
    """Per-module fix plan derived from the fixable findings."""

    removed: dict[str, list[str]]  # provider module -> symbols to drop
    added: dict[str, list[str]]  # provider module -> symbols to add
    results: dict[LintFinding, str]  # finding -> human description
    failed: list[LintFinding]  # fixable findings that cannot be fixed


def _build_fix_plan(
    module: MibModule,
    findings: list[LintFinding],
    modules_by_name: dict[str, MibModule],
) -> _FixPlan:
    """Translate a module's fixable findings into concrete import edits."""
    removed: dict[str, list[str]] = {}
    added: dict[str, list[str]] = {}
    results: dict[LintFinding, str] = {}
    failed: list[LintFinding] = []
    for finding in findings:
        if finding.symbol is None:
            # A fixable finding without a symbol cannot be turned into an
            # import edit — defensive, never reached by the current checks.
            failed.append(finding)
            results[finding] = "finding has no symbol; cannot apply an import edit"
            continue
        if finding.check is CheckId.UNUSED_IMPORT:
            provider = module.import_reverse_map().get(finding.symbol)
            if provider is None:
                failed.append(finding)
                results[finding] = (
                    f"imported symbol {finding.symbol!r} has no recorded provider module"
                )
                continue
            removed.setdefault(provider, []).append(finding.symbol)
            results[finding] = f"removed unused import {finding.symbol!r} from IMPORTS"
        elif finding.check is CheckId.MISSING_IMPORT:
            provider = _resolve_import_provider(finding.symbol, module, modules_by_name)
            if provider is None:
                failed.append(finding)
                results[finding] = (
                    f"symbol {finding.symbol!r} does not resolve to exactly one "
                    f"provider module in the loaded closure"
                )
                continue
            added.setdefault(provider, []).append(finding.symbol)
            results[finding] = f"added import {finding.symbol!r} FROM {provider!r} to IMPORTS"
    return _FixPlan(removed=removed, added=added, results=results, failed=failed)


def _fix_imports_text(text: str, plan: _FixPlan) -> str:
    """Apply a module's fix plan to its raw source text."""
    block = _parse_imports_block(text)
    if block is None:
        if not plan.added:
            return text
        return _insert_new_imports_block(text, plan.added)
    return _edit_imports_block(text, block, plan)


def _edit_imports_block(text: str, block: _ImportsBlock, plan: _FixPlan) -> str:
    """Edit the IMPORTS block for removed/added symbols.

    Only the affected clauses are rewritten (symbol-list span edits) or
    removed (whole-line removal); every other line of the block — and the
    entire rest of the file — keeps its exact bytes.
    """
    rewrite_edits: list[tuple[int, int, str]] = []
    drop_spans: list[tuple[int, int]] = []
    surviving: set[str] = set()
    for clause in block.clauses:
        to_remove = set(plan.removed.get(clause.module, ()))
        to_add = plan.added.get(clause.module, ())
        if not to_remove and not to_add:
            surviving.add(clause.module)
            continue
        current = [name for name, _, _ in clause.symbols]
        target = [name for name in current if name not in to_remove]
        for name in to_add:
            if name not in target:
                target.append(name)
        if not target:
            # Drop the clause's own token span; a whole-line removal is only
            # safe when the clause is the sole token-run on its line (the
            # grammar allows several clauses, the IMPORTS keyword, and the
            # `;` to share a line — deleting the line would silently destroy
            # that neighbouring content, and the re-parse cannot catch it
            # because the imports clause is grammar-optional).
            if _clause_is_sole_on_line(text, block, clause):
                drop_spans.append(_clause_line_span(text, clause))
            else:
                drop_spans.append((clause.start, clause.end))
        else:
            surviving.add(clause.module)
            if target != current:
                # Rewrite only the symbol-list span (first symbol through the
                # last symbol) — the `` FROM module`` tail stays untouched.
                rewrite_edits.append(
                    (
                        clause.symbols[0][1],
                        clause.symbols[-1][2],
                        ", ".join(target),
                    )
                )

    if drop_spans and len(drop_spans) == len(block.clauses):
        # The whole clause empties: either drop the IMPORTS block entirely,
        # or rebuild it from scratch when new imports must be added.
        if not plan.added:
            start, end = _block_span(text, block)
            return text[:start] + text[end:]
        return _rebuild_imports_block(text, block, plan.added)

    if drop_spans or rewrite_edits:
        text = _apply_edits(text, rewrite_edits + [(start, end, "") for start, end in drop_spans])

    new_clauses = [
        (provider, symbols) for provider, symbols in plan.added.items() if provider not in surviving
    ]
    if new_clauses:
        text = _insert_new_clauses(text, new_clauses)
    return text


def _insert_new_clauses(text: str, new_clauses: list[tuple[str, list[str]]]) -> str:
    """Insert ``symbols FROM module`` clauses before the IMPORTS terminator."""
    block = _parse_imports_block(text)
    if block is None:
        return text  # defensive: nothing to anchor on
    indent = _block_indent(text, block)
    line_ending = _block_line_ending(text, block)
    lines = [f"{indent}{', '.join(symbols)} FROM {provider}" for provider, symbols in new_clauses]
    insertion = line_ending.join(lines)

    if block.semicolon is not None:
        ws_start = block.semicolon
        while ws_start > 0 and text[ws_start - 1] in " \t":
            ws_start -= 1
        line_start = text.rfind("\n", 0, block.semicolon) + 1
        if ws_start == line_start:
            # ``;`` sits on its own line: add a full clause line above it.
            return text[:line_start] + insertion + line_ending + text[line_start:]
        # ``;`` trails the last clause on the same line: put the new clause
        # line right before it (the trailing space before ``;`` is consumed).
        return text[:ws_start] + line_ending + insertion + text[block.semicolon :]
    # No terminating ``;`` (grammar-optional): append after the last clause.
    end = text.find("\n", block.end)
    if end == -1:
        end = len(text)
    return text[:end] + line_ending + insertion + text[end:]


def _rebuild_imports_block(text: str, block: _ImportsBlock, added: dict[str, list[str]]) -> str:
    """Replace a fully-emptied IMPORTS block with a fresh one from *added*."""
    start, end = _block_span(text, block)
    indent = _block_indent(text, block)
    line_ending = _block_line_ending(text, block)
    lines = [f"{indent}{', '.join(symbols)} FROM {provider}" for provider, symbols in added.items()]
    new_block = (
        "IMPORTS" + line_ending + line_ending.join(lines) + line_ending + indent + ";" + line_ending
    )
    return text[:start] + new_block + text[end:]


def _insert_new_imports_block(text: str, added: dict[str, list[str]]) -> str:
    """Add a fresh IMPORTS clause in the grammar-required position.

    SMIv2 places IMPORTS directly after BEGIN; SMIv1 allows an
    ``EXPORTS ... ;`` section first (smiv1.lark module_definition), so the
    new clause is inserted after that section when present.
    """
    tokens = _tokenize(text)
    begin_idx = next((i for i, (value, _, _) in enumerate(tokens) if value == "BEGIN"), None)
    if begin_idx is None:
        return text  # defensive: not a parseable module header
    anchor_end = tokens[begin_idx][2]
    index = begin_idx + 1
    if index < len(tokens) and tokens[index][0] == "EXPORTS":
        while index < len(tokens) and tokens[index][0] != ";":
            index += 1
        if index < len(tokens):
            anchor_end = tokens[index][2]  # insert after the EXPORTS terminator
    line_end = text.find("\n", anchor_end)
    line_ending = "\r\n" if line_end > 0 and text[line_end - 1] == "\r" else "\n"
    if line_end == -1:
        line_end = len(text)
    else:
        line_end += 1
    lines = [f"    {', '.join(symbols)} FROM {provider}" for provider, symbols in added.items()]
    block_text = (
        "IMPORTS" + line_ending + line_ending.join(lines) + line_ending + "    ;" + line_ending
    )
    return text[:line_end] + block_text + text[line_end:]


def _locate_local_source_path(name: str, local_dirs: Sequence[Path]) -> Path | None:
    """Find the local --mib-dir file for *name*, or None when not local."""
    for directory in local_dirs:
        for ext in _EXTENSIONS:
            candidate = directory / f"{name}{ext}"
            if candidate.is_file():
                return candidate
    return None


def _read_local_source(path: Path) -> tuple[str, str]:
    """Read the file the way FileReader does; return ``(text, encoding)``.

    Files that are not valid UTF-8 are re-decoded as latin-1 (a 1:1 byte
    mapping) so the write-back round-trips the original bytes exactly
    (issue #24 convention).
    """
    data = path.read_bytes()
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def _atomic_write(path: Path, text: str, encoding: str) -> None:
    """Write *text* to *path* atomically via temp-file rename (MibCache convention)."""
    fd: int | None = None
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fd = None  # fd is now owned by the buffered writer
            fh.write(text.encode(encoding))
        Path(tmp_name).replace(path)  # atomic on POSIX
    except OSError:
        if fd is not None:
            os.close(fd)
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)
        raise


def _split_newlines(text: str) -> list[str]:
    """Split on ``\\n`` only, keeping the newline attached to each line.

    ``str.splitlines()`` additionally splits on ``\\x0b \\x0c \\x1c-\\x1e
    \\x85 \\u2028 \\u2029``, which would segment latin-1 files containing
    form-feed or record-separator bytes differently than the fixer's own
    span math (all of which splits on ``\\n`` only). A trailing newline does
    not produce an extra empty line (matching ``splitlines(keepends=True)``).
    """
    if not text:
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _unified_diff(path: Path, original: str, new: str) -> str:
    """Unified diff (default context) of *original* → *new* for *path*."""
    return "".join(
        difflib.unified_diff(
            _split_newlines(original),
            _split_newlines(new),
            fromfile=str(path),
            tofile=str(path),
        )
    )


def _fixed_finding(finding: LintFinding, file: str | None, message: str) -> FixedFinding:
    return FixedFinding(
        module=finding.module,
        check=finding.check,
        severity=finding.severity,
        symbol=finding.symbol,
        file=file,
        status=FixStatus.FIXED,
        message=message,
    )


def _left_finding(finding: LintFinding, message: str, file: str | None = None) -> FixedFinding:
    return FixedFinding(
        module=finding.module,
        check=finding.check,
        severity=finding.severity,
        symbol=finding.symbol,
        file=file,
        status=FixStatus.LEFT,
        message=message,
    )


async def _apply_fixes(
    findings: list[LintFinding],
    modules_by_name: dict[str, MibModule],
    local_dirs: Sequence[Path],
    parser: SmiParser,
    *,
    diff_only: bool,
) -> tuple[list[LintFinding], list[FixedFinding], dict[str, str]]:
    """Run the ``--fix`` pass over the fixable findings.

    Returns ``(remaining_findings, fixed_entries, diffs_by_file)``. Fixable
    findings that are fixed are moved out of ``remaining`` into
    ``fixed_entries`` (status ``fixed``); fixable findings that cannot be
    fixed stay in ``remaining`` and get a ``left`` entry explaining why.
    """
    remaining: list[LintFinding] = []
    fixed: list[FixedFinding] = []
    diffs: dict[str, str] = {}

    fixable_by_module: dict[str, list[LintFinding]] = {}
    for finding in findings:
        module = modules_by_name.get(finding.module)
        if module is not None and _is_fixable(finding, module):
            fixable_by_module.setdefault(finding.module, []).append(finding)
        else:
            remaining.append(finding)

    for module_name, module_findings in fixable_by_module.items():
        module = modules_by_name[module_name]
        path = _locate_local_source_path(module_name, local_dirs)
        if path is None:
            # Not a local --mib-dir file (HTTP/ZIP/caller-supplied source):
            # out-of-scope sources are report-only (plan safety rule).
            remaining.extend(module_findings)
            fixed.extend(
                _left_finding(
                    f,
                    "source is not a local --mib-dir file; out-of-scope sources are report-only",
                )
                for f in module_findings
            )
            continue

        plan = _build_fix_plan(module, module_findings, modules_by_name)
        if plan.failed:
            remaining.extend(plan.failed)
            fixed.extend(_left_finding(f, plan.results[f], str(path)) for f in plan.failed)
        editable = [f for f in module_findings if f not in set(plan.failed)]
        if not editable:
            continue

        try:
            original, encoding = _read_local_source(path)
        except OSError as exc:
            remaining.extend(editable)
            fixed.extend(
                _left_finding(f, f"cannot read local source {path}: {exc}", str(path))
                for f in editable
            )
            continue

        new_text = _fix_imports_text(original, plan)
        if new_text == original:
            remaining.extend(editable)
            fixed.extend(
                _left_finding(f, "no text change was produced", str(path)) for f in editable
            )
            continue

        # Safety rule: a fix that would leave the file unparseable is
        # detected (re-parse after edit) and rolled back with an error.
        try:
            await asyncio.to_thread(parser.parse, new_text)
        except Exception as exc:  # noqa: BLE001
            remaining.extend(editable)
            fixed.extend(
                _left_finding(
                    f, f"fix would leave the file unparseable; rolled back ({exc})", str(path)
                )
                for f in editable
            )
            continue

        if diff_only:
            diff_text = _unified_diff(path, original, new_text)
            if diff_text:
                diffs[str(path)] = diff_text
        else:
            try:
                _atomic_write(path, new_text, encoding)
            except OSError as exc:
                remaining.extend(editable)
                fixed.extend(
                    _left_finding(f, f"failed to write {path}: {exc}", str(path)) for f in editable
                )
                continue

        fixed.extend(_fixed_finding(f, str(path), plan.results[f]) for f in editable)

    return remaining, fixed, diffs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_lint(
    names: Sequence[str],
    config: CompilerConfig,
    readers: Sequence[FetchProtocol] | None = None,
    *,
    mib_dirs: Sequence[Path] = (),
    use_http: bool = False,
    fix: bool = False,
    diff: bool = False,
) -> LintReport:
    """Lint *names* and their transitive import closure.

    Runs the same resolve pipeline as ``MibCompiler.compile`` (reader chain →
    fetch → parse → compiled-module cache → topological sort → ``resolve_oids``)
    and inspects the resulting closure for the v1 check set. No output files
    are written unless ``fix=True``.

    Reader assembly (each stage falls back to the next):
      1. ``readers`` — caller-supplied readers (e.g. a mock in tests).
      2. ``mib_dirs`` — one :class:`FileReader` per existing directory.
      3. ``use_http`` — an :class:`HttpReader` over ``config.sources``
         (imported lazily so httpx stays an optional import for library use).

    Fixing (``fix=True``):
      Applies the two fixable check kinds (type-role ``missing-import`` and
      ``unused-import``) to the local ``--mib-dir`` source files, writing them
      atomically (temp-file rename). Modules not sourced from a local
      ``--mib-dir`` file (HTTP/ZIP/caller-supplied readers) are report-only.
      Findings that were fixed are moved out of ``LintReport.findings`` into
      ``LintReport.fixed`` (status ``fixed``); fixable findings that could not
      be fixed stay in ``findings`` and are listed in ``fixed`` as ``left``
      with a reason. With ``diff=True`` nothing is written — the planned
      edits are returned as unified diffs in ``LintReport.diffs`` instead.

    Note that the compiled-module cache is used when ``config.cache_dir`` is
    set; ``resolve_oids`` mutates the resolved modules in memory, matching
    ``MibCompiler.compile`` behaviour.

    Returns:
        A :class:`LintReport` with findings (ordered, errors first) and a
        summary. Modules that could not be fetched or parsed are reported in
        ``LintReport.resolve_errors``, not as findings.

    Raises:
        MibSizeLimitError: if a source exceeds ``config.max_mib_size``
            (configuration error — propagates immediately, as in compile).
        CircularDependencyError: if the import graph contains a cycle.
    """
    from trishul_smi.reader.localfile import FileReader

    chain_readers: list[FetchProtocol] = list(readers) if readers else []
    local_dirs: list[Path] = []
    for directory in mib_dirs:
        if directory.is_dir():
            local_dirs.append(directory)
            chain_readers.append(FileReader(directory, max_size=config.max_mib_size))

    if use_http:
        from trishul_smi.reader.httpclient import HttpReader

        async with HttpReader(
            *config.sources,
            timeout=config.http_timeout,
            retries=config.http_retries,
            max_size=config.max_mib_size,
        ) as http:
            chain_readers.append(http)
            return await _lint_async(names, config, chain_readers, local_dirs, fix=fix, diff=diff)

    return await _lint_async(names, config, chain_readers, local_dirs, fix=fix, diff=diff)


async def _lint_async(
    names: Sequence[str],
    config: CompilerConfig,
    chain_readers: list[FetchProtocol],
    local_dirs: Sequence[Path],
    *,
    fix: bool = False,
    diff: bool = False,
) -> LintReport:
    """Resolve, inspect, and build the report for an assembled reader chain."""
    chain = ReaderChain(*chain_readers)
    cache = (
        MibCache(config.cache_dir, config.cache_ttl_days) if config.cache_dir is not None else None
    )
    resolver = MibResolver(chain, SmiParser(), cache)
    resolve_result = await resolver.resolve(list(names))

    modules = resolve_result.modules
    modules_by_name = {module.name: module for module in modules}
    parser = SmiParser()

    # Reference-based checks first: resolve_oids clears oid_parent on every
    # resolved object, which would erase OID-parent references the
    # missing-import / unused-import checks need to see.
    findings = _run_reference_checks(modules, modules_by_name)
    findings.extend(_run_clause_checks(modules))
    resolve_oids(modules)
    findings.extend(_run_oid_checks(modules))
    findings = _order_findings(findings)

    fixed: list[FixedFinding] = []
    diffs: dict[str, str] = {}
    if fix or diff:
        findings, fixed, diffs = await _apply_fixes(
            findings, modules_by_name, local_dirs, parser, diff_only=diff
        )
        findings = _order_findings(findings)

    summary = LintSummary(
        modules_checked=len(modules),
        errors=sum(1 for f in findings if f.severity is Severity.ERROR),
        warnings=sum(1 for f in findings if f.severity is Severity.WARNING),
    )
    return LintReport(
        findings=findings,
        summary=summary,
        resolve_errors={name: str(exc) for name, exc in resolve_result.errors.items()},
        fixed=fixed,
        diffs=diffs,
    )


# ---------------------------------------------------------------------------
# Stable renderings (used by the CLI; safe for CI)
# ---------------------------------------------------------------------------


def lint_report_to_dict(report: LintReport) -> dict[str, Any]:
    """Serialize a :class:`LintReport` to a stable, JSON-ready document.

    Shape (keys are part of the public contract and stay stable)::

        {
          "findings": [
            {"check": "missing-import", "severity": "error",
             "module": "A-MIB", "symbol": "MysteryType",
             "message": "..."},
            ...
          ],
          "summary": {"modules_checked": 2, "errors": 1, "warnings": 1},
          "resolve_errors": {"NO-SUCH-MIB": "MIB 'NO-SUCH-MIB' not found ..."},
          "fixed": [
            {"check": "unused-import", "severity": "warning",
             "module": "D-MIB", "symbol": "Integer32", "file": "/path/D-MIB",
             "status": "fixed", "message": "removed unused import ..."},
            ...
          ],
          "diffs": {"/path/D-MIB": "--- /path/D-MIB\\n+++ /path/D-MIB\\n@@"}
        }

    ``severity`` and ``check`` are the stable string values of
    :class:`Severity` / :class:`CheckId`; ``symbol`` is null when a finding
    has no symbol; ``resolve_errors`` maps each module that could not be
    fetched or parsed to its error message. ``fixed`` lists the outcomes of
    the ``--fix`` pass (empty on plain lint runs) and ``diffs`` maps each
    local file the fixer would change to its unified diff (only populated in
    ``--diff`` dry-run mode).
    """
    return {
        "findings": [
            {
                "check": finding.check.value,
                "severity": finding.severity.value,
                "module": finding.module,
                "symbol": finding.symbol,
                "message": finding.message,
            }
            for finding in report.findings
        ],
        "summary": {
            "modules_checked": report.summary.modules_checked,
            "errors": report.summary.errors,
            "warnings": report.summary.warnings,
        },
        "resolve_errors": dict(report.resolve_errors),
        "fixed": [
            {
                "check": entry.check.value,
                "severity": entry.severity.value,
                "module": entry.module,
                "symbol": entry.symbol,
                "file": entry.file,
                "status": entry.status.value,
                "message": entry.message,
            }
            for entry in report.fixed
        ],
        "diffs": dict(report.diffs),
    }


def _format_finding_line(finding: LintFinding) -> str:
    """One text-mode line for a finding: ``[module] symbol (check): message``."""
    symbol = finding.symbol if finding.symbol is not None else "-"
    return f"[{finding.module}] {symbol} ({finding.check.value}): {finding.message}"


def _format_fix_line(entry: FixedFinding) -> str:
    """One text-mode line for a --fix outcome (same shape as a finding line)."""
    symbol = entry.symbol if entry.symbol is not None else "-"
    return f"[{entry.module}] {symbol} ({entry.check.value}): {entry.message}"


def format_lint_report_text(report: LintReport) -> str:
    """Render a :class:`LintReport` for ``tsmi lint`` text mode.

    Findings are grouped by severity (errors first), one line per finding;
    the ``--fix`` outcomes follow under ``Fixed:`` / ``Left (not fixed):``
    headings; modules that failed to fetch or parse are listed under an
    ``Unresolved modules`` heading; a single ``Summary:`` line closes the
    block. Sections that have nothing to report are omitted.
    """
    lines: list[str] = []
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    warnings = [f for f in report.findings if f.severity is Severity.WARNING]

    if errors:
        lines.append("Errors:")
        lines.extend(f"  {_format_finding_line(f)}" for f in errors)
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"  {_format_finding_line(f)}" for f in warnings)

    fixed_entries = [f for f in report.fixed if f.status is FixStatus.FIXED]
    left_entries = [f for f in report.fixed if f.status is FixStatus.LEFT]
    if fixed_entries:
        lines.append("Fixed:")
        lines.extend(f"  {_format_fix_line(f)}" for f in fixed_entries)
    if left_entries:
        lines.append("Left (not fixed):")
        lines.extend(f"  {_format_fix_line(f)}" for f in left_entries)

    if report.resolve_errors:
        lines.append("Unresolved modules:")
        for name, message in sorted(report.resolve_errors.items()):
            lines.append(f"  [{name}]: {message}")

    modules = report.summary.modules_checked
    summary_parts = [
        f"{modules} module{'s' if modules != 1 else ''} checked",
        f"{report.summary.errors} error{'s' if report.summary.errors != 1 else ''}",
        f"{report.summary.warnings} warning{'s' if report.summary.warnings != 1 else ''}",
    ]
    unresolved = len(report.resolve_errors)
    if unresolved:
        summary_parts.append(f"{unresolved} unresolved module{'s' if unresolved != 1 else ''}")
    lines.append("Summary: " + ", ".join(summary_parts))

    return "\n".join(lines)
