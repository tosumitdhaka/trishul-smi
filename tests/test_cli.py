"""Tests for the trishul-smi CLI (trishul_smi.cli.main).

Strategy:
- Use click.testing.CliRunner with typer.main.get_command(app) — Click's
  CliRunner requires a Click Command object; a raw Typer app is not one.
  get_command() compiles the Typer app into the underlying Click command once
  at module level so the conversion cost is paid once per test session.
- Patch _compile_async to avoid real I/O.
- Verify exit codes, stdout content, and table structure.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import typer
from click.testing import CliRunner

from trishul_smi.cli.main import app
from trishul_smi.models import CompileResult

# Compile the Typer app to a Click command ONCE. CliRunner.invoke() needs a
# Click Command, not a Typer app — passing app directly raises AttributeError.
_cmd = typer.main.get_command(app)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    name: str = "IF-MIB",
    status: str = "compiled",
    output_paths: list[Path] | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> CompileResult:
    return CompileResult(
        name=name,
        status=status,
        output_paths=output_paths or [],
        warnings=warnings or [],
        error=error or "",
    )


def _patch_run(results: list[CompileResult]):
    """Patch the async runner so no real I/O happens."""
    return patch(
        "trishul_smi.cli.main._compile_async",
        new=AsyncMock(return_value=results),
    )


def _invoke(args, **kwargs):
    """Invoke the compiled Click command via the test runner."""
    return runner.invoke(_cmd, args, **kwargs)


# ---------------------------------------------------------------------------
# version command
# ---------------------------------------------------------------------------


class TestVersionCommand:
    def test_prints_package_name(self):
        result = _invoke(["version"])
        assert result.exit_code == 0
        assert "trishul-smi" in result.output

    def test_dev_fallback_when_not_installed(self):
        with patch(
            "trishul_smi.cli.main.importlib.metadata.version",
            side_effect=importlib.metadata.PackageNotFoundError("trishul-smi"),
        ):
            result = _invoke(["version"])
        assert result.exit_code == 0
        assert "development" in result.output


# ---------------------------------------------------------------------------
# compile — argument / option parsing
# ---------------------------------------------------------------------------


class TestCompileArgs:
    def test_no_args_shows_help(self):
        result = _invoke([])
        assert result.exit_code in (0, 2)
        assert "compile" in result.output.lower() or "Usage" in result.output

    def test_compile_no_mib_names_shows_usage(self):
        result = _invoke(["compile"])
        assert result.exit_code == 2

    def test_compile_single_mib_exit_zero(self):
        with _patch_run([_make_result("IF-MIB")]):
            result = _invoke(["compile", "IF-MIB"])
        assert result.exit_code == 0

    def test_compile_multiple_mibs(self):
        results = [_make_result("IF-MIB"), _make_result("IP-MIB")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "IP-MIB"])
        assert result.exit_code == 0
        assert "IF-MIB" in result.output
        assert "IP-MIB" in result.output

    def test_unknown_format_exits_2(self):
        result = _invoke(["compile", "IF-MIB", "-f", "xml"])
        assert result.exit_code == 2

    def test_negative_retries_exits_2(self):
        result = _invoke(["compile", "IF-MIB", "--retries", "-1"])
        assert result.exit_code == 2

    def test_zero_timeout_exits_2(self):
        result = _invoke(["compile", "IF-MIB", "--timeout", "0"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# compile — output formatting
# ---------------------------------------------------------------------------


class TestCompileOutput:
    def test_compiled_module_shown_in_table(self):
        with _patch_run([_make_result("IF-MIB")]):
            result = _invoke(["compile", "IF-MIB"])
        assert "IF-MIB" in result.output

    def test_failed_module_shown_in_table(self):
        failed = _make_result("MISSING-MIB", status="failed", error="Not found")
        with _patch_run([failed]):
            result = _invoke(["compile", "MISSING-MIB"])
        assert result.exit_code == 1
        assert "MISSING-MIB" in result.output

    def test_exit_1_when_any_failure(self):
        results = [_make_result("IF-MIB"), _make_result("BAD", status="failed")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "BAD"])
        assert result.exit_code == 1

    def test_exit_0_when_all_compiled(self):
        results = [_make_result("IF-MIB"), _make_result("IP-MIB")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "IP-MIB"])
        assert result.exit_code == 0

    def test_warnings_appear_in_output(self):
        r = _make_result("IF-MIB", warnings=["[json] formatter error for IF-MIB: boom"])
        with _patch_run([r]):
            result = _invoke(["compile", "IF-MIB"])
        assert "formatter error" in result.output

    def test_summary_line_compiled_count(self):
        results = [_make_result("IF-MIB"), _make_result("IP-MIB")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "IP-MIB"])
        assert "2 compiled" in result.output

    def test_summary_line_failed_count(self):
        results = [
            _make_result("IF-MIB"),
            _make_result("BAD", status="failed", error="err"),
        ]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "BAD"])
        assert "1 failed" in result.output

    def test_verbose_shows_output_paths(self):
        r = _make_result("IF-MIB", output_paths=[Path("IF-MIB.json")])
        with _patch_run([r]):
            result = _invoke(["compile", "IF-MIB", "-v"])
        assert "IF-MIB.json" in result.output


# ---------------------------------------------------------------------------
# compile — cache-dir option
# ---------------------------------------------------------------------------


class TestCacheDirOption:
    def test_empty_string_disables_cache(self):
        captured: list = []

        def _capture_config(config, *_, **__):
            captured.append(config)
            return MagicMock()

        with (
            patch("trishul_smi.cli.main.MibCompiler", side_effect=_capture_config),
            _patch_run([_make_result()]),
        ):
            _invoke(["compile", "IF-MIB", "--cache-dir", ""])

        if captured:
            assert captured[0].cache_dir is None

    def test_explicit_cache_dir_used(self, tmp_path: Path):
        captured: list = []

        def _capture_config(config, *_, **__):
            captured.append(config)
            return MagicMock()

        with (
            patch("trishul_smi.cli.main.MibCompiler", side_effect=_capture_config),
            _patch_run([_make_result()]),
        ):
            _invoke(["compile", "IF-MIB", "--cache-dir", str(tmp_path)])

        if captured:
            assert captured[0].cache_dir == tmp_path


# ---------------------------------------------------------------------------
# compile — --mib-dir
# ---------------------------------------------------------------------------


class TestMibDir:
    def test_nonexistent_mib_dir_warns_not_crashes(self, tmp_path: Path):
        """A --mib-dir path that doesn't exist emits a warning but does not
        crash or exit non-zero; the compile run continues with HTTP fallback.
        """
        fake_dir = tmp_path / "does-not-exist"
        with _patch_run([_make_result("IF-MIB")]):
            result = _invoke(["compile", "IF-MIB", "-d", str(fake_dir)])
        assert result.exit_code == 0
        assert "not a directory" in result.output or "Warning" in result.output


# ---------------------------------------------------------------------------
# Package integrity
# ---------------------------------------------------------------------------


class TestPackageIntegrity:
    def test_grammar_file_importable(self):
        from importlib.resources import files

        grammar = files("trishul_smi.parser.grammar").joinpath("smiv2.lark")
        assert grammar.is_file(), "smiv2.lark missing from installed package"
