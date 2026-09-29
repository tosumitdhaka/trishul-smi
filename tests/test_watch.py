"""Watch-mode tests: trishul_smi/watch.py engine + the CLI ``compile --watch`` surface.

Strategy
--------
- Engine tests drive ``run_watch()`` directly with real MibCompiler instances
  over tmp_path MIB fixtures, injecting a short debounce + poll interval and a
  bound (``max_cycles`` / ``stop_event``) so no test sleeps real time.
- Parse counts come from a CountingParser patched into ``trishul_smi.compiler``
  (MibCompiler constructs its parser by class name at __init__).
- CLI surface tests reuse the Click CliRunner conventions from test_cli.py,
  patching ``_watch_async`` so no real watching runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer
from click.testing import CliRunner

from trishul_smi.cli.main import app
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.models import CompileResult, MibModule
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.localfile import FileReader
from trishul_smi.watch import WatchSummary, run_watch

_cmd = typer.main.get_command(app)
runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixture MIBs
# ---------------------------------------------------------------------------

A_MIB = """
A-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-IDENTITY FROM SNMPv2-SMI ;
aMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Watch fixture root module."
    ::= { 1 7 }
vendorRoot OBJECT-IDENTITY
    STATUS      current
    DESCRIPTION "OID root imported by B-MIB."
    ::= { aMIB 1 }
END
"""

# Same module with the root arc moved — changes B-MIB's resolved OIDs.
A_MIB_CHANGED = A_MIB.replace("::= { aMIB 1 }", "::= { aMIB 2 }")

B_MIB = """
B-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    vendorRoot FROM A-MIB ;
bMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Depends on A-MIB."
    ::= { vendorRoot 1 }
bObj OBJECT-TYPE
    SYNTAX      INTEGER
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Child object."
    ::= { bMIB 1 }
END
"""

C_MIB = """
C-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
cMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Independent sibling."
    ::= { 1 8 }
END
"""

# Multi-branch chain fixtures: TOP → MID → BASE and SIB → BASE (plus the
# optional longer head W → TOP). These exercise the cumulative watch set.
BASE_MIB = """
BASE-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-IDENTITY FROM SNMPv2-SMI ;
baseMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Chain base."
    ::= { 1 7 }
baseRoot OBJECT-IDENTITY
    STATUS      current
    DESCRIPTION "Root imported by MID-MIB and SIB-MIB."
    ::= { baseMIB 1 }
END
"""

MID_MIB = """
MID-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-IDENTITY FROM SNMPv2-SMI
    baseRoot FROM BASE-MIB ;
midMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Chain middle."
    ::= { baseRoot 1 }
midRoot OBJECT-IDENTITY
    STATUS      current
    DESCRIPTION "Root imported by TOP-MIB."
    ::= { midMIB 1 }
END
"""

MID_MIB_CHANGED = MID_MIB.replace("::= { midMIB 1 }", "::= { midMIB 2 }")

TOP_MIB = """
TOP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-IDENTITY FROM SNMPv2-SMI
    midRoot FROM MID-MIB ;
topMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Chain top."
    ::= { midRoot 1 }
topRoot OBJECT-IDENTITY
    STATUS      current
    DESCRIPTION "Root imported by W-MIB."
    ::= { topMIB 1 }
END
"""

W_MIB = """
W-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    topRoot FROM TOP-MIB ;
wMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Chain head."
    ::= { topRoot 1 }
wObj OBJECT-TYPE
    SYNTAX      INTEGER
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Leaf object."
    ::= { wMIB 1 }
END
"""

SIB_MIB = """
SIB-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    baseRoot FROM BASE-MIB ;
sibMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Sibling branch."
    ::= { baseRoot 2 }
sibObj OBJECT-TYPE
    SYNTAX      INTEGER
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Leaf object."
    ::= { sibMIB 1 }
END
"""

SIB_MIB_CHANGED = SIB_MIB.replace("::= { sibMIB 1 }", "::= { sibMIB 2 }")

# Issue #38 fixtures: B-MIB depends on MISSING-MIB (absent at first compile),
# and C-MIB depends on B-MIB. Adopting MISSING-MIB mid-watch must recompile
# B-MIB and, transitively, C-MIB — without editing either dependent.
MISSING_MIB = """
MISSING-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-IDENTITY FROM SNMPv2-SMI ;
missingMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Adopted mid-watch."
    ::= { 1 7 }
vendorRoot OBJECT-IDENTITY
    STATUS      current
    DESCRIPTION "Root imported by B-MIB."
    ::= { missingMIB 1 }
END
"""

B_MIB_WAITING = """
B-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-IDENTITY FROM SNMPv2-SMI
    vendorRoot FROM MISSING-MIB ;
bMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Depends on MISSING-MIB."
    ::= { vendorRoot 1 }
bRoot OBJECT-IDENTITY
    STATUS      current
    DESCRIPTION "Root imported by C-MIB."
    ::= { bMIB 1 }
END
"""

C_MIB_WAITING = """
C-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE FROM SNMPv2-SMI
    bRoot FROM B-MIB ;
cMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Watch Test"
    CONTACT-INFO "watch@example.com"
    DESCRIPTION  "Transitive dependent of MISSING-MIB."
    ::= { bRoot 1 }
cObj OBJECT-TYPE
    SYNTAX      INTEGER
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Leaf object."
    ::= { cMIB 1 }
