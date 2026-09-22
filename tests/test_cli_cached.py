"""CLI rendering of the 'cached' status (issue #15).

``cached`` is a success state: the results table shows it and the exit code
stays 0 — only ``failed``/``missing`` results force exit 1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import typer
from click.testing import CliRunner

from trishul_smi.cli.main import app
from trishul_smi.models import CompileResult

_cmd = typer.main.get_command(app)
runner = CliRunner()


def _cached(name: str = "IF-MIB") -> CompileResult:
    return CompileResult(name=name, status="cached", output_paths=[Path(f"{name}.json")])


class TestCachedStatusDisplay:
    def test_cached_module_shown_in_table_exit_zero(self):
        with patch(
            "trishul_smi.cli.main._compile_async",
            new=AsyncMock(return_value=[_cached()]),
        ):
            result = runner.invoke(_cmd, ["compile", "IF-MIB", "--online"])
        assert result.exit_code == 0
        assert "IF-MIB" in result.output

    def test_cached_summary_count(self):
        with patch(
            "trishul_smi.cli.main._compile_async",
            new=AsyncMock(return_value=[_cached(), _cached("IP-MIB")]),
        ):
            result = runner.invoke(_cmd, ["compile", "IF-MIB", "IP-MIB", "--online"])
        assert result.exit_code == 0
        assert "2 cached" in result.output

    def test_cached_and_compiled_both_counted(self):
        results = [_cached("IF-MIB"), CompileResult(name="IP-MIB", status="compiled")]
        with patch(
            "trishul_smi.cli.main._compile_async",
            new=AsyncMock(return_value=results),
        ):
            result = runner.invoke(_cmd, ["compile", "IF-MIB", "IP-MIB", "--online"])
        assert result.exit_code == 0
        assert "1 cached" in result.output
        assert "1 compiled" in result.output

    def test_cached_is_not_exit_one(self):
        """A cached result alongside a compiled one must not trip exit 1."""
        results = [_cached(), CompileResult(name="IP-MIB", status="compiled")]
        with patch(
            "trishul_smi.cli.main._compile_async",
            new=AsyncMock(return_value=results),
        ):
            result = runner.invoke(_cmd, ["compile", "IF-MIB", "IP-MIB", "--online"])
        assert result.exit_code == 0

    def test_missing_alongside_cached_still_exits_one(self):
        """The missing/failed exit-1 contract is unchanged by cached."""
        results = [
            _cached(),
            CompileResult(name="GHOST", status="missing", error="not found"),
        ]
        with patch(
            "trishul_smi.cli.main._compile_async",
            new=AsyncMock(return_value=results),
        ):
            result = runner.invoke(_cmd, ["compile", "IF-MIB", "GHOST", "--online"])
        assert result.exit_code == 1
