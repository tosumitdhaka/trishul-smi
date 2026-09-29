"""Tests for trishul_smi.lint — the v0.5.0 MIB lint engine (library core).

Fixture strategy: one minimal MIB (plus a dependency where a check needs a
closure) per check, each crafted to trigger EXACTLY that check and nothing
else; plus one clean module that must produce zero findings. Every test
asserts the finding count, check id, severity, module, and symbol so the
"exactly this check" property is pinned.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.helpers import MockReader
from trishul_smi.config import CompilerConfig
from trishul_smi.lint import (
    CheckId,
    FixedFinding,
    FixStatus,
    LintFinding,
    LintReport,
    LintSummary,
    Severity,
    _apply_edits,
    _apply_fixes,
    _atomic_write,
    _block_indent,
    _block_span,
    _build_fix_plan,
    _check_missing_description,
    _check_missing_status,
    _clause_line_span,
    _fix_imports_text,
    _FixPlan,
    _insert_new_clauses,
    _insert_new_imports_block,
    _parse_imports_block,
    _run_reference_checks,
    _split_newlines,
    _syntax_symbols,
    format_lint_report_text,
    lint_report_to_dict,
    run_lint,
)
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.models.mib_type import MibType
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.resolver.resolver import _source_fingerprint

# ---------------------------------------------------------------------------
# Fixtures — one per check
# ---------------------------------------------------------------------------

# (a) missing-import, TYPE role: `MysteryType` is referenced in SYNTAX but
# never imported and not defined in-module. Not a base type, so nothing else
# fires. A broken type reference stays error-severity.
MISSING_IMPORT_MIB = """
A-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;
aMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Missing-import fixture."
    ::= { 1 3 }
ghostObj OBJECT-TYPE
    SYNTAX      MysteryType
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "References a symbol that is neither imported nor defined."
    ::= { aMIB 1 }
END
"""

# (a2) missing-import, MEMBER role: the notification's OBJECTS clause lists
# `ifIndex` and `ifDescr` from another module without importing them. Member
# references are OID references — legal SMI without an import — so both
# findings are WARNINGs, not errors.
MEMBER_ROLE_MISSING_IMPORT_MIB = """
G-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, NOTIFICATION-TYPE FROM SNMPv2-SMI ;
gMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Missing-import member-role fixture."
    ::= { 1 10 }
gTrap NOTIFICATION-TYPE
    OBJECTS     { ifIndex, ifDescr }
    STATUS      current
    DESCRIPTION "OBJECTS members from another module, not imported."
    ::= { gMIB 1 }
END
"""

# (b) undefined-type: A-MIB imports `GhostBase` FROM B-MIB (so it is not a
# missing import) but B-MIB defines no such type, so the type chain
# dead-ends inside the closure. B-MIB itself is clean.
UNDEFINED_TYPE_IMPORTER_MIB = """
A-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    GhostBase FROM B-MIB ;
aMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Undefined-type fixture."
    ::= { 1 3 }
ghostObj OBJECT-TYPE
    SYNTAX      GhostBase
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses an imported type the provider does not define."
    ::= { aMIB 1 }
END
"""

UNDEFINED_TYPE_PROVIDER_MIB = """
B-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
bMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Provider module; defines no GhostBase type."
    ::= { 1 4 }
END
"""

# (c) unresolvable-oid: `deadEndObj` is parented on `cType`, a type
# assignment that carries no OID, so resolve_oids leaves the parent chain
# dead. `cType` IS defined in-module, so missing-import cannot fire.
UNRESOLVABLE_OID_MIB = """
C-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
cMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Unresolvable-OID fixture."
    ::= { 1 5 }
deadEndObj OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Parent is a type with no OID of its own."
    ::= { cType 1 }
cType ::= OCTET STRING
END
"""

# (d) unused-import: `Integer32` is imported but the object's SYNTAX is the
# base type OCTET STRING, so the import is never referenced.
UNUSED_IMPORT_MIB = """
D-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
dMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Unused-import fixture."
    ::= { 1 6 }
dObj OBJECT-TYPE
    SYNTAX      OCTET STRING
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses only base types; Integer32 is never referenced."
    ::= { dMIB 1 }
END
"""

# (e) duplicate-oid-arc: `firstObj` and `secondObj` both claim arc 1 under
# `eMIB`, resolving to the same absolute OID 1.7.1.
DUPLICATE_OID_ARC_MIB = """
E-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
eMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Duplicate-OID-arc fixture."
    ::= { 1 7 }
firstObj OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "First claim on arc 1."
    ::= { eMIB 1 }
secondObj OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Second claim on the same arc 1."
    ::= { eMIB 1 }
END
"""

# Clean module: every import is used, every reference is satisfied, OIDs
# resolve, no duplicate arcs — must produce zero findings.
CLEAN_MIB = """
CLEAN-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
cleanMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Clean module — must produce zero findings."
    ::= { 1 8 }
cleanObj OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A well-formed object."
    ::= { cleanMIB 1 }
END
"""


# (f) missing-status fires on a STATUS-bearing macro whose STATUS clause is
# absent. The strict v1 grammar currently requires STATUS on every SMIv2
# macro, so no text fixture can carry this omission and parse — the check is
# exercised at the model level (see TestMissingStatus).
#
# (g) missing-description, object level: `trap` is a TRAP-TYPE whose
# DESCRIPTION clause is optional per RFC 1215 and therefore parseable with
# the clause omitted. Everything else in the module is compliant, so this is
# the only finding.
MISSING_DESCRIPTION_TRAP_MIB = """
T-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, TRAP-TYPE FROM SNMPv2-SMI ;
tMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Missing-description trap fixture."
    ::= { 1 3 }
trap TRAP-TYPE
    ENTERPRISE tMIB
    ::= 1
END
"""

# (g) missing-description, module level: an SMIv2 module that declares
# objects but has no MODULE-IDENTITY has no module-level DESCRIPTION. The
# one object is fully compliant otherwise (SYNTAX/MAX-ACCESS/STATUS/
# DESCRIPTION), so the module-level finding is the only one. Object-bearing
# modules without MODULE-IDENTITY are the ones the module-level check is
# scoped to (issue #34).
MISSING_DESCRIPTION_MODULE_MIB = """
O-MIB DEFINITIONS ::= BEGIN
IMPORTS
    OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
oObj OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "An object-bearing module with no MODULE-IDENTITY."
    ::= { 1 3 }
END
"""

# (g) missing-description, module level: a TC-only module (modelled on
# SNMPv2-TC — types but no objects and no MODULE-IDENTITY) is legal SMI and
# must NOT trigger the module-level check (issue #34). The one TC is fully
# compliant (STATUS + DESCRIPTION + SYNTAX), so this module must produce
# zero findings.
TC_ONLY_MODULE_MIB = """
R-MIB DEFINITIONS ::= BEGIN
IMPORTS
    DisplayString FROM SNMPv2-TC ;
MyTc ::= TEXTUAL-CONVENTION
    STATUS      current
    DESCRIPTION "A well-formed TC; the module has no MODULE-IDENTITY."
    SYNTAX      DisplayString (SIZE (0..255))
END
"""


# (g) missing-description, module level: a root/infrastructure module with
# no OBJECT-TYPE instances and no notifications (bare OBJECT IDENTIFIER /
# OBJECT-IDENTITY registry shape — the SNMPv2-SMI class) is legal SMI and
# must NOT trigger the module-level check. Regression surfaced by the
# v0.5.3 #41 dialect fix, which correctly labels import-free SMIv2 modules
# (SNMPv2-SMI previously parsed as SMIv1 and skipped the SMIv2-only check).
OID_ASSIGNMENT_ONLY_MODULE_MIB = """
ROOT-SMI DEFINITIONS ::= BEGIN
rootOid OBJECT IDENTIFIER ::= { 1 3 6 }
registryEntry OBJECT-IDENTITY
    STATUS      current
    DESCRIPTION "A registry-style identity, not an OBJECT-TYPE instance."
    ::= { rootOid 1 }
END
"""


# Regression: an import used ONLY as an OID parent must count as a use for
# the unused-import check. resolve_oids clears oid_parent on resolved
# objects, so the reference-based checks must run before that pass.
PARENT_MIB = """
PARENT-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-IDENTITY FROM SNMPv2-SMI ;
parentMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "OID-parent-import fixture parent."
    ::= { 1 9 }
vendorRoot OBJECT-IDENTITY
    STATUS      current
    DESCRIPTION "Root imported by the child."
    ::= { parentMIB 1 }
END
"""

OID_PARENT_IMPORT_MIB = """
F-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    vendorRoot FROM PARENT-MIB ;
fMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Uses vendorRoot only as an OID parent."
    ::= { vendorRoot 1 }
fObj OBJECT-TYPE
    SYNTAX      INTEGER
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Child object."
    ::= { fMIB 1 }