END
"""

# C2 fixture: a MISNAMED source file — stem "provider_file" declaring A-MIB.
# The watch engine must poll its ACTUAL path and recompile A-MIB's dependents
# when that file changes, even though no file is named after the declared
# module.
A_MIB_MISNAMED = A_MIB

_MODULE_HEADER_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s+DEFINITIONS\s*::=\s*BEGIN", re.MULTILINE)


class CountingParser(SmiParser):
    """Records every parsed module's declared name (tested parse precision)."""

    def __init__(self) -> None:
        super().__init__()
        self.parsed: list[str] = []

    def parse(self, text: str) -> MibModule:
        match = _MODULE_HEADER_RE.search(text)
        self.parsed.append(match.group(1) if match else "<unknown>")
        return super().parse(text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fixture(mib_dir: Path, texts: dict[str, str]) -> None:
    mib_dir.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (mib_dir / name).write_text(text, encoding="utf-8")


def _write_changed(path: Path, text: str, *, delta_ns: int = 1_000_000_000) -> None:
    """Write changed content and force a distinct mtime.

    Some filesystems (ext2/ext3, overlay mounts) coalesce rapid writes onto
    one timestamp tick, which would make the engine's mtime polling miss the
    change. Pinning a future mtime via os.utime keeps the tests deterministic
    (the engine only compares values, so a future mtime is harmless).
    """
    path.write_text(text, encoding="utf-8")
    mtime = path.stat().st_mtime_ns + delta_ns
    os.utime(path, ns=(mtime, mtime))


def _build_compiler(
    mib_dir: Path, out_dir: Path, cache_dir: Path, *, reproducible: bool = False
) -> tuple[MibCompiler, CountingParser]:
    """MibCompiler over a tmp mib-dir with a counting parser injected."""
    parsers: list[CountingParser] = []

    def make_parser() -> CountingParser:
        p = CountingParser()
        parsers.append(p)
        return p

    with patch("trishul_smi.compiler.SmiParser", make_parser):
        config = CompilerConfig(
            output_dir=out_dir,
            cache_dir=cache_dir,
            cache_ttl_days=0,
            reproducible=reproducible,
        )
        compiler = MibCompiler(config)
        compiler.add_reader(FileReader(mib_dir, max_size=config.max_mib_size))
    return compiler, parsers[0]


def _compiler_cycle(compiler: MibCompiler):
    async def cycle(names: list[str]) -> list[CompileResult]:
        return await compiler.compile(*names)

    return cycle


async def _run_real_watch(
    compiler: MibCompiler,
    mib_dir: Path,
    out_dir: Path,
    names: list[str],
    *,
    debounce: float = 0.05,
    poll: float = 0.005,
    on_cycle=None,
    stop_event: asyncio.Event | None = None,
    max_cycles: int = 2,
) -> WatchSummary:
    return await run_watch(
        _compiler_cycle(compiler),
        initial_names=names,
        mib_dirs=[mib_dir],
        output_dir=out_dir,
        debounce_seconds=debounce,
        poll_interval=poll,
        max_cycles=max_cycles,
        stop_event=stop_event,
        on_cycle=on_cycle,
    )


def _watch_task(
    compile_cycle,
    mib_dir: Path,
    out_dir: Path,
    names: list[str],
    *,
    debounce: float,
    poll: float = 0.005,
    max_cycles: int = 2,
    on_cycle=None,
    on_new_files=None,
    stop_event: asyncio.Event | None = None,
):
    return asyncio.create_task(
        run_watch(
            compile_cycle,
            initial_names=names,
            mib_dirs=[mib_dir],
            output_dir=out_dir,
            debounce_seconds=debounce,
            poll_interval=poll,
            max_cycles=max_cycles,
            stop_event=stop_event,
            on_cycle=on_cycle,
            on_new_files=on_new_files,
        )
    )


def _invoke(args, **kwargs):
    return runner.invoke(_cmd, args, **kwargs)


# ---------------------------------------------------------------------------
# Engine: file change → recompile cycle
# ---------------------------------------------------------------------------


async def test_file_change_triggers_recompile_and_reparse(tmp_path: Path):
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"A-MIB": A_MIB})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    cycle_numbers: list[int] = []
    first_cycle = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        cycle_numbers.append(n)
        if n == 1:
            first_cycle.set()

    task = asyncio.create_task(
        _run_real_watch(
            compiler, mib_dir, out_dir, ["A-MIB"], debounce=0.05, poll=0.005, on_cycle=on_cycle
        )
    )
    await first_cycle.wait()
    assert parser.parsed == ["A-MIB"]

    _write_changed(mib_dir / "A-MIB", A_MIB_CHANGED)

    summary = await task
    assert summary.cycles_run == 2
    assert cycle_numbers == [1, 2]
    # The changed module re-parsed on the second cycle.
    assert parser.parsed.count("A-MIB") == 2


# ---------------------------------------------------------------------------
# Engine: parse precision — only the changed module re-parses
# ---------------------------------------------------------------------------


async def test_only_changed_module_reparses_across_cycle(tmp_path: Path):
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"A-MIB": A_MIB, "B-MIB": B_MIB, "C-MIB": C_MIB})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    first_cycle = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        if n == 1:
            first_cycle.set()

    task = asyncio.create_task(
        _run_real_watch(compiler, mib_dir, out_dir, ["A-MIB", "B-MIB", "C-MIB"], on_cycle=on_cycle)
    )
    await first_cycle.wait()
    assert sorted(parser.parsed) == ["A-MIB", "B-MIB", "C-MIB"]

    _write_changed(mib_dir / "A-MIB", A_MIB_CHANGED)

    summary = await task
    assert summary.cycles_run == 2
    assert summary.modules_recompiled == 2  # A-MIB + its dependent B-MIB
    assert parser.parsed.count("A-MIB") == 2  # re-parsed — content changed
    assert parser.parsed.count("B-MIB") == 1  # fingerprint cache hit
    assert parser.parsed.count("C-MIB") == 1  # outside the invalidation set


# ---------------------------------------------------------------------------
# Engine: dependent output refreshed, non-dependent output untouched
# ---------------------------------------------------------------------------


async def test_dependent_output_refreshed_non_dependent_untouched(tmp_path: Path):
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"A-MIB": A_MIB, "B-MIB": B_MIB, "C-MIB": C_MIB})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    first_cycle = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        if n == 1:
            first_cycle.set()

    task = asyncio.create_task(
        _run_real_watch(compiler, mib_dir, out_dir, ["A-MIB", "B-MIB", "C-MIB"], on_cycle=on_cycle)
    )
    await first_cycle.wait()

    b_before = (out_dir / "B-MIB.json").read_bytes()
    c_path = out_dir / "C-MIB.json"
    c_before = c_path.read_bytes()
    c_mtime_ns = c_path.stat().st_mtime_ns
    assert json.loads(b_before)["objects"]["bObj"]["oid"] == "1.7.1.1.1"

    _write_changed(mib_dir / "A-MIB", A_MIB_CHANGED)

    summary = await task
    assert summary.cycles_run == 2

    # The dependent's output was refreshed against A's new OID root.
    b_after = (out_dir / "B-MIB.json").read_bytes()
    assert b_after != b_before
    assert json.loads(b_after)["objects"]["bObj"]["oid"] == "1.7.2.1.1"

    # The non-dependent sibling was never recompiled: byte-identical, same mtime.
    assert c_path.read_bytes() == c_before
    assert c_path.stat().st_mtime_ns == c_mtime_ns


# ---------------------------------------------------------------------------
# Engine: debounce coalesces a burst of writes into one cycle
# ---------------------------------------------------------------------------


