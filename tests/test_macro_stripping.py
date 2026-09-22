"""Regression tests for quote/comment-aware macro stripping (#11) and
imports-based dialect detection (#24).

The macro stripper must only reduce *genuine* ``NAME MACRO ::= BEGIN ... END``
blocks. Words like ``MACRO``/``END`` inside DESCRIPTION strings or ``--``
comments must neither start nor end a match, otherwise a module can be
destroyed or a bare ``END`` injected. Dialect detection must be driven by the
IMPORTS clauses, not by incidental mentions of SMIv2 module names in comments.
"""

from __future__ import annotations

import pytest

from trishul_smi.parser.smi_parser import SmiParser, _detect_dialect

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DESCRIPTION_WITH_MACRO_V2 = """
MACRO-WORD-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32
        FROM SNMPv2-SMI
    ;

macroWordMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "This module does not define a new MACRO here, honest."
    ::= { 1 3 }

macroWordScalar OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Another sentence that mentions a MACRO in passing."
    ::= { macroWordMIB 1 }

END
"""

COMMENTED_OUT_MACRO_V2 = """
COMMENTED-MACRO-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32
        FROM SNMPv2-SMI
    ;

commentedMacroMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Commented-out macro regression fixture."
    ::= { 1 4 }

-- The original design defined a custom macro:
--   FOO-MACRO MACRO ::=
--   BEGIN
--       TYPE NOTATION ::= "SYNTAX" Syntax
--       VALUE NOTATION ::= "VALUE" Value
--   END
-- It was replaced by the plain OBJECT-TYPE definitions below.

commentedScalar OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Scalar that replaced the retired macro."
    ::= { commentedMacroMIB 1 }

END
"""

MIXED_MACRO_V2 = """
MIXED-MACRO-MIB DEFINITIONS ::= BEGIN

IMPORTS
    INTEGER, OCTET STRING, OBJECT IDENTIFIER
        FROM SNMPv2-SMI;

-- Retired macro kept for historical reference:
--   RETIRED-MACRO MACRO ::=
--   BEGIN
--       TYPE NOTATION ::= "SYNTAX" Syntax
--   END

SAMPLE-MACRO MACRO ::=
BEGIN
    TYPE NOTATION ::=
        "SYNTAX" Syntax
    VALUE NOTATION ::=
        "VALUE" Value
END

mixedMacroMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "This module defines a real MACRO and also mentions a MACRO in text."
    ::= { 1 5 }

END
"""

V1_COMMENT_MENTIONS_SMIV2 = """
RFC1155-STYLE-MIB DEFINITIONS ::= BEGIN

IMPORTS
    OBJECT-TYPE FROM RFC-1212
    mgmt, NetworkAddress, Counter, Gauge, TimeTicks
        FROM RFC1155-SMI
    ;

-- NOTE: this SMIv1 module predates SNMPv2-SMI; converted from RFC1213.

sysDescr OBJECT-TYPE
    SYNTAX  DisplayString
    ACCESS  read-only
    STATUS  mandatory
    DESCRIPTION "The old-style description mentioning the SNMPv2-SMI migration."
    ::= { 1 3 6 1 2 1 1 1 }

END
"""

# Root SMIv2 module with NO imports at all (like SNMPv2-SMI itself):
# import-based detection has nothing to work with, so the dialect decision
# falls back to SMIv2-only construct keywords. Regression: the v0.4.7
# detection rework initially parsed SNMPv2-SMI with the SMIv1 grammar and
# failed at its first OBJECT-IDENTITY.
ROOT_SMIV2_NO_IMPORTS = """
ROOT-V2-MIB DEFINITIONS ::= BEGIN

zeroDotZero    OBJECT-IDENTITY
    STATUS     current
    DESCRIPTION
        "A root module that imports nothing, like SNMPv2-SMI itself."
    ::= { 0 0 }

rootScalar OBJECT-TYPE
    SYNTAX      INTEGER
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Uses v2-only clauses."
    ::= { zeroDotZero 1 }

END
"""

# Import-less SMIv1 module: the construct fallback must not flip it to v2 —
# not even when a comment mentions an SMIv2-only construct by name
# (comments are masked out before the fallback runs).
ROOT_SMIV1_NO_IMPORTS = """
ROOT-V1-MIB DEFINITIONS ::= BEGIN

-- TODO: migrate this module to OBJECT-IDENTITY / MODULE-IDENTITY someday.

rootV1 OBJECT-TYPE
    SYNTAX  INTEGER
    ACCESS  read-only
    STATUS  mandatory
    DESCRIPTION "Old-style module that imports nothing."
    ::= { 1 3 6 1 4 1 99999 }

END
"""