END
"""


def _lint(texts: dict[str, str], names: list[str] | None = None) -> LintReport:
    """Run the lint engine over in-memory texts via a mock reader."""
    config = CompilerConfig(cache_dir=None)
    report = asyncio.run(run_lint(names or list(texts), config, readers=[MockReader(texts)]))
    return report


def _unused_finding(module: str, symbol: str = "Integer32") -> LintFinding:
    """A fixable unused-import finding for direct ``_apply_fixes`` tests."""
    return LintFinding(
        check=CheckId.UNUSED_IMPORT,
        severity=Severity.WARNING,
        module=module,
        symbol=symbol,
        message="unused",
    )


# ---------------------------------------------------------------------------
# Check (a): missing imports
# ---------------------------------------------------------------------------


class TestMissingImport:
    def test_type_role_unresolved_reference_is_error(self) -> None:
        # TYPE role: a broken SYNTAX reference stays error-severity.
        report = _lint({"A-MIB": MISSING_IMPORT_MIB})
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.check is CheckId.MISSING_IMPORT
        assert finding.severity is Severity.ERROR
        assert finding.module == "A-MIB"
        assert finding.symbol == "MysteryType"
        assert "MysteryType" in finding.message

    def test_summary_counts(self) -> None:
        report = _lint({"A-MIB": MISSING_IMPORT_MIB})
        assert report.summary.modules_checked == 1
        assert report.summary.errors == 1
        assert report.summary.warnings == 0


class TestMissingImportMemberRole:
    def test_member_role_unresolved_reference_is_warning(self) -> None:
        # MEMBER role: notification OBJECTS are OID references, legal without
        # an import, so unresolved-by-import references are warnings.
        report = _lint({"G-MIB": MEMBER_ROLE_MISSING_IMPORT_MIB})
        assert len(report.findings) == 2
        assert all(f.check is CheckId.MISSING_IMPORT for f in report.findings)
        assert all(f.severity is Severity.WARNING for f in report.findings)
        assert all(f.module == "G-MIB" for f in report.findings)
        assert {f.symbol for f in report.findings} == {"ifDescr", "ifIndex"}

    def test_summary_counts(self) -> None:
        report = _lint({"G-MIB": MEMBER_ROLE_MISSING_IMPORT_MIB})
        assert report.summary.modules_checked == 1
        assert report.summary.errors == 0
        assert report.summary.warnings == 2


# ---------------------------------------------------------------------------
# Check (b): undefined types
# ---------------------------------------------------------------------------


class TestUndefinedType:
    def test_reports_exactly_one_finding(self) -> None:
        report = _lint(
            {"A-MIB": UNDEFINED_TYPE_IMPORTER_MIB, "B-MIB": UNDEFINED_TYPE_PROVIDER_MIB},
            names=["A-MIB"],
        )
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.check is CheckId.UNDEFINED_TYPE
        assert finding.severity is Severity.ERROR
        assert finding.module == "A-MIB"
        assert finding.symbol == "GhostBase"
        assert "GhostBase" in finding.message

    def test_closure_includes_transitive_dependency(self) -> None:
        report = _lint(
            {"A-MIB": UNDEFINED_TYPE_IMPORTER_MIB, "B-MIB": UNDEFINED_TYPE_PROVIDER_MIB},
            names=["A-MIB"],
        )
        # B-MIB was pulled in as a dependency and inspected; it is clean.
        assert report.summary.modules_checked == 2
        assert report.findings[0].module == "A-MIB"


# ---------------------------------------------------------------------------
# Check (c): unresolvable OIDs
# ---------------------------------------------------------------------------


class TestUnresolvableOid:
    def test_reports_exactly_one_finding(self) -> None:
        report = _lint({"C-MIB": UNRESOLVABLE_OID_MIB})
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.check is CheckId.UNRESOLVABLE_OID
        assert finding.severity is Severity.ERROR
        assert finding.module == "C-MIB"
        assert finding.symbol == "deadEndObj"
        assert "cType" in finding.message


# ---------------------------------------------------------------------------
# Check (d): unused imports
# ---------------------------------------------------------------------------


class TestUnusedImport:
    def test_reports_exactly_one_finding(self) -> None:
        report = _lint({"D-MIB": UNUSED_IMPORT_MIB})
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.check is CheckId.UNUSED_IMPORT
        assert finding.severity is Severity.WARNING
        assert finding.module == "D-MIB"
        assert finding.symbol == "Integer32"

    def test_summary_counts(self) -> None:
        report = _lint({"D-MIB": UNUSED_IMPORT_MIB})
        assert report.summary.errors == 0
        assert report.summary.warnings == 1


# ---------------------------------------------------------------------------
# Check (e): duplicate OID arcs
# ---------------------------------------------------------------------------


class TestDuplicateOidArc:
    def test_reports_exactly_one_finding(self) -> None:
        report = _lint({"E-MIB": DUPLICATE_OID_ARC_MIB})
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.check is CheckId.DUPLICATE_OID_ARC
        assert finding.severity is Severity.WARNING
        assert finding.module == "E-MIB"
        assert finding.symbol == "secondObj"
        assert "firstObj" in finding.message

    def test_summary_counts(self) -> None:
        report = _lint({"E-MIB": DUPLICATE_OID_ARC_MIB})
        assert report.summary.errors == 0
        assert report.summary.warnings == 1


# ---------------------------------------------------------------------------
# Check (f): missing status
# ---------------------------------------------------------------------------


class TestMissingStatus:
    def test_reports_missing_status_on_object(self) -> None:
        # Model-level fixture: the v1 grammar requires STATUS on every SMIv2
        # macro, so a text fixture omitting it cannot parse (see module
        # docstring for the scope rationale).
        module = MibModule(
            name="S-MIB",
            language="SMIv2",
            objects={
                "sObj": MibObject(
                    name="sObj",
                    oid="1.3.6.1.2.1.1",
                    object_type="OBJECT-TYPE",
                    status=None,
                )
            },
        )
        findings: list[LintFinding] = []
        _check_missing_status(module, findings)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.check is CheckId.MISSING_STATUS
        assert finding.severity is Severity.WARNING
        assert finding.module == "S-MIB"
        assert finding.symbol == "sObj"
        assert "STATUS" in finding.message

    def test_reports_missing_status_on_tc_shaped_type(self) -> None:
        # A TEXTUAL-CONVENTION marker (here a DESCRIPTION) identifies a type
        # as a TC; plain type assignments carry neither marker and are exempt.
        module = MibModule(
            name="S-MIB",
            language="SMIv2",
            types={
                "MyTc": MibType(
                    name="MyTc",
                    base_type="OCTET STRING",
                    description="has a description but no status",
                )
            },
        )
        findings: list[LintFinding] = []
        _check_missing_status(module, findings)
        assert len(findings) == 1
        assert findings[0].check is CheckId.MISSING_STATUS
        assert findings[0].symbol == "MyTc"

    def test_silent_on_compliant_module(self) -> None:
        report = _lint({"CLEAN-MIB": CLEAN_MIB})
        assert not [f for f in report.findings if f.check is CheckId.MISSING_STATUS]


# ---------------------------------------------------------------------------
# Check (g): missing description
# ---------------------------------------------------------------------------


class TestMissingDescription:
    def test_reports_missing_description_on_trap_type(self) -> None:
        report = _lint({"T-MIB": MISSING_DESCRIPTION_TRAP_MIB})
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.check is CheckId.MISSING_DESCRIPTION
        assert finding.severity is Severity.WARNING
        assert finding.module == "T-MIB"
        assert finding.symbol == "trap"
        assert "DESCRIPTION" in finding.message

    def test_reports_module_level_missing_description(self) -> None:
        # An OBJECT-bearing module without MODULE-IDENTITY fires the
        # module-level check (issue #34).
        report = _lint({"O-MIB": MISSING_DESCRIPTION_MODULE_MIB})
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.check is CheckId.MISSING_DESCRIPTION
        assert finding.severity is Severity.WARNING
        assert finding.module == "O-MIB"
        assert finding.symbol is None

    def test_tc_only_module_no_module_level_finding(self) -> None:
        # A TC-only module (types but no objects, no MODULE-IDENTITY — the
        # SNMPv2-TC / SNMPv2-CONF / IPV6-TC shape) is legal SMI and must not
        # be flagged at the module level (issue #34).
        report = _lint({"R-MIB": TC_ONLY_MODULE_MIB})
        assert report.findings == []
        assert report.summary.modules_checked == 1

    def test_oid_assignment_only_module_no_module_level_finding(self) -> None:
        # A root/infrastructure module whose objects are not OBJECT-TYPE
        # instances (bare OBJECT IDENTIFIER / OBJECT-IDENTITY registry
        # shape — the SNMPv2-SMI class) is legal SMI without
        # MODULE-IDENTITY and must not fire the module-level check.
        # Regression surfaced by the v0.5.3 #41 dialect fix, which
        # correctly labels such import-free SMIv2 modules.
        report = _lint({"ROOT-SMI": OID_ASSIGNMENT_ONLY_MODULE_MIB})
        assert report.findings == []
        assert report.summary.modules_checked == 1

    def test_silent_on_compliant_module(self) -> None:
        report = _lint({"CLEAN-MIB": CLEAN_MIB})
        assert not [f for f in report.findings if f.check is CheckId.MISSING_DESCRIPTION]


# ---------------------------------------------------------------------------
# Clean module and report plumbing
# ---------------------------------------------------------------------------


class TestCleanModule:
    def test_zero_findings(self) -> None:
        report = _lint({"CLEAN-MIB": CLEAN_MIB})
        assert report.findings == []
        assert report.summary.modules_checked == 1
        assert report.summary.errors == 0
        assert report.summary.warnings == 0

    def test_resolve_errors_empty(self) -> None:
        report = _lint({"CLEAN-MIB": CLEAN_MIB})
        assert report.resolve_errors == {}


class TestResolveErrors:
    def test_unfetchable_module_reported_separately(self) -> None:
        report = _lint({"A-MIB": MISSING_IMPORT_MIB}, names=["A-MIB", "NO-SUCH-MIB"])
        assert "NO-SUCH-MIB" in report.resolve_errors
        # The fetchable module still lints normally.
        assert report.summary.modules_checked == 1
        assert report.findings[0].check is CheckId.MISSING_IMPORT


class TestOidParentImportIsAUse:
    def test_import_used_only_as_oid_parent_not_flagged_unused(self) -> None:
        report = _lint(
            {"PARENT-MIB": PARENT_MIB, "F-MIB": OID_PARENT_IMPORT_MIB},
            names=["F-MIB"],
        )
        assert report.findings == []
        assert report.summary.modules_checked == 2


# ---------------------------------------------------------------------------
# Issue #37 — detection semantics: imported TEXTUAL-CONVENTION macro and
# macro-definition body symbol uses count as uses for unused-import.
# ---------------------------------------------------------------------------

# Imports the TEXTUAL-CONVENTION macro and defines a TC: the macro keyword in
# `MyTc ::= TEXTUAL-CONVENTION` is the reference, so the import is used.
TC_MACRO_USE_MIB = """
R-MIB DEFINITIONS ::= BEGIN
IMPORTS
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;
MyTc ::= TEXTUAL-CONVENTION
    STATUS      current
    DESCRIPTION "A TC."
    SYNTAX      OCTET STRING
END
"""

# Imports the TEXTUAL-CONVENTION macro but defines only a PLAIN type
# assignment — the macro has not been used and must still be flagged.
PLAIN_TYPE_TC_IMPORT_MIB = """
R-MIB DEFINITIONS ::= BEGIN
IMPORTS
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;
MyType ::= OCTET STRING
END
"""

# Modelled on SNMPv2-CONF: ObjectName / NotificationName / ObjectSyntax are
# used ONLY inside the macro-definition body (value(ObjectName) & co.). They
# must count as uses; macro-internal tokens that are not imported (Status,
# Text, IA5String, ...) must not fire missing-import.
MACRO_BODY_USE_MIB = """
SNMPv2-CONF DEFINITIONS ::= BEGIN
IMPORTS
    ObjectName, NotificationName, ObjectSyntax FROM SNMPv2-SMI ;