async def test_rapid_writes_debounce_to_single_cycle(tmp_path: Path):
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    _write_fixture(mib_dir, {"A-MIB": A_MIB})

    call_times: list[float] = []

    async def fake_cycle(names: list[str]) -> list[CompileResult]:
        call_times.append(time.monotonic())
        return [CompileResult(name=n, status="compiled") for n in names]

    started = asyncio.Event()
    stop = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        if n == 1:
            started.set()
        if n >= 2:
            stop.set()

    debounce = 0.1
    t0 = time.monotonic()
    task = _watch_task(
        fake_cycle,
        mib_dir,
        out_dir,
        ["A-MIB"],
        debounce=debounce,
        poll=0.005,
        max_cycles=10,
        on_cycle=on_cycle,
        stop_event=stop,
    )
    await started.wait()

    # A burst of rapid writes — all inside the debounce window, each with
    # a distinct (pinned) mtime so coarse filesystem ticks cannot hide them.
    for i, arc in enumerate(("aMIB 9", "aMIB 8", "aMIB 7"), start=1):
        _write_changed(mib_dir / "A-MIB", A_MIB.replace("aMIB 1", arc), delta_ns=i * 1_000_000)

    summary = await task
    assert summary.cycles_run == 2  # initial + exactly one debounced recompile
    assert len(call_times) == 2
    # The recompile fired only after the debounce window elapsed.
    assert call_times[1] - t0 >= debounce - 0.02


# ---------------------------------------------------------------------------
# Engine: new files in --mib-dir are adopted — compiled and watched
# ---------------------------------------------------------------------------


async def test_new_file_adopted_compiled_and_watched(tmp_path: Path):
    """A file appearing in --mib-dir mid-watch is adopted: its module joins
    the watch set, gets an initial compile (its output file appears), and is
    polled like any other module — a later modification of IT fires a cycle."""
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"A-MIB": A_MIB})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    started = asyncio.Event()
    cycle_done = {n: asyncio.Event() for n in (2, 3)}
    closures: dict[int, set[str]] = {}
    new_files: list[str] = []
    new_seen = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        closures[n] = {r.name for r in results}
        if n == 1:
            started.set()
        if n in cycle_done:
            cycle_done[n].set()

    def on_new_files(files: list[str]) -> None:
        new_files.extend(files)
        new_seen.set()

    task = _watch_task(
        _compiler_cycle(compiler),
        mib_dir,
        out_dir,
        ["A-MIB"],
        debounce=0.05,
        poll=0.005,
        max_cycles=3,
        on_cycle=on_cycle,
        on_new_files=on_new_files,
    )
    await asyncio.wait_for(started.wait(), timeout=10)

    new_mib = C_MIB.replace("C-MIB", "NEW-MIB").replace("cMIB", "newMIB")
    _write_fixture(mib_dir, {"NEW-MIB": new_mib})
    await asyncio.wait_for(new_seen.wait(), timeout=10)
    assert "NEW-MIB" in new_files
    await asyncio.wait_for(cycle_done[2].wait(), timeout=10)
    assert "NEW-MIB" in closures[2]
    assert (out_dir / "NEW-MIB.json").is_file()

    # The adopted module is watched from the next cycle: modifying IT triggers
    # a recompile and refreshes its output.
    new_path = out_dir / "NEW-MIB.json"
    before = new_path.read_bytes()
    _write_changed(mib_dir / "NEW-MIB", new_mib.replace("::= { 1 8 }", "::= { 1 9 }"))
    await asyncio.wait_for(cycle_done[3].wait(), timeout=10)
    assert "NEW-MIB" in closures[3]
    assert new_path.read_bytes() != before

    summary = await task
    assert summary.cycles_run == 3
    assert summary.modules_recompiled == 2  # cycle 2: adoption; cycle 3: modification


async def test_new_unparseable_file_failed_row_not_crash(tmp_path: Path):
    """An unparseable new file is adopted like any other: its initial compile
    surfaces a failed result row (not a crash), and the watcher keeps running."""
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"A-MIB": A_MIB})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    started = asyncio.Event()
    adoption_done = asyncio.Event()
    cycle_results: dict[int, list[CompileResult]] = {}

    def on_cycle(n: int, results) -> None:
        cycle_results[n] = list(results)
        if n == 1:
            started.set()
        if n >= 2:
            adoption_done.set()

    task = _watch_task(
        _compiler_cycle(compiler),
        mib_dir,
        out_dir,
        ["A-MIB"],
        debounce=0.05,
        poll=0.005,
        max_cycles=2,
        on_cycle=on_cycle,
    )
    await asyncio.wait_for(started.wait(), timeout=10)

    (mib_dir / "BAD-MIB").write_text("this is not a MIB module", encoding="utf-8")
    await asyncio.wait_for(adoption_done.wait(), timeout=10)

    bad = [r for r in cycle_results[2] if r.name == "BAD-MIB"]
    assert len(bad) == 1
    assert bad[0].status == "failed"
    assert bad[0].error is not None

    summary = await task
    assert summary.cycles_run == 2
    assert summary.modules_recompiled == 1


