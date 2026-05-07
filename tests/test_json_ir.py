"""Focused tests for the v0.4.0 JSON IR foundation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import MockReader
from trishul_smi import __version__
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.models.mib_module import MibModule
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.output.json_ir import (
    JSON_IR_SCHEMA_VERSION,
    JsonArtifactMetadata,
    make_json_artifact_metadata,
)

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

TWO_MIB = """
TWO-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
twoMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Two Org"
    CONTACT-INFO "two@example.com"
    DESCRIPTION  "Second module."
    ::= { 1 102 }
END
"""


class TestJsonFormatterMetadata:
    def test_output_includes_schema_and_producer_version(self):
        data = json.loads(JsonFormatter().format(MibModule(name="IF-MIB", language="SMIv2")))

        assert data["schema_version"] == JSON_IR_SCHEMA_VERSION
        assert data["producer_version"] == __version__
        assert data["generated_by"] == "trishul-smi"
        assert "generated_at" in data

    def test_formatter_uses_supplied_artifact_metadata(self):
        metadata = JsonArtifactMetadata(
            schema_version="99.0",
            producer_version="9.9.9",
            generated_by="custom-producer",
            generated_at="2026-05-07T00:00:00Z",
        )
        data = json.loads(
            JsonFormatter(
                artifact_metadata=metadata,
            ).format(MibModule(name="IF-MIB", language="SMIv2"))
        )

        assert data["schema_version"] == "99.0"
        assert data["producer_version"] == "9.9.9"
        assert data["generated_by"] == "custom-producer"
        assert data["generated_at"] == "2026-05-07T00:00:00Z"


class TestCompilerJsonArtifactMetadata:
    @pytest.mark.asyncio
    async def test_compile_run_shares_json_metadata_across_modules(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(
            MockReader({"ONE-MIB": ONE_MIB, "TWO-MIB": TWO_MIB})
        )

        results = await compiler.compile("ONE-MIB", "TWO-MIB")

        assert all(result.status == "compiled" for result in results)
        one = json.loads((tmp_path / "ONE-MIB.json").read_bytes())
        two = json.loads((tmp_path / "TWO-MIB.json").read_bytes())

        assert one["generated_at"] == two["generated_at"]
        assert one["schema_version"] == two["schema_version"] == JSON_IR_SCHEMA_VERSION
        assert one["producer_version"] == two["producer_version"] == __version__

    @pytest.mark.asyncio
    async def test_reused_compiler_refreshes_json_metadata_each_run(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"ONE-MIB": ONE_MIB}))
        first_metadata = make_json_artifact_metadata(generated_at="2026-05-07T00:00:00Z")
        second_metadata = make_json_artifact_metadata(generated_at="2026-05-07T00:00:01Z")

        with patch(
            "trishul_smi.compiler.make_json_artifact_metadata",
            side_effect=[first_metadata, second_metadata],
        ):
            await compiler.compile("ONE-MIB")
            first = json.loads((tmp_path / "ONE-MIB.json").read_bytes())
            await compiler.compile("ONE-MIB")
            second = json.loads((tmp_path / "ONE-MIB.json").read_bytes())

        assert first["generated_at"] == "2026-05-07T00:00:00Z"
        assert second["generated_at"] == "2026-05-07T00:00:01Z"
