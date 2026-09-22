"""Deprecation tests for the pysnmp `.py` output format (issue #24).

The pysnmp output format is formally deprecated but not removed: constructing
``PysnmpFormatter`` emits a ``DeprecationWarning``, and the CLI prints a
one-line notice when ``pysnmp`` is among the selected formats. Output is still
produced — deprecation, not removal — and removal is targeted at v0.5.0.
``tsmi convert`` (reading existing pysnmp `.py` files) is NOT deprecated.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
import typer
from click.testing import CliRunner

from trishul_smi.cli.main import app
from trishul_smi.output.pysnmp_fmt import PysnmpFormatter

# Compile the Typer app to a Click command ONCE (see tests/test_cli.py).
_cmd = typer.main.get_command(app)
runner = CliRunner()

MINIMAL_V2 = """
TEST-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI ;

testMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "A minimal test MIB."
    ::= { 1 3 }

END
"""


class TestPysnmpFormatterConstructionWarning:
    def test_construction_emits_deprecation_warning(self):
        with pytest.warns(DeprecationWarning, match="pysnmp .py output"):
            PysnmpFormatter()

    def test_warning_names_replacement_and_removal_target(self):
        with pytest.warns(DeprecationWarning) as record:
            PysnmpFormatter()
        msg = str(record[0].message)
        # Replacement: JSON bundle output.
        assert "--format json" in msg
        assert "--emit-manifest" in msg
        assert "--emit-oid-index" in msg
        # Removal target.
        assert "v0.5.0" in msg

    def test_warning_stacklevel_points_at_caller(self):
        # stacklevel=2: the warning is attributed to the caller's frame, not to
        # PysnmpFormatter.__init__ itself.
        with pytest.warns(DeprecationWarning) as record:
            PysnmpFormatter()
        assert record[0].filename == __file__


class TestCliPysnmpDeprecationNotice:
    def _write_minimal_mib(self, tmp_path: Path) -> tuple[Path, Path]:
        mib_dir = tmp_path / "mibs"
        out_dir = tmp_path / "out"
        mib_dir.mkdir()
        (mib_dir / "TEST-MIB").write_text(MINIMAL_V2, encoding="utf-8")
        return mib_dir, out_dir

    def _invoke(self, args):
        return runner.invoke(_cmd, args)

    def test_format_pysnmp_shows_notice_and_still_writes_output(self, tmp_path: Path):
        """--format pysnmp prints a deprecation notice but still produces .py."""
        mib_dir, out_dir = self._write_minimal_mib(tmp_path)

        with warnings.catch_warnings():
            # The PysnmpFormatter DeprecationWarning fires in-process during the
            # compile; keep it out of pytest's warnings summary — the CLI's own
            # visible notice is what we assert here.
            warnings.simplefilter("ignore", DeprecationWarning)
            result = self._invoke(
                [
                    "compile",
                    "TEST-MIB",
                    "-d",
                    str(mib_dir),
                    "-o",
                    str(out_dir),
                    "--cache-dir",
                    "",
                    "-f",
                    "pysnmp",
                ]
            )

        assert result.exit_code == 0, result.output
        assert "DeprecationWarning" in result.output
        assert "v0.5.0" in result.output
        assert "--format json" in result.output
        # Output is still produced — deprecation, not removal.
        assert (out_dir / "TEST-MIB.py").is_file()

    def test_format_json_alone_produces_no_notice(self, tmp_path: Path):
        mib_dir, out_dir = self._write_minimal_mib(tmp_path)

        result = self._invoke(
            [
                "compile",
                "TEST-MIB",
                "-d",
                str(mib_dir),
                "-o",
                str(out_dir),
                "--cache-dir",
                "",
                "-f",
                "json",
            ]
        )

        assert result.exit_code == 0, result.output
        assert "deprecat" not in result.output.lower()
        assert (out_dir / "TEST-MIB.json").is_file()

    def test_convert_command_is_not_deprecated(self, tmp_path: Path):
        """tsmi convert reads pysnmp .py files — untouched by the deprecation."""
        py_file = tmp_path / "IF_MIB.py"
        py_file.write_text(
            "ifMIB = ModuleIdentity((1, 3, 6, 1, 2, 1, 31,))\n"
            "ifDescr = MibScalar((1, 3, 6, 1, 2, 1, 2, 2, 1, 2,), DisplayString())\n"
            "mibBuilder.exportSymbols('IF-MIB', **{'ifMIB': ifMIB, 'ifDescr': ifDescr})\n",
            encoding="utf-8",
        )
        out_dir = tmp_path / "out"

        result = self._invoke(["convert", str(py_file), "-o", str(out_dir)])

        assert result.exit_code == 0, result.output
        assert "deprecat" not in result.output.lower()
        assert (out_dir / "IF-MIB.json").is_file()
