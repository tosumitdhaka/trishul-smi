"""Tests for the ``--reproducible`` determinism guarantee (issue #23 item 8).

With reproducibility enabled, ``generated_at`` is pinned to a fixed epoch, so
two compiles of the same source (and version) emit byte-identical module JSON,
manifest.json, and oid_index.json. Without the flag, ``generated_at`` is the
live clock and must not equal the pinned epoch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from click.testing import CliRunner

from tests.helpers import MockReader
from trishul_smi.cli.main import app
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.output.json_bundle import MANIFEST_FILENAME, OID_INDEX_FILENAME
from trishul_smi.output.json_ir import REPRODUCIBLE_GENERATED_AT, make_json_artifact_metadata

ONE_MIB = """
ONE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
oneMIB MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "One Org"
    CONTACT-INFO "one@example.com"
    DESCRIPTION  "First module."
    ::= { 1 401 }
END
"""

# Compile the Typer app to a Click command once (same approach as test_cli.py).
_cmd = typer.main.get_command(app)

runner = CliRunner()


async def _compile_run(tmp_path: Path, subdir: str, *, reproducible: bool) -> dict[str, bytes]:
    """One full compile of ONE-MIB into ``tmp_path/subdir``; return file bytes."""
    out_dir = tmp_path / subdir
    config = CompilerConfig(
        output_dir=out_dir,
        formats=["json"],
        cache_dir=None,
        emit_manifest=True,
        emit_oid_index=True,
        reproducible=reproducible,
    )
    compiler = MibCompiler(config).add_reader(MockReader({"ONE-MIB": ONE_MIB}))

    results = await compiler.compile("ONE-MIB")
    assert all(result.status == "compiled" for result in results)

    return {path.name: path.read_bytes() for path in out_dir.iterdir() if path.is_file()}


class TestReproducibleCompiles:
    @pytest.mark.asyncio
    async def test_reproducible_runs_are_byte_identical(self, tmp_path: Path):
        first = await _compile_run(tmp_path, "out1", reproducible=True)
        second = await _compile_run(tmp_path, "out2", reproducible=True)

        assert set(first) == {"ONE-MIB.json", MANIFEST_FILENAME, OID_INDEX_FILENAME}
        assert set(first) == set(second)
        for filename, data in first.items():
            assert data == second[filename], f"{filename} differs between runs"

    @pytest.mark.asyncio
    async def test_generated_at_pinned_to_epoch_when_reproducible(self, tmp_path: Path):
        files = await _compile_run(tmp_path, "out", reproducible=True)

        module_json = json.loads(files["ONE-MIB.json"])
        manifest = json.loads(files[MANIFEST_FILENAME])
        oid_index = json.loads(files[OID_INDEX_FILENAME])

        assert module_json["generated_at"] == REPRODUCIBLE_GENERATED_AT
        assert manifest["generated_at"] == REPRODUCIBLE_GENERATED_AT
        assert oid_index["generated_at"] == REPRODUCIBLE_GENERATED_AT

    @pytest.mark.asyncio
    async def test_generated_at_not_pinned_by_default(self, tmp_path: Path):
        files = await _compile_run(tmp_path, "out", reproducible=False)

        module_json = json.loads(files["ONE-MIB.json"])
        # Not pinned to the reproducible epoch (may or may not differ between
        # runs — clock has second resolution; only "not pinned" is guaranteed).
        assert module_json["generated_at"] != REPRODUCIBLE_GENERATED_AT


class TestReproducibleMetadata:
    def test_reproducible_pins_generated_at(self):
        metadata = make_json_artifact_metadata(reproducible=True)
        assert metadata.generated_at == REPRODUCIBLE_GENERATED_AT

    def test_explicit_generated_at_wins_over_reproducible(self):
        metadata = make_json_artifact_metadata(
            reproducible=True,
            generated_at="2026-05-07T00:00:00Z",
        )
        assert metadata.generated_at == "2026-05-07T00:00:00Z"

    def test_config_exposes_reproducible_flag(self):
        assert CompilerConfig().reproducible is False
        assert CompilerConfig(reproducible=True).reproducible is True


class TestCliReproducible:
    def test_help_shows_reproducible_flag(self):
        result = runner.invoke(_cmd, ["compile", "--help"])
        assert result.exit_code == 0
        assert "--reproducible" in result.output

    def test_cli_reproducible_runs_are_byte_identical(self, tmp_path: Path):
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        (mib_dir / "ONE-MIB").write_text(ONE_MIB)

        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        args = [
            "compile",
            "ONE-MIB",
            "--mib-dir",
            str(mib_dir),
            "--cache-dir",
            "",
            "--emit-manifest",
            "--emit-oid-index",
            "--reproducible",
        ]

        first_result = runner.invoke(_cmd, args + ["--output-dir", str(out1)])
        assert first_result.exit_code == 0, first_result.output
        second_result = runner.invoke(_cmd, args + ["--output-dir", str(out2)])
        assert second_result.exit_code == 0, second_result.output

        first = {p.name: p.read_bytes() for p in out1.iterdir() if p.is_file()}
        second = {p.name: p.read_bytes() for p in out2.iterdir() if p.is_file()}
        assert set(first) == {"ONE-MIB.json", MANIFEST_FILENAME, OID_INDEX_FILENAME}
        assert set(first) == set(second)
        for filename, data in first.items():
            assert data == second[filename], f"{filename} differs across CLI runs"
