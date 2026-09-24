"""MIB lint engine — validation mode (v0.5.0 plan item 3).

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
                                module-level description (a SMIv2 module with
                                no MODULE-IDENTITY DESCRIPTION).

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
  clause).
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

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
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
        findings: All findings, deterministically ordered (errors before
            warnings, then by module, check, symbol).
        summary: Aggregate counts derived from ``findings``.
        resolve_errors: Modules that failed to fetch or parse
            (``{requested name: error message}``). These modules could not
            be inspected and are not included in ``summary.modules_checked``;
            the CLI should surface them separately from findings.
    """

    findings: list[LintFinding]
    summary: LintSummary
    resolve_errors: dict[str, str]


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
    if module.language == "SMIv2" and module.description is None:
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
# Entry point
# ---------------------------------------------------------------------------


async def run_lint(
    names: Sequence[str],
    config: CompilerConfig,
    readers: Sequence[FetchProtocol] | None = None,
    *,
    mib_dirs: Sequence[Path] = (),
    use_http: bool = False,
) -> LintReport:
    """Lint *names* and their transitive import closure.

    Runs the same resolve pipeline as ``MibCompiler.compile`` (reader chain →
    fetch → parse → compiled-module cache → topological sort → ``resolve_oids``)
    and inspects the resulting closure for the v1 check set. No output files
    are written.

    Reader assembly (each stage falls back to the next):
      1. ``readers`` — caller-supplied readers (e.g. a mock in tests).
      2. ``mib_dirs`` — one :class:`FileReader` per existing directory.
      3. ``use_http`` — an :class:`HttpReader` over ``config.sources``
         (imported lazily so httpx stays an optional import for library use).

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
    for directory in mib_dirs:
        if directory.is_dir():
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
            return await _lint_async(names, config, chain_readers)

    return await _lint_async(names, config, chain_readers)


async def _lint_async(
    names: Sequence[str],
    config: CompilerConfig,
    chain_readers: list[FetchProtocol],
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

    # Reference-based checks first: resolve_oids clears oid_parent on every
    # resolved object, which would erase OID-parent references the
    # missing-import / unused-import checks need to see.
    findings = _run_reference_checks(modules, modules_by_name)
    findings.extend(_run_clause_checks(modules))
    resolve_oids(modules)
    findings.extend(_run_oid_checks(modules))

    summary = LintSummary(
        modules_checked=len(modules),
        errors=sum(1 for f in findings if f.severity is Severity.ERROR),
        warnings=sum(1 for f in findings if f.severity is Severity.WARNING),
    )
    return LintReport(
        findings=_order_findings(findings),
        summary=summary,
        resolve_errors={name: str(exc) for name, exc in resolve_result.errors.items()},
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
          "resolve_errors": {"NO-SUCH-MIB": "MIB 'NO-SUCH-MIB' not found ..."}
        }

    ``severity`` and ``check`` are the stable string values of
    :class:`Severity` / :class:`CheckId`; ``symbol`` is null when a finding
    has no symbol; ``resolve_errors`` maps each module that could not be
    fetched or parsed to its error message.
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
    }


def _format_finding_line(finding: LintFinding) -> str:
    """One text-mode line for a finding: ``[module] symbol (check): message``."""
    symbol = finding.symbol if finding.symbol is not None else "-"
    return f"[{finding.module}] {symbol} ({finding.check.value}): {finding.message}"


def format_lint_report_text(report: LintReport) -> str:
    """Render a :class:`LintReport` for ``tsmi lint`` text mode.

    Findings are grouped by severity (errors first), one line per finding;
    modules that failed to fetch or parse are listed under an
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
