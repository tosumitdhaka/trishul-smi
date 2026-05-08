"""Unit tests for SmiParser — uses fixture MIB strings."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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

TAGGED_TYPES_MIB = """
TAGGED-TYPES-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE
        FROM SNMPv2-SMI
    ;

taggedTypesMib MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Tagged type regression fixture."
    ::= { 1 4 }

IpAddress ::=
    [APPLICATION 0]
        IMPLICIT OCTET STRING (SIZE (4))

Counter32 ::=
    [APPLICATION 1]
        IMPLICIT INTEGER (0..4294967295)

addr OBJECT-TYPE
    SYNTAX      IpAddress
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Address."
    ::= { taggedTypesMib 1 }

counter OBJECT-TYPE
    SYNTAX      Counter32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Counter."
    ::= { taggedTypesMib 2 }

END
"""

WRAPPED_COMMENT_MIB = """
COMMENT-WRAP-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI ;

commentWrapMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Wrapped comment regression fixture."
    ::= { 1 5 }

WrappedEntry ::= SEQUENCE {
    first Integer32, -- wrapped
                     comment
    second Integer32
}

END
"""

SNMPV2_PDU_COMPAT_MIB = """
SNMPV2-PDU-COMPAT DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

snmpv2PduCompat MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "SNMPv2-PDU compatibility regression fixture."
    ::= { 1 6 }

max-bindings INTEGER ::= 2147483647

BoundedInt ::= INTEGER (0..max-bindings)

PDU ::= SEQUENCE {
    request-id INTEGER (-214783648..214783647),
    error-index INTEGER (0..max-bindings),
    variable-bindings VarBindList
}

VarBind ::= SEQUENCE {
    name ObjectName,
    CHOICE {
        value ObjectSyntax,
        unSpecified NULL,
        noSuchObject [0] IMPLICIT NULL
    }
}

VarBindList ::= SEQUENCE (SIZE (0..max-bindings)) OF VarBind

END
"""

INDENTED_COMMENT_COMPLIANCE_MIB = """
COMMENT-COMPLIANCE-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-COMPLIANCE FROM SNMPv2-CONF ;

   -- Compliance Statements
   compSpec MODULE-COMPLIANCE
       STATUS current
       DESCRIPTION "Indented standalone comment must not swallow this assignment."
       ::= { 1 7 }

END
"""

INDENTED_COMMENT_CHOICE_MIB = """
COMMENT-CHOICE-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;

commentChoiceMib MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Indented comment regression fixture."
    ::= { 1 8 }

SimpleSyntax ::=
    CHOICE {
        integer-value
            INTEGER (-2147483648..2147483647),
        -- OCTET STRINGs with a more restrictive size
        -- may also be used
        string-value
            OCTET STRING (SIZE (0..65535)),
        objectID-value
            OBJECT IDENTIFIER
    }

END
"""

SNMPV2_TC_IMPORTS_VARIANT_MIB = """
SNMPv2-TC DEFINITIONS ::= BEGIN

IMPORTS
    INTEGER, OCTET STRING, OBJECT IDENTIFIER
        FROM SNMPv2-SMI;

TEXTUAL-CONVENTION MACRO ::=
BEGIN
    TYPE NOTATION ::=
                  DisplayPart
                  "STATUS" Status
                  "DESCRIPTION" Text
                  ReferPart
                  "SYNTAX" Syntax
END

DisplayString ::= TEXTUAL-CONVENTION
    STATUS       current
    DESCRIPTION  "String."
    SYNTAX       OCTET STRING (SIZE (0..255))

