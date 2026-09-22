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