async def test_new_file_adoption_first_mib_dir_wins(tmp_path: Path):
    """Stem-dedup semantics of --mib-dir discovery carry into adoption: when a
    new stem appears in two directories mid-watch, the FIRST --mib-dir's file
    is the one that gets adopted and compiled."""
    mib_dir_a = tmp_path / "mibs-a"
    mib_dir_b = tmp_path / "mibs-b"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir_a, {"A-MIB": A_MIB})
    _write_fixture(mib_dir_b, {"A-MIB": A_MIB})

    config = CompilerConfig(output_dir=out_dir, cache_dir=cache_dir, cache_ttl_days=0)
    compiler = MibCompiler(config)
    compiler.add_reader(FileReader(mib_dir_a, max_size=config.max_mib_size))
    compiler.add_reader(FileReader(mib_dir_b, max_size=config.max_mib_size))

    started = asyncio.Event()
    adoption_done = asyncio.Event()
    closures: dict[int, set[str]] = {}

    def on_cycle(n: int, results) -> None:
        closures[n] = {r.name for r in results}
        if n == 1:
            started.set()
        if n >= 2:
            adoption_done.set()

    task = asyncio.create_task(
        run_watch(
            _compiler_cycle(compiler),
            initial_names=["A-MIB"],
            mib_dirs=[mib_dir_a, mib_dir_b],
            output_dir=out_dir,
            debounce_seconds=0.05,
            poll_interval=0.005,
            max_cycles=2,
            on_cycle=on_cycle,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=10)

    new_mib = C_MIB.replace("C-MIB", "NEW-MIB").replace("cMIB", "newMIB")
    _write_fixture(mib_dir_a, {"NEW-MIB": new_mib.replace("::= { 1 8 }", "::= { 1 9 }")})
    _write_fixture(mib_dir_b, {"NEW-MIB": new_mib.replace("::= { 1 8 }", "::= { 1 10 }")})
    await asyncio.wait_for(adoption_done.wait(), timeout=10)

    data = json.loads((out_dir / "NEW-MIB.json").read_text(encoding="utf-8"))
    assert data["objects"]["newMIB"]["oid"] == "1.9"  # first --mib-dir wins
    assert data["objects"]["newMIB"]["oid"] != "1.10"

    summary = await task
    assert summary.cycles_run == 2
    assert summary.modules_recompiled == 1


# ---------------------------------------------------------------------------
# Engine: stop conditions — KeyboardInterrupt and stop_event
# ---------------------------------------------------------------------------


async def test_keyboard_interrupt_during_cycle_returns_summary(tmp_path: Path):
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    _write_fixture(mib_dir, {"A-MIB": A_MIB})

    calls = 0

    async def fake_cycle(names: list[str]) -> list[CompileResult]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return [CompileResult(name=n, status="compiled") for n in names]

    started = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        if n == 1:
            started.set()

    task = _watch_task(
        fake_cycle, mib_dir, out_dir, ["A-MIB"], debounce=0.05, poll=0.005, on_cycle=on_cycle
    )
    await started.wait()
    _write_changed(mib_dir / "A-MIB", A_MIB_CHANGED)

    summary = await task
    assert summary.cycles_run == 1  # initial cycle only; the recompile was interrupted
    assert summary.modules_recompiled == 0


async def test_stop_event_ends_session_cleanly(tmp_path: Path):
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"A-MIB": A_MIB})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    started = asyncio.Event()
    stop = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        if n == 1:
            started.set()

    task = _watch_task(
        _compiler_cycle(compiler),
        mib_dir,
        out_dir,
        ["A-MIB"],
        debounce=0.05,
        poll=0.005,
        max_cycles=10,
        on_cycle=on_cycle,
        stop_event=stop,
    )
    await started.wait()
    stop.set()

    summary = await task
    assert summary.cycles_run == 1
    assert summary.modules_recompiled == 0


# ---------------------------------------------------------------------------
# Engine: cumulative watch set (regression — the watch set must never shrink
# to the last invalidation cycle's closure)
# ---------------------------------------------------------------------------


async def test_watch_set_cumulative_sibling_branch_still_polled(tmp_path: Path):
    """Oracle repro: TOP → MID → BASE plus a sibling SIB → BASE. After a MID
    edit recompiles only {MID, TOP}, a subsequent SIB edit MUST still fire a
    cycle and refresh SIB's output, while the TOP branch stays untouched."""
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(
        mib_dir,
        {"BASE-MIB": BASE_MIB, "MID-MIB": MID_MIB, "TOP-MIB": TOP_MIB, "SIB-MIB": SIB_MIB},
    )
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    closures: list[set[str]] = []
    cycle_done = {n: asyncio.Event() for n in (1, 2, 3)}

    def on_cycle(n: int, results) -> None:
        closures.append({r.name for r in results})
        if n in cycle_done:
            cycle_done[n].set()

    task = asyncio.create_task(
        _run_real_watch(
            compiler,
            mib_dir,
            out_dir,
            ["TOP-MIB", "SIB-MIB"],
            debounce=0.05,
            poll=0.005,
            max_cycles=3,
            on_cycle=on_cycle,
        )
    )
    await asyncio.wait_for(cycle_done[1].wait(), timeout=10)
    assert closures[0] == {"BASE-MIB", "MID-MIB", "TOP-MIB", "SIB-MIB"}

    top_path = out_dir / "TOP-MIB.json"
    mid_path = out_dir / "MID-MIB.json"
    sib_path = out_dir / "SIB-MIB.json"
    top_before = top_path.read_bytes()
    sib_before = sib_path.read_bytes()
    sib_mtime_before = sib_path.stat().st_mtime_ns

    # Cycle 2: edit MID → MID + its dependent TOP recompile.
    _write_changed(mib_dir / "MID-MIB", MID_MIB_CHANGED)
    await asyncio.wait_for(cycle_done[2].wait(), timeout=10)
    assert closures[1] == {"BASE-MIB", "MID-MIB", "TOP-MIB"}  # SIB not re-resolved
    top_after_mid = top_path.read_bytes()
    top_mtime_after_mid = top_path.stat().st_mtime_ns
    mid_mtime_after_mid = mid_path.stat().st_mtime_ns
    assert top_after_mid != top_before  # dependent refreshed
    assert sib_path.read_bytes() == sib_before  # sibling untouched by the MID cycle
    assert sib_path.stat().st_mtime_ns == sib_mtime_before

    # Cycle 3: edit SIB — must still fire even though SIB was absent from
    # cycle 2's closure (the watch set is cumulative, not the last cycle's).
    _write_changed(mib_dir / "SIB-MIB", SIB_MIB_CHANGED)
    await asyncio.wait_for(cycle_done[3].wait(), timeout=10)
    assert closures[2] == {"BASE-MIB", "SIB-MIB"}
    assert sib_path.read_bytes() != sib_before  # SIB output refreshed
    # The other (non-edited) branch is untouched by the SIB cycle.
    assert top_path.read_bytes() == top_after_mid
    assert top_path.stat().st_mtime_ns == top_mtime_after_mid
    assert mid_path.stat().st_mtime_ns == mid_mtime_after_mid

    summary = await task
    assert summary.cycles_run == 3
    assert summary.modules_recompiled == 3  # 2 (MID + TOP) + 1 (SIB)


