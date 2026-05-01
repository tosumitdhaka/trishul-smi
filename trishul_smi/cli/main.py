"""trishul-smi CLI — entry point: trishul_smi.cli.main:app

Commands
--------
trishul-smi compile MIB [MIB ...]   Fetch, parse, and write MIB output files.
trishul-smi version                 Print the installed package version.

Examples
--------
    # Compile from a local directory:
    trishul-smi compile IF-MIB -d /usr/share/snmp/mibs

    # Compile using HTTP sources (opt-in):
    trishul-smi compile IF-MIB IP-MIB --online

    # Write pysnmp modules; local dir first, fall back to HTTP:
    trishul-smi compile IF-MIB -f pysnmp -d /usr/share/snmp/mibs --online

    # Multiple formats, custom output dir, no disk cache:
    trishul-smi compile IF-MIB -f json -f pysnmp -o ./out --cache-dir ""

Exit codes
----------
    0   All requested MIBs compiled successfully.
    1   One or more MIBs failed to fetch, parse, or format.
    2   Configuration error (bad CLI option value).
"""

from __future__ import annotations

import asyncio
import importlib.metadata
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.models import CompileResult

app = typer.Typer(
    name="trishul-smi",
    help="Compile SNMP MIB definitions to JSON or pysnmp format.",
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
        list[str],
        typer.Argument(help="One or more MIB names to compile (e.g. IF-MIB IP-MIB)."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for output files."),
    ] = Path("./mibs-output"),
    formats: Annotated[
        list[str] | None,
        typer.Option(
            "--format",
            "-f",
            help="Output format: json or pysnmp. Repeat to write both.",
        ),
    ] = None,
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
            help="Fetch missing MIBs from HTTP sources (pysnmp.com + circitor.fr). "
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
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show per-module output paths."),
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
            **extra,
        )
        compiler = MibCompiler(config)
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

    console.print(
        f"[bold]Compiling[/bold] {', '.join(mib_names)} → "
        f"{output_dir} [dim]({', '.join(config.formats)})[/dim]"
    )
    try:
        results = asyncio.run(
            _compile_async(compiler, config, mib_dirs or [], mib_names, use_http=use_http)
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

    if any(r.status == "failed" for r in results):
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
            compiler.add_reader(FileReader(d))

    if use_http:
        from trishul_smi.reader.httpclient import HttpReader

        # HttpReader takes *url_templates (varargs), not a list — unpack with *.
        async with HttpReader(*config.sources) as http:
            compiler.add_reader(http)
            return await compiler.compile(*mib_names)

    return await compiler.compile(*mib_names)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_results(results: list[CompileResult], *, verbose: bool) -> None:
    compiled = [r for r in results if r.status == "compiled"]
    failed = [r for r in results if r.status == "failed"]
    warned = [r for r in compiled if r.warnings]

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    tbl.add_column("Status", width=10)
    tbl.add_column("Module", style="cyan")
    tbl.add_column("Details")

    for r in results:
        if r.status == "compiled":
            icon = "[green]✅[/green]"
            if r.warnings:
                detail = "[yellow]" + "; ".join(r.warnings) + "[/yellow]"
            elif verbose:
                detail = "  ".join(str(p) for p in r.output_paths)
            else:
                detail = ""
        else:
            icon = "[red]❌[/red]"
            detail = f"[red]{r.error}[/red]"
        tbl.add_row(icon, r.name, detail)

    console.print(tbl)

    parts = [f"[green]{len(compiled)} compiled[/green]"]
    if failed:
        parts.append(f"[red]{len(failed)} failed[/red]")
    if warned:
        parts.append(f"[yellow]{len(warned)} with warnings[/yellow]")
    console.print("  ".join(parts))