MODULE-COMPLIANCE MACRO ::=
BEGIN
    TYPE NOTATION ::=
        "STATUS" Status
        "DESCRIPTION" Text
        "MODULE" Modules
    VALUE NOTATION ::=
        value(VALUE OBJECT IDENTIFIER)
    Modules ::=
        Module
    Module ::=
        "OBJECT" Objects
    Objects ::=
        value(ObjectName)
    Syntax ::=
        value(ObjectSyntax)
    Notification ::=
        value(NotificationName)
    Status ::=
        "current"
    Text ::= value(IA5String)
END
END
"""


class TestMacroAndTcUseCounting:
    """Issue #37: imported symbols used only as a TC macro or inside a
    macro-definition body no longer fire unused-import."""

    def test_textual_convention_import_counts_as_used_when_tc_defined(self) -> None:
        report = _lint({"R-MIB": TC_MACRO_USE_MIB})
        assert report.findings == []

    def test_plain_type_assignment_does_not_count_textual_convention_used(self) -> None:
        report = _lint({"R-MIB": PLAIN_TYPE_TC_IMPORT_MIB})
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.check is CheckId.UNUSED_IMPORT
        assert finding.symbol == "TEXTUAL-CONVENTION"

    def test_macro_body_symbol_uses_counted(self) -> None:
        report = _lint({"SNMPv2-CONF": MACRO_BODY_USE_MIB})
        assert report.findings == []

    def test_unrelated_macro_body_tokens_do_not_fire_missing_import(self) -> None:
        """Harvested tokens that are NOT in the import map (Status, Text,
        IA5String, ...) must not surface as missing-import — the counting is
        restricted to imported symbols."""
        report = _lint({"SNMPv2-CONF": MACRO_BODY_USE_MIB})
        missing = [f for f in report.findings if f.check is CheckId.MISSING_IMPORT]
        assert missing == []
        # And they cannot mask a genuinely unused import: a module importing
        # a symbol that appears nowhere (not even in a macro body) still
        # fires unused-import even when its macro body mentions other tokens.
        report2 = _lint({"D-MIB": UNUSED_IMPORT_MIB})
        assert {f.symbol for f in report2.findings} == {"Integer32"}


# ---------------------------------------------------------------------------
# Stable renderings (CLI text / JSON output)
# ---------------------------------------------------------------------------


def _sample_report() -> LintReport:
    findings = [
        LintFinding(
            check=CheckId.MISSING_IMPORT,
            severity=Severity.ERROR,
            module="A-MIB",
            symbol="MysteryType",
            message="referenced but never imported",
        ),
        LintFinding(
            check=CheckId.UNUSED_IMPORT,
            severity=Severity.WARNING,
            module="D-MIB",
            symbol="Integer32",
            message="never referenced",
        ),
    ]
    return LintReport(
        findings=findings,
        summary=LintSummary(modules_checked=2, errors=1, warnings=1),
        resolve_errors={"NO-SUCH-MIB": "MIB 'NO-SUCH-MIB' not found"},
    )


class TestLintReportToDict:
    def test_document_shape_is_stable(self) -> None:
        doc = lint_report_to_dict(_sample_report())
        assert doc["findings"] == [
            {
                "check": "missing-import",
                "severity": "error",
                "module": "A-MIB",
                "symbol": "MysteryType",
                "message": "referenced but never imported",
            },
            {
                "check": "unused-import",
                "severity": "warning",
                "module": "D-MIB",
                "symbol": "Integer32",
                "message": "never referenced",
            },
        ]
        assert doc["summary"] == {"modules_checked": 2, "errors": 1, "warnings": 1}
        assert doc["resolve_errors"] == {"NO-SUCH-MIB": "MIB 'NO-SUCH-MIB' not found"}
        # The --fix outcome keys are part of the stable contract (empty on a
        # plain lint run, populated on --fix runs).
        assert doc["fixed"] == []
        assert doc["diffs"] == {}

    def test_fixed_section_serializes(self) -> None:
        report = LintReport(
            findings=[],
            summary=LintSummary(modules_checked=1, errors=0, warnings=0),
            resolve_errors={},
            fixed=[
                FixedFinding(
                    module="D-MIB",
                    check=CheckId.UNUSED_IMPORT,
                    severity=Severity.WARNING,
                    symbol="Integer32",
                    file="/mibs/D-MIB",
                    status=FixStatus.FIXED,
                    message="removed unused import 'Integer32' from IMPORTS",
                ),
                FixedFinding(
                    module="A-MIB",
                    check=CheckId.MISSING_IMPORT,
                    severity=Severity.ERROR,
                    symbol="MysteryType",
                    file=None,
                    status=FixStatus.LEFT,
                    message="symbol 'MysteryType' does not resolve to exactly one "
                    "provider module in the loaded closure",
                ),
            ],
            diffs={"/mibs/D-MIB": "--- /mibs/D-MIB\n+++ /mibs/D-MIB\n@@ -1 +1 @@\n"},
        )
        doc = lint_report_to_dict(report)
        assert doc["fixed"] == [
            {
                "check": "unused-import",
                "severity": "warning",
                "module": "D-MIB",
                "symbol": "Integer32",
                "file": "/mibs/D-MIB",
                "status": "fixed",
                "message": "removed unused import 'Integer32' from IMPORTS",
            },
            {
                "check": "missing-import",
                "severity": "error",
                "module": "A-MIB",
                "symbol": "MysteryType",
                "file": None,
                "status": "left",
                "message": "symbol 'MysteryType' does not resolve to exactly one "
                "provider module in the loaded closure",
            },
        ]
        assert doc["diffs"] == {"/mibs/D-MIB": "--- /mibs/D-MIB\n+++ /mibs/D-MIB\n@@ -1 +1 @@\n"}

    def test_empty_report_serializes(self) -> None:
        report = LintReport(
            findings=[],
            summary=LintSummary(modules_checked=1, errors=0, warnings=0),
            resolve_errors={},
        )
        assert lint_report_to_dict(report) == {
            "findings": [],
            "summary": {"modules_checked": 1, "errors": 0, "warnings": 0},
            "resolve_errors": {},
            "fixed": [],
            "diffs": {},
        }


class TestFormatLintReportText:
    def test_groups_by_severity_and_includes_summary(self) -> None:
        text = format_lint_report_text(_sample_report())
        assert text.index("Errors:") < text.index("Warnings:")
        assert "A-MIB" in text
        assert "MysteryType" in text
        assert "missing-import" in text
        assert "Integer32" in text
        assert "Unresolved modules:" in text
        assert "NO-SUCH-MIB" in text
        assert "Summary: 2 modules checked, 1 error, 1 warning, 1 unresolved module" in text

    def test_clean_report_has_summary_only(self) -> None:
        report = LintReport(
            findings=[],
            summary=LintSummary(modules_checked=1, errors=0, warnings=0),
            resolve_errors={},
        )
        assert format_lint_report_text(report) == "Summary: 1 module checked, 0 errors, 0 warnings"

    def test_fixed_and_left_sections(self) -> None:
        report = LintReport(
            findings=[],
            summary=LintSummary(modules_checked=1, errors=0, warnings=0),
            resolve_errors={},
            fixed=[
                FixedFinding(
                    module="D-MIB",
                    check=CheckId.UNUSED_IMPORT,
                    severity=Severity.WARNING,
                    symbol="Integer32",
                    file="/mibs/D-MIB",
                    status=FixStatus.FIXED,
                    message="removed unused import 'Integer32' from IMPORTS",
                ),
                FixedFinding(
                    module="A-MIB",
                    check=CheckId.MISSING_IMPORT,
                    severity=Severity.ERROR,
                    symbol="MysteryType",
                    file=None,
                    status=FixStatus.LEFT,
                    message="symbol 'MysteryType' does not resolve to exactly one "
                    "provider module in the loaded closure",
                ),
            ],
        )
        text = format_lint_report_text(report)
        assert "Fixed:" in text
        assert "[D-MIB] Integer32 (unused-import): removed unused import" in text
        assert "Left (not fixed):" in text
        assert "[A-MIB] MysteryType (missing-import): symbol 'MysteryType' does not" in text
        assert text.index("Fixed:") < text.index("Left (not fixed):")
        assert "Summary: 1 module checked, 0 errors, 0 warnings" in text


# ---------------------------------------------------------------------------
# --fix remediation (v0.5.2, plan items 1–3)
# ---------------------------------------------------------------------------

# The Juniper `DisplayString` missing-import shape: an object SYNTAX
# references DisplayString but the module never imports it from SNMPv2-TC.
# The `;` sits on its own line so the fix is a pure line insertion.
FIX_MISSING_IMPORT_MIB = """
JUNIPER-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    ;
jnxMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Juniper Networks"
    CONTACT-INFO "jnx@example.com"
    DESCRIPTION  "Juniper-style DisplayString missing-import fixture."
    ::= { 1 3 }
jnxString OBJECT-TYPE
    SYNTAX      DisplayString
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "References DisplayString without importing it."
    ::= { jnxMIB 1 }
END
"""

# Minimal SNMPv2-TC-shaped provider: defines DisplayString and TruthValue,
# TC-only (no MODULE-IDENTITY) per convention. Plain type assignments are
# used (rather than TEXTUAL-CONVENTION) so the stub is lint-clean: the macro
# import for TEXTUAL-CONVENTION would otherwise be flagged unused-import (a
# pre-existing lint quirk — type macros are not counted as uses).
FIX_SNMPV2_TC_STUB = """
SNMPv2-TC DEFINITIONS ::= BEGIN
DisplayString ::= OCTET STRING
TruthValue ::= INTEGER
END
"""

FIX_UNUSED_IMPORT_MIB = """
D-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI ;
dMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Unused-import fix fixture."
    ::= { 1 6 }
dObj OBJECT-TYPE
    SYNTAX      OCTET STRING
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses only base types; Integer32 is never referenced."
    ::= { dMIB 1 }
END
"""

# Only one import, unused: the fix must drop the whole IMPORTS clause.
FIX_SINGLE_UNUSED_IMPORT_MIB = """
E-MIB DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM SNMPv2-SMI ;
eMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Single unused import."
    ::= { 1 7 }
END
"""

# Only unused import emptied AND a new import to add: the block is rebuilt.
FIX_REBUILD_BLOCK_MIB = """
R-MIB DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM SNMPv2-SMI ;
rMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Rebuild-path fixture."
    ::= { 1 3 }
rObj OBJECT-TYPE
    SYNTAX      TruthValue
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses TruthValue without importing it."
    ::= { rMIB 1 }
END
"""

# No IMPORTS clause at all; a missing type import must create one.
FIX_NO_IMPORTS_MIB = """
F-MIB DEFINITIONS ::= BEGIN
fMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "No-IMPORTS fixture."
    ::= { 1 3 }
fObj OBJECT-TYPE
    SYNTAX      TruthValue
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses TruthValue without importing it."
    ::= { fMIB 1 }
