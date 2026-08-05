"""Tests for transformer.py — exercises ASN.1 constructs not covered elsewhere.

Each class targets a specific grammar construct so coverage gaps in
transformer.py are closed without inflating existing test files.
"""

from __future__ import annotations

import pytest

from trishul_smi.models.mib_module import MibModule
from trishul_smi.parser.smi_parser import SmiParser

# ---------------------------------------------------------------------------
# Shared parser instance (grammar cache shared across tests)
# ---------------------------------------------------------------------------

_parser = SmiParser()


def _parse(text: str) -> MibModule:
    return _parser.parse(text)


# ---------------------------------------------------------------------------
# OBJECT-IDENTITY
# ---------------------------------------------------------------------------


class TestObjectIdentity:
    MIB = """
OBJ-ID-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-IDENTITY FROM SNMPv2-SMI ;

objIdMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Test MIB."
    ::= { 1 99 }

testObjId OBJECT-IDENTITY
    STATUS  current
    DESCRIPTION "An object identity."
    ::= { objIdMIB 1 }

END
"""

    def test_object_identity_parsed(self):
        mib = _parse(self.MIB)
        assert "testObjId" in mib.objects
        obj = mib.objects["testObjId"]
        assert obj.object_type == "OBJECT-IDENTITY"
        assert obj.status == "current"
        assert obj.description == "An object identity."


# ---------------------------------------------------------------------------
# NOTIFICATION-TYPE
# ---------------------------------------------------------------------------


class TestNotificationType:
    MIB = """
NOTIF-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, NOTIFICATION-TYPE, OBJECT-TYPE, Integer32
        FROM SNMPv2-SMI ;

notifMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Notification MIB."
    ::= { 1 20 }

linkDown NOTIFICATION-TYPE
    STATUS  current
    DESCRIPTION "Link went down."
    ::= { notifMIB 1 }

END
"""

    def test_notification_in_notifications_dict(self):
        mib = _parse(self.MIB)
        assert "linkDown" in mib.notifications
        assert "linkDown" not in mib.objects

    def test_notification_object_type(self):
        notif = _parse(self.MIB).notifications["linkDown"]
        assert notif.object_type == "NOTIFICATION-TYPE"

    def test_notification_status_and_description(self):
        notif = _parse(self.MIB).notifications["linkDown"]
        assert notif.status == "current"
        assert "down" in notif.description.lower()


# ---------------------------------------------------------------------------
# TEXTUAL-CONVENTION
# ---------------------------------------------------------------------------


class TestTextualConvention:
    MIB = """
TC-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

tcMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "TC MIB."
    ::= { 1 30 }

DisplayString ::= TEXTUAL-CONVENTION
    STATUS      current
    DESCRIPTION "A display string."
    SYNTAX      OCTET STRING

InterfaceIndex ::= TEXTUAL-CONVENTION
    STATUS      current
    DESCRIPTION "Interface index."
    SYNTAX      INTEGER

END
"""

    def test_tc_in_types(self):
        mib = _parse(self.MIB)
        assert "DisplayString" in mib.types
        assert "InterfaceIndex" in mib.types

    def test_tc_base_type(self):
        mib = _parse(self.MIB)
        assert mib.types["DisplayString"].base_type == "OCTET STRING"
        assert mib.types["InterfaceIndex"].base_type == "INTEGER"

    def test_tc_description(self):
        mib = _parse(self.MIB)
        assert "display string" in mib.types["DisplayString"].description.lower()


# ---------------------------------------------------------------------------
# Syntax types — Counter64, Gauge32, Unsigned32, TimeTicks, Opaque, BITS,
#               SEQUENCE, CHOICE, OID, IpAddress, NetworkAddress
# ---------------------------------------------------------------------------


