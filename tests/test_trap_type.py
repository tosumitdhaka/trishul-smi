"""Tests for SMIv1 TRAP-TYPE handling (issue #13).

TRAP-TYPE definitions must retain their ENTERPRISE reference, DESCRIPTION and
VARIABLES list in the compiled output, and resolve to a full OID
(enterprise chain + trap number) the same way other objects do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers import MockReader
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.resolver.oid_resolver import resolve_oids

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Representative SMIv1 module (RFC 1155/1212/1215 style, no IMPORTS):
# - a symbolic ENTERPRISE referencing an OBJECT-TYPE OID in the same module,
# - a VARIABLES list,
# - a DESCRIPTION,
# - a trap number (last subidentifier).
TRAP_V1_MODULE = """
TRAP-MIB DEFINITIONS ::= BEGIN

exampleTrapEnterprise OBJECT-TYPE
    SYNTAX      OBJECT IDENTIFIER
    ACCESS      not-accessible
    STATUS      mandatory
    DESCRIPTION "Enterprise root."
    ::= { iso 3 6 1 4 1 9999 }

trapSource OBJECT-TYPE
    SYNTAX      OCTET STRING
    ACCESS      read-only
    STATUS      mandatory
    DESCRIPTION "Trap variable source."
    ::= { exampleTrapEnterprise 1 }

sampleTrap TRAP-TYPE
    ENTERPRISE  exampleTrapEnterprise
    VARIABLES   { trapSource }
    DESCRIPTION "A sample trap."
    ::= 3

END
"""

# TRAP-TYPE whose ENTERPRISE is an imported symbol from another module —
# the defining module is not part of the compiled set, so the OID cannot be
# resolved but the reference must still be recorded.
EXTERNAL_ENTERPRISE_TRAP_MODULE = """
EXT-TRAP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    someEnterprise FROM ENTERPRISE-MIB ;
extTrap TRAP-TYPE
    ENTERPRISE  someEnterprise
    DESCRIPTION "External enterprise trap."
    ::= 7
END
"""

# TRAP-TYPE whose ENTERPRISE is a numeric value (no symbolic chain to walk).
NUMERIC_ENTERPRISE_TRAP_MODULE = """
NUM-TRAP-MIB DEFINITIONS ::= BEGIN
numTrap TRAP-TYPE
    ENTERPRISE  9999
    DESCRIPTION "Numeric enterprise trap."
    ::= 5
END
"""

# Same numeric ENTERPRISE, but the module is detected as SMIv2 (imports FROM
# SNMPv2-SMI). The grammar fix must apply to both dialects (review M1).
NUMERIC_ENTERPRISE_V2_MODULE = """
NUM2-TRAP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
num2Trap TRAP-TYPE
    ENTERPRISE  9999
    DESCRIPTION "Numeric enterprise trap, SMIv2 dialect."
    ::= 5