END
"""

# An existing SNMPv2-TC clause: DisplayString must be appended to it.
FIX_ADD_TO_EXISTING_CLAUSE_MIB = """
J-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    TruthValue FROM SNMPv2-TC
    ;
jMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Existing-clause fixture."
    ::= { 1 3 }
jTruth OBJECT-TYPE
    SYNTAX      TruthValue
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses the imported TruthValue."
    ::= { jMIB 1 }
jString OBJECT-TYPE
    SYNTAX      DisplayString
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses DisplayString without importing it."
    ::= { jMIB 2 }
END
"""

# Member-role missing-import (legal unimported OID references): NOT fixable.
FIX_MEMBER_ROLE_MIB = """
G-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, NOTIFICATION-TYPE FROM SNMPv2-SMI ;
gMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Member-role missing-import fixture."
    ::= { 1 10 }
gTrap NOTIFICATION-TYPE
    OBJECTS     { ifIndex, ifDescr }
    STATUS      current
    DESCRIPTION "OBJECTS members from another module, not imported."
    ::= { gMIB 1 }
END
"""

# A type referenced by two closure modules: ambiguous, must NOT be fixed.
FIX_AMBIGUOUS_MIB = """
A-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;
aMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Ambiguous-provider fixture."
    ::= { 1 3 }
ghostObj OBJECT-TYPE
    SYNTAX      MysteryType
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "MysteryType is defined by two closure modules."
    ::= { aMIB 1 }
END
"""

FIX_PROVIDER1_MIB = """
P1-MIB DEFINITIONS ::= BEGIN
MysteryType ::= OCTET STRING
END
"""

FIX_PROVIDER2_MIB = """
P2-MIB DEFINITIONS ::= BEGIN
MysteryType ::= OCTET STRING
END
"""

# Object-bearing module, no MODULE-IDENTITY: the missing-description finding
# is NOT fixable; the unused Integer32 import IS. Only the latter may edit
# the file.
FIX_NON_FIXABLE_MIX_MIB = """
N-MIB DEFINITIONS ::= BEGIN
IMPORTS
    OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
nObj OBJECT-TYPE
    SYNTAX      OCTET STRING
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses only base types."
    ::= { 1 3 }
END
"""

# HTTP-sourced module with a fixable-looking finding (report-only).
FIX_HTTP_MIB = """
HTTP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;
httpMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "HTTP Test"
    CONTACT-INFO "http@example.com"
    DESCRIPTION  "HTTP-sourced fixture."
    ::= { 1 3 }
httpObj OBJECT-TYPE
    SYNTAX      MysteryType
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "References an unresolved type."
    ::= { httpMIB 1 }
END
"""

# CRLF fixture with trailing whitespace on an untouched line: byte-preservation.
FIX_CRLF_MIB = (
    "M-MIB DEFINITIONS ::= BEGIN\n"
    "IMPORTS\n"
    "    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI ;\n"
    "mMIB MODULE-IDENTITY\n"
    '    LAST-UPDATED "200001010000Z"\n'
    '    ORGANIZATION "Lint Test"   \n'
    '    CONTACT-INFO "lint@example.com"\n'
    '    DESCRIPTION  "CRLF fixture."\n'
    "    ::= { 1 5 }\n"
    "mObj OBJECT-TYPE\n"
    "    SYNTAX      OCTET STRING\n"
    "    MAX-ACCESS  read-only\n"
    "    STATUS      current\n"
    '    DESCRIPTION "Desc."\n'
    "    ::= { mMIB 1 }\n"
    "END\n"
)


def _lint_fix(
    texts: dict[str, str],
    names: list[str],
    tmp_path: Path,
    *,
    diff: bool = False,
) -> tuple[LintReport, Path]:
    """Run ``run_lint(..., fix=True)`` over files written to a tmp mib-dir.

    Files already present are left untouched, so a second call in the same
    tmp_path operates on the previously-fixed content (idempotency tests).
    """
    mib_dir = tmp_path / "mibs"
    mib_dir.mkdir(exist_ok=True)
    for name, text in texts.items():
        path = mib_dir / name
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    config = CompilerConfig(cache_dir=None)
    report = asyncio.run(run_lint(names, config, mib_dirs=[mib_dir], fix=True, diff=diff))
    return report, mib_dir


class TestLintFixMissingImport:
    def test_adds_unique_provider_import_and_reports_fixed(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"JUNIPER-MIB": FIX_MISSING_IMPORT_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB},
            ["JUNIPER-MIB", "SNMPv2-TC"],
            tmp_path,
        )
        path = mib_dir / "JUNIPER-MIB"
        text = path.read_text(encoding="utf-8")
        assert "    DisplayString FROM SNMPv2-TC" in text
        assert report.findings == []
        assert report.diffs == {}
        fixed = [f for f in report.fixed if f.status is FixStatus.FIXED]
        assert len(fixed) == 1
        entry = fixed[0]
        assert entry.check is CheckId.MISSING_IMPORT
        assert entry.severity is Severity.ERROR
        assert entry.module == "JUNIPER-MIB"
        assert entry.symbol == "DisplayString"
        assert entry.file == str(path)
        assert "DisplayString" in entry.message and "SNMPv2-TC" in entry.message

    def test_adds_to_existing_provider_clause(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"J-MIB": FIX_ADD_TO_EXISTING_CLAUSE_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB},
            ["J-MIB", "SNMPv2-TC"],
            tmp_path,
        )
        text = (mib_dir / "J-MIB").read_text(encoding="utf-8")
        assert "    TruthValue, DisplayString FROM SNMPv2-TC" in text
        assert report.findings == []
        assert all(f.status is FixStatus.FIXED for f in report.fixed)

    def test_creates_imports_clause_when_module_has_none(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"F-MIB": FIX_NO_IMPORTS_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB},
            ["F-MIB", "SNMPv2-TC"],
            tmp_path,
        )
        text = (mib_dir / "F-MIB").read_text(encoding="utf-8")
        assert "IMPORTS" in text
        assert "    TruthValue FROM SNMPv2-TC" in text
        assert report.findings == []


class TestLintFixUnusedImport:
    def test_removes_unused_symbol_from_clause(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix({"D-MIB": FIX_UNUSED_IMPORT_MIB}, ["D-MIB"], tmp_path)
        text = (mib_dir / "D-MIB").read_text(encoding="utf-8")
        assert "    MODULE-IDENTITY FROM SNMPv2-SMI ;" in text
        assert "Integer32 FROM SNMPv2-SMI" not in text
        fixed = [f for f in report.fixed if f.status is FixStatus.FIXED]
        assert len(fixed) == 1
        assert fixed[0].check is CheckId.UNUSED_IMPORT
        assert fixed[0].symbol == "Integer32"
        assert report.findings == []

    def test_removes_imports_clause_entirely_when_empty(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix({"E-MIB": FIX_SINGLE_UNUSED_IMPORT_MIB}, ["E-MIB"], tmp_path)
        text = (mib_dir / "E-MIB").read_text(encoding="utf-8")
        assert "IMPORTS" not in text
        assert "Integer32" not in text
        assert "eMIB MODULE-IDENTITY" in text
        assert report.findings == []

    def test_rebuilds_block_when_emptied_but_additions_remain(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"R-MIB": FIX_REBUILD_BLOCK_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB},
            ["R-MIB", "SNMPv2-TC"],
            tmp_path,
        )
        text = (mib_dir / "R-MIB").read_text(encoding="utf-8")
        assert "    TruthValue FROM SNMPv2-TC" in text
        assert "Integer32" not in text
        assert report.findings == []
        assert all(f.status is FixStatus.FIXED for f in report.fixed)


class TestLintFixSafety:
    """Safety rules pinned by dedicated tests (plan safety rules)."""

    def test_only_fixable_checks_modify_files(self, tmp_path: Path) -> None:
        # A module with ONLY a non-fixable finding is never modified.
        report, mib_dir = _lint_fix({"O-MIB": MISSING_DESCRIPTION_MODULE_MIB}, ["O-MIB"], tmp_path)
        assert report.fixed == []
        assert len(report.findings) == 1
        assert report.findings[0].check is CheckId.MISSING_DESCRIPTION
        assert (mib_dir / "O-MIB").read_text(encoding="utf-8") == MISSING_DESCRIPTION_MODULE_MIB

    def test_non_fixable_check_is_report_only_alongside_fixable(self, tmp_path: Path) -> None:
        # Mix: the unused-import is fixed; the module-level missing-description
        # (a different, non-fixable check) stays a finding and the file edit
        # does not touch anything outside IMPORTS.
        report, mib_dir = _lint_fix({"N-MIB": FIX_NON_FIXABLE_MIX_MIB}, ["N-MIB"], tmp_path)
        text = (mib_dir / "N-MIB").read_text(encoding="utf-8")
        assert "Integer32" not in text
        assert {f.check for f in report.findings} == {CheckId.MISSING_DESCRIPTION}
        assert {f.check for f in report.fixed if f.status is FixStatus.FIXED} == {
            CheckId.UNUSED_IMPORT
        }

    def test_member_role_missing_import_not_fixable(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix({"G-MIB": FIX_MEMBER_ROLE_MIB}, ["G-MIB"], tmp_path)
        assert report.fixed == []
        assert {f.symbol for f in report.findings} == {"ifDescr", "ifIndex"}
        assert all(f.severity is Severity.WARNING for f in report.findings)
        # File byte-identical.
        assert (mib_dir / "G-MIB").read_bytes() == FIX_MEMBER_ROLE_MIB.encode("utf-8")

    def test_ambiguous_provider_reported_not_fixed(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {
                "A-MIB": FIX_AMBIGUOUS_MIB,
                "P1-MIB": FIX_PROVIDER1_MIB,
                "P2-MIB": FIX_PROVIDER2_MIB,
            },
            ["A-MIB", "P1-MIB", "P2-MIB"],
            tmp_path,
        )
        text = (mib_dir / "A-MIB").read_text(encoding="utf-8")
        assert "MysteryType FROM" not in text
        assert (mib_dir / "A-MIB").read_bytes() == FIX_AMBIGUOUS_MIB.encode("utf-8")
        assert len(report.findings) == 1
        assert report.findings[0].check is CheckId.MISSING_IMPORT
        left = [f for f in report.fixed if f.status is FixStatus.LEFT]
        assert len(left) == 1
        assert "does not resolve to exactly one provider" in left[0].message

    def test_unresolvable_symbol_reported_not_fixed(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix({"A-MIB": FIX_AMBIGUOUS_MIB}, ["A-MIB"], tmp_path)
        assert (mib_dir / "A-MIB").read_bytes() == FIX_AMBIGUOUS_MIB.encode("utf-8")
        assert len(report.findings) == 1
        left = [f for f in report.fixed if f.status is FixStatus.LEFT]
        assert len(left) == 1
        assert "does not resolve to exactly one provider" in left[0].message

    def test_idempotent_second_fix_is_noop(self, tmp_path: Path) -> None:
        texts = {"JUNIPER-MIB": FIX_MISSING_IMPORT_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB}
        names = ["JUNIPER-MIB", "SNMPv2-TC"]
        report1, mib_dir = _lint_fix(texts, names, tmp_path)
        after_first = (mib_dir / "JUNIPER-MIB").read_bytes()
        report2, _ = _lint_fix(texts, names, tmp_path)
        assert (mib_dir / "JUNIPER-MIB").read_bytes() == after_first
        assert report2.fixed == []
        assert report2.findings == []

    def test_untouched_lines_preserved_byte_for_byte(self, tmp_path: Path) -> None:
        fixture = FIX_CRLF_MIB.replace("\n", "\r\n")
        report, mib_dir = _lint_fix({"M-MIB": fixture}, ["M-MIB"], tmp_path)
        path = mib_dir / "M-MIB"
        # Only the symbol-list span inside the IMPORTS clause may change;
        # CRLF endings and trailing whitespace elsewhere survive exactly.
        expected = fixture.replace("MODULE-IDENTITY, Integer32", "MODULE-IDENTITY")
        assert path.read_bytes() == expected.encode("utf-8")
        assert report.findings == []

    def test_unparseable_fix_rolled_back_with_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trishul_smi.parser.smi_parser import SmiParser

        real_parse = SmiParser.parse

        def _flaky_parse(self, text: str, *args, **kwargs):
            if "DisplayString FROM SNMPv2-TC" in text:
                raise ValueError("simulated unparseable output")
            return real_parse(self, text, *args, **kwargs)

        monkeypatch.setattr(SmiParser, "parse", _flaky_parse)
        report, mib_dir = _lint_fix(
            {"JUNIPER-MIB": FIX_MISSING_IMPORT_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB},
            ["JUNIPER-MIB", "SNMPv2-TC"],
            tmp_path,
        )
        # Rolled back: file untouched, finding remains, left with a reason.
        assert (mib_dir / "JUNIPER-MIB").read_text(encoding="utf-8") == FIX_MISSING_IMPORT_MIB
        assert len(report.findings) == 1
        left = [f for f in report.fixed if f.status is FixStatus.LEFT]
        assert len(left) == 1
        assert "unparseable" in left[0].message

    def test_http_sourced_module_report_only(self, httpx_mock) -> None:
        from pytest_httpx import HTTPXMock

        httpx_mock: HTTPXMock
        httpx_mock.add_response(url="https://mibs.pysnmp.com/asn1/HTTP-MIB", text=FIX_HTTP_MIB)
        config = CompilerConfig(cache_dir=None)
        report = asyncio.run(run_lint(["HTTP-MIB"], config, use_http=True, fix=True))
        assert len(report.findings) == 1
        assert report.findings[0].check is CheckId.MISSING_IMPORT
        left = [f for f in report.fixed if f.status is FixStatus.LEFT]
        assert len(left) == 1
        assert "local --mib-dir" in left[0].message

    def test_non_mib_dir_reader_report_only(self) -> None:
        # Caller-supplied readers (ZIP-style sources) are not local --mib-dir
        # files: report-only.
        config = CompilerConfig(cache_dir=None)
        report = asyncio.run(
            run_lint(
                ["JUNIPER-MIB"],
                config,
                readers=[MockReader({"JUNIPER-MIB": FIX_MISSING_IMPORT_MIB})],
                fix=True,
            )
        )
        assert len(report.findings) == 1
        left = [f for f in report.fixed if f.status is FixStatus.LEFT]
        assert len(left) == 1
        assert "local --mib-dir" in left[0].message


class TestLintFixDiff:
    def test_diff_dry_run_writes_nothing_and_matches_exact_diff(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"JUNIPER-MIB": FIX_MISSING_IMPORT_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB},
            ["JUNIPER-MIB", "SNMPv2-TC"],
            tmp_path,
            diff=True,
        )
        path = mib_dir / "JUNIPER-MIB"
        # Nothing written:
        assert path.read_text(encoding="utf-8") == FIX_MISSING_IMPORT_MIB
        # Exactly the expected unified diff (pure line insertion; the hunk
        # starts at line 2 because the fixture string opens with a blank
        # line):
        assert report.diffs == {
            str(path): (
                f"--- {path}\n"
                f"+++ {path}\n"
                "@@ -2,6 +2,7 @@\n"
                " JUNIPER-MIB DEFINITIONS ::= BEGIN\n"
                " IMPORTS\n"
                "     MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI\n"
                "+    DisplayString FROM SNMPv2-TC\n"
                "     ;\n"
                " jnxMIB MODULE-IDENTITY\n"
                '     LAST-UPDATED "200001010000Z"\n'
            )
        }
        # The finding is reported as fixed (dry-run: nothing written).
        assert all(f.status is FixStatus.FIXED for f in report.fixed)
        assert report.findings == []

    def test_plain_fix_writes_corrected_file_and_relint_is_clean(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"JUNIPER-MIB": FIX_MISSING_IMPORT_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB},
            ["JUNIPER-MIB", "SNMPv2-TC"],
            tmp_path,
        )
        assert report.diffs == {}
        path = mib_dir / "JUNIPER-MIB"
        assert "    DisplayString FROM SNMPv2-TC" in path.read_text(encoding="utf-8")

        # Re-lint (no --fix): zero fixable findings on the corrected file.
        config = CompilerConfig(cache_dir=None)
        relint = asyncio.run(run_lint(["JUNIPER-MIB"], config, mib_dirs=[mib_dir]))
        assert relint.findings == []
        assert relint.fixed == []


# B1 regression: two clauses sharing one physical line. Dropping the unused
# clause must NOT delete the surviving clause (or the keyword / `;`), which
# would silently corrupt a still-parseable file. OTHER-REF is a stub provider
# module (distinct from SNMPv2-SMI/SNMPv2-TC so the model records both
# clauses — same-module clauses collapse in the model, a pre-existing quirk
# tracked separately by the orchestrator).
FIX_OTHER_REF_STUB = """
OTHER-REF DEFINITIONS ::= BEGIN
END
"""

FIX_SHARED_LINE_V1 = """
S-MIB DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM OTHER-REF TruthValue FROM SNMPv2-TC
    MODULE-IDENTITY FROM SNMPv2-SMI
    ;
sMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "B1 variant-1 fixture."
    ::= { 1 3 }
sObj OBJECT-TYPE
    SYNTAX      TruthValue
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses the imported TruthValue."
    ::= { sMIB 1 }
END
"""

FIX_SHARED_LINE_V2 = """
T-MIB DEFINITIONS ::= BEGIN
IMPORTS Integer32 FROM OTHER-REF TruthValue FROM SNMPv2-TC;
tMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "B1 variant-2 fixture."
    ::= { 1 4 }
tObj OBJECT-TYPE
    SYNTAX      TruthValue
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses the imported TruthValue."
    ::= { tMIB 1 }
END
"""


class TestLintFixClauseRemoval:
    """B1: a dropped clause must never take a sibling/keyword/`;` with it.

    Two clauses may share one physical line (the grammar juxtaposes import
    clauses with whitespace); deleting the whole line silently corrupts the
    file — which still parses because the imports clause is grammar-optional.
    """

    def _run_variant(self, tmp_path: Path, module_text: str, name: str) -> LintReport:
        report, mib_dir = _lint_fix(
            {name: module_text, "OTHER-REF": FIX_OTHER_REF_STUB},
            [name, "OTHER-REF"],
            tmp_path,
        )
        text = (mib_dir / name).read_text(encoding="utf-8")
        # Only the dropped clause's content is gone; the used TruthValue
        # clause, the IMPORTS keyword, and the `;` all survive.
        assert "Integer32 FROM OTHER-REF" not in text
        assert "TruthValue FROM SNMPv2-TC" in text
        assert "IMPORTS" in text
        # No NEW findings after the fix (the B1 bug introduced a
        # missing-import error on the surviving symbol).
        assert report.findings == []
        relint = _lint({"S-MIB": text} if name == "S-MIB" else {"T-MIB": text}, names=[name])
        assert relint.findings == []
        return report

    def test_variant1_dropped_clause_shares_line_with_surviving_clause(
        self, tmp_path: Path
    ) -> None:
        report = self._run_variant(tmp_path, FIX_SHARED_LINE_V1, "S-MIB")
        assert all(f.status is FixStatus.FIXED for f in report.fixed)
        assert {f.symbol for f in report.fixed} == {"Integer32"}

    def test_variant2_single_line_imports_block(self, tmp_path: Path) -> None:
        report = self._run_variant(tmp_path, FIX_SHARED_LINE_V2, "T-MIB")
        assert all(f.status is FixStatus.FIXED for f in report.fixed)
        assert {f.symbol for f in report.fixed} == {"Integer32"}

    def test_second_fix_is_noop_after_shared_line_removal(self, tmp_path: Path) -> None:
        texts = {
            "S-MIB": FIX_SHARED_LINE_V1,
            "OTHER-REF": FIX_OTHER_REF_STUB,
        }
        report1, mib_dir = _lint_fix(texts, ["S-MIB", "OTHER-REF"], tmp_path)
        after_first = (mib_dir / "S-MIB").read_bytes()
        report2, _ = _lint_fix(texts, ["S-MIB", "OTHER-REF"], tmp_path)
        assert (mib_dir / "S-MIB").read_bytes() == after_first
        assert report2.fixed == []
        assert report2.findings == []


# S1: an SMIv1 module may place an ``EXPORTS ... ;`` section between BEGIN
# and IMPORTS (smiv1.lark). The fixer must skip it when locating the IMPORTS
# clause so both fix kinds work (and the SMIv1 order EXPORTS-then-IMPORTS is
# preserved when a fresh IMPORTS block is inserted).
FIX_SMIV1_EXPORTS_MIB = """
V-MIB DEFINITIONS ::= BEGIN
EXPORTS
    vMIB, vObj ;
IMPORTS
    Counter FROM RFC1155-SMI
    Integer32 FROM SNMPv2-TC-v1 ;
vMIB OBJECT IDENTIFIER ::= { 1 3 }
vObj OBJECT-TYPE
    SYNTAX      Counter
    ACCESS      read-only
    STATUS      mandatory
    DESCRIPTION "Uses the imported Counter."
    ::= { vMIB 1 }
