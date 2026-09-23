"""trishul-smi CLI — entry point: trishul_smi.cli.main:app

Commands
--------
trishul-smi compile MIB [MIB ...]   Fetch, parse, and write MIB output files.
trishul-smi lint MIB [MIB ...]      Validate MIBs against the v1 lint check set.
trishul-smi version                 Print the installed package version.

Examples
--------
    # Compile from a local directory:
    trishul-smi compile IF-MIB -d /usr/share/snmp/mibs

    # Compile using HTTP sources (opt-in):
    trishul-smi compile IF-MIB IP-MIB --online

    # Lean output without description text:
    trishul-smi compile IF-MIB -d /usr/share/snmp/mibs --no-texts

    # Custom output dir, no disk cache:
    trishul-smi compile IF-MIB -o ./out --cache-dir "" -d /usr/share/snmp/mibs

    # Lint a MIB from a local directory:
    trishul-smi lint IF-MIB -d /usr/share/snmp/mibs

Exit codes
----------
compile:
    0   All requested MIBs compiled successfully.
    1   One or more MIBs failed to fetch, parse, or format.
    2   Configuration error (bad CLI option value).
lint:
    0   No lint findings and no unresolved modules.
    1   One or more lint findings or unresolved modules.
    2   Configuration error (bad CLI option value).
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig, validate_mib_name
from trishul_smi.lint import format_lint_report_text, lint_report_to_dict, run_lint
from trishul_smi.models import CompileResult

if TYPE_CHECKING:
    from trishul_smi.watch import WatchSummary

app = typer.Typer(
    name="trishul-smi",
    help="Compile SNMP MIB definitions to portable JSON.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

console = Console()
err = Console(stderr=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_cache_dir(raw: str | None) -> Path | None:
    """Convert the --cache-dir CLI string to the Path | None expected by CompilerConfig.

    Rules
    -----
    ''   (empty string)  → None  (cache disabled)
    None (flag not set)  → default XDG-style path under ~/.cache
    anything else        → Path(raw)
    """
    if raw == "":
        return None
    if raw is None:
        return Path.home() / ".cache" / "trishul-smi"
    return Path(raw)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the installed trishul-smi version."""
    try:
        v = importlib.metadata.version("trishul-smi")
    except importlib.metadata.PackageNotFoundError:
        v = "(development — not installed via pip)"
    console.print(f"trishul-smi {v}")


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------


