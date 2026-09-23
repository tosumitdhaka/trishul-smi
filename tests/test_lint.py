"""Tests for trishul_smi.lint — the v0.5.0 MIB lint engine (library core).

Fixture strategy: one minimal MIB (plus a dependency where a check needs a
closure) per check, each crafted to trigger EXACTLY that check and nothing
else; plus one clean module that must produce zero findings. Every test
asserts the finding count, check id, severity, module, and symbol so the
"exactly this check" property is pinned.
"""

from __future__ import annotations

import asyncio

from tests.helpers import MockReader
from trishul_smi.config import CompilerConfig
from trishul_smi.lint import (
    CheckId,
    LintFinding,
    LintReport,
    LintSummary,
    Severity,
    format_lint_report_text,
    lint_report_to_dict,
    run_lint,
)

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