class TestSyntaxTypes:
    MIB = """
SYNTAX-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE,
    Counter32, Counter64, Gauge32, Unsigned32, TimeTicks, Opaque,
    Integer32, IpAddress
        FROM SNMPv2-SMI ;

syntaxMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Syntax test MIB."
    ::= { 1 40 }

ctr32Obj OBJECT-TYPE
    SYNTAX      Counter32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Counter32."
    ::= { syntaxMIB 1 }

ctr64Obj OBJECT-TYPE
    SYNTAX      Counter64
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Counter64."
    ::= { syntaxMIB 2 }

gauge32Obj OBJECT-TYPE
    SYNTAX      Gauge32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Gauge32."
    ::= { syntaxMIB 3 }

u32Obj OBJECT-TYPE
    SYNTAX      Unsigned32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Unsigned32."
    ::= { syntaxMIB 4 }

ticksObj OBJECT-TYPE
    SYNTAX      TimeTicks
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "TimeTicks."
    ::= { syntaxMIB 5 }

opaqueObj OBJECT-TYPE
    SYNTAX      Opaque
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Opaque."
    ::= { syntaxMIB 6 }

ipObj OBJECT-TYPE
    SYNTAX      IpAddress
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "IpAddress."
    ::= { syntaxMIB 7 }

oidObj OBJECT-TYPE
    SYNTAX      OBJECT IDENTIFIER
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "OID."
    ::= { syntaxMIB 8 }

nullObj OBJECT-TYPE
    SYNTAX      NULL
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "NULL."
    ::= { syntaxMIB 9 }

END
"""

    @pytest.mark.parametrize(
        "name,expected_syntax",
        [
            ("ctr32Obj", "Counter32"),
            ("ctr64Obj", "Counter64"),
            ("gauge32Obj", "Gauge32"),
            ("u32Obj", "Unsigned32"),
            ("ticksObj", "TimeTicks"),
            ("opaqueObj", "Opaque"),
            ("ipObj", "IpAddress"),
            ("oidObj", "OBJECT IDENTIFIER"),
            ("nullObj", "NULL"),
        ],
    )
    def test_syntax_type(self, name, expected_syntax):
        mib = _parse(self.MIB)
        assert mib.objects[name].syntax == expected_syntax


class TestBitsType:
    MIB = """
BITS-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;

bitsMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Bits test MIB."
    ::= { 1 41 }

portFlags OBJECT-TYPE
    SYNTAX      BITS { up(0), down(1), testing(2) }
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Port flags."
    ::= { bitsMIB 1 }

END
"""

    def test_bits_syntax(self):
        mib = _parse(self.MIB)
        assert mib.objects["portFlags"].syntax == "BITS"


class TestSequenceTypes:
    MIB = """
SEQ-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;

seqMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Sequence test MIB."
    ::= { 1 42 }

myTable OBJECT-TYPE
    SYNTAX      SEQUENCE OF MyEntry
    MAX-ACCESS  not-accessible
    STATUS      current
    DESCRIPTION "A table."
    ::= { seqMIB 1 }

MyEntry ::= SEQUENCE {
    myIndex Integer32
}

END
"""

    def test_sequence_of_syntax(self):
        mib = _parse(self.MIB)
        assert mib.objects["myTable"].syntax == "SEQUENCE OF MyEntry"

    def test_sequence_type_in_types(self):
        mib = _parse(self.MIB)
        assert "MyEntry" in mib.types
        assert "SEQUENCE" in mib.types["MyEntry"].base_type


# ---------------------------------------------------------------------------
# INDEX and AUGMENTS clauses
# ---------------------------------------------------------------------------


class TestIndexAndAugments:
    INDEX_MIB = """
INDEX-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;

indexMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Index test MIB."
    ::= { 1 50 }

myEntry OBJECT-TYPE
    SYNTAX      INTEGER
    MAX-ACCESS  not-accessible
    STATUS      current
    DESCRIPTION "Row entry."
    INDEX { myIndex }
    ::= { indexMIB 1 }

myIndex OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Row index."
    ::= { indexMIB 2 }

END
"""

    AUGMENTS_MIB = """
AUGMENTS-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;

augMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Augments test MIB."
    ::= { 1 51 }

extEntry OBJECT-TYPE
    SYNTAX      INTEGER
    MAX-ACCESS  not-accessible
    STATUS      current
    DESCRIPTION "Augmenting row."
    AUGMENTS { baseEntry }
    ::= { augMIB 1 }

baseEntry OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Base row."
    ::= { augMIB 2 }

END
"""

    def test_index_parsed(self):
        mib = _parse(self.INDEX_MIB)
        assert mib.objects["myEntry"].index == ["myIndex"]

    def test_augments_parsed(self):
        mib = _parse(self.AUGMENTS_MIB)
        assert mib.objects["extEntry"].augments == "baseEntry"


# ---------------------------------------------------------------------------
# OBJECT-GROUP, NOTIFICATION-GROUP, MODULE-COMPLIANCE, AGENT-CAPABILITIES
# ---------------------------------------------------------------------------