@app.command()
def compile(  # noqa: A001
    mib_names: Annotated[
        list[str] | None,
        typer.Argument(
            help="MIB names to compile (e.g. IF-MIB IP-MIB). "
            "Omit to compile every MIB found in --mib-dir directories."
        ),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for output files."),
    ] = Path("./mibs-output"),
    formats: Annotated[
        list[str] | None,
        typer.Option(
            "--format",
            "-f",
            help="Output format: json (default).",
        ),
    ] = None,
    emit_manifest: Annotated[
        bool,
        typer.Option(
            "--emit-manifest",
            help="Emit optional manifest.json bundle metadata alongside JSON output. "
            "Requires json output.",
        ),
    ] = False,
    emit_oid_index: Annotated[
        bool,
        typer.Option(
            "--emit-oid-index",
            help="Emit optional oid_index.json reverse-lookup metadata alongside JSON "
            "output. Requires json output.",
        ),
    ] = False,
    mib_dirs: Annotated[
        list[Path] | None,
        typer.Option(
            "--mib-dir",
            "-d",
            help="Local directory to search for MIB text files. Repeat for multiple.",
        ),
    ] = None,
    online: Annotated[
        bool,
        typer.Option(
            "--online",
            help="Fetch missing MIBs from HTTP sources (mibs.pysnmp.com + mibbrowser.online). "
            "Off by default — use --mib-dir for local-only operation.",
        ),
    ] = False,
    sources: Annotated[
        list[str] | None,
        typer.Option(
            "--source",
            "-s",
            help="HTTP source URL template (@mib@ replaced with MIB name). "
            "Repeat for multiple. Implies --online; replaces default sources.",
        ),
    ] = None,
    cache_dir: Annotated[
        str | None,
        typer.Option(
            "--cache-dir",
            help="Compiled-module cache directory. Pass empty string to disable.",
        ),
    ] = None,
    cache_ttl_days: Annotated[
        int,
        typer.Option("--cache-ttl-days", help="Cache TTL in days (0 = never expire)."),
    ] = 7,
    max_mib_size: Annotated[
        int,
        typer.Option("--max-mib-size", help="Maximum MIB source size in bytes."),
    ] = 10 * 1024 * 1024,
    http_timeout: Annotated[
        float,
        typer.Option("--timeout", help="HTTP request timeout in seconds."),
    ] = 30.0,
    http_retries: Annotated[
        int,
        typer.Option("--retries", help="Number of HTTP retries on transient failure."),
    ] = 3,
    no_texts: Annotated[
        bool,
        typer.Option(
            "--no-texts",
            help="Omit description, organization, and contact text from output for leaner files. "
            "Structural metadata (OIDs, dates, types) is always preserved.",
        ),
    ] = False,
    reproducible: Annotated[
        bool,
        typer.Option(
            "--reproducible",
            help="Pin generated_at to a fixed epoch so repeated compiles of the "
            "same source produce byte-identical output files.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show per-module output paths."),
    ] = False,
    watch: Annotated[
        bool,
        typer.Option(
            "--watch",
            help="Watch MIB source files for changes and recompile only the "
            "changed module and its dependents. Requires explicit MIB names "
            "or at least one --mib-dir.",
        ),
    ] = False,
) -> None:
    """Compile one or more MIB definitions and all transitive dependencies."""

    try:
        # dict[str, Any]: values are either list[str] or left absent entirely.
        # Any is correct here — mypy cannot check **kwargs spread into a dataclass.
        extra: dict[str, Any] = {}
        if sources:
            extra["sources"] = sources
        if formats:
            extra["formats"] = formats
        config = CompilerConfig(
            output_dir=output_dir,
            cache_dir=_resolve_cache_dir(cache_dir),
            cache_ttl_days=cache_ttl_days,
            max_mib_size=max_mib_size,
            http_timeout=http_timeout,
            http_retries=http_retries,
            no_texts=no_texts,
            emit_manifest=emit_manifest,
            emit_oid_index=emit_oid_index,
            reproducible=reproducible,
            **extra,
        )
        compiler = MibCompiler(config)
    except ValueError as exc:
        err.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(2) from exc

    use_http = online or bool(sources)

    if watch and not mib_names and not mib_dirs:
        err.print(
            "[bold red]Error:[/bold red] --watch requires explicit MIB names "
            "or at least one --mib-dir to watch for changes."
        )
        raise typer.Exit(2)

    if not mib_dirs and not use_http:
        err.print(
            "[bold red]Error:[/bold red] No MIB source configured. "
            "Pass --mib-dir to read from a local directory, "
            "or add --online to fetch from HTTP sources."
        )
        raise typer.Exit(2)

    for d in mib_dirs or []:
        if not d.is_dir():
            err.print(f"[yellow]Warning:[/yellow] --mib-dir {d} is not a directory, skipping.")

    # Auto-discover MIB names from --mib-dir when none are specified explicitly.
    resolved_names: list[str] = list(mib_names) if mib_names else []
    if not resolved_names:
        if not mib_dirs:
            err.print(
                "[bold red]Error:[/bold red] No MIB names given and no --mib-dir to discover from."
            )
            raise typer.Exit(2)
        seen: set[str] = set()
        for d in mib_dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in {"", ".mib", ".my", ".txt"}:
                    name = f.stem
                    if name not in seen:
                        seen.add(name)
                        resolved_names.append(name)
        if not resolved_names:
            err.print(
                "[bold red]Error:[/bold red] No MIB files found in the given --mib-dir directories."  # noqa: E501
            )
            raise typer.Exit(2)
        console.print(f"[dim]Discovered {len(resolved_names)} MIBs from --mib-dir[/dim]")

    # MIB-name validation choke point (issue #22): every name that reaches the
    # compiler — explicit CLI args AND --mib-dir auto-discovered stems — flows
    # through resolved_names, so this single loop covers both entry paths.
    # Names are later interpolated into filesystem paths (FileReader) and HTTP
    # URL templates (HttpReader); rejecting anything outside the allowlist here
    # prevents path/URL escapes. Report each offender as a clear per-name usage
    # error (exit 2), never an unhandled crash.
    invalid_names: list[str] = []
    for name in resolved_names:
        try:
            validate_mib_name(name)
        except ValueError as exc:
            invalid_names.append(str(exc))
    if invalid_names:
        for msg in invalid_names:
            err.print(f"[bold red]Error:[/bold red] {msg}")
        err.print("No MIBs were compiled — fix the invalid name(s) and retry.")
        raise typer.Exit(2)

    if watch:
        console.print(
            f"[bold]Watching[/bold] {', '.join(resolved_names)} → "
            f"{output_dir} [dim]({', '.join(config.formats)}, Ctrl-C to stop)[/dim]"
        )
        try:
            summary = asyncio.run(
                _watch_async(
                    compiler,
                    config,
                    mib_dirs or [],
                    resolved_names,
                    use_http=use_http,
                    verbose=verbose,
                )
            )
        except KeyboardInterrupt:
            err.print("\n[yellow]Interrupted.[/yellow]")
            raise typer.Exit(0) from None
        except Exception as exc:  # noqa: BLE001
            err.print(f"[bold red]Fatal error:[/bold red] {exc}")
            raise typer.Exit(1) from exc
        console.print(
            f"[dim]Watch stopped: {summary.cycles_run} cycle(s) run, "
            f"{summary.modules_recompiled} module(s) recompiled.[/dim]"
        )
        raise typer.Exit(0)

    console.print(
        f"[bold]Compiling[/bold] {', '.join(resolved_names)} → "
        f"{output_dir} [dim]({', '.join(config.formats)})[/dim]"
    )
    try:
        results = asyncio.run(
            _compile_async(compiler, config, mib_dirs or [], resolved_names, use_http=use_http)
        )
    except KeyboardInterrupt:
        err.print("\n[yellow]Interrupted.[/yellow]")
        # typer.Exit is intentional control flow, not derived from
        # KeyboardInterrupt — suppress the spurious exception context chain.
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001
        err.print(f"[bold red]Fatal error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    _print_results(results, verbose=verbose)

    if any(r.status in {"failed", "missing"} for r in results):
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


@app.command()
def lint(
    mib_names: Annotated[
        list[str],
        typer.Argument(help="MIB names to lint (e.g. IF-MIB IP-MIB)."),
    ],
    output_format: Annotated[
        Literal["text", "json"],
        typer.Option(
            "--format",
            "-f",
            help="Output format: text (default) or json (machine-readable, for CI).",
        ),
    ] = "text",
    mib_dirs: Annotated[
        list[Path] | None,
        typer.Option(
            "--mib-dir",
            "-d",
            help="Local directory to search for MIB text files. Repeat for multiple.",
        ),
    ] = None,
    online: Annotated[
        bool,
        typer.Option(
            "--online",
            help="Fetch missing MIBs from HTTP sources (mibs.pysnmp.com + mibbrowser.online). "
            "Off by default — use --mib-dir for local-only operation.",
        ),
    ] = False,
    sources: Annotated[
        list[str] | None,
        typer.Option(
            "--source",
            "-s",
            help="HTTP source URL template (@mib@ replaced with MIB name). "
            "Repeat for multiple. Implies --online; replaces default sources.",
        ),
    ] = None,
    cache_dir: Annotated[
        str | None,
        typer.Option(
            "--cache-dir",
            help="Compiled-module cache directory. Pass empty string to disable.",
        ),
    ] = None,
    cache_ttl_days: Annotated[
        int,
        typer.Option("--cache-ttl-days", help="Cache TTL in days (0 = never expire)."),
    ] = 7,
    max_mib_size: Annotated[
        int,
        typer.Option("--max-mib-size", help="Maximum MIB source size in bytes."),
    ] = 10 * 1024 * 1024,
    http_timeout: Annotated[
        float,
        typer.Option("--timeout", help="HTTP request timeout in seconds."),
    ] = 30.0,
    http_retries: Annotated[
        int,
        typer.Option("--retries", help="Number of HTTP retries on transient failure."),
    ] = 3,
) -> None:
    """Validate one or more MIB definitions and their transitive dependencies.

    Runs the same resolve pipeline as compile (fetch → parse → cache →
    resolve_oids) and reports the v1 lint check set. No output files are
    written. Exits 0 when clean, 1 when any finding or unresolved module is
    reported.
    """
    try:
        # dict[str, Any]: values are either list[str] or left absent entirely.
        # Any is correct here — mypy cannot check **kwargs spread into a dataclass.
        extra: dict[str, Any] = {}
        if sources:
            extra["sources"] = sources
        config = CompilerConfig(
            cache_dir=_resolve_cache_dir(cache_dir),
            cache_ttl_days=cache_ttl_days,
            max_mib_size=max_mib_size,
            http_timeout=http_timeout,
            http_retries=http_retries,
            **extra,
        )
    except ValueError as exc:
        err.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(2) from exc

    use_http = online or bool(sources)

    if not mib_dirs and not use_http:
        err.print(
            "[bold red]Error:[/bold red] No MIB source configured. "
            "Pass --mib-dir to read from a local directory, "
            "or add --online to fetch from HTTP sources."
        )
        raise typer.Exit(2)

    for d in mib_dirs or []:
        if not d.is_dir():
            err.print(f"[yellow]Warning:[/yellow] --mib-dir {d} is not a directory, skipping.")

    # MIB-name validation choke point (issue #22): names flow into filesystem
    # paths (FileReader) and HTTP URL templates (HttpReader); rejecting
    # anything outside the allowlist here prevents path/URL escapes.
    invalid_names: list[str] = []
    for name in mib_names:
        try:
            validate_mib_name(name)
        except ValueError as exc:
            invalid_names.append(str(exc))
    if invalid_names:
        for msg in invalid_names:
            err.print(f"[bold red]Error:[/bold red] {msg}")
        err.print("No MIBs were linted — fix the invalid name(s) and retry.")
        raise typer.Exit(2)

    try:
        report = asyncio.run(
            run_lint(mib_names, config, mib_dirs=mib_dirs or [], use_http=use_http)
        )
    except KeyboardInterrupt:
        err.print("\n[yellow]Interrupted.[/yellow]")
        # typer.Exit is intentional control flow, not derived from
        # KeyboardInterrupt — suppress the spurious exception context chain.
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001
        err.print(f"[bold red]Fatal error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    # markup=False: finding messages may contain rich markup characters
    # (e.g. "[json] ...") and must be printed verbatim. soft_wrap for JSON
    # always (a wrapped document would be invalid JSON when piped); text mode
    # only wraps on a real terminal, never when piped to a file or CI.
    if output_format == "json":
        console.print(
            json.dumps(lint_report_to_dict(report), indent=2),
            markup=False,
            soft_wrap=True,
        )
    else:
        console.print(
            format_lint_report_text(report),
            markup=False,
            soft_wrap=not console.is_terminal,
        )

    if report.findings or report.resolve_errors:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Async runner
# ---------------------------------------------------------------------------


async def _compile_async(
    compiler: MibCompiler,
    config: CompilerConfig,
    mib_dirs: list[Path],
    mib_names: list[str],
    *,
    use_http: bool,
) -> list[CompileResult]:
    """Wire up readers and run the compiler inside the async event loop."""
    # Deferred imports: avoids pulling httpx into the import graph at CLI
    # startup for users who use the library programmatically without HTTP.
    from trishul_smi.reader.localfile import FileReader

    for d in mib_dirs:
        if d.is_dir():
            compiler.add_reader(FileReader(d, max_size=config.max_mib_size))

    if use_http:
        from trishul_smi.reader.httpclient import HttpReader

        async with HttpReader(
            *config.sources,
            timeout=config.http_timeout,
            retries=config.http_retries,
            max_size=config.max_mib_size,
        ) as http:
            compiler.add_reader(http)
            return await compiler.compile(*mib_names)

    return await compiler.compile(*mib_names)


async def _watch_async(
    compiler: MibCompiler,
    config: CompilerConfig,
    mib_dirs: list[Path],
    mib_names: list[str],
    *,
    use_http: bool,
    verbose: bool,
) -> WatchSummary:
    """Run the watch loop. Readers are assembled once — MibCompiler rejects
    add_reader() after the first compile() — and every recompile cycle reuses
    the same compiler and reader chain. Returns a WatchSummary on clean stop
    (Ctrl-C, max_cycles, or stop_event).
    """
    from trishul_smi.reader.localfile import FileReader
    from trishul_smi.watch import run_watch

    for d in mib_dirs:
        if d.is_dir():
            compiler.add_reader(FileReader(d, max_size=config.max_mib_size))

    local_dirs = [d for d in mib_dirs if d.is_dir()]
    if not local_dirs:
        err.print(
            "[dim]Note: no local --mib-dir sources to poll — watch will only "
            "run the initial compile.[/dim]"
        )

    def on_cycle(cycle_no: int, results: list[CompileResult]) -> None:
        if cycle_no > 1:
            console.print(f"\n[dim]Cycle {cycle_no}:[/dim]")
        _print_results(results, verbose=verbose)

    def on_new_files(files: list[str]) -> None:
        console.print(
            f"[dim]Note: new MIB file(s) appeared in --mib-dir (not watched): "
            f"{', '.join(files)}[/dim]"
        )

    async def compile_cycle(names: list[str]) -> list[CompileResult]:
        return await compiler.compile(*names)

    # Ctrl-C / SIGTERM end the watch session instead of interrupting it
    # mid-cycle: route the signal to the engine's stop_event so it returns a
    # summary (and exits 0). A KeyboardInterrupt raised during compile_cycle
    # is still caught by the engine as a fallback.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    installed: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            # Non-Unix or a signal that cannot be handled on this loop — the
            # engine's KeyboardInterrupt path covers it.
            pass

    try:
        if use_http:
            from trishul_smi.reader.httpclient import HttpReader

            async with HttpReader(
                *config.sources,
                timeout=config.http_timeout,
                retries=config.http_retries,
                max_size=config.max_mib_size,
            ) as http:
                compiler.add_reader(http)
                return await run_watch(
                    compile_cycle,
                    initial_names=mib_names,
                    mib_dirs=local_dirs,
                    output_dir=config.output_dir,
                    stop_event=stop_event,
                    on_cycle=on_cycle,
                    on_new_files=on_new_files,
                )

        return await run_watch(
            compile_cycle,
            initial_names=mib_names,
            mib_dirs=local_dirs,
            output_dir=config.output_dir,
            stop_event=stop_event,
            on_cycle=on_cycle,
            on_new_files=on_new_files,
        )
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)

    return await compiler.compile(*mib_names)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_results(results: list[CompileResult], *, verbose: bool) -> None:
    compiled = [r for r in results if r.status == "compiled"]
    cached = [r for r in results if r.status == "cached"]
    failed = [r for r in results if r.status == "failed"]
    missing = [r for r in results if r.status == "missing"]
    warned = [r for r in results if r.status in {"compiled", "cached"} and r.warnings]

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    tbl.add_column("Status", width=10)
    tbl.add_column("Module", style="cyan")
    tbl.add_column("Details")

    for r in results:
        if r.status in {"compiled", "cached"}:
            icon = "[green]✅[/green]" if r.status == "compiled" else "[cyan]♻[/cyan]"
            if r.warnings:
                detail = f"[yellow]{len(r.warnings)} warning(s)[/yellow]"
            elif verbose:
                detail = "  ".join(str(p) for p in r.output_paths)
            else:
                detail = ""
            name_cell = f"[dim]{r.name}[/dim]" if r.is_dependency and not verbose else r.name
        elif r.status == "missing":
            icon = "[dim]–[/dim]"
            detail = f"[dim]{r.error}[/dim]"
            name_cell = f"[dim]{r.name}[/dim]"
        else:
            icon = "[red]❌[/red]"
            detail = f"[red]{r.error}[/red]"
            name_cell = r.name
        tbl.add_row(icon, name_cell, detail)

    console.print(tbl)

    parts = [f"[green]{len(compiled)} compiled[/green]"]
    if cached:
        parts.append(f"[cyan]{len(cached)} cached[/cyan]")
    if failed:
        parts.append(f"[red]{len(failed)} failed[/red]")
    if missing:
        parts.append(f"[dim]{len(missing)} missing[/dim]")
    if warned:
        parts.append(f"[yellow]{len(warned)} with warnings[/yellow]")
    console.print("  ".join(parts))

    # Full warning details (kept out of the table for readability).
    if warned:
        console.print()
        for r in warned:
            console.print(f"[cyan]{r.name}[/cyan] [yellow]({len(r.warnings)} warning(s)):[/yellow]")
            for w in r.warnings:
                console.print(f"  [yellow]•[/yellow] {w}")


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------


@app.command()
def convert(
    input_file: Annotated[
        Path,
        typer.Argument(help="Compiled PySNMP .py MIB file to convert to JSON."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for JSON output."),
    ] = Path("./mibs-output"),
) -> None:
    """Convert a compiled PySNMP .py MIB module to JSON."""
    from trishul_smi.convert import PySNMPReader
    from trishul_smi.output.json_fmt import JsonFormatter

    if not input_file.is_file():
        err.print(f"[bold red]Error:[/bold red] {input_file} is not a file.")
        raise typer.Exit(2)

    try:
        module = PySNMPReader().read(input_file)
    except Exception as exc:  # noqa: BLE001
        err.print(f"[bold red]Parse error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"{module.name}.json"
        content = JsonFormatter().format(module)
        if isinstance(content, bytes):
            out_path.write_bytes(content)
        else:
            out_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        err.print(f"[bold red]Write error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✅[/green] {module.name} → {out_path}")