END
"""


def _compile_json(mib_text: str) -> dict:
    """Parse a module, resolve OIDs, and return its JSON payload."""
    module = SmiParser().parse(mib_text)
    resolve_oids([module])
    return json.loads(JsonFormatter().format(module))


# ---------------------------------------------------------------------------
# 1. Symbolic ENTERPRISE in the same module
# ---------------------------------------------------------------------------


class TestSymbolicEnterprise:
    def test_trap_in_objects_with_trap_type(self):
        data = _compile_json(TRAP_V1_MODULE)
        assert data["language"] == "SMIv1"
        trap = data["objects"]["sampleTrap"]
        assert trap["object_type"] == "TRAP-TYPE"
        assert trap["class"] == "traptype"

    def test_enterprise_reference_recorded(self):
        data = _compile_json(TRAP_V1_MODULE)
        trap = data["objects"]["sampleTrap"]
        assert trap["enterprise"] == {
            "module": "TRAP-MIB",
            "object": "exampleTrapEnterprise",
        }

    def test_resolved_full_oid(self):
        """Full OID = enterprise chain + trap number: 1.3.6.1.4.1.9999.3."""
        data = _compile_json(TRAP_V1_MODULE)
        trap = data["objects"]["sampleTrap"]
        assert trap["oid"] == "1.3.6.1.4.1.9999.3"
        assert trap["oid_path"] == [1, 3, 6, 1, 4, 1, 9999, 3]
        assert trap["trap_number"] == 3

    def test_description_recorded(self):
        data = _compile_json(TRAP_V1_MODULE)
        trap = data["objects"]["sampleTrap"]
        assert trap["description"] == "A sample trap."

    def test_variables_recorded_as_members(self):
        """VARIABLES maps to the same members shape NOTIFICATION-TYPE uses."""
        data = _compile_json(TRAP_V1_MODULE)
        trap = data["objects"]["sampleTrap"]
        assert trap["members"] == [{"module": "TRAP-MIB", "object": "trapSource"}]

    def test_enterprise_object_still_resolves(self):
        """The referenced OBJECT-TYPE must not be disturbed by trap handling."""
        data = _compile_json(TRAP_V1_MODULE)
        assert data["objects"]["exampleTrapEnterprise"]["oid"] == "1.3.6.1.4.1.9999"


# ---------------------------------------------------------------------------
# 2. Externally-defined / numeric ENTERPRISE
# ---------------------------------------------------------------------------


class TestExternalEnterprise:
    def test_imported_symbol_recorded_as_module_object_ref(self):
        data = _compile_json(EXTERNAL_ENTERPRISE_TRAP_MODULE)
        trap = data["objects"]["extTrap"]
        assert trap["object_type"] == "TRAP-TYPE"
        # Imported symbol attributed to its defining module via the reverse map.
        assert trap["enterprise"] == {
            "module": "ENTERPRISE-MIB",
            "object": "someEnterprise",
        }
        assert trap["trap_number"] == 7
        assert trap["description"] == "External enterprise trap."

    def test_unresolvable_enterprise_omits_oid_but_keeps_reference(self):
        """Defining module not in the compiled set → no full OID, but the
        enterprise reference must still be present in the output."""
        data = _compile_json(EXTERNAL_ENTERPRISE_TRAP_MODULE)
        trap = data["objects"]["extTrap"]
        assert "oid" not in trap
        assert trap["enterprise"] == {
            "module": "ENTERPRISE-MIB",
            "object": "someEnterprise",
        }

    def test_numeric_enterprise_recorded(self):
        """A bare numeric ENTERPRISE has no owning module or resolvable chain;
        the value is still recorded so the trap is not silently lost."""
        data = _compile_json(NUMERIC_ENTERPRISE_TRAP_MODULE)
        trap = data["objects"]["numTrap"]
        assert trap["enterprise"] == {"module": None, "object": "9999"}
        assert trap["trap_number"] == 5
        assert "oid" not in trap

    def test_numeric_enterprise_under_smiv2_dialect(self):
        """The numeric ENTERPRISE grammar fix applies to the SMIv2 dialect
        too — the trap_type_assignment compat rule exists in both grammars,
        so the ENTERPRISE token rule must match in both (review M1)."""
        data = _compile_json(NUMERIC_ENTERPRISE_V2_MODULE)
        assert data["language"] == "SMIv2"
        trap = data["objects"]["num2Trap"]
        assert trap["enterprise"] == {"module": None, "object": "9999"}
        assert trap["trap_number"] == 5


# ---------------------------------------------------------------------------
# 3. Cache round-trip
# ---------------------------------------------------------------------------


class TestCacheRoundTrip:
    @pytest.mark.asyncio
    async def test_cache_hit_round_trips_serialised_fields(self, tmp_path: Path):
        """All TRAP-TYPE fields survive a cache-hit compile (resolver/cache.py
        `_obj`/`_module_from_dict` carry enterprise/trap_number too)."""
        cache_dir = tmp_path / "cache"
        out_dir = tmp_path / "out"
        config = CompilerConfig(
            output_dir=out_dir, formats=["json"], cache_dir=cache_dir, cache_ttl_days=0
        )
        reader = MockReader({"TRAP-MIB": TRAP_V1_MODULE})

        compiler = MibCompiler(config).add_reader(reader)
        await compiler.compile("TRAP-MIB")
        fresh = json.loads((out_dir / "TRAP-MIB.json").read_bytes())["objects"]["sampleTrap"]

        # Second run: same cache dir → cache hit, no fetch/parse.
        compiler2 = MibCompiler(config).add_reader(reader)
        await compiler2.compile("TRAP-MIB")
        cached = json.loads((out_dir / "TRAP-MIB.json").read_bytes())["objects"]["sampleTrap"]

        # oid / oid_path / oid_parent / description / members are serialised,
        # so the cache-hit output preserves the resolved OID and texts.
        assert cached["oid"] == fresh["oid"] == "1.3.6.1.4.1.9999.3"
        assert cached["description"] == fresh["description"] == "A sample trap."
        assert (
            cached["members"]
            == fresh["members"]
            == [{"module": "TRAP-MIB", "object": "trapSource"}]
        )

        # enterprise / trap_number round-trip through the compiled cache.
        assert cached["enterprise"] == fresh["enterprise"]
        assert cached["trap_number"] == fresh["trap_number"] == 3