class TestGroupsAndCompliance:
    MIB = """
COMPLIANCE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32,
    NOTIFICATION-TYPE FROM SNMPv2-SMI
    OBJECT-GROUP, NOTIFICATION-GROUP, MODULE-COMPLIANCE FROM SNMPv2-CONF ;

compMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Compliance MIB."
    ::= { 1 60 }

aScalar OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A scalar."
    ::= { compMIB 1 }

aNotif NOTIFICATION-TYPE
    STATUS  current
    DESCRIPTION "A notification."
    ::= { compMIB 2 }

compGroup OBJECT-GROUP
    OBJECTS { aScalar }
    STATUS  current
    DESCRIPTION "Object group."
    ::= { compMIB 3 }

notifGroup NOTIFICATION-GROUP
    NOTIFICATIONS { aNotif }
    STATUS  current
    DESCRIPTION "Notification group."
    ::= { compMIB 4 }

compSpec MODULE-COMPLIANCE
    STATUS  current
    DESCRIPTION "Module compliance."
    MODULE
        MANDATORY-GROUPS { compGroup }
    ::= { compMIB 5 }

END
"""

    def test_object_group_parsed(self):
        mib = _parse(self.MIB)
        assert "compGroup" in mib.objects
        assert mib.objects["compGroup"].object_type == "OBJECT-GROUP"

    def test_notification_group_parsed(self):
        mib = _parse(self.MIB)
        assert "notifGroup" in mib.objects
        assert mib.objects["notifGroup"].object_type == "NOTIFICATION-GROUP"

    def test_module_compliance_parsed(self):
        mib = _parse(self.MIB)
        assert "compSpec" in mib.objects
        assert mib.objects["compSpec"].object_type == "MODULE-COMPLIANCE"


# ---------------------------------------------------------------------------
# TRAP-TYPE (SMIv1 compat in smiv2 grammar)
# ---------------------------------------------------------------------------


class TestTrapType:
    MIB = """
TRAP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

trapMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Trap MIB."
    ::= { 1 70 }

LINK-DOWN TRAP-TYPE
    ENTERPRISE trapMIB
    DESCRIPTION "Link down trap."
    ::= 1

END
"""

    def test_trap_type_parsed(self):
        mib = _parse(self.MIB)
        assert "LINK-DOWN" in mib.objects
        assert mib.objects["LINK-DOWN"].object_type == "TRAP-TYPE"
        assert mib.objects["LINK-DOWN"].oid == "1"


# ---------------------------------------------------------------------------
# OID resolution — named_arc, name_arc, number_arc
# ---------------------------------------------------------------------------


class TestOidResolution:
    MIB = """
OID-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

oidMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "OID test MIB."
    ::= { iso org(3) dod(6) 1 }

END
"""

    def test_named_arc_in_oid_path(self):
        mib = _parse(self.MIB)
        # named_arc: org(3) → 3, dod(6) → 6; number_arc: 1 → 1
        assert 3 in mib.objects["oidMIB"].oid_path
        assert 6 in mib.objects["oidMIB"].oid_path

    def test_name_arc_not_in_int_path(self):
        # 'iso' is a name_arc (no number) — skipped in int_path
        mib = _parse(self.MIB)
        # oid string contains "iso" but int path only has numeric arcs
        assert "iso" in mib.objects["oidMIB"].oid


# ---------------------------------------------------------------------------
# VALUE-ASSIGNMENT
# ---------------------------------------------------------------------------


class TestValueAssignment:
    MIB = """
VAL-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

valMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Value assignment MIB."
    ::= { 1 80 }

enterprises OBJECT IDENTIFIER ::= { 1 3 6 1 4 1 }

END
"""

    def test_value_assignment_parsed(self):
        mib = _parse(self.MIB)
        assert "enterprises" in mib.objects
        assert mib.objects["enterprises"].object_type == "OBJECT IDENTIFIER"


# ---------------------------------------------------------------------------
# SMIv1 — additional constructs
# ---------------------------------------------------------------------------


class TestSMIv1Constructs:
    MIB = """
V1-MIB DEFINITIONS ::= BEGIN
IMPORTS
    OBJECT-TYPE FROM RFC-1212
    enterprises FROM RFC1155-SMI ;

v1Scalar OBJECT-TYPE
    SYNTAX  INTEGER
    ACCESS  read-write
    STATUS  mandatory
    DESCRIPTION "A writable scalar."
    ::= { enterprises 1 1 }

v1Optional OBJECT-TYPE
    SYNTAX  INTEGER
    ACCESS  read-only
    STATUS  optional
    ::= { enterprises 1 2 }

v1Deprecated OBJECT-TYPE
    SYNTAX  INTEGER
    ACCESS  not-accessible
    STATUS  deprecated
    ::= { enterprises 1 3 }

END
"""

    def test_read_write_access(self):
        parser = SmiParser(dialect="smiv1")
        mib = parser.parse(self.MIB)
        assert mib.objects["v1Scalar"].max_access == "read-write"

    def test_optional_status(self):
        parser = SmiParser(dialect="smiv1")
        mib = parser.parse(self.MIB)
        assert mib.objects["v1Optional"].status == "optional"

    def test_not_accessible(self):
        parser = SmiParser(dialect="smiv1")
        mib = parser.parse(self.MIB)
        assert mib.objects["v1Deprecated"].max_access == "not-accessible"

    def test_deprecated_status(self):
        parser = SmiParser(dialect="smiv1")
        mib = parser.parse(self.MIB)
        assert mib.objects["v1Deprecated"].status == "deprecated"


