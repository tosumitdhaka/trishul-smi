"""Focused tests for optional JSON bundle sidecars."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import MockReader
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.errors import WriterError
from trishul_smi.output.json_bundle import MANIFEST_FILENAME
from trishul_smi.output.json_fmt import JsonFormatter

ONE_MIB = """
ONE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
oneMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "One Org"
    CONTACT-INFO "one@example.com"
    DESCRIPTION  "First module."
    ::= { 1 201 }
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
    ::= { 1 202 }
END
"""

BAD_MIB = """
BAD-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
badMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Bad Org"
    CONTACT-INFO "bad@example.com"
    DESCRIPTION  "Bad module."
    ::= { 1 203 }
END
"""


class TestManifestEmission:
    @pytest.mark.asyncio
    async def test_manifest_not_emitted_by_default(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"ONE-MIB": ONE_MIB}))

        await compiler.compile("ONE-MIB")

        assert not (tmp_path / MANIFEST_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_manifest_emitted_when_requested_and_sorted(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_manifest=True,
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"ONE-MIB": ONE_MIB, "TWO-MIB": TWO_MIB})
        )

        await compiler.compile("TWO-MIB", "ONE-MIB")

        manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_bytes())
        assert [entry["module"] for entry in manifest["modules"]] == ["ONE-MIB", "TWO-MIB"]
        assert [entry["file"] for entry in manifest["modules"]] == ["ONE-MIB.json", "TWO-MIB.json"]

    @pytest.mark.asyncio
    async def test_manifest_uses_same_metadata_as_module_json(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_manifest=True,
        )
        compiler = MibCompiler(config).add_reader(MockReader({"ONE-MIB": ONE_MIB}))

        await compiler.compile("ONE-MIB")

        module_json = json.loads((tmp_path / "ONE-MIB.json").read_bytes())
        manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_bytes())
        assert manifest["schema_version"] == module_json["schema_version"]
        assert manifest["producer_version"] == module_json["producer_version"]
        assert manifest["generated_by"] == module_json["generated_by"]
        assert manifest["generated_at"] == module_json["generated_at"]

    @pytest.mark.asyncio
    async def test_manifest_lists_only_successfully_emitted_json_modules(self, tmp_path: Path):
        class FailingJsonFormatter(JsonFormatter):
            def format(self, module):  # noqa: ANN001
                if module.name == "BAD-MIB":
                    raise RuntimeError("simulated json failure")
                return super().format(module)

        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_manifest=True,
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"ONE-MIB": ONE_MIB, "BAD-MIB": BAD_MIB})
        )
        compiler._formatters = {"json": FailingJsonFormatter()}

        results = await compiler.compile("ONE-MIB", "BAD-MIB")

        manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_bytes())
        assert manifest["modules"] == [{"module": "ONE-MIB", "file": "ONE-MIB.json"}]
        bad_result = next(result for result in results if result.name == "BAD-MIB")
        assert bad_result.status == "compiled"
        assert bad_result.output_paths == []
        assert bad_result.warnings

    @pytest.mark.asyncio
    async def test_manifest_omitted_when_no_json_modules_emitted(self, tmp_path: Path):
        class FailingJsonFormatter(JsonFormatter):
            def format(self, module):  # noqa: ANN001
                raise RuntimeError(f"simulated json failure for {module.name}")

        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_manifest=True,
        )
        compiler = MibCompiler(config).add_reader(MockReader({"BAD-MIB": BAD_MIB}))
        compiler._formatters = {"json": FailingJsonFormatter()}

        await compiler.compile("BAD-MIB")

        assert not (tmp_path / MANIFEST_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_manifest_write_error_raises_writer_error(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path,
            formats=["json"],
            cache_dir=None,
            emit_manifest=True,
        )
        compiler = MibCompiler(config).add_reader(MockReader({"ONE-MIB": ONE_MIB}))
        original_write_bytes = Path.write_bytes

        def _write_bytes(path: Path, data: bytes) -> int:
            if path.name == MANIFEST_FILENAME:
                raise OSError("disk full")
            return original_write_bytes(path, data)

        with patch("pathlib.Path.write_bytes", autospec=True, side_effect=_write_bytes):
            with pytest.raises(WriterError, match="bundle manifest"):
                await compiler.compile("ONE-MIB")
