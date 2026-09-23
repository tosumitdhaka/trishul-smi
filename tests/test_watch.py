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
    mib_dir: Path, out_dir: Path, cache_dir: Path
) -> tuple[MibCompiler, CountingParser]:
    """MibCompiler over a tmp mib-dir with a counting parser injected."""
    parsers: list[CountingParser] = []

    def make_parser() -> CountingParser:
        p = CountingParser()
        parsers.append(p)
        return p

    with patch("trishul_smi.compiler.SmiParser", make_parser):
        config = CompilerConfig(output_dir=out_dir, cache_dir=cache_dir, cache_ttl_days=0)
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
# Engine: new files in --mib-dir are noticed, not watched (v1)
# ---------------------------------------------------------------------------


async def test_new_file_in_mib_dir_noticed_not_crashing(tmp_path: Path):
    mib_dir = tmp_path / "mibs"
    out_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    _write_fixture(mib_dir, {"A-MIB": A_MIB})
    compiler, parser = _build_compiler(mib_dir, out_dir, cache_dir)

    started = asyncio.Event()
    new_seen = asyncio.Event()
    new_files: list[str] = []
    stop = asyncio.Event()

    def on_cycle(n: int, results) -> None:
        if n == 1:
            started.set()

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
        max_cycles=10,
        on_cycle=on_cycle,
        on_new_files=on_new_files,
        stop_event=stop,
    )
    await started.wait()

    new_mib = C_MIB.replace("C-MIB", "NEW-MIB").replace("cMIB", "newMIB")
    _write_fixture(mib_dir, {"NEW-MIB": new_mib})
    await asyncio.wait_for(new_seen.wait(), timeout=5)
    assert "NEW-MIB" in new_files

    stop.set()
    summary = await task
    assert summary.cycles_run == 1  # the new file never triggered a compile


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