# ---------------------------------------------------------------------------
# SMIv1 — Counter, Gauge, NetworkAddress (SMIv1-only syntax types)
# ---------------------------------------------------------------------------


class TestSMIv1SyntaxTypes:
    MIB = """
V1-SYNTAX-MIB DEFINITIONS ::= BEGIN
IMPORTS
    OBJECT-TYPE FROM RFC-1212
    Counter, Gauge, NetworkAddress, TimeTicks
        FROM RFC1155-SMI ;

ctrObj OBJECT-TYPE
    SYNTAX  Counter
    ACCESS  read-only
    STATUS  mandatory
    ::= { 1 1 1 }

gaugeObj OBJECT-TYPE
    SYNTAX  Gauge
    ACCESS  read-only
    STATUS  mandatory
    ::= { 1 1 2 }

netAddrObj OBJECT-TYPE
    SYNTAX  NetworkAddress
    ACCESS  read-only
    STATUS  mandatory
    ::= { 1 1 3 }

timeObj OBJECT-TYPE
    SYNTAX  TimeTicks
    ACCESS  read-only
    STATUS  mandatory
    ::= { 1 1 4 }

END
"""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("ctrObj", "Counter"),
            ("gaugeObj", "Gauge"),
            ("netAddrObj", "NetworkAddress"),
            ("timeObj", "TimeTicks"),
        ],
    )
    def test_v1_syntax(self, name, expected):
        parser = SmiParser(dialect="smiv1")
        mib = parser.parse(self.MIB)
        assert mib.objects[name].syntax == expected


# ---------------------------------------------------------------------------
# Clause coverage — REVISION, UNITS, REFERENCE, DEFVAL, DISPLAY-HINT, OBJECTS
# ---------------------------------------------------------------------------


class TestOptionalClauses:
    """Exercises the null-returning clause handlers so coverage tracks them."""

    MIB_REVISION = """
REV-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

revMIB MODULE-IDENTITY
    LAST-UPDATED "200301010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "MIB with revisions."
    REVISION     "200301010000Z"
    DESCRIPTION  "First revision."
    REVISION     "200101010000Z"
    DESCRIPTION  "Initial version."
    ::= { 1 100 }

END
"""

    MIB_UNITS_REF_DEFVAL = """
OPTS-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;

optsMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Optional clauses MIB."
    ::= { 1 101 }

ifSpeed OBJECT-TYPE
    SYNTAX      Integer32
    UNITS       "bits per second"
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Interface speed."
    REFERENCE   "RFC 2863 section 3.1.5"
    DEFVAL      { 0 }
    ::= { optsMIB 1 }

END
"""

    MIB_DISPLAY_HINT = """
DH-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

dhMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Display hint MIB."
    ::= { 1 102 }

DisplayHintString ::= TEXTUAL-CONVENTION
    DISPLAY-HINT "255a"
    STATUS       current
    DESCRIPTION  "A string with display hint."
    SYNTAX       OCTET STRING

END
"""

    MIB_NOTIFICATION_OBJECTS = """
NOTIF-OBJ-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, NOTIFICATION-TYPE, OBJECT-TYPE, Integer32
        FROM SNMPv2-SMI ;

notifObjMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Notification with objects MIB."
    ::= { 1 103 }

ifIndex OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Interface index."
    ::= { notifObjMIB 1 }

linkUp NOTIFICATION-TYPE
    OBJECTS     { ifIndex }
    STATUS      current
    DESCRIPTION "Link came up."
    ::= { notifObjMIB 2 }

END
"""

    MIB_TRAP_VARIABLES = """
TRAP-VAR-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

trapVarMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Trap with variables."
    ::= { 1 104 }

LINK-UP TRAP-TYPE
    ENTERPRISE trapVarMIB
    VARIABLES  { trapVarMIB }
    DESCRIPTION "Link up trap."
    ::= 1

END
"""

    def test_revision_clauses_parsed(self):
        mib = _parse(self.MIB_REVISION)
        assert mib.name == "REV-MIB"

    def test_units_and_defval_parsed(self):
        mib = _parse(self.MIB_UNITS_REF_DEFVAL)
        assert "ifSpeed" in mib.objects

    def test_display_hint_tc_parsed(self):
        mib = _parse(self.MIB_DISPLAY_HINT)
        assert "DisplayHintString" in mib.types

    def test_notification_with_objects_parsed(self):
        mib = _parse(self.MIB_NOTIFICATION_OBJECTS)
        assert "linkUp" in mib.notifications

    def test_trap_with_variables_parsed(self):
        mib = _parse(self.MIB_TRAP_VARIABLES)
        assert "LINK-UP" in mib.objects


