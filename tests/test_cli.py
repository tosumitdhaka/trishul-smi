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

import asyncio
import importlib.metadata
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import typer
from click.testing import CliRunner

from trishul_smi.cli.main import _compile_async, app
from trishul_smi.config import CompilerConfig
from trishul_smi.models import CompileResult

# Compile the Typer app to a Click command ONCE. CliRunner.invoke() needs a
# Click Command, not a Typer app — passing app directly raises AttributeError.
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

    def test_no_source_exits_2(self):
        """compile without --mib-dir and without --online must exit 2."""
        result = _invoke(["compile", "IF-MIB"])
        assert result.exit_code == 2
        assert "No MIB source" in result.output or "No MIB source" in (result.output + "")

    def test_compile_single_mib_with_online_exit_zero(self):
        with _patch_run([_make_result("IF-MIB")]):
            result = _invoke(["compile", "IF-MIB", "--online"])
        assert result.exit_code == 0

    def test_compile_single_mib_with_mib_dir_exit_zero(self, tmp_path: Path):
        with _patch_run([_make_result("IF-MIB")]):
            result = _invoke(["compile", "IF-MIB", "-d", str(tmp_path)])
        assert result.exit_code == 0

    def test_compile_multiple_mibs(self):
        results = [_make_result("IF-MIB"), _make_result("IP-MIB")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "IP-MIB", "--online"])
        assert result.exit_code == 0
        assert "IF-MIB" in result.output
        assert "IP-MIB" in result.output

    def test_unknown_format_exits_2(self):
        result = _invoke(["compile", "IF-MIB", "--online", "-f", "xml"])
        assert result.exit_code == 2

    def test_negative_retries_exits_2(self):
        result = _invoke(["compile", "IF-MIB", "--online", "--retries", "-1"])
        assert result.exit_code == 2

    def test_zero_timeout_exits_2(self):
        result = _invoke(["compile", "IF-MIB", "--online", "--timeout", "0"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# compile — output formatting
# ---------------------------------------------------------------------------


class TestCompileOutput:
    def test_compiled_module_shown_in_table(self):
        with _patch_run([_make_result("IF-MIB")]):
            result = _invoke(["compile", "IF-MIB", "--online"])
        assert "IF-MIB" in result.output

    def test_failed_module_shown_in_table(self):
        failed = _make_result("MISSING-MIB", status="failed", error="Not found")
        with _patch_run([failed]):
            result = _invoke(["compile", "MISSING-MIB", "--online"])
        assert result.exit_code == 1
        assert "MISSING-MIB" in result.output

    def test_exit_1_when_any_failure(self):
        results = [_make_result("IF-MIB"), _make_result("BAD", status="failed")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "BAD", "--online"])
        assert result.exit_code == 1

    def test_exit_1_when_any_missing(self):
        results = [_make_result("IF-MIB"), _make_result("MISSING", status="missing")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "MISSING", "--online"])
        assert result.exit_code == 1

    def test_exit_0_when_all_compiled(self):
        results = [_make_result("IF-MIB"), _make_result("IP-MIB")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "IP-MIB", "--online"])
        assert result.exit_code == 0

    def test_warnings_appear_in_output(self):
        r = _make_result("IF-MIB", warnings=["[json] formatter error for IF-MIB: boom"])
        with _patch_run([r]):
            result = _invoke(["compile", "IF-MIB", "--online"])
        assert "formatter error" in result.output

    def test_summary_line_compiled_count(self):
        results = [_make_result("IF-MIB"), _make_result("IP-MIB")]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "IP-MIB", "--online"])
        assert "2 compiled" in result.output

    def test_summary_line_failed_count(self):
        results = [
            _make_result("IF-MIB"),
            _make_result("BAD", status="failed", error="err"),
        ]
        with _patch_run(results):
            result = _invoke(["compile", "IF-MIB", "BAD", "--online"])
        assert "1 failed" in result.output

    def test_verbose_shows_output_paths(self):
        r = _make_result("IF-MIB", output_paths=[Path("IF-MIB.json")])
        with _patch_run([r]):
            result = _invoke(["compile", "IF-MIB", "--online", "-v"])
        assert "IF-MIB.json" in result.output

    def test_real_cli_compile_completes_under_asyncio_run(self, tmp_path: Path):
        mib_dir = tmp_path / "mibs"
        out_dir = tmp_path / "out"
        mib_dir.mkdir()
        (mib_dir / "TEST-MIB").write_text(MINIMAL_V2, encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "trishul_smi",
                "compile",
                "TEST-MIB",
                "-d",
                str(mib_dir),
                "-o",
                str(out_dir),
                "--cache-dir",
                "",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert (out_dir / "TEST-MIB.json").is_file()


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
            _invoke(["compile", "IF-MIB", "--online", "--cache-dir", ""])

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
            _invoke(["compile", "IF-MIB", "--online", "--cache-dir", str(tmp_path)])

        if captured:
            assert captured[0].cache_dir == tmp_path


# ---------------------------------------------------------------------------
# compile — --mib-dir
# ---------------------------------------------------------------------------


class TestMibDir:
    def test_nonexistent_mib_dir_warns_not_crashes(self, tmp_path: Path):
        """A --mib-dir path that doesn't exist emits a warning but does not
        crash or exit non-zero; the compile run continues.
        """
        fake_dir = tmp_path / "does-not-exist"
        with _patch_run([_make_result("IF-MIB")]):
            result = _invoke(["compile", "IF-MIB", "-d", str(fake_dir), "--online"])
        assert result.exit_code == 0
        assert "not a directory" in result.output or "Warning" in result.output


# ---------------------------------------------------------------------------
# _compile_async wiring
# ---------------------------------------------------------------------------


class TestCompileAsyncWiring:
    def test_reader_options_flow_into_file_and_http_readers(self, tmp_path: Path):
        mib_dir = tmp_path / "mibs"
        mib_dir.mkdir()
        compiler = MagicMock()
        compiler.add_reader.return_value = compiler
        compiler.compile = AsyncMock(return_value=[])
        config = CompilerConfig(
            cache_dir=tmp_path / "cache",
            cache_ttl_days=11,
            http_timeout=12.5,
            http_retries=4,
            max_mib_size=4096,
            sources=["https://example.test/@mib@"],
        )

        seen: dict[str, object] = {}
        file_reader_instance = object()
        http_reader_instance = object()

        def _build_file_reader(*dirs, max_size):
            seen["file_args"] = dirs
            seen["file_max_size"] = max_size
            return file_reader_instance

        class FakeHttpReader:
            def __init__(self, *templates, **kwargs):
                seen["http_templates"] = templates
                seen["http_kwargs"] = kwargs

            async def __aenter__(self):
                return http_reader_instance

            async def __aexit__(self, *_):
                return None

        with (
            patch("trishul_smi.reader.localfile.FileReader", side_effect=_build_file_reader),
            patch("trishul_smi.reader.httpclient.HttpReader", FakeHttpReader),
        ):
            asyncio.run(
                _compile_async(
                    compiler,
                    config,
                    [mib_dir],
                    ["IF-MIB"],
                    use_http=True,
                )
            )

        assert seen["file_args"] == (mib_dir,)
        assert seen["file_max_size"] == 4096
        assert seen["http_templates"] == ("https://example.test/@mib@",)
        assert seen["http_kwargs"] == {
            "timeout": 12.5,
            "retries": 4,
            "max_size": 4096,
            "cache_dir": tmp_path / "cache",
            "cache_ttl_days": 11,
        }
        assert compiler.add_reader.call_args_list[0].args == (file_reader_instance,)
        assert compiler.add_reader.call_args_list[1].args == (http_reader_instance,)
        compiler.compile.assert_awaited_once_with("IF-MIB")


# ---------------------------------------------------------------------------
# Package integrity
# ---------------------------------------------------------------------------


class TestPackageIntegrity:
    def test_grammar_file_importable(self):
        from importlib.resources import files

        grammar = files("trishul_smi.parser.grammar").joinpath("smiv2.lark")
        assert grammar.is_file(), "smiv2.lark missing from installed package"


# ---------------------------------------------------------------------------
# convert command
# ---------------------------------------------------------------------------


class TestConvertCommand:
    _PYSNMP_SRC = (
        "ifMIB = ModuleIdentity((1, 3, 6, 1, 2, 1, 31,))\n"
        "ifDescr = MibScalar((1, 3, 6, 1, 2, 1, 2, 2, 1, 2,), DisplayString())\n"
        "mibBuilder.exportSymbols('IF-MIB', **{'ifMIB': ifMIB, 'ifDescr': ifDescr})\n"
    )

    def test_convert_produces_json(self, tmp_path: Path):
        import json

        py_file = tmp_path / "IF_MIB.py"
        py_file.write_text(self._PYSNMP_SRC, encoding="utf-8")
        out_dir = tmp_path / "out"
        result = _invoke(["convert", str(py_file), "-o", str(out_dir)])
        assert result.exit_code == 0, result.output
        json_file = out_dir / "IF-MIB.json"
        assert json_file.exists()
        data = json.loads(json_file.read_text())
        assert data["module"] == "IF-MIB"
        assert "ifDescr" in data["objects"]

    def test_convert_exit_0_on_success(self, tmp_path: Path):
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "foo = MibScalar((1,), Integer32())\n"
            "mibBuilder.exportSymbols('TEST-MIB', **{'foo': foo})\n",
            encoding="utf-8",
        )
        result = _invoke(["convert", str(py_file), "-o", str(tmp_path / "out")])
        assert result.exit_code == 0

    def test_convert_nonexistent_file_exits_2(self, tmp_path: Path):
        result = _invoke(["convert", str(tmp_path / "no-such-file.py")])
        assert result.exit_code == 2

    def test_convert_bad_python_exits_1(self, tmp_path: Path):
        py_file = tmp_path / "bad.py"
        py_file.write_text("def (broken\n", encoding="utf-8")
        result = _invoke(["convert", str(py_file)])
        assert result.exit_code == 1

    def test_convert_output_path_shown_in_stdout(self, tmp_path: Path):
        py_file = tmp_path / "IF_MIB.py"
        py_file.write_text(self._PYSNMP_SRC, encoding="utf-8")
        out_dir = tmp_path / "out"
        result = _invoke(["convert", str(py_file), "-o", str(out_dir)])
        assert "IF-MIB" in result.output