vStr OBJECT-TYPE
    SYNTAX      DisplayString
    ACCESS      read-only
    STATUS      mandatory
    DESCRIPTION "Uses DisplayString without importing it."
    ::= { vMIB 2 }
END
"""

FIX_SMIV1_PROVIDER_MIB = """
V-TC DEFINITIONS ::= BEGIN
DisplayString ::= OCTET STRING
END
"""


class TestLintFixSmiv1Exports:
    def test_exports_before_imports_both_fix_kinds_work(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"V-MIB": FIX_SMIV1_EXPORTS_MIB, "V-TC": FIX_SMIV1_PROVIDER_MIB},
            ["V-MIB", "V-TC"],
            tmp_path,
        )
        text = (mib_dir / "V-MIB").read_text(encoding="utf-8")
        assert "Integer32 FROM SNMPv2-TC-v1" not in text
        assert "    DisplayString FROM V-TC" in text
        assert "EXPORTS" in text  # the SMIv1 section is left in place
        fixed = [f for f in report.fixed if f.status is FixStatus.FIXED]
        assert {f.symbol for f in fixed} == {"DisplayString", "Integer32"}
        messages = {f.symbol: f.message for f in fixed}
        assert "removed unused import 'Integer32' from IMPORTS" in messages["Integer32"]
        assert "added import 'DisplayString' FROM 'V-TC' to IMPORTS" in messages["DisplayString"]
        assert report.findings == []
        # The fixed file re-lints cleanly (still SMIv1-shaped).
        relint = _lint(
            {"V-MIB": text, "V-TC": FIX_SMIV1_PROVIDER_MIB},
            names=["V-MIB", "V-TC"],
        )
        assert relint.findings == []

    def test_second_fix_is_noop(self, tmp_path: Path) -> None:
        texts = {"V-MIB": FIX_SMIV1_EXPORTS_MIB, "V-TC": FIX_SMIV1_PROVIDER_MIB}
        report1, mib_dir = _lint_fix(texts, ["V-MIB", "V-TC"], tmp_path)
        after_first = (mib_dir / "V-MIB").read_bytes()
        report2, _ = _lint_fix(texts, ["V-MIB", "V-TC"], tmp_path)
        assert (mib_dir / "V-MIB").read_bytes() == after_first
        assert report2.fixed == []
        assert report2.findings == []


# Nit 4: the trailing-`;` insertion branch of _insert_new_clauses — a new
# provider clause placed right before a `;` that trails the last clause on
# the same line.
FIX_TRAILING_SEMICOLON_MIB = """
K-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;
kMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Trailing-semicolon insertion fixture."
    ::= { 1 3 }
kStr OBJECT-TYPE
    SYNTAX      DisplayString
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses DisplayString without importing it."
    ::= { kMIB 1 }
END
"""


class TestLintFixTrailingSemicolonInsertion:
    def test_new_provider_clause_inserted_before_mid_line_semicolon(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"K-MIB": FIX_TRAILING_SEMICOLON_MIB, "SNMPv2-TC": FIX_SNMPV2_TC_STUB},
            ["K-MIB", "SNMPv2-TC"],
            tmp_path,
        )
        text = (mib_dir / "K-MIB").read_text(encoding="utf-8")
        # The new clause takes the last line and the `;` trails it; the
        # original clause line loses only its trailing " ;".
        assert "    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI\n" in text
        assert "    DisplayString FROM SNMPv2-TC;" in text
        assert report.findings == []
        assert all(f.status is FixStatus.FIXED for f in report.fixed)


# ---------------------------------------------------------------------------
# Coverage: small branches / defensive paths (v0.5.2 release gate)
# ---------------------------------------------------------------------------


class TestSyntaxSymbols:
    def test_sequence_of_yields_member_type(self) -> None:
        assert _syntax_symbols("SEQUENCE OF IfEntry") == ["IfEntry"]

    def test_sequence_of_extra_whitespace_is_stripped(self) -> None:
        assert _syntax_symbols("SEQUENCE OF  IfEntry ") == ["IfEntry"]

    def test_empty_syntax_yields_nothing(self) -> None:
        assert _syntax_symbols(None) == []
        assert _syntax_symbols("") == []


# INDEX / AUGMENTS references count as uses (and as member-role references):
# an in-module object referenced only via INDEX or AUGMENTS must not be
# flagged anything.
INDEX_AUGMENTS_MIB = """
I-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
iMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Index/augments fixture."
    ::= { 1 3 }
iIndex OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Index object."
    ::= { iMIB 1 }
iRow OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Row object with an INDEX clause."
    INDEX       { iIndex }
    ::= { iMIB 2 }
iAug OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Row object with an AUGMENTS clause."
    AUGMENTS    { iIndex }
    ::= { iMIB 3 }
END
"""


class TestIndexAndAugmentsReferences:
    def test_index_and_augments_are_clean_uses(self) -> None:
        report = _lint({"I-MIB": INDEX_AUGMENTS_MIB})
        assert report.findings == []


# Same symbol referenced twice must be reported once (dedup).
DEDUP_MIB = """
A-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;
aMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Dedup fixture."
    ::= { 1 3 }
ghostObj OBJECT-TYPE
    SYNTAX      MysteryType
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "First reference to the missing type."
    ::= { aMIB 1 }
ghostObj2 OBJECT-TYPE
    SYNTAX      MysteryType
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Second reference to the same missing type."
    ::= { aMIB 2 }
END
"""


class TestMissingImportDedup:
    def test_same_symbol_referenced_twice_reported_once(self) -> None:
        report = _lint({"A-MIB": DEDUP_MIB})
        assert len(report.findings) == 1
        assert report.findings[0].check is CheckId.MISSING_IMPORT
        assert report.findings[0].symbol == "MysteryType"


# A TRAP-TYPE ENTERPRISE written as a bare number is an absolute value, not
# a symbol reference: neither missing-import nor unresolvable-oid may fire.
NUMERIC_TRAP_MIB = """
N-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, TRAP-TYPE FROM SNMPv2-SMI ;
nMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Numeric-enterprise fixture."
    ::= { 1 3 }
numTrap TRAP-TYPE
    ENTERPRISE 1
    DESCRIPTION "Numeric enterprise."
    ::= 1
END
"""


class TestNumericEnterprise:
    def test_numeric_enterprise_is_neither_missing_import_nor_unresolvable(self) -> None:
        report = _lint({"N-MIB": NUMERIC_TRAP_MIB})
        assert report.findings == []


# undefined-type closure-walk dead-ends: cycle (483), chain to an unimported
# symbol (490), provider module absent from the closure (495), and an empty
# base type (503).
CYCLE_TYPE_MIB = """
Y-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;
yMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Cyclic type chain."
    ::= { 1 3 }
TypeA ::= TypeB
TypeB ::= TypeA
yObj OBJECT-TYPE
    SYNTAX      TypeA
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Cyclic chain bottoms out nowhere."
    ::= { yMIB 1 }
END
"""

CHAIN_UNIMPORTED_MIB = """
Z-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;
zMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Chain to an unimported symbol."
    ::= { 1 3 }
TypeA ::= TypeB
zObj OBJECT-TYPE
    SYNTAX      TypeA
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Chain bottoms out in an unimported symbol."
    ::= { zMIB 1 }
END
"""

PROVIDER_FAILED_MIB = """
Q-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    GhostBase FROM NO-SUCH-MIB ;
qMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Provider failed to resolve."
    ::= { 1 3 }
qObj OBJECT-TYPE
    SYNTAX      GhostBase
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Imported from a provider absent from the closure."
    ::= { qMIB 1 }
END
"""


class TestUndefinedTypeDeadEnds:
    def test_circular_type_chain_bottoms_out_nowhere(self) -> None:
        report = _lint({"Y-MIB": CYCLE_TYPE_MIB})
        assert {f.check for f in report.findings} == {CheckId.UNDEFINED_TYPE}
        assert "TypeA" in {f.symbol for f in report.findings}

    def test_chain_bottoms_out_in_unimported_symbol(self) -> None:
        report = _lint({"Z-MIB": CHAIN_UNIMPORTED_MIB})
        checks = {f.check for f in report.findings}
        assert checks == {CheckId.MISSING_IMPORT, CheckId.UNDEFINED_TYPE}
        assert "TypeB" in {f.symbol for f in report.findings if f.check is CheckId.MISSING_IMPORT}

    def test_provider_absent_from_closure(self) -> None:
        report = _lint({"Q-MIB": PROVIDER_FAILED_MIB})
        assert "NO-SUCH-MIB" in report.resolve_errors
        assert {f.check for f in report.findings} == {CheckId.UNDEFINED_TYPE}
        assert report.findings[0].symbol == "GhostBase"

    def test_type_with_empty_base_type(self) -> None:
        # Model-level: the grammar cannot produce an empty base_type, so this
        # dead-end is pinned directly against the model.
        module = MibModule(
            name="E-MIB",
            language="SMIv2",
            types={"EmptyType": MibType(name="EmptyType", base_type="")},
            objects={
                "eObj": MibObject(
                    name="eObj",
                    oid="1.3.6.1.2.1.1",
                    object_type="OBJECT-TYPE",
                    syntax="EmptyType",
                )
            },
        )
        findings = _run_reference_checks([module], {"E-MIB": module})
        assert len(findings) == 1
        assert findings[0].check is CheckId.UNDEFINED_TYPE
        assert findings[0].symbol == "EmptyType"


class TestMissingDescriptionTcShaped:
    def test_display_hint_without_description_reported(self) -> None:
        # Model-level: the SMIv2 grammar requires a DESCRIPTION on a
        # TEXTUAL-CONVENTION, so a TC-shaped type (DISPLAY-HINT set) without
        # one is pinned directly against the model.
        module = MibModule(
            name="H-MIB",
            language="SMIv2",
            types={"HintTc": MibType(name="HintTc", base_type="Integer32", display_hint="d")},
        )
        findings: list[LintFinding] = []
        _check_missing_description(module, findings)
        assert len(findings) == 1
        assert findings[0].check is CheckId.MISSING_DESCRIPTION
        assert findings[0].symbol == "HintTc"


# Multi-word import symbols (OCTET STRING) merge correctly when parsing the
# IMPORTS block for the fixer.
MULTIWORD_IMPORT_MIB = """
W-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    OCTET STRING FROM OTHER-REF ;
wMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Multi-word import fixture."
    ::= { 1 3 }
wObj OBJECT-TYPE
    SYNTAX      OCTET STRING
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses the imported OCTET STRING."
    ::= { wMIB 1 }