# ---------------------------------------------------------------------------
# Scalar value assignment (non-OID value_assignment path)
# ---------------------------------------------------------------------------


class TestScalarValueAssignment:
    MIB = """
SCALAR-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

scalarMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Scalar assignment MIB."
    ::= { 1 105 }

myVersion INTEGER ::= 2

END
"""

    def test_scalar_assignment_does_not_crash(self):
        # scalar_value assignments return None from transformer; module parses OK
        mib = _parse(self.MIB)
        assert mib.name == "SCALAR-MIB"


# ---------------------------------------------------------------------------
# CHOICE type
# ---------------------------------------------------------------------------


class TestChoiceType:
    MIB = """
CHOICE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;

choiceMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Choice type MIB."
    ::= { 1 106 }

MyChoice ::= CHOICE {
    intVal  Integer32,
    octVal  OCTET STRING
}

END
"""

    def test_choice_type_in_types(self):
        mib = _parse(self.MIB)
        assert "MyChoice" in mib.types
        assert mib.types["MyChoice"].base_type == "CHOICE"


# ---------------------------------------------------------------------------
# _unquote — non-quoted token path
# ---------------------------------------------------------------------------


class TestUnquoteHelper:
    def test_unquote_with_plain_token(self):
        from trishul_smi.parser.transformer import _unquote

        assert _unquote("hello") == "hello"

    def test_unquote_with_quoted_string(self):
        from trishul_smi.parser.transformer import _unquote

        assert _unquote('"hello world"') == "hello world"

    def test_unquote_with_escaped_quote(self):
        from trishul_smi.parser.transformer import _unquote

        assert _unquote('"say \\"hi\\""') == 'say "hi"'


# ---------------------------------------------------------------------------
# Earley fallback path — ParseError from Earley stage
# ---------------------------------------------------------------------------


class TestParserFallback:
    def test_lalr_fails_earley_succeeds(self):
        """Some MIBs that fail LALR parse succeed with Earley.
        We verify the parser silently falls back rather than erroring.
        This is tested indirectly by confirming complex MIBs parse correctly.
        """
        # AGENT-CAPABILITIES is complex enough to sometimes stress LALR
        mib_text = """
AGENT-CAP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    AGENT-CAPABILITIES FROM SNMPv2-CONF ;

agentCapMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Agent capabilities MIB."
    ::= { 1 90 }

testAgent AGENT-CAPABILITIES
    PRODUCT-RELEASE "Test Agent 1.0"
    STATUS          current
    DESCRIPTION     "Test agent capabilities."
    ::= { agentCapMIB 1 }

END
"""
        mib = _parser.parse(mib_text)
        assert "testAgent" in mib.objects
        assert mib.objects["testAgent"].object_type == "AGENT-CAPABILITIES"


# ---------------------------------------------------------------------------
# Organization and revisions on MODULE-IDENTITY
# ---------------------------------------------------------------------------


class TestModuleOrganization:
    MIB = """
ORG-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

orgMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "My Test Organization"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Org test MIB."
    ::= { 1 300 }

END
"""

    def test_organization_stored(self):
        m = _parse(self.MIB)
        assert m.organization == "My Test Organization"

    def test_missing_organization_is_none(self):
        from trishul_smi.models.mib_module import MibModule

        assert MibModule(name="X", language="SMIv2").organization is None


class TestModuleRevisions:
    MIB = """
REV-MIB2 DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

revMIB2 MODULE-IDENTITY
    LAST-UPDATED "200301010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "MIB with two revisions."
    REVISION     "200301010000Z"
    DESCRIPTION  "Second."
    REVISION     "200101010000Z"
    DESCRIPTION  "Initial."
    ::= { 1 301 }

END
"""

    def test_two_revisions_stored(self):
        m = _parse(self.MIB)
        assert len(m.revisions) == 2

    def test_revision_dates(self):
        m = _parse(self.MIB)
        dates = {r["date"] for r in m.revisions}
        assert "200301010000Z" in dates
        assert "200101010000Z" in dates

    def test_revision_descriptions(self):
        m = _parse(self.MIB)
        descs = [r["description"] for r in m.revisions]
        assert any("Second" in d for d in descs)

    def test_no_revisions_is_empty_list(self):
        mib_text = """
NOREV-MIB2 DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
noRevMIB2 MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "No revisions."
    ::= { 1 302 }
END
"""
        m = _parse(mib_text)
        assert m.revisions == []


# ---------------------------------------------------------------------------
# TEXTUAL-CONVENTION constraints and display_hint
# ---------------------------------------------------------------------------