async def test_longer_chain_invalidates_all_transitive_dependents(tmp_path: Path):
    """W → TOP → MID → BASE: editing MID must recompile TOP and W (two
    reverse-edge hops) in one cycle and leave the sibling SIB branch alone."""
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(
        mib_dir,
        {
            "BASE-MIB": BASE_MIB,
            "MID-MIB": MID_MIB,
            "TOP-MIB": TOP_MIB,
            "W-MIB": W_MIB,
            "SIB-MIB": SIB_MIB,
        },
    )
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    closures: list[set[str]] = []
    cycle_done = {n: asyncio.Event() for n in (1, 2)}

    def on_cycle(n: int, results) -> None:
        closures.append({r.name for r in results})
        if n in cycle_done:
            cycle_done[n].set()

    task = asyncio.create_task(
        _run_real_watch(
            compiler,
            mib_dir,
            out_dir,
            ["W-MIB", "SIB-MIB"],
            debounce=0.05,
            poll=0.005,
            max_cycles=2,
            on_cycle=on_cycle,
        )
    )
    await asyncio.wait_for(cycle_done[1].wait(), timeout=10)
    assert closures[0] == {"BASE-MIB", "MID-MIB", "TOP-MIB", "W-MIB", "SIB-MIB"}

    w_path = out_dir / "W-MIB.json"
    sib_path = out_dir / "SIB-MIB.json"
    w_before = w_path.read_bytes()
    sib_before = sib_path.read_bytes()
    sib_mtime_before = sib_path.stat().st_mtime_ns

    _write_changed(mib_dir / "MID-MIB", MID_MIB_CHANGED)
    await asyncio.wait_for(cycle_done[2].wait(), timeout=10)
    # Every transitive dependent of MID recompiles: TOP and W.
    assert closures[1] == {"BASE-MIB", "MID-MIB", "TOP-MIB", "W-MIB"}
    assert w_path.read_bytes() != w_before  # leaf refreshed through two hops
    assert sib_path.read_bytes() == sib_before  # sibling branch untouched
    assert sib_path.stat().st_mtime_ns == sib_mtime_before

    summary = await task
    assert summary.cycles_run == 2
    assert summary.modules_recompiled == 3  # MID + TOP + W


# ---------------------------------------------------------------------------
# Engine: issue #38 — adoption of a missing dependency recompiles the failed
# module and its transitive dependents, without editing the dependent
# ---------------------------------------------------------------------------


async def test_adopted_dependency_recompiles_failed_dependents(tmp_path: Path):
    """B-MIB fails on an unresolved import of MISSING-MIB. When MISSING-MIB's
    file appears mid-watch, adoption supplies the dependency and the failed
    module B-MIB (and its dependent C-MIB, transitively) recompiles in the
    next debounced cycle — neither dependent file is edited."""
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"B-MIB": B_MIB_WAITING, "C-MIB": C_MIB_WAITING})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    closures: list[set[str]] = []
    cycle_done = {n: asyncio.Event() for n in (1, 2, 3)}
    new_files: list[str] = []
    new_seen = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        closures.append({r.name for r in results})
        if n in cycle_done:
            cycle_done[n].set()

    def on_new_files(files: list[str]) -> None:
        new_files.extend(files)
        new_seen.set()

    task = asyncio.create_task(
        run_watch(
            _compiler_cycle(compiler),
            initial_names=["B-MIB", "C-MIB"],
            mib_dirs=[mib_dir],
            output_dir=out_dir,
            debounce_seconds=0.05,
            poll_interval=0.005,
            max_cycles=3,
            on_cycle=on_cycle,
            on_new_files=on_new_files,
        )
    )
    await asyncio.wait_for(cycle_done[1].wait(), timeout=10)

    # Cycle 1: B-MIB fails on MISSING-MIB, C-MIB fails on B-MIB. The missing
    # dependency itself surfaces as a "missing" result row. No outputs are
    # written yet.
    assert closures[0] == {"B-MIB", "C-MIB", "MISSING-MIB"}
    assert not (out_dir / "B-MIB.json").is_file()

    # MISSING-MIB appears mid-watch — adoption compiles it in cycle 2.
    _write_fixture(mib_dir, {"MISSING-MIB": MISSING_MIB})
    await asyncio.wait_for(new_seen.wait(), timeout=10)
    assert "MISSING-MIB" in new_files
    await asyncio.wait_for(cycle_done[2].wait(), timeout=10)
    assert closures[1] == {"MISSING-MIB"}
    assert (out_dir / "MISSING-MIB.json").is_file()

    # Cycle 3: the failed dependents were retried without being edited.
    # MISSING-MIB re-enters the closure as their (cached) dependency.
    await asyncio.wait_for(cycle_done[3].wait(), timeout=10)
    assert closures[2] == {"B-MIB", "C-MIB", "MISSING-MIB"}
    assert (out_dir / "B-MIB.json").is_file()
    assert (out_dir / "C-MIB.json").is_file()
    data = json.loads((out_dir / "C-MIB.json").read_text(encoding="utf-8"))
    # 1.7 (missingMIB) .1 (vendorRoot) .1 (bMIB) .1 (bRoot) .1 (cMIB) .1 (cObj)
    assert data["objects"]["cObj"]["oid"] == "1.7.1.1.1.1.1"

    summary = await task
    assert summary.cycles_run == 3
    assert summary.modules_recompiled == 3  # adoption + transitive retry batch
    assert summary.failed is False


# ---------------------------------------------------------------------------
# Engine: issue C2 — a misnamed source (stem != declared name) is watched at
# its ACTUAL path; editing it recompiles the module and its dependents
# ---------------------------------------------------------------------------


async def test_misnamed_source_edit_recompiles_module_and_dependents(tmp_path: Path):
    """The provider A-MIB lives in a misnamed file 'provider_file' (stem !=
    declared name). Editing that file must recompile A-MIB and its dependent
    B-MIB, even though no file is named after the declared module (the old
    name-based lookup would not poll it at all)."""
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    # provider_file declares A-MIB (same text as the normal A-MIB fixture).
    _write_fixture(mib_dir, {"provider_file": A_MIB, "B-MIB": B_MIB})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    cycle_done = {n: asyncio.Event() for n in (1, 2)}

    def on_cycle(n: int, results) -> None:
        if n in cycle_done:
            cycle_done[n].set()

    task = asyncio.create_task(
        _run_real_watch(
            compiler,
            mib_dir,
            out_dir,
            ["provider_file", "B-MIB"],
            debounce=0.05,
            poll=0.005,
            max_cycles=2,
            on_cycle=on_cycle,
        )
    )
    await asyncio.wait_for(cycle_done[1].wait(), timeout=10)
    b_before = (out_dir / "B-MIB.json").read_bytes()
    assert json.loads(b_before)["objects"]["bObj"]["oid"] == "1.7.1.1.1"

    # Edit the MISNAMED file (its stem never matches the declared name).
    _write_changed(mib_dir / "provider_file", A_MIB_CHANGED)
    await asyncio.wait_for(cycle_done[2].wait(), timeout=10)

    b_after = (out_dir / "B-MIB.json").read_bytes()
    assert b_after != b_before
    assert json.loads(b_after)["objects"]["bObj"]["oid"] == "1.7.2.1.1"

    summary = await task
    assert summary.cycles_run == 2
    assert summary.failed is False