END
"""


class TestFixParseMultiWordImport:
    def test_multiword_import_symbol_is_used_cleanly(self) -> None:
        report = _lint(
            {"W-MIB": MULTIWORD_IMPORT_MIB, "OTHER-REF": FIX_OTHER_REF_STUB},
            names=["W-MIB", "OTHER-REF"],
        )
        assert report.findings == []


# A dropped clause that is the sole token-run on its line is removed as a
# whole line (the block survives), exercising the sole-on-line decision.
SOLE_ON_LINE_MIB = """
P-MIB DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM OTHER-REF
    MODULE-IDENTITY FROM SNMPv2-SMI
    ;
pMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Sole-on-line fixture."
    ::= { 1 3 }
pObj OBJECT-TYPE
    SYNTAX      OCTET STRING
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses only base types."
    ::= { pMIB 1 }
END
"""


class TestFixSoleOnLineClauseRemoval:
    def test_sole_on_line_clause_removed_as_whole_line(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"P-MIB": SOLE_ON_LINE_MIB, "OTHER-REF": FIX_OTHER_REF_STUB},
            ["P-MIB", "OTHER-REF"],
            tmp_path,
        )
        text = (mib_dir / "P-MIB").read_text(encoding="utf-8")
        assert "Integer32 FROM OTHER-REF" not in text
        assert "    MODULE-IDENTITY FROM SNMPv2-SMI" in text
        assert report.findings == []
        relint = _lint({"P-MIB": text, "OTHER-REF": FIX_OTHER_REF_STUB}, names=["P-MIB"])
        assert relint.findings == []


# Issue #36: deleting a sole-line import clause retains its trailing comment
# as a standalone comment line. Variant 1: the clause shares the block with a
# surviving clause (individual-drop path). Variant 2: the clause is the
# block's only clause (whole-block removal path).
FIX_TRAILING_COMMENT_MIB = """
C-MIB DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM OTHER-REF  -- legacy note
    MODULE-IDENTITY FROM SNMPv2-SMI
    ;
cMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Trailing-comment fixture."
    ::= { 1 3 }
cObj OBJECT-TYPE
    SYNTAX      OCTET STRING
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses only base types."
    ::= { cMIB 1 }
END
"""

FIX_SINGLE_CLAUSE_COMMENT_MIB = """
E-MIB DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM SNMPv2-SMI  -- legacy note
    ;
eMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Single-clause trailing-comment fixture."
    ::= { 1 7 }
END
"""


class TestLintFixTrailingCommentRetention:
    """Issue #36: a trailing comment on a removed sole-line import clause is
    retained as a standalone comment line (the comment documents the removed
    import)."""

    def test_surviving_clause_drop_retains_comment_as_standalone_line(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"C-MIB": FIX_TRAILING_COMMENT_MIB, "OTHER-REF": FIX_OTHER_REF_STUB},
            ["C-MIB", "OTHER-REF"],
            tmp_path,
        )
        text = (mib_dir / "C-MIB").read_text(encoding="utf-8")
        assert "Integer32 FROM OTHER-REF" not in text
        assert "    -- legacy note" in text
        assert "    MODULE-IDENTITY FROM SNMPv2-SMI" in text
        assert report.findings == []
        # The comment must sit on its own line, not dangle after surviving
        # clause content.
        assert "-- legacy note\n    MODULE-IDENTITY" in text
        relint = _lint({"C-MIB": text, "OTHER-REF": FIX_OTHER_REF_STUB}, names=["C-MIB"])
        assert relint.findings == []

    def test_whole_block_removal_retains_clause_comment(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"E-MIB": FIX_SINGLE_CLAUSE_COMMENT_MIB, "OTHER-REF": FIX_OTHER_REF_STUB},
            ["E-MIB", "OTHER-REF"],
            tmp_path,
        )
        text = (mib_dir / "E-MIB").read_text(encoding="utf-8")
        assert "IMPORTS" not in text
        assert "Integer32" not in text
        assert "    -- legacy note" in text
        assert "eMIB MODULE-IDENTITY" in text
        assert report.findings == []


# Priority-1 gap: an SMIv1 module with EXPORTS but NO IMPORTS clause at all —
# the missing-import fix must insert the fresh IMPORTS block after EXPORTS.
FIX_EXPORTS_NO_IMPORTS_MIB = """
W-MIB DEFINITIONS ::= BEGIN
EXPORTS
    wMIB, wObj ;
wMIB OBJECT IDENTIFIER ::= { 1 3 }
wObj OBJECT-TYPE
    SYNTAX      DisplayString
    ACCESS      read-only
    STATUS      mandatory
    DESCRIPTION "SMIv1 object."
    ::= { wMIB 1 }
END
"""


class TestFixExportsNoImports:
    def test_new_imports_block_inserted_after_exports(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"W-MIB": FIX_EXPORTS_NO_IMPORTS_MIB, "V-TC": FIX_SMIV1_PROVIDER_MIB},
            ["W-MIB", "V-TC"],
            tmp_path,
        )
        text = (mib_dir / "W-MIB").read_text(encoding="utf-8")
        exports_idx = text.index("EXPORTS")
        imports_idx = text.index("IMPORTS")
        assert exports_idx < imports_idx  # SMIv1 order preserved
        assert "    DisplayString FROM V-TC" in text
        assert report.findings == []
        relint = _lint(
            {"W-MIB": text, "V-TC": FIX_SMIV1_PROVIDER_MIB},
            names=["W-MIB", "V-TC"],
        )
        assert relint.findings == []


# No terminating `;` on the IMPORTS clause (grammar-optional): new clauses
# are appended after the last clause line.
FIX_NO_SEMICOLON_MIB = """
U-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
uMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "No-semicolon fixture."
    ::= { 1 3 }
uObj OBJECT-TYPE
    SYNTAX      DisplayString
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses DisplayString."
    ::= { uMIB 1 }
END
"""


class TestFixNoSemicolonImports:
    def test_new_clause_appended_after_last_clause_line(self, tmp_path: Path) -> None:
        report, mib_dir = _lint_fix(
            {"U-MIB": FIX_NO_SEMICOLON_MIB, "V-TC": FIX_SMIV1_PROVIDER_MIB},
            ["U-MIB", "V-TC"],
            tmp_path,
        )
        text = (mib_dir / "U-MIB").read_text(encoding="utf-8")
        assert "    DisplayString FROM V-TC" in text
        assert "MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI" in text
        assert report.findings == []
        relint = _lint(
            {"U-MIB": text, "V-TC": FIX_SMIV1_PROVIDER_MIB},
            names=["U-MIB", "V-TC"],
        )
        assert relint.findings == []


# Latin-1 source with CRLF endings: the fixer must decode via the latin-1
# fallback, preserve every untouched byte, and use CRLF for the inserted
# clause line.
FIX_LATIN1_CRLF_TEXT = (
    "L-MIB DEFINITIONS ::= BEGIN\r\n"
    "IMPORTS\r\n"
    "    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI\r\n"
    "    ;\r\n"
    "lMIB MODULE-IDENTITY\r\n"
    '    LAST-UPDATED "200001010000Z"\r\n'
    '    ORGANIZATION "Juniper \u00e9"\r\n'  # é = latin-1 0xE9
    '    CONTACT-INFO "l@example.com"\r\n'
    '    DESCRIPTION  "Latin-1 fixture."\r\n'
    "    ::= { 1 3 }\r\n"
    "lStr OBJECT-TYPE\r\n"
    "    SYNTAX      DisplayString\r\n"
    "    MAX-ACCESS  read-only\r\n"
    "    STATUS      current\r\n"
    '    DESCRIPTION "Uses DisplayString."\r\n'
    "    ::= { lMIB 1 }\r\n"
    "END\r\n"
)


class TestFixLatin1CrlfSource:
    def test_latin1_fallback_and_crlf_preserved(self, tmp_path: Path) -> None:
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        (mib_dir / "L-MIB").write_bytes(FIX_LATIN1_CRLF_TEXT.encode("latin-1"))
        (mib_dir / "SNMPv2-TC").write_text(FIX_SNMPV2_TC_STUB, encoding="utf-8")
        config = CompilerConfig(cache_dir=None)
        report = asyncio.run(
            run_lint(
                ["L-MIB", "SNMPv2-TC"],
                config,
                mib_dirs=[mib_dir],
                fix=True,
            )
        )
        assert all(f.status is FixStatus.FIXED for f in report.fixed)
        out = (mib_dir / "L-MIB").read_bytes()
        assert b"    DisplayString FROM SNMPv2-TC\r\n" in out  # CRLF inserted clause
        assert out.decode("latin-1").count("\u00e9") == 1  # latin-1 byte preserved
        relint = asyncio.run(run_lint(["L-MIB", "SNMPv2-TC"], config, mib_dirs=[mib_dir]))
        assert relint.findings == []


# Internal/defensive branches, pinned with focused unit tests.
class TestFixInternalDefensivePaths:
    def test_parse_imports_block_without_begin(self) -> None:
        assert _parse_imports_block("no BEGIN keyword here") is None
        # BEGIN is the final token — nothing after it to anchor IMPORTS.
        assert _parse_imports_block("X DEFINITIONS ::= BEGIN") is None

    def test_clause_line_span_without_trailing_newline(self) -> None:
        text = "M DEFINITIONS ::= BEGIN\nIMPORTS\n    A FROM B"
        block = _parse_imports_block(text)
        assert block is not None
        start, end = _clause_line_span(text, block.clauses[0])
        assert text[start:end] == "    A FROM B"

    def test_block_span_without_trailing_newline(self) -> None:
        text = "M DEFINITIONS ::= BEGIN\nIMPORTS\n    A FROM B ;"
        block = _parse_imports_block(text)
        assert block is not None
        start, end = _block_span(text, block)
        assert text[start:end] == "IMPORTS\n    A FROM B ;"

    def test_block_indent_defaults_without_clauses(self) -> None:
        text = "M DEFINITIONS ::= BEGIN\nIMPORTS\n    ;"
        block = _parse_imports_block(text)
        assert block is not None
        assert _block_indent(text, block) == "    "

    def test_apply_edits_rejects_overlapping_spans(self) -> None:
        with pytest.raises(ValueError, match="overlapping fix edits"):
            _apply_edits("abcdef", [(0, 3, "X"), (2, 5, "Y")])

    def test_build_fix_plan_symbol_none_is_failed(self) -> None:
        module = MibModule(name="X-MIB", language="SMIv2")
        finding = LintFinding(CheckId.UNUSED_IMPORT, Severity.WARNING, "X-MIB", None, "no symbol")
        plan = _build_fix_plan(module, [finding], {})
        assert plan.failed == [finding]
        assert "no symbol" in plan.results[finding]

    def test_build_fix_plan_unused_import_without_provider_is_failed(self) -> None:
        module = MibModule(name="X-MIB", language="SMIv2")  # no imports at all
        finding = LintFinding(CheckId.UNUSED_IMPORT, Severity.WARNING, "X-MIB", "Ghost", "unused")
        plan = _build_fix_plan(module, [finding], {})
        assert plan.failed == [finding]
        assert "no recorded provider module" in plan.results[finding]

    def test_fix_imports_text_without_block_or_additions_unchanged(self) -> None:
        plan = _FixPlan(removed={"SNMPv2-SMI": ["Integer32"]}, added={}, results={}, failed=[])
        assert _fix_imports_text("no IMPORTS here", plan) == "no IMPORTS here"

    def test_insert_new_clauses_without_block_unchanged(self) -> None:
        assert _insert_new_clauses("no IMPORTS here", [("M", ["A"])]) == "no IMPORTS here"

    def test_insert_new_clauses_no_semicolon_at_eof(self) -> None:
        # No terminating `;` and the last clause sits at EOF: the new clause
        # is appended at the end of the text.
        text = "X DEFINITIONS ::= BEGIN\nIMPORTS\n    A FROM B"
        out = _insert_new_clauses(text, [("N", ["X"])])
        assert out == text + "\n    X FROM N"

    def test_insert_new_imports_block_without_begin_unchanged(self) -> None:
        assert _insert_new_imports_block("no begin keyword here", {"M": ["A"]}) == (
            "no begin keyword here"
        )

    def test_insert_new_imports_block_anchor_at_eof(self) -> None:
        text = "X-MIB DEFINITIONS ::= BEGIN\nEXPORTS a, b ;"
        out = _insert_new_imports_block(text, {"V-TC": ["DisplayString"]})
        assert out == text + "IMPORTS\n    DisplayString FROM V-TC\n    ;\n"

    def test_atomic_write_cleans_up_temp_file_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        def _fail_fdopen(*_args, **_kwargs):
            raise OSError("boom")

        monkeypatch.setattr(os, "fdopen", _fail_fdopen)
        path = tmp_path / "f.mib"
        with pytest.raises(OSError):
            _atomic_write(path, "x", "utf-8")
        assert not path.exists()
        assert list(tmp_path.glob("*.tmp")) == []  # temp fd closed + file unlinked

    def test_split_newlines(self) -> None:
        assert _split_newlines("") == []
        assert _split_newlines("a\nb") == ["a\n", "b"]
        assert _split_newlines("a\nb\n") == ["a\n", "b\n"]


# --fix error/left paths inside _apply_fixes.
_UNUSED_IMPORT_SOURCE = """
R-MIB DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM SNMPv2-SMI ;
rMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Lint Test"
    CONTACT-INFO "lint@example.com"
    DESCRIPTION  "Unused import fixture."
    ::= { 1 3 }