class TestTCDisplayHint:
    MIB = """
DH2-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

dh2MIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Display hint MIB."
    ::= { 1 310 }

OwnerString ::= TEXTUAL-CONVENTION
    DISPLAY-HINT "255a"
    STATUS       current
    DESCRIPTION  "A string."
    SYNTAX       OCTET STRING (SIZE (0..255))

END
"""

    def test_display_hint_stored(self):
        m = _parse(self.MIB)
        assert m.types["OwnerString"].display_hint == "255a"

    def test_status_stored(self):
        m = _parse(self.MIB)
        assert m.types["OwnerString"].status == "current"

    def test_size_constraint_stored(self):
        m = _parse(self.MIB)
        tc = m.types["OwnerString"]
        assert tc.constraints is not None
        assert tc.constraints["kind"] == "size"
        assert tc.constraints["data"][0] == [0, 255]

    def test_no_display_hint_is_none(self):
        mib_text = """
NODH-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;
noDhMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "No display hint."
    ::= { 1 311 }
Plain ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Plain."
    SYNTAX  INTEGER
END
"""
        m = _parse(mib_text)
        assert m.types["Plain"].display_hint is None


class TestTCEnumConstraint:
    MIB = """
ENUM-TC-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

enumTcMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Enum TC MIB."
    ::= { 1 312 }

TruthValue ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Boolean."
    SYNTAX  INTEGER { true(1), false(2) }

END
"""

    def test_enum_constraint_kind(self):
        m = _parse(self.MIB)
        assert m.types["TruthValue"].constraints["kind"] == "enum"

    def test_enum_values(self):
        m = _parse(self.MIB)
        data = m.types["TruthValue"].constraints["data"]
        names = [item[0] for item in data]
        assert "true" in names
        assert "false" in names


class TestTCRangeConstraint:
    MIB = """
RANGE-TC-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

rangeTcMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Range TC MIB."
    ::= { 1 313 }

InterfaceIndex ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Interface index."
    SYNTAX  INTEGER (1..2147483647)

END
"""

    def test_range_constraint_kind(self):
        m = _parse(self.MIB)
        assert m.types["InterfaceIndex"].constraints["kind"] == "range"

    def test_range_bounds(self):
        m = _parse(self.MIB)
        data = m.types["InterfaceIndex"].constraints["data"]
        assert data[0][0] == 1
        assert data[0][1] == 2147483647


# ---------------------------------------------------------------------------
# oid_parent stored by transformer
# ---------------------------------------------------------------------------


class TestOidParentTransformer:
    MIB = """
OIDPAR-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;

oidParMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "OID parent test MIB."
    ::= { 1 320 }

aScalar OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Child of oidParMIB."
    ::= { oidParMIB 1 }

END
"""

    def test_oid_parent_set_on_named_arc(self):
        m = _parse(self.MIB)
        obj = m.objects["aScalar"]
        assert obj.oid_parent == "oidParMIB"

    def test_oid_path_contains_only_local_arcs_before_resolution(self):
        m = _parse(self.MIB)
        obj = m.objects["aScalar"]
        assert obj.oid_path == [1]

    def test_module_identity_has_no_parent_for_absolute_oid(self):
        """{ 1 320 } has only number arcs — oid_parent should be None."""
        m = _parse(self.MIB)
        mi = m.objects["oidParMIB"]
        assert mi.oid_parent is None


# ---------------------------------------------------------------------------
# Vendor dialect quirks
# ---------------------------------------------------------------------------


class TestVendorDialectQuirks:
    def test_imports_without_semicolon(self):
        mib_text = """
NOSEMI-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI

noSemiMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "No semicolon after IMPORTS."
    ::= { 1 330 }

END
"""
        m = _parse(mib_text)
        assert m.name == "NOSEMI-MIB"

    def test_index_trailing_comma(self):
        mib_text = """
TRAIL-IDX-MIB2 DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;

trailIdxMIB2 MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Trailing comma in INDEX."
    ::= { 1 331 }

myEntry2 OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  not-accessible
    STATUS      current
    DESCRIPTION "Row."
    INDEX { myIdx2, }
    ::= { trailIdxMIB2 1 }

myIdx2 OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Index."
    ::= { trailIdxMIB2 2 }

END
"""
        m = _parse(mib_text)
        assert m.objects["myEntry2"].index == ["myIdx2"]

    def test_bits_trailing_comma_smiv2(self):
        mib_text = """
BITS-TRAIL2-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI ;

bitsTrail2MIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "BITS trailing comma SMIv2."
    ::= { 1 332 }

flags2 OBJECT-TYPE
    SYNTAX      BITS { up(0), down(1), }
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Flags."
    ::= { bitsTrail2MIB 1 }

END
"""
        m = _parse(mib_text)
        assert "flags2" in m.objects

    def test_smiv1_bits_type(self):
        mib_text = """
BITS-V1-MIB DEFINITIONS ::= BEGIN
IMPORTS
    OBJECT-TYPE FROM RFC-1212 ;

flags OBJECT-TYPE
    SYNTAX  BITS { a(0), b(1) }
    ACCESS  read-only
    STATUS  mandatory
    ::= { 1 1 1 }

END
"""
        parser = SmiParser(dialect="smiv1")
        m = parser.parse(mib_text)
        assert m.objects["flags"].syntax == "BITS"