# ---------------------------------------------------------------------------
# Engine: issue #38 — deletion marks outputs stale (offline cache fallback
# must not mask a confirmed local deletion); restoration recovers
# ---------------------------------------------------------------------------


async def test_deleted_source_marks_stale_and_restoration_recovers(tmp_path: Path):
    """Deleting a watched source invalidates the module and its dependents.
    The offline compiled-module cache fallback must NOT silently turn the
    confirmed local deletion into a successful compile: the output is marked
    stale and its path reported. Existing output files are never deleted.
    Restoring the file re-adopts it and clears the stale state."""
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"A-MIB": A_MIB, "B-MIB": B_MIB})
    # reproducible=True pins generated_at so the byte-equality assertion below
    # is stable across cycles: the compiler rebuilds artifact metadata (with a
    # second-granularity timestamp) on every compile() call, and a
    # cache-served re-emission ~0.1s later otherwise straddles a wall-clock
    # second ~5-15% of runs (v0.5.3 pre-release review finding M1).
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir, reproducible=True)

    closures: list[set[str]] = []
    cycle_done = {n: asyncio.Event() for n in (1, 2, 3)}
    stale_paths: list[Path] = []

    def on_cycle(n: int, results) -> None:
        closures.append({r.name for r in results})
        if n in cycle_done:
            cycle_done[n].set()

    def on_stale(paths: list[Path]) -> None:
        stale_paths.extend(paths)

    task = asyncio.create_task(
        run_watch(
            _compiler_cycle(compiler),
            initial_names=["A-MIB", "B-MIB"],
            mib_dirs=[mib_dir],
            output_dir=out_dir,
            debounce_seconds=0.05,
            poll_interval=0.005,
            max_cycles=3,
            on_cycle=on_cycle,
            on_stale=on_stale,
        )
    )
    await asyncio.wait_for(cycle_done[1].wait(), timeout=10)
    a_output = out_dir / "A-MIB.json"
    assert a_output.is_file()
    a_before = a_output.read_bytes()

    # Delete the A-MIB source mid-watch.
    (mib_dir / "A-MIB").unlink()
    await asyncio.wait_for(cycle_done[2].wait(), timeout=10)
    # The removed module and its dependent were invalidated and recompiled.
    assert closures[1] == {"A-MIB", "B-MIB"}
    # A-MIB is now served from the offline cache fallback — which must NOT
    # count as a successful watch compile: its output is marked stale.
    assert stale_paths == [a_output]
    # Existing output files are never deleted.
    assert a_output.is_file()
    assert a_output.read_bytes() == a_before

    # Restore the file — re-adoption recompiles A-MIB and B-MIB and clears
    # the stale state.
    _write_fixture(mib_dir, {"A-MIB": A_MIB})
    await asyncio.wait_for(cycle_done[3].wait(), timeout=10)
    assert closures[2] == {"A-MIB", "B-MIB"}

    summary = await task
    assert summary.cycles_run == 3
    assert summary.failed is False  # stale state cleared by the restoration


# ---------------------------------------------------------------------------
# Engine: a misnamed file that fails to parse is still polled, and recovering
# it migrates the watch tracking to its declared name (no phantom exit-1)
# ---------------------------------------------------------------------------


async def test_misnamed_parse_failure_recovers_without_phantom_failure(tmp_path: Path):
    """A misnamed, unparseable file is watched at its actual path. Once fixed,
    its module compiles under the DECLARED name and the alias (requested)
    name stops counting as a failure — the session exits clean."""
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"broken_src": "this is not a MIB module\n"})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    closures: list[set[str]] = []
    cycle_done = {n: asyncio.Event() for n in (1, 2)}

    def on_cycle(n: int, results) -> None:
        closures.append({r.name for r in results})
        if n in cycle_done:
            cycle_done[n].set()

    task = asyncio.create_task(
        run_watch(
            _compiler_cycle(compiler),
            initial_names=["broken_src"],
            mib_dirs=[mib_dir],
            output_dir=out_dir,
            debounce_seconds=0.05,
            poll_interval=0.005,
            max_cycles=2,
            on_cycle=on_cycle,
        )
    )
    await asyncio.wait_for(cycle_done[1].wait(), timeout=10)
    assert closures[0] == {"broken_src"}

    # Fix the file so it declares a proper module name.
    fixed = A_MIB.replace("A-MIB", "FIXED-MIB").replace("aMIB", "fixedMIB")
    _write_changed(mib_dir / "broken_src", fixed)
    await asyncio.wait_for(cycle_done[2].wait(), timeout=10)
    assert closures[1] == {"FIXED-MIB"}
    assert (out_dir / "FIXED-MIB.json").is_file()

    summary = await task
    assert summary.cycles_run == 2
    assert summary.failed is False


# ---------------------------------------------------------------------------
# CLI: --watch guard, clean stop, Ctrl-C
# ---------------------------------------------------------------------------


class TestWatchCli:
    def test_watch_without_names_or_mib_dir_exits_2(self):
        result = _invoke(["compile", "--watch"])
        assert result.exit_code == 2
        assert "--watch requires" in result.output

    def test_watch_with_names_but_no_mib_dir_exits_2(self):
        """Explicit names alone pass the --watch guard; the existing
        no-source check still rejects a source-less run."""
        result = _invoke(["compile", "IF-MIB", "--watch"])
        assert result.exit_code == 2
        assert "No MIB source" in result.output

    def test_watch_clean_stop_prints_summary_and_exits_0(self, tmp_path: Path):
        with patch(
            "trishul_smi.cli.main._watch_async",
            new=AsyncMock(return_value=WatchSummary(cycles_run=3, modules_recompiled=2)),
        ):
            result = _invoke(["compile", "IF-MIB", "-d", str(tmp_path), "--watch"])
        assert result.exit_code == 0
        assert "Watching" in result.output
        assert "Watch stopped" in result.output
        assert "3 cycle(s) run" in result.output
        assert "2 module(s) recompiled" in result.output

    def test_watch_keyboard_interrupt_exits_0(self, tmp_path: Path):
        with patch("trishul_smi.cli.main._watch_async", side_effect=KeyboardInterrupt):
            result = _invoke(["compile", "IF-MIB", "-d", str(tmp_path), "--watch"])
        assert result.exit_code == 0
        assert "Interrupted" in result.output


# ---------------------------------------------------------------------------
# Engine: guard rails, import-graph fallbacks, mid-compile races
# ---------------------------------------------------------------------------


