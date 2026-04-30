"""Unit tests for SmiParser — uses fixture MIB strings."""
from __future__ import annotations

import pytest

from trishul_smi.errors import ParseError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.parser.smi_parser import SmiParser

# ---------------------------------------------------------------------------
# Minimal fixture MIBs
# ---------------------------------------------------------------------------

MINIMAL_V2 = """
TEST-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32
        FROM SNMPv2-SMI
    ;

testMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "A minimal test MIB."
    ::= { 1 3 }

END
"""

MINIMAL_V2_OBJECT = """
SIMPLE-MIB DEFINITIONS ::= BEGIN

IMPORTS
    OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;

simpleScalar OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A simple scalar."
    ::= { 1 3 6 1 2 1 1 1 }

END
"""

MINIMAL_V1 = """
RFC1213-MIB DEFINITIONS ::= BEGIN

IMPORTS
    OBJECT-TYPE FROM RFC-1212
    mgmt, NetworkAddress, Counter, Gauge, TimeTicks
        FROM RFC1155-SMI
    ;

sysDescr OBJECT-TYPE
    SYNTAX  DisplayString
    ACCESS  read-only
    STATUS  mandatory
    ::= { 1 3 6 1 2 1 1 1 }

END
"""

INVALID_MIB = "this is not a valid MIB"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSmiParserV2:
    def test_parse_minimal_module(self):
        parser = SmiParser()
        mib = parser.parse(MINIMAL_V2)
        assert isinstance(mib, MibModule)
        assert mib.name == "TEST-MIB"
        assert mib.language == "SMIv2"

    def test_imports_populated(self):
        parser = SmiParser()
        mib = parser.parse(MINIMAL_V2)
        assert "SNMPv2-SMI" in mib.imports
        assert "MODULE-IDENTITY" in mib.imports["SNMPv2-SMI"]

    def test_module_identity_in_objects(self):
        parser = SmiParser()
        mib = parser.parse(MINIMAL_V2)
        assert "testMIB" in mib.objects
        assert mib.objects["testMIB"].object_type == "MODULE-IDENTITY"

    def test_object_type_parsed(self):
        parser = SmiParser()
        mib = parser.parse(MINIMAL_V2_OBJECT)
        assert "simpleScalar" in mib.objects
        obj = mib.objects["simpleScalar"]
        assert obj.object_type == "OBJECT-TYPE"
        assert obj.syntax == "Integer32"
        assert obj.max_access == "read-only"
        assert obj.status == "current"
        assert obj.oid != ""

    def test_description_extracted(self):
        parser = SmiParser()
        mib = parser.parse(MINIMAL_V2_OBJECT)
        assert mib.objects["simpleScalar"].description == "A simple scalar."


class TestSmiParserV1:
    def test_parse_minimal_v1(self):
        parser = SmiParser(dialect="smiv1")
        mib = parser.parse(MINIMAL_V1)
        assert isinstance(mib, MibModule)
        assert mib.name == "RFC1213-MIB"

    def test_v1_object_parsed(self):
        parser = SmiParser(dialect="smiv1")
        mib = parser.parse(MINIMAL_V1)
        assert "sysDescr" in mib.objects


class TestSmiParserErrors:
    def test_invalid_mib_raises_parse_error(self):
        parser = SmiParser()
        with pytest.raises(ParseError):
            parser.parse(INVALID_MIB)

    def test_empty_string_raises_parse_error(self):
        parser = SmiParser()
        with pytest.raises(ParseError):
            parser.parse("")


class TestAutoDetect:
    def test_detects_smiv2_from_imports(self):
        from trishul_smi.parser.smi_parser import _detect_dialect
        assert _detect_dialect(MINIMAL_V2) == "smiv2"

    def test_detects_smiv1_without_markers(self):
        from trishul_smi.parser.smi_parser import _detect_dialect
        assert _detect_dialect(MINIMAL_V1) == "smiv1"