# ---------------------------------------------------------------------------
# Tests — quote/comment-aware macro stripping (issue #11)
# ---------------------------------------------------------------------------


class TestMacroStripping:
    def test_macro_word_inside_description_string_parses(self):
        """A DESCRIPTION containing the word MACRO must not swallow the module."""
        mib = SmiParser().parse(DESCRIPTION_WITH_MACRO_V2)

        assert mib.name == "MACRO-WORD-MIB"
        # Description preserved verbatim — the stripper must not have touched it.
        assert mib.objects["macroWordMIB"].description == (
            "This module does not define a new MACRO here, honest."
        )
        assert mib.objects["macroWordScalar"].description == (
            "Another sentence that mentions a MACRO in passing."
        )
        assert mib.objects["macroWordScalar"].oid != ""

    def test_commented_out_macro_block_parses_without_bare_end(self):
        """A commented-out MACRO block must not inject a bare END token."""
        mib = SmiParser().parse(COMMENTED_OUT_MACRO_V2)

        assert mib.name == "COMMENTED-MACRO-MIB"
        assert mib.objects["commentedScalar"].object_type == "OBJECT-TYPE"
        assert mib.objects["commentedScalar"].description == (
            "Scalar that replaced the retired macro."
        )

    def test_genuine_macro_block_coexists_with_string_and_comment_macros(self):
        """A real macro block is still stripped while fake ones are ignored."""
        mib = SmiParser().parse(MIXED_MACRO_V2)

        assert mib.name == "MIXED-MACRO-MIB"
        assert mib.objects["mixedMacroMIB"].object_type == "MODULE-IDENTITY"
        assert mib.objects["mixedMacroMIB"].description == (
            "This module defines a real MACRO and also mentions a MACRO in text."
        )


# ---------------------------------------------------------------------------
# Tests — imports-driven dialect detection (issue #24)
# ---------------------------------------------------------------------------


class TestDialectDetection:
    def test_smiv1_module_mentioning_smiv2_in_comment_stays_smiv1(self):
        """A comment mentioning SNMPv2-SMI must not flip detection to SMIv2."""
        assert _detect_dialect(V1_COMMENT_MENTIONS_SMIV2) == "smiv1"

        mib = SmiParser().parse(V1_COMMENT_MENTIONS_SMIV2)

        assert mib.name == "RFC1155-STYLE-MIB"
        assert mib.language == "SMIv1"
        # Language recorded by the transformer agrees with the detected dialect:
        # neither comment nor DESCRIPTION leaks SNMPv2-SMI into the imports.
        assert "SNMPv2-SMI" not in mib.imports

    def test_smiv2_module_importing_marker_still_detected_smiv2(self):
        """An IMPORTS FROM SNMPv2-SMI is still recognised as SMIv2."""
        mib = SmiParser().parse(DESCRIPTION_WITH_MACRO_V2)

        assert _detect_dialect(DESCRIPTION_WITH_MACRO_V2) == "smiv2"
        assert mib.language == "SMIv2"
        assert "SNMPv2-SMI" in mib.imports

    def test_root_smiv2_module_without_imports_detected_smiv2(self):
        """Root SMIv2 modules (like SNMPv2-SMI) import nothing, so import-
        based detection has nothing to work with; the construct-keyword
        fallback must classify them as SMIv2 (regression caught by the
        v0.4.7 corpus compile: SNMPv2-SMI parsed as smiv1 failed at its
        first OBJECT-IDENTITY)."""
        assert _detect_dialect(ROOT_SMIV2_NO_IMPORTS) == "smiv2"

        mib = SmiParser().parse(ROOT_SMIV2_NO_IMPORTS)
        assert mib.name == "ROOT-V2-MIB"
        assert mib.objects["zeroDotZero"].object_type == "OBJECT-IDENTITY"

    def test_importless_smiv1_module_stays_smiv1(self):
        """An import-less SMIv1 module must not be flipped to SMIv2 by the
        construct fallback — not even when a comment mentions an
        SMIv2-only construct by name."""
        assert _detect_dialect(ROOT_SMIV1_NO_IMPORTS) == "smiv1"

        mib = SmiParser().parse(ROOT_SMIV1_NO_IMPORTS)
        assert mib.name == "ROOT-V1-MIB"
        assert mib.objects["rootV1"].object_type == "OBJECT-TYPE"
        assert mib.objects["rootV1"].description == ("Old-style module that imports nothing.")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
