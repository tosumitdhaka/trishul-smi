#!/usr/bin/env python3
"""Run the local release gate for trishul-smi."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import venv
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


class ReleaseGateError(RuntimeError):
    """Raised when a release-gate step fails."""


@dataclass(slots=True)
class StepResult:
    name: str
    status: str
    detail: str
    duration_ms: float


@dataclass(slots=True)
class ReleaseGateReport:
    tool_python: str
    project_version: str
    wheel_path: str | None = None
    wheel_test_venv: str | None = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(step.status == "failed" for step in self.steps)


@dataclass(frozen=True, slots=True)
class Settings:
    tool_python: Path
    project_version: str
    dist_dir: Path
    wheel_test_venv: Path | None
    keep_wheel_test_venv: bool
    smoke_mibs: list[str]
    json_output: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local release gate: ruff, mypy, pytest, coverage, "
            "wheel build, wheel duplicate check, and CLI smoke test."
        )
    )
    parser.add_argument(
        "--python",
        type=Path,
        help=(
            "Python interpreter used for ruff/mypy/pytest/build. "
            "Defaults to .venv/bin/python when present, otherwise the current interpreter."
        ),
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=REPO_ROOT / "dist",
        help="Directory containing built distributions (default: ./dist)",
    )
    parser.add_argument(
        "--wheel-test-venv",
        type=Path,
        help=(
            "Explicit venv path used for wheel smoke tests. "
            "If omitted, a temporary venv is created and cleaned up automatically."
        ),
    )
    parser.add_argument(
        "--keep-wheel-test-venv",
        action="store_true",
        help="Keep the temporary wheel-test venv instead of cleaning it up",
    )
    parser.add_argument(
        "--smoke-mibs",
        nargs="+",
        default=["IF-MIB", "IP-MIB"],
        metavar="MIB",
        help="MIB names to compile in the online smoke test (default: IF-MIB IP-MIB)",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the final release-gate report as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings(
        tool_python=_resolve_tool_python(args.python),
        project_version=_read_project_version(REPO_ROOT / "pyproject.toml"),
        dist_dir=args.dist_dir.expanduser().resolve(),
        wheel_test_venv=None
        if args.wheel_test_venv is None
        else args.wheel_test_venv.expanduser().resolve(),
        keep_wheel_test_venv=args.keep_wheel_test_venv,
        smoke_mibs=args.smoke_mibs,
        json_output=args.json_output,
    )
    report = _run_release_gate(settings)
    if settings.json_output:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(_render_report(report))
    return 1 if report.failed else 0


def _run_release_gate(settings: Settings) -> ReleaseGateReport:
    report = ReleaseGateReport(
        tool_python=str(settings.tool_python),
        project_version=settings.project_version,
    )

    _record_step(
        report,
        "ruff check",
        lambda: _run_checked(
            [str(settings.tool_python), "-m", "ruff", "check", "trishul_smi", "tests", "scripts"]
        ),
    )
    _record_step(
        report,
        "ruff format --check",
        lambda: _run_checked(
            [
                str(settings.tool_python),
                "-m",
                "ruff",
                "format",
                "trishul_smi",
                "tests",
                "scripts",
                "--check",
            ]
        ),
    )
    _record_step(
        report,
        "mypy",
        lambda: _run_checked([str(settings.tool_python), "-m", "mypy", "trishul_smi"]),
    )
    _record_step(
        report,
        "pytest",
        lambda: _run_checked([str(settings.tool_python), "-m", "pytest", "-q"]),
    )
    _record_step(
        report,
        "pytest coverage",
        lambda: _run_checked(
            [
                str(settings.tool_python),
                "-m",
                "pytest",
                "--cov=trishul_smi",
                "--cov-report=term-missing:skip-covered",
                "--cov-fail-under=95",
                "-q",
            ]
        ),
    )
    _record_step(
        report,
        "build distributions",
        lambda: _build_distributions(settings, report),
    )
    _record_step(
        report,
        "wheel duplicate check",
        lambda: _check_wheel_duplicates(report),
    )
    _record_step(
        report,
        "wheel smoke test",
        lambda: _wheel_smoke_test(settings, report),
    )
    return report


def _record_step(report: ReleaseGateReport, name: str, action: Any) -> None:
    started = time.perf_counter_ns()
    try:
        detail = str(action())
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        detail = _format_exception(exc)
    else:
        status = "passed"
    duration_ms = (time.perf_counter_ns() - started) / 1_000_000
    report.steps.append(
        StepResult(name=name, status=status, detail=detail, duration_ms=duration_ms)
    )


def _resolve_tool_python(requested: Path | None) -> Path:
    if requested is not None:
        return _abspath_no_resolve(requested.expanduser())
    venv_python = REPO_ROOT / ".venv" / _bin_dir_name() / _python_name()
    if venv_python.exists():
        return _abspath_no_resolve(venv_python)
    return _abspath_no_resolve(Path(sys.executable))


def _read_project_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, flags=re.MULTILINE)
    if match is None:
        raise ReleaseGateError(f"Could not find version in {pyproject_path}")
    return match.group(1)


def _build_distributions(settings: Settings, report: ReleaseGateReport) -> str:
    _run_checked([str(settings.tool_python), "-m", "build", "-q"])
    wheel_path = settings.dist_dir / f"trishul_smi-{settings.project_version}-py3-none-any.whl"
    if not wheel_path.exists():
        raise ReleaseGateError(f"Built wheel not found at {wheel_path}")
    report.wheel_path = str(wheel_path)
    return f"wheel={wheel_path.name}"


def _check_wheel_duplicates(report: ReleaseGateReport) -> str:
    wheel_path = _require_wheel_path(report)
    with zipfile.ZipFile(wheel_path) as archive:
        duplicates = [name for name, count in Counter(archive.namelist()).items() if count > 1]
    if duplicates:
        raise ReleaseGateError(f"Wheel contains duplicate entries: {duplicates}")
    return "no duplicate entries"


def _wheel_smoke_test(settings: Settings, report: ReleaseGateReport) -> str:
    wheel_path = _require_wheel_path(report)
    wheel_test_venv, created_temp = _prepare_wheel_test_venv(settings.wheel_test_venv)
    wheel_python = wheel_test_venv / _bin_dir_name() / _python_name()
    wheel_tsmi = wheel_test_venv / _bin_dir_name() / _executable_name("trishul-smi")

    keep_venv = settings.keep_wheel_test_venv or not created_temp
    try:
        _create_venv(wheel_test_venv)
        _run_checked([str(wheel_python), "-m", "pip", "install", str(wheel_path), "-q"])

        version_result = _run_checked([str(wheel_tsmi), "version"])
        # CLI outputs "trishul-smi x.y.z" — extract just the version number
        installed_version = version_result.stdout.strip().split()[-1]
        if installed_version != settings.project_version:
            raise ReleaseGateError(
                f"Version mismatch: expected {settings.project_version}, got {installed_version}"
            )

        import_result = _run_checked(
            [
                str(wheel_python),
                "-c",
                (
                    "from trishul_smi import ("
                    "MibCompiler, CompilerConfig, CompileResult, "
                    "FileReader, HttpReader, ZipReader, "
                    "TrishulError, MibNotFoundError"
                    "); print('import ok')"
                ),
            ]
        )
        import_output = import_result.stdout.strip()
        if import_output != "import ok":
            raise ReleaseGateError(f"Unexpected import output: {import_output!r}")

        compile_result = _run_checked(
            [
                str(wheel_tsmi),
                "compile",
                *settings.smoke_mibs,
                "-f",
                "json",
                "-f",
                "pysnmp",
                "--online",
                "--verbose",
            ]
        )
        output = compile_result.stdout + compile_result.stderr
        failed_mibs = [mib for mib in settings.smoke_mibs if f"✅           {mib}" not in output]
        if failed_mibs:
            raise ReleaseGateError(
                f"Smoke compile did not show ✅ for: {failed_mibs}\nOutput:\n{output}"
            )

        report.wheel_test_venv = str(wheel_test_venv)
        smoke_list = ",".join(settings.smoke_mibs)
        return f"trishul-smi version={installed_version}; {import_output}; smoke={smoke_list} ok"
    finally:
        if created_temp and not keep_venv:
            shutil.rmtree(wheel_test_venv, ignore_errors=True)
            report.wheel_test_venv = None


def _prepare_wheel_test_venv(requested: Path | None) -> tuple[Path, bool]:
    if requested is not None:
        return _abspath_no_resolve(requested), False
    return _abspath_no_resolve(Path(tempfile.mkdtemp(prefix="tsmi-release-gate-"))), True


def _create_venv(venv_dir: Path) -> None:
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv.EnvBuilder(with_pip=True).create(venv_dir)


def _require_wheel_path(report: ReleaseGateReport) -> Path:
    if report.wheel_path is None:
        raise ReleaseGateError("Wheel path not available; build step must succeed first")
    return Path(report.wheel_path)


def _run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        env=_subprocess_env(),
    )


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _render_report(report: ReleaseGateReport) -> str:
    lines = [
        "Release gate",
        f"tool_python:     {report.tool_python}",
        f"project_version: {report.project_version}",
    ]
    if report.wheel_path is not None:
        lines.append(f"wheel_path:      {report.wheel_path}")
    if report.wheel_test_venv is not None:
        lines.append(f"wheel_test_venv: {report.wheel_test_venv}")
    lines.append("")
    for step in report.steps:
        marker = "PASS" if step.status == "passed" else "FAIL"
        lines.append(f"[{marker}] {step.name} ({step.duration_ms:.1f} ms)")
        if step.detail:
            lines.append(f"      {step.detail}")
    lines.append("")
    overall = "FAILED" if report.failed else "PASSED"
    lines.append(f"Overall: {overall}")
    return "\n".join(lines)


def _format_exception(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        return _format_command_failure(exc)
    return f"{type(exc).__name__}: {exc}"


def _format_command_failure(exc: subprocess.CalledProcessError) -> str:
    stdout = exc.stdout.strip() if isinstance(exc.stdout, str) else ""
    stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
    detail = f"command failed (exit {exc.returncode}): {' '.join(str(c) for c in exc.cmd)}"
    if stdout:
        detail += f"\n      stdout: {stdout}"
    if stderr:
        detail += f"\n      stderr: {stderr}"
    return detail


def _bin_dir_name() -> str:
    return "Scripts" if os.name == "nt" else "bin"


def _python_name() -> str:
    return "python.exe" if os.name == "nt" else "python"


def _executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _abspath_no_resolve(path: Path) -> Path:
    return Path(os.path.abspath(path))


if __name__ == "__main__":
    raise SystemExit(main())