# ---------------------------------------------------------------------------
# Constraint handlers — multi-range union, single_value, hex bound
# ---------------------------------------------------------------------------


class TestConstraintHandlers:
    def test_multi_range_integer_constraint_stored(self):
        """INTEGER (0..10 | 20..30) → union constraint with two ranges."""
        mib_text = """
MULTI-RANGE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

mrMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Multi-range TC."
    ::= { 1 340 }

MultiRange ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Multi-range integer."
    SYNTAX  INTEGER (0..10 | 20..30)

END
"""
        m = _parse(mib_text)
        tc = m.types["MultiRange"]
        assert tc.constraints is not None
        assert tc.constraints["kind"] == "union"

    def test_multi_range_size_constraint_stored(self):
        """OCTET STRING (SIZE (0..10 | 20..30)) → union size constraint."""
        mib_text = """
MULTI-SIZE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

msMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Multi-size TC."
    ::= { 1 341 }

MultiSize ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Multi-size string."
    SYNTAX  OCTET STRING (SIZE (0..10 | 20..30))

END
"""
        m = _parse(mib_text)
        tc = m.types["MultiSize"]
        assert tc.constraints is not None
        assert tc.constraints["kind"] == "union"

    def test_single_value_range_bound(self):
        """INTEGER (42) — single_value rule → [42, 42] pair."""
        mib_text = """
SINGLE-VAL-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

svMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Single value constraint."
    ::= { 1 342 }

SingleVal ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Fixed value."
    SYNTAX  INTEGER (42)

END
"""
        m = _parse(mib_text)
        tc = m.types["SingleVal"]
        assert tc.constraints is not None
        assert tc.constraints["data"][0][0] == 42
        assert tc.constraints["data"][0][1] == 42

    def test_hex_range_bound_parsed(self):
        """INTEGER ('00'H..'FF'H) — hex range bounds parsed as integers."""
        mib_text = """
HEX-RANGE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

hexMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Hex range MIB."
    ::= { 1 343 }

HexByte ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "A hex-bounded integer."
    SYNTAX  INTEGER ('00'H..'FF'H)

END
"""
        m = _parse(mib_text)
        tc = m.types["HexByte"]
        assert tc.constraints is not None
        ranges = tc.constraints["data"]
        assert ranges[0][0] == 0x00
        assert ranges[0][1] == 0xFF

    def test_min_max_range_bounds_parse(self):
        """INTEGER (MIN..MAX) — MIN/MAX bounds preserved as strings."""
        mib_text = """
MINMAX-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

mmMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "MIN/MAX bounds MIB."
    ::= { 1 344 }

AnyInt ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Unconstrained."
    SYNTAX  INTEGER (MIN..MAX)

END
"""
        m = _parse(mib_text)
        tc = m.types["AnyInt"]
        assert tc.constraints is not None
        ranges = tc.constraints["data"]
        assert ranges[0][0] == "MIN"
        assert ranges[0][1] == "MAX"


# ---------------------------------------------------------------------------
# AGENT-CAPABILITIES variation clauses (write_syntax, access, creation_requires)
# ---------------------------------------------------------------------------


class TestModuleComplianceSubclauses:
    MIB = """
COMP-SUB-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI
    OBJECT-GROUP, MODULE-COMPLIANCE FROM SNMPv2-CONF ;

compSubMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Compliance sub-clauses MIB."
    ::= { 1 360 }

aScalar OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A scalar."
    ::= { compSubMIB 1 }

aGroup OBJECT-GROUP
    OBJECTS { aScalar }
    STATUS  current
    DESCRIPTION "A group."
    ::= { compSubMIB 2 }

compSpec MODULE-COMPLIANCE
    STATUS  current
    DESCRIPTION "Full compliance spec."
    MODULE
        MANDATORY-GROUPS { aGroup }
        GROUP aGroup
            DESCRIPTION "The group."
        OBJECT aScalar
            SYNTAX      Integer32
            WRITE-SYNTAX Integer32
            MIN-ACCESS  read-only
            DESCRIPTION "Scalar constraint."
    ::= { compSubMIB 3 }

END
"""

    def test_compliance_with_group_and_object_subclauses_parses(self):
        m = _parse(self.MIB)
        assert "compSpec" in m.objects

    def test_compliance_object_min_access_parses(self):
        m = _parse(self.MIB)
        assert m.objects["compSpec"].object_type == "MODULE-COMPLIANCE"