class TestWatchGuards:
    async def test_run_watch_rejects_empty_initial_names(self, tmp_path: Path):
        async def _cycle(names: list[str]) -> list[CompileResult]:
            return []

        with pytest.raises(ValueError):
            await run_watch(
                _cycle,
                initial_names=[],
                mib_dirs=[tmp_path],
                output_dir=tmp_path,
            )

    async def test_run_watch_rejects_zero_max_cycles(self, tmp_path: Path):
        async def _cycle(names: list[str]) -> list[CompileResult]:
            return []

        with pytest.raises(ValueError):
            await run_watch(
                _cycle,
                initial_names=["A-MIB"],
                mib_dirs=[tmp_path],
                output_dir=tmp_path,
                max_cycles=0,
            )

    async def test_initial_compile_keyboard_interrupt_returns_summary(self, tmp_path: Path):
        """A KeyboardInterrupt during the INITIAL compile returns a summary
        (cycles_run 0) instead of propagating."""

        async def _cycle(names: list[str]) -> list[CompileResult]:
            raise KeyboardInterrupt

        summary = await run_watch(
            _cycle,
            initial_names=["A-MIB"],
            mib_dirs=[tmp_path],
            output_dir=tmp_path,
        )
        assert summary.cycles_run == 0
        assert summary.modules_recompiled == 0
        assert summary.failed is False


class TestWatchGraphFallbacks:
    def test_imports_from_json_falls_back_on_missing_corrupt_or_shape(self, tmp_path: Path):
        from trishul_smi.watch import _imports_from_json

        out = tmp_path / "out"
        out.mkdir()
        # Absent file → None.
        assert _imports_from_json(out, "NO-SUCH") is None
        # Corrupt JSON → None (falls back to a source scan).
        (out / "A-MIB.json").write_text("{not json", encoding="utf-8")
        assert _imports_from_json(out, "A-MIB") is None
        # Valid JSON without an imports section → None.
        (out / "B-MIB.json").write_text('{"module": "B-MIB"}', encoding="utf-8")
        assert _imports_from_json(out, "B-MIB") is None
        # Valid imports section → exact set.
        (out / "C-MIB.json").write_text(
            '{"module": "C-MIB", "imports": {"SNMPv2-SMI": ["OBJECT-TYPE"]}}',
            encoding="utf-8",
        )
        assert _imports_from_json(out, "C-MIB") == {"SNMPv2-SMI"}

    def test_imports_from_source_path_unreadable(self, tmp_path: Path):
        from trishul_smi.watch import _imports_from_source_path

        assert _imports_from_source_path(None) is None
        # A directory cannot be read as text → OSError → None.
        d = tmp_path / "adir"
        d.mkdir()
        assert _imports_from_source_path(d) is None

    def test_notice_new_files_skips_non_files_and_known_stems(self, tmp_path: Path):
        from trishul_smi.watch import _notice_new_files

        mib_dir = tmp_path / "mibs"
        (mib_dir / "sub").mkdir(parents=True)  # directory, not a file
        (mib_dir / "bad.py").write_text("x", encoding="utf-8")  # wrong suffix
        (mib_dir / "NEW-MIB").write_text("x", encoding="utf-8")  # adoptable
        (mib_dir / "KNOWN-MIB").write_text("x", encoding="utf-8")  # already watched

        noticed: set[str] = set()
        # A missing --mib-dir is skipped (iterdir OSError); sub/bad.py are
        # skipped; KNOWN-MIB is already tracked; NEW-MIB is reported.
        new = _notice_new_files([tmp_path / "no-such-dir", mib_dir], {"KNOWN-MIB"}, noticed, None)
        assert new == ["NEW-MIB"]
        assert noticed == {"NEW-MIB"}
        # A repeated scan reports nothing (noticed is idempotent).
        assert _notice_new_files([mib_dir], {"KNOWN-MIB"}, noticed, None) == []