END
"""


class TestApplyFixesErrorPaths:
    def _finding(self, module: str, symbol: str = "Integer32") -> LintFinding:
        return LintFinding(
            check=CheckId.UNUSED_IMPORT,
            severity=Severity.WARNING,
            module=module,
            symbol=symbol,
            message="unused",
        )

    def test_unreadable_local_source_left(self, tmp_path: Path) -> None:
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        path = mib_dir / "R-MIB"
        path.write_text(_UNUSED_IMPORT_SOURCE, encoding="utf-8")
        module = SmiParser().parse(_UNUSED_IMPORT_SOURCE)
        path.chmod(0)
        try:
            remaining, fixed, _ = asyncio.run(
                _apply_fixes(
                    [self._finding("R-MIB")],
                    {"R-MIB": module},
                    [mib_dir],
                    SmiParser(),
                    diff_only=False,
                )
            )
        finally:
            path.chmod(0o644)
        assert len(remaining) == 1
        assert len(fixed) == 1 and fixed[0].status is FixStatus.LEFT
        assert "cannot read local source" in fixed[0].message

    def test_no_text_change_left(self, tmp_path: Path) -> None:
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        path = mib_dir / "N-MIB"
        # The file has no IMPORTS clause; the model claims an unused import.
        path.write_text("N-MIB DEFINITIONS ::= BEGIN\nEND\n", encoding="utf-8")
        module = MibModule(name="N-MIB", language="SMIv2", imports={"SNMPv2-SMI": ["Integer32"]})
        remaining, fixed, _ = asyncio.run(
            _apply_fixes(
                [self._finding("N-MIB")],
                {"N-MIB": module},
                [mib_dir],
                SmiParser(),
                diff_only=False,
            )
        )
        assert len(remaining) == 1
        assert fixed[0].status is FixStatus.LEFT
        assert "no text change was produced" in fixed[0].message

    def test_write_failure_left(self, tmp_path: Path) -> None:
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        path = mib_dir / "R-MIB"
        path.write_text(_UNUSED_IMPORT_SOURCE, encoding="utf-8")
        module = SmiParser().parse(_UNUSED_IMPORT_SOURCE)
        mib_dir.chmod(0o555)
        try:
            remaining, fixed, _ = asyncio.run(
                _apply_fixes(
                    [self._finding("R-MIB")],
                    {"R-MIB": module},
                    [mib_dir],
                    SmiParser(),
                    diff_only=False,
                )
            )
        finally:
            mib_dir.chmod(0o755)
        assert len(remaining) == 1
        assert fixed[0].status is FixStatus.LEFT
        assert "failed to write" in fixed[0].message


class TestSourceIdentityGuard:
    """Issue #36: before editing a local file, the fixer compares its decoded
    content fingerprint with the fingerprint of the source that produced the
    resolved module; mismatches and offline-cache-fallback modules are
    refused."""

    def test_caller_reader_same_named_different_local_file_left_untouched(
        self, tmp_path: Path
    ) -> None:
        """A caller-supplied reader serving a same-named, different local file
        must leave that file untouched — the resolved module came from the
        reader, not from the local file."""
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        local_text = _UNUSED_IMPORT_SOURCE
        (mib_dir / "R-MIB").write_text(local_text, encoding="utf-8")
        foreign_text = _UNUSED_IMPORT_SOURCE.replace(
            'LAST-UPDATED "200001010000Z"', 'LAST-UPDATED "200101010000Z"'
        )
        config = CompilerConfig(cache_dir=None)
        report = asyncio.run(
            run_lint(
                ["R-MIB"],
                config,
                readers=[MockReader({"R-MIB": foreign_text})],
                mib_dirs=[mib_dir],
                fix=True,
            )
        )

        assert (mib_dir / "R-MIB").read_text(encoding="utf-8") == local_text
        assert len(report.findings) == 1  # the finding remains
        left = [f for f in report.fixed if f.status is FixStatus.LEFT]
        assert len(left) == 1
        assert "differs from the resolved source" in left[0].message

    def test_missing_source_fingerprint_refuses_fix(self, tmp_path: Path) -> None:
        """A module with no recorded live-source fingerprint (served from the
        offline compiled-module cache fallback) is unfixable."""
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        path = mib_dir / "R-MIB"
        path.write_text(_UNUSED_IMPORT_SOURCE, encoding="utf-8")
        module = SmiParser().parse(_UNUSED_IMPORT_SOURCE)

        remaining, fixed, _ = asyncio.run(
            _apply_fixes(
                [_unused_finding("R-MIB")],
                {"R-MIB": module},
                [mib_dir],
                SmiParser(),
                source_fingerprints={},  # offline cache fallback: no entry
                diff_only=False,
            )
        )

        assert len(remaining) == 1
        assert len(fixed) == 1 and fixed[0].status is FixStatus.LEFT
        assert "offline cache fallback" in fixed[0].message
        assert path.read_text(encoding="utf-8") == _UNUSED_IMPORT_SOURCE

    def test_matching_fingerprint_allows_fix(self, tmp_path: Path) -> None:
        """The normal path still works: a local file whose content matches the
        resolved source fingerprint is edited."""
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        path = mib_dir / "R-MIB"
        path.write_text(_UNUSED_IMPORT_SOURCE, encoding="utf-8")
        module = SmiParser().parse(_UNUSED_IMPORT_SOURCE)

        remaining, fixed, _ = asyncio.run(
            _apply_fixes(
                [_unused_finding("R-MIB")],
                {"R-MIB": module},
                [mib_dir],
                SmiParser(),
                source_fingerprints={"R-MIB": _source_fingerprint(_UNUSED_IMPORT_SOURCE)},
                diff_only=False,
            )
        )

        assert remaining == []
        assert all(f.status is FixStatus.FIXED for f in fixed)
        assert "Integer32" not in path.read_text(encoding="utf-8")


class TestApplyFixesOverlapDegradation:
    """Issue #36: an `_apply_edits` overlap-invariant failure must degrade to
    per-finding `left` entries for that module and let the other modules keep
    fixing — it must not abort the whole run."""

    def test_overlapping_edits_degrade_per_module_not_abort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trishul_smi.lint import _apply_edits as real_apply_edits

        def _flaky_apply_edits(text: str, edits: list):
            if "D-MIB" in text:
                raise ValueError("overlapping fix edits: [1, 3) and [2, 4)")
            return real_apply_edits(text, edits)

        monkeypatch.setattr("trishul_smi.lint._apply_edits", _flaky_apply_edits)
        report, mib_dir = _lint_fix(
            {
                "D-MIB": FIX_UNUSED_IMPORT_MIB,
                "P-MIB": SOLE_ON_LINE_MIB,
                "OTHER-REF": FIX_OTHER_REF_STUB,
            },
            ["D-MIB", "P-MIB", "OTHER-REF"],
            tmp_path,
        )

        # D-MIB: the overlap failure is contained — file untouched, finding
        # left with a clear reason.
        assert (mib_dir / "D-MIB").read_text(encoding="utf-8") == FIX_UNUSED_IMPORT_MIB
        d_left = [f for f in report.fixed if f.status is FixStatus.LEFT and f.module == "D-MIB"]
        assert len(d_left) == 1
        assert "overlapping fix edits" in d_left[0].message
        assert len(report.findings) == 1 and report.findings[0].module == "D-MIB"

        # P-MIB: still fixed — the run continued past the failure.
        p_text = (mib_dir / "P-MIB").read_text(encoding="utf-8")
        assert "Integer32 FROM OTHER-REF" not in p_text
        assert "    MODULE-IDENTITY FROM SNMPv2-SMI" in p_text
        p_fixed = [f for f in report.fixed if f.status is FixStatus.FIXED and f.module == "P-MIB"]
        assert len(p_fixed) == 1