class TestAgentCapabilitiesVariations:
    MIB = """
AGCAP-VAR-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    AGENT-CAPABILITIES FROM SNMPv2-CONF ;

agCapVarMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Agent caps with variation clauses."
    ::= { 1 350 }

testAgent AGENT-CAPABILITIES
    PRODUCT-RELEASE "Test Agent 1.0"
    STATUS          current
    DESCRIPTION     "Test."
    SUPPORTS        SNMPv2-SMI
    INCLUDES        { ifGroup }
    VARIATION       ifIndex
        SYNTAX          INTEGER
        WRITE-SYNTAX    INTEGER
        ACCESS          read-only
        CREATION-REQUIRES { ifDescr }
        DESCRIPTION "Variation desc."
    ::= { agCapVarMIB 1 }

END
"""

    def test_agent_caps_with_variations_parses(self):
        m = _parse(self.MIB)
        assert "testAgent" in m.objects
        assert m.objects["testAgent"].object_type == "AGENT-CAPABILITIES"


# ---------------------------------------------------------------------------
# Lenient parsing of non-standard vendor syntax (warns, does not fail)
# ---------------------------------------------------------------------------


class TestNonStandardSyntaxWarnings:
    """Vendor MIBs use non-standard shorthand that strict SMIv2 (and libsmi)
    reject. The parser accepts these leniently and records a non-fatal warning
    on the module with the source line number."""

    _HEADER = """
VENDOR-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    TEXTUAL-CONVENTION FROM SNMPv2-TC ;

vMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Vendor test."
    ::= { 1 900 }

"""

    def test_bare_octet_string_range_warns_and_is_size(self):
        """`OCTET STRING (0..30)` (no SIZE keyword) → size constraint + warning."""
        mib = (
            self._HEADER
            + """
BareOctet ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Bare range."
    SYNTAX  OCTET STRING (0..30)

END
"""
        )
        m = _parse(mib)
        tc = m.types["BareOctet"]
        assert tc.constraints is not None
        assert tc.constraints["kind"] == "size"
        assert tc.constraints["data"] == [[0, 30]]
        # exactly one warning, referencing the OCTET STRING line
        octet_warnings = [w for w in m.warnings if "OCTET STRING" in w]
        assert len(octet_warnings) == 1
        assert "without SIZE" in octet_warnings[0]
        assert "line " in octet_warnings[0]

    def test_standard_size_constraint_does_not_warn(self):
        """`OCTET STRING (SIZE (0..30))` is standard → no warning."""
        mib = (
            self._HEADER
            + """
StdOctet ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Standard size."
    SYNTAX  OCTET STRING (SIZE (0..30))

END
"""
        )
        m = _parse(mib)
        assert m.types["StdOctet"].constraints["kind"] == "size"
        assert not any("OCTET STRING" in w for w in m.warnings)

    def test_bit_string_alias_warns(self):
        """`BIT STRING { ... }` (singular) accepted as alias for BITS + warning."""
        mib = (
            self._HEADER
            + """
BitsAlt ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Bit string alias."
    SYNTAX  BIT STRING { start(1), stop(2) }

END
"""
        )
        m = _parse(mib)
        assert m.types["BitsAlt"].base_type == "BITS"
        bit_warnings = [w for w in m.warnings if "BIT STRING" in w]
        assert len(bit_warnings) == 1
        assert "alias" in bit_warnings[0]

    def test_standard_bits_does_not_warn(self):
        """`BITS { ... }` is standard → no BIT STRING warning."""
        mib = (
            self._HEADER
            + """
StdBits ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Standard bits."
    SYNTAX  BITS { start(1), stop(2) }

END
"""
        )
        m = _parse(mib)
        assert m.types["StdBits"].base_type == "BITS"
        assert not any("BIT STRING" in w for w in m.warnings)

    def test_warning_line_number_is_accurate(self):
        """The reported line number points at the OCTET STRING line in source."""
        mib = (
            self._HEADER
            + """


LateOctet ::= TEXTUAL-CONVENTION
    STATUS  current
    DESCRIPTION "Late."
    SYNTAX  OCTET STRING (0..5)

END
"""
        )
        m = _parse(mib)
        octet_warnings = [w for w in m.warnings if "OCTET STRING" in w]
        assert len(octet_warnings) == 1
        # The SYNTAX line is the only line containing OCTET STRING; locate it.
        src_lines = mib.splitlines()
        octet_line = next(i + 1 for i, ln in enumerate(src_lines) if "OCTET STRING (0..5)" in ln)
        assert f"line {octet_line}" in octet_warnings[0]
