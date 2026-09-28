"""Contract tests for the JSON bundle compatibility policy (issue #16).

Pins the producer-side contract documented in docs/architecture.md
("Bundle compatibility policy"): every emitted JSON artifact — module JSON,
manifest.json, and oid_index.json — carries a consistent metadata block, and
the documented schema pairing matches the code constant. The single source of
truth is ``JSON_IR_SCHEMA_VERSION`` in trishul_smi/output/json_ir.py; tests
reference the constant rather than re-defining a second copy of the value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers import MockReader
from trishul_smi import __version__
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.output.json_bundle import MANIFEST_FILENAME, OID_INDEX_FILENAME
from trishul_smi.output.json_ir import JSON_IR_SCHEMA_VERSION

ONE_MIB = """
ONE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
oneMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "One Org"
    CONTACT-INFO "one@example.com"
    DESCRIPTION  "First module."
    ::= { 1 101 }
END
"""


class TestBundleCompatibilityContract:
    @pytest.mark.asyncio
    async def test_all_artifacts_declare_consistent_metadata(self, tmp_path: Path):
        """Module JSON, manifest.json, and oid_index.json must share one
        metadata block: the same schema_version, producer_version == package
        version, generated_by, and a common generated_at."""
        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_manifest=True,
            emit_oid_index=True,
        )
        compiler = MibCompiler(config).add_reader(MockReader({"ONE-MIB": ONE_MIB}))

        results = await compiler.compile("ONE-MIB")
        assert all(result.status == "compiled" for result in results)

        module_json = json.loads((tmp_path / "ONE-MIB.json").read_bytes())
        manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_bytes())
        oid_index = json.loads((tmp_path / OID_INDEX_FILENAME).read_bytes())

        for artifact in (module_json, manifest, oid_index):
            assert artifact["schema_version"] == JSON_IR_SCHEMA_VERSION
            assert artifact["producer_version"] == __version__
            assert artifact["generated_by"] == "trishul-smi"
            assert "generated_at" in artifact

        assert module_json["generated_at"] == manifest["generated_at"] == oid_index["generated_at"]

    def test_documented_pairing_matches_code_constant(self):
        """docs/architecture.md documents ``1.1 ↔ trishul-smi >= 0.4.0``.
        Pin the code constant to that documented pairing — the constant is the
        single source of truth for emitted artifacts."""
        assert JSON_IR_SCHEMA_VERSION == "1.1"

    def test_producer_version_equals_package_version(self):
        """Policy (c): producer_version always equals the trishul-smi version."""
        from trishul_smi.version import VERSION, get_producer_version

        assert VERSION == __version__
        assert get_producer_version() == __version__


# MIB exercising the v0.5.2 value-level enrichment (issue #35): an enum
# constraint, a UNITS clause, a range constraint, and a plain object.
VALUE_METADATA_MIB = """
VALUE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Gauge32, Integer32 FROM SNMPv2-SMI ;
valueMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "One Org"
    CONTACT-INFO "one@example.com"
    DESCRIPTION  "Value metadata."
    ::= { 1 102 }
ifOperStatus OBJECT-TYPE
    SYNTAX  INTEGER { up(1), down(2), testing(3) }
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Status."
    ::= { valueMIB 1 }
ifSpeed OBJECT-TYPE
    SYNTAX      Gauge32
    UNITS       "bits/second"
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Speed."
    ::= { valueMIB 2 }
ifMtu OBJECT-TYPE
    SYNTAX      Integer32 (64..65535)
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Mtu."
    ::= { valueMIB 3 }
plain OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Plain."
    ::= { valueMIB 4 }
END
"""


class TestJsonIrAdditiveFields:
    """v0.5.2 policy: the new object-level fields are additive and optional.

    ``enums``, ``units``, and ``constraints`` appear only when the source
    carries the data; consumers that predate them must see plain objects
    unchanged. ``schema_version`` stays 1.1 (no breaking IR change).
    """

    @pytest.mark.asyncio
    async def test_enrichment_fields_emitted_when_present(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"VALUE-MIB": VALUE_METADATA_MIB}))

        results = await compiler.compile("VALUE-MIB")
        assert all(result.status == "compiled" for result in results)

        module_json = json.loads((tmp_path / "VALUE-MIB.json").read_bytes())
        assert module_json["schema_version"] == JSON_IR_SCHEMA_VERSION == "1.1"

        oper_status = module_json["objects"]["ifOperStatus"]
        assert oper_status["enums"] == {"up": 1, "down": 2, "testing": 3}
        assert oper_status["constraints"] == {
            "kind": "enum",
            "data": [["up", 1], ["down", 2], ["testing", 3]],
        }
        assert "units" not in oper_status

        speed = module_json["objects"]["ifSpeed"]
        assert speed["units"] == "bits/second"
        assert "enums" not in speed
        assert "constraints" not in speed

        mtu = module_json["objects"]["ifMtu"]
        assert mtu["constraints"] == {"kind": "range", "data": [[64, 65535]]}
        assert "enums" not in mtu
        assert "units" not in mtu

    @pytest.mark.asyncio
    async def test_plain_objects_are_unchanged(self, tmp_path: Path):
        """A source without value metadata must not gain the new keys —
        older consumers keep parsing the JSON as before."""
        config = CompilerConfig(output_dir=tmp_path, formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"VALUE-MIB": VALUE_METADATA_MIB}))

        results = await compiler.compile("VALUE-MIB")
        assert all(result.status == "compiled" for result in results)

        module_json = json.loads((tmp_path / "VALUE-MIB.json").read_bytes())
        plain = module_json["objects"]["plain"]
        for key in ("enums", "units", "constraints"):
            assert key not in plain