class TestWatchDeletionEdges:
    async def test_deleted_misnamed_source_marks_stale_conservatively(self, tmp_path: Path):
        """No cache: deleting a misnamed source leaves the DECLARED module
        without a result row (the compile of its stem reports the stem name),
        so the conservative stale branch applies and the output path is
        reported. The output file itself is never deleted."""
        from trishul_smi.watch import run_watch

        mib_dir = tmp_path / "mibs"
        out_dir = tmp_path / "out"
        _write_fixture(mib_dir, {"provider_file": A_MIB, "B-MIB": B_MIB})
        config = CompilerConfig(output_dir=out_dir, cache_dir=None)
        compiler = MibCompiler(config)
        compiler.add_reader(FileReader(mib_dir, max_size=config.max_mib_size))

        stale_paths: list[Path] = []
        started = asyncio.Event()
        done = asyncio.Event()

        def on_cycle(n: int, results) -> None:
            if n == 1:
                started.set()
            if n >= 2:
                done.set()

        def on_stale(paths: list[Path]) -> None:
            stale_paths.extend(paths)

        task = asyncio.create_task(
            run_watch(
                _compiler_cycle(compiler),
                initial_names=["provider_file", "B-MIB"],
                mib_dirs=[mib_dir],
                output_dir=out_dir,
                debounce_seconds=0.05,
                poll_interval=0.005,
                max_cycles=2,
                on_cycle=on_cycle,
                on_stale=on_stale,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        assert (out_dir / "A-MIB.json").is_file()

        (mib_dir / "provider_file").unlink()
        await asyncio.wait_for(done.wait(), timeout=10)

        # The declared module's output is marked stale and reported.
        assert stale_paths == [out_dir / "A-MIB.json"]
        assert (out_dir / "A-MIB.json").is_file()  # never deleted

        summary = await task
        assert summary.failed is True  # stale at stop

    async def test_deleted_source_recreated_during_compile_not_stale(self, tmp_path: Path):
        """A transient deletion — the file is recreated while the compile is
        running — is NOT stale: the result carries a live source path for the
        very file that was momentarily gone (issue #38)."""
        mib_dir = tmp_path / "mibs"
        out_dir = tmp_path / "out"
        _write_fixture(mib_dir, {"A-MIB": A_MIB})

        recreated = False
        started = asyncio.Event()
        done = asyncio.Event()
        stale_paths: list[Path] = []

        async def fake_cycle(names: list[str]) -> list[CompileResult]:
            nonlocal recreated
            if not recreated:
                recreated = True
                # The file deleted between polls comes back mid-compile.
                _write_fixture(mib_dir, {"A-MIB": A_MIB})
            return [
                CompileResult(name=n, status="compiled", source_path=mib_dir / "A-MIB")
                for n in names
            ]

        def on_cycle(n: int, results) -> None:
            if n == 1:
                started.set()
            if n >= 2:
                done.set()

        task = asyncio.create_task(
            run_watch(
                fake_cycle,
                initial_names=["A-MIB"],
                mib_dirs=[mib_dir],
                output_dir=out_dir,
                debounce_seconds=0.05,
                poll_interval=0.005,
                max_cycles=2,
                on_cycle=on_cycle,
                on_stale=lambda paths: stale_paths.extend(paths),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        (mib_dir / "A-MIB").unlink()
        await asyncio.wait_for(done.wait(), timeout=10)

        assert stale_paths == []
        summary = await task
        assert summary.failed is False

    async def test_source_changed_during_compile_rearms_debounce(self, tmp_path: Path):
        """The source keeps changing while a cycle compiles — the debounce is
        re-armed so the newest state still gets compiled. The re-armed window
        then produces an empty cycle (no changed paths) that is skipped, and
        the session stops cleanly on stop_event."""
        mib_dir = tmp_path / "mibs"
        out_dir = tmp_path / "out"
        _write_fixture(mib_dir, {"A-MIB": A_MIB})

        writes = 0
        cycle_done = {n: asyncio.Event() for n in (1, 2)}
        stop = asyncio.Event()

        async def fake_cycle(names: list[str]) -> list[CompileResult]:
            nonlocal writes
            writes += 1
            if writes == 2:
                # The second (incremental) cycle changes the file mid-compile.
                _write_changed(mib_dir / "A-MIB", A_MIB_CHANGED)
            return [CompileResult(name=n, status="compiled") for n in names]

        def on_cycle(n: int, results) -> None:
            if n in cycle_done:
                cycle_done[n].set()

        task = asyncio.create_task(
            run_watch(
                fake_cycle,
                initial_names=["A-MIB"],
                mib_dirs=[mib_dir],
                output_dir=out_dir,
                debounce_seconds=0.05,
                poll_interval=0.005,
                stop_event=stop,
                on_cycle=on_cycle,
            )
        )
        await asyncio.wait_for(cycle_done[1].wait(), timeout=10)
        _write_changed(mib_dir / "A-MIB", A_MIB_CHANGED)
        await asyncio.wait_for(cycle_done[2].wait(), timeout=10)

        # Let the re-armed debounce fire its (empty, skipped) cycle, then stop.
        await asyncio.sleep(0.3)
        stop.set()
        summary = await task
        assert summary.cycles_run == 2
        assert summary.modules_recompiled == 1

    async def test_deleted_module_without_result_row_marks_stale(self, tmp_path: Path):
        """A deleted watched file whose module is absent from the cycle's
        results (e.g. the compile returns a differently-declared module) is
        conservatively marked stale — the defensive branch of _mark_stale."""
        mib_dir = tmp_path / "mibs"
        out_dir = tmp_path / "out"
        _write_fixture(mib_dir, {"A-MIB": A_MIB})

        second = False
        started = asyncio.Event()
        done = asyncio.Event()
        stale_paths: list[Path] = []

        async def fake_cycle(names: list[str]) -> list[CompileResult]:
            nonlocal second
            if not second:
                second = True
                return [
                    CompileResult(name=n, status="compiled", source_path=mib_dir / "A-MIB")
                    for n in names
                ]
            # The module vanished from the results entirely.
            return [CompileResult(name="OTHER-MIB", status="compiled")]

        def on_cycle(n: int, results) -> None:
            if n == 1:
                started.set()
            if n >= 2:
                done.set()

        task = asyncio.create_task(
            run_watch(
                fake_cycle,
                initial_names=["A-MIB"],
                mib_dirs=[mib_dir],
                output_dir=out_dir,
                debounce_seconds=0.05,
                poll_interval=0.005,
                max_cycles=2,
                on_cycle=on_cycle,
                on_stale=lambda paths: stale_paths.extend(paths),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        (mib_dir / "A-MIB").unlink()
        await asyncio.wait_for(done.wait(), timeout=10)

        assert stale_paths == [out_dir / "A-MIB.json"]
        summary = await task
        assert summary.failed is True

    async def test_deleted_module_failed_result_keeps_failed(self, tmp_path: Path):
        """A deleted watched file whose result comes back 'failed' (not
        missing/cached) stays failed — the final else branch of _mark_stale."""
        mib_dir = tmp_path / "mibs"
        out_dir = tmp_path / "out"
        _write_fixture(mib_dir, {"A-MIB": A_MIB})

        second = False
        started = asyncio.Event()
        done = asyncio.Event()

        async def fake_cycle(names: list[str]) -> list[CompileResult]:
            nonlocal second
            if not second:
                second = True
                return [
                    CompileResult(name=n, status="compiled", source_path=mib_dir / "A-MIB")
                    for n in names
                ]
            return [CompileResult(name="A-MIB", status="failed", error="boom")]

        def on_cycle(n: int, results) -> None:
            if n == 1:
                started.set()
            if n >= 2:
                done.set()

        task = asyncio.create_task(
            run_watch(
                fake_cycle,
                initial_names=["A-MIB"],
                mib_dirs=[mib_dir],
                output_dir=out_dir,
                debounce_seconds=0.05,
                poll_interval=0.005,
                max_cycles=2,
                on_cycle=on_cycle,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        (mib_dir / "A-MIB").unlink()
        await asyncio.wait_for(done.wait(), timeout=10)

        summary = await task
        assert summary.failed is True

    async def test_keyboard_interrupt_during_poll_returns_summary(self, tmp_path: Path):
        """A KeyboardInterrupt raised while the loop is polling (not during a
        cycle) returns a summary instead of propagating."""
        mib_dir = tmp_path / "mibs"
        out_dir = tmp_path / "out"
        _write_fixture(mib_dir, {"A-MIB": A_MIB})

        async def fake_cycle(names: list[str]) -> list[CompileResult]:
            return [CompileResult(name=n, status="compiled") for n in names]

        async def _boom(*args, **kwargs):
            raise KeyboardInterrupt

        with patch("trishul_smi.watch.asyncio.sleep", new=_boom):
            summary = await run_watch(
                fake_cycle,
                initial_names=["A-MIB"],
                mib_dirs=[mib_dir],
                output_dir=out_dir,
            )

        assert summary.cycles_run == 1  # initial cycle only
        assert summary.modules_recompiled == 0