END
"""


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


class TestParserCompatibilityFixes:
    def test_wrapped_inline_comment_continuation_parses(self):
        mib = SmiParser().parse(WRAPPED_COMMENT_MIB)

        assert mib.types["WrappedEntry"].base_type == "SEQUENCE"

    def test_snmpv2_pdu_compat_constructs_parse_and_preserve_constraints(self):
        mib = SmiParser().parse(SNMPV2_PDU_COMPAT_MIB)

        assert mib.types["BoundedInt"].constraints == {
            "kind": "range",
            "data": [[0, "max-bindings"]],
        }
        assert mib.types["VarBind"].base_type == "SEQUENCE"
        assert mib.types["VarBindList"].base_type == "SEQUENCE OF VarBind"
        assert mib.types["VarBindList"].constraints == {
            "kind": "size",
            "data": [[0, "max-bindings"]],
        }

    def test_indented_standalone_comment_does_not_swallow_module_compliance_assignment(self):
        mib = SmiParser().parse(INDENTED_COMMENT_COMPLIANCE_MIB)

        assert mib.objects["compSpec"].object_type == "MODULE-COMPLIANCE"
        assert mib.objects["compSpec"].description == (
            "Indented standalone comment must not swallow this assignment."
        )

    def test_indented_standalone_comment_does_not_swallow_choice_fields(self):
        mib = SmiParser().parse(INDENTED_COMMENT_CHOICE_MIB)

        assert mib.types["SimpleSyntax"].base_type == "CHOICE"

    def test_snmpv2_tc_variant_with_builtin_import_symbols_parses(self):
        mib = SmiParser().parse(SNMPV2_TC_IMPORTS_VARIANT_MIB)

        assert mib.name == "SNMPv2-TC"
        assert mib.imports["SNMPv2-SMI"] == [
            "INTEGER",
            "OCTET STRING",
            "OBJECT IDENTIFIER",
        ]
        assert mib.types["DisplayString"].base_type == "OCTET STRING"


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


class TestParserErrorPaths:
    def test_lalr_unexpected_error_raises_parse_error(self):
        """Non-UnexpectedInput exceptions from LALR must surface as ParseError."""
        from unittest.mock import patch

        from trishul_smi.errors import ParseError

        parser = SmiParser()
        with patch(
            "trishul_smi.parser.transformer.MibTransformer.transform",
            side_effect=RuntimeError("unexpected transformer crash"),
        ):
            with pytest.raises(ParseError, match="Unexpected error in LALR parse"):
                parser.parse(MINIMAL_V2)

    def test_earley_unexpected_error_raises_parse_error(self):
        """Non-UnexpectedInput exceptions from Earley must surface as ParseError."""
        from unittest.mock import patch

        from lark import UnexpectedInput

        from trishul_smi.errors import ParseError

        parser = SmiParser()

        call_count = 0

        def _fail_second(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise UnexpectedInput()
            raise RuntimeError("earley transformer crash")

        with patch(
            "trishul_smi.parser.transformer.MibTransformer.transform",
            side_effect=_fail_second,
        ):
            with pytest.raises(ParseError, match="Unexpected error in Earley parse"):
                parser.parse(MINIMAL_V2)


class TestTaggedTypeAssignments:
    def test_tagged_type_assignments_preserve_underlying_syntax_and_constraints(self):
        mib = SmiParser().parse(TAGGED_TYPES_MIB)

        assert mib.types["IpAddress"].base_type == "OCTET STRING"
        assert mib.types["IpAddress"].constraints == {"kind": "size", "data": [[4, 4]]}

        assert mib.types["Counter32"].base_type == "INTEGER"
        assert mib.types["Counter32"].constraints == {
            "kind": "range",
            "data": [[0, 4294967295]],
        }

    def test_objects_can_reference_tagged_type_assignments(self):
        mib = SmiParser().parse(TAGGED_TYPES_MIB)

        assert mib.objects["addr"].syntax == "IpAddress"
        assert mib.objects["counter"].syntax == "Counter32"


class TestParserCacheIsolation:
    def test_compiled_parser_is_reused_within_same_thread(self):
        parser = SmiParser()

        lalr = parser._get_parser("smiv2", earley=False)
        assert parser._get_parser("smiv2", earley=False) is lalr

    def test_compiled_parser_cache_is_thread_local(self):
        parser = SmiParser()
        main_thread_parser = parser._get_parser("smiv2", earley=False)

        with ThreadPoolExecutor(max_workers=1) as pool:
            worker_parser = pool.submit(parser._get_parser, "smiv2", False).result()
            worker_parser_again = pool.submit(parser._get_parser, "smiv2", False).result()

        assert worker_parser is worker_parser_again
        assert worker_parser is not main_thread_parser


class TestMainModule:
    def test_main_module_entrypoint(self):
        """python -m trishul_smi --help must exit 0 and print usage."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "trishul_smi", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "compile" in result.stdout.lower() or "usage" in result.stdout.lower()
