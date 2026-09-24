"""Watch-mode engine for the MIB compiler.

``run_watch()`` drives debounced mtime polling of the source files that
participated in the last compile, then recompiles only the changed module and
its transitive dependents. The dependency graph is rebuilt after every cycle
from the emitted JSON module files (the ``imports`` section), so the reverse
edges — module → who imports it — come straight from the last resolve rather
than a second parse.

Design notes
------------
- Polling + invalidation logic lives here, NOT in cli/main.py, so tests can
  drive the loop with an injected debounce interval and a stop condition
  (``max_cycles`` or ``stop_event``) and never sleep real time.
- No new runtime dependency: ``asyncio.sleep`` polling over ``(mtime_ns, size)``
  stat signatures of the watched source files.
- Recompile cycles re-request only the invalidation set (changed module +
  transitive dependents) through the same compile pipeline. Unchanged modules
  in that set's closure are served by the fingerprinted compiled-module cache
  (never re-parsed); modules outside the closure are never re-requested, so
  their output files are left byte-untouched.
- The changed module's own dependencies are re-emitted as part of the closure
  but never re-parsed (fingerprint cache hit) — their output bytes do not
  change.
- Watched set: the CUMULATIVE closure — the union of every module name (and
  its source file) ever observed in any resolved closure, from the initial
  full compile onward. A module never leaves the watch set because one cycle's
  invalidation set happened not to include it. New MIB-looking files appearing
  in ``--mib-dir`` mid-watch are ADOPTED: announced once via ``on_new_files``,
  folded into the cumulative closure, given an initial compile in the next
  debounced cycle, and polled like any other module afterwards. File removal
  stays notice-only (deletion handling is a separate design question).
- Misnamed source files (file stem ≠ declared module name) are tracked under
  their declared name; if no file matches the declared name, the module is not
  polled (v1 limitation).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from trishul_smi.models import CompileResult

# Mirror of FileReader's extension search order (reader/localfile.py). Keep in
# sync if that list ever changes.
_EXTENSIONS = ["", ".mib", ".txt", ".my"]

# Fallback import extraction used only when the emitted output has no JSON
# module files (e.g. a non-json format). The JSON graph is exact; this regex
# only approximates the IMPORTS clause — comments/strings may add false edges,
# which is safe: over-invalidation only costs a redundant recompile.
_IMPORTS_FROM_RE = re.compile(r"\bFROM\s+([A-Za-z0-9_.-]+)\b")


@dataclass(frozen=True)
class WatchSummary:
    """Result of a watch session."""

    cycles_run: int
    """Total compile cycles: the initial full compile plus every incremental one."""
    modules_recompiled: int
    """Total modules compiled in incremental cycles: change-triggered
    recompiles (invalidation-set sizes) plus adopted new files."""


CompileCycle = Callable[[list[str]], Awaitable[list[CompileResult]]]
CycleHandler = Callable[[int, list[CompileResult]], None]
NewFilesHandler = Callable[[list[str]], None]


def _stat_sig(path: Path) -> tuple[int, int] | None:
    """(mtime_ns, size) snapshot of *path*; None when it vanished.

    Size is part of the signature because some filesystems (e.g. ext2/ext3,
    overlay mounts) coalesce rapid writes onto one mtime tick — a fast save
    within the same tick as the baseline would otherwise be invisible to
    mtime polling alone. A same-size rewrite in the same tick can still be
    missed; that is a documented filesystem-granularity limitation.
    """
    try:
        st = path.stat()
    except OSError:
        # Deleted or unreadable — a change worth noticing.
        return None
    return st.st_mtime_ns, st.st_size


def _find_source_file(mib_dirs: list[Path], name: str) -> Path | None:
    """Locate the source file FileReader would serve for *name*."""
    for directory in mib_dirs:
        for ext in _EXTENSIONS:
            candidate = directory / f"{name}{ext}"
            if candidate.is_file():
                return candidate
    return None


def _collect_sources(
    mib_dirs: list[Path],
    names: list[str],
    *,
    previous: dict[str, Path] | None = None,
) -> dict[str, Path]:
    """name → source file path, for every name with a local source file.

    *previous* carries paths recorded on an earlier cycle: a name whose source
    file is temporarily missing keeps its last-known path, so a later
    recreation of that file is still detected by polling.
    """
    sources: dict[str, Path] = {}
    for name in names:
        path = _find_source_file(mib_dirs, name)
        if path is None and previous is not None:
            path = previous.get(name)
        if path is not None:
            sources[name] = path
    return sources


def _imports_from_json(output_dir: Path, name: str) -> set[str] | None:
    """Read the ``imports`` section from the emitted module JSON, if any."""
    path = output_dir / f"{name}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    imports = data.get("imports")
    if isinstance(imports, dict):
        return set(imports)
    return None


def _imports_from_source(mib_dirs: list[Path], name: str) -> set[str] | None:
    """Approximate a module's imports from its source text (non-JSON fallback)."""
    path = _find_source_file(mib_dirs, name)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return set(_IMPORTS_FROM_RE.findall(text))


def _load_import_graph(
    output_dir: Path,
    mib_dirs: list[Path],
    names: list[str],
) -> dict[str, set[str]]:
    """module name → set of imported module names, for the last resolved closure."""
    graph: dict[str, set[str]] = {}
    for name in names:
        imports = _imports_from_json(output_dir, name)
        if imports is None:
            imports = _imports_from_source(mib_dirs, name)
        if imports:
            graph[name] = imports
    return graph


def _transitive_dependents(changed: set[str], graph: dict[str, set[str]]) -> set[str]:
    """Close *changed* under reverse import edges: every module that
    transitively imports a changed module."""
    importers: dict[str, set[str]] = {}
    for module, deps in graph.items():
        for dep in deps:
            importers.setdefault(dep, set()).add(module)

    closure = set(changed)
    queue = list(changed)
    while queue:
        dep = queue.pop()
        for importer in importers.get(dep, ()):
            if importer not in closure:
                closure.add(importer)
                queue.append(importer)
    return closure


def _notice_new_files(
    mib_dirs: list[Path],
    known: set[str],
    noticed: set[str],
    on_new_files: NewFilesHandler | None,
) -> list[str]:
    """Detect MIB-looking files that appeared in --mib-dir mid-watch.

    Returns the newly-seen stems (and adds them to *noticed* so a repeated
    scan never reports them again, even if adoption fails). *known* is the
    union of every module name ever seen in a resolved closure, so a file that
    merely drops out of the last cycle's closure (an incremental cycle only
    resolves the invalidation set) is not mistaken for a new arrival. The
    caller adopts the returned stems into the watch set; adoption compiles
    them through the normal cycle path.
    """
    new: list[str] = []
    for directory in mib_dirs:
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for f in entries:
            if not f.is_file():
                continue
            if f.suffix.lower() not in {"", ".mib", ".txt", ".my"}:
                continue
            stem = f.stem
            if stem not in known and stem not in noticed:
                noticed.add(stem)
                new.append(stem)
    if new and on_new_files is not None:
        on_new_files(sorted(new))
    return new


async def run_watch(
    compile_cycle: CompileCycle,
    *,
    initial_names: list[str],
    mib_dirs: list[Path],
    output_dir: Path,
    debounce_seconds: float = 0.3,
    poll_interval: float = 0.05,
    max_cycles: int | None = None,
    stop_event: asyncio.Event | None = None,
    on_cycle: CycleHandler | None = None,
    on_new_files: NewFilesHandler | None = None,
) -> WatchSummary:
    """Run a watch session: initial compile, then debounced mtime polling over
    the resolved closure.

    The initial compile covers *initial_names*; afterwards the loop polls the
    source files of the cumulative closure (every module ever resolved, across
    all cycles — modules never leave the watch set). When the file set stops
    changing for *debounce_seconds*, the changed modules plus their transitive
    dependents are recompiled and *on_cycle* is invoked with the results.

    Stopping:
    - Ctrl-C (KeyboardInterrupt) returns normally with a ``WatchSummary``.
    - *max_cycles* bounds the total number of compile cycles (including the
      initial one).
    - Setting *stop_event* ends the session after the current cycle.
    """
    if not initial_names:
        raise ValueError("run_watch() requires at least one MIB name")
    if max_cycles is not None and max_cycles < 1:
        raise ValueError("max_cycles must be >= 1")

    cycles_run = 0
    modules_recompiled = 0
    graph: dict[str, set[str]] = {}
    watched: dict[str, Path] = {}
    # Cumulative closure: every module name ever seen in any resolved closure
    # (initial full compile + every incremental cycle). The watch set and the
    # dependency graph are driven from this union — never from the LAST cycle's
    # results — so a module that one invalidation cycle happened not to
    # re-resolve stays polled and its dependents stay discoverable.
    known_names: set[str] = set(initial_names)
    noticed: set[str] = set()
    # Stems adopted mid-watch whose initial compile is still pending the
    # debounce window (folded into the next cycle's invalidation set).
    pending_adopt: list[str] = []
    # Timestamp of the last detected change (file edit or new-file arrival);
    # a compile fires only after the debounce window has elapsed.
    last_change: float | None = None

    def _current_summary() -> WatchSummary:
        return WatchSummary(cycles_run=cycles_run, modules_recompiled=modules_recompiled)

    def _refresh_watch_state(
        previous_watched: dict[str, Path],
    ) -> tuple[dict[str, set[str]], dict[str, Path], dict[Path, tuple[int, int] | None]]:
        """Rebuild the graph / watched set / baseline over the cumulative
        closure. Cheap: graph edges come from the emitted JSON (or a source
        scan), and baseline entries are plain stat signatures."""
        graph = _load_import_graph(output_dir, mib_dirs, sorted(known_names))
        watched = _collect_sources(mib_dirs, sorted(known_names), previous=previous_watched)
        baseline = {path: _stat_sig(path) for path in watched.values()}
        return graph, watched, baseline

    def _adopt_new_files() -> None:
        """Scan for new MIB-looking files and adopt them into the watch set.

        Detection is idempotent: a stem joins ``known_names`` (and
        ``noticed``) the first time it is seen, so it is never announced or
        compiled twice. The initial compile of an adopted module is debounced
        like a file change — the stem is folded into the next cycle's
        invalidation set — so a half-written new file settles before its first
        parse. Adoption follows the same stem-dedup rule as --mib-dir
        discovery: the first directory that provides a source file for the
        stem wins (``_find_source_file`` iterates the dirs in order).
        """
        new_stems = _notice_new_files(mib_dirs, known_names, noticed, on_new_files)
        if new_stems:
            known_names.update(new_stems)
            pending_adopt.extend(new_stems)
            # A new arrival is a change event too: re-arm the debounce so the
            # file's state settles before its initial compile.
            nonlocal last_change
            last_change = time.monotonic()

    # --- Initial cycle ---
    try:
        results = await compile_cycle(list(initial_names))
    except KeyboardInterrupt:
        return _current_summary()
    cycles_run += 1
    if on_cycle is not None:
        on_cycle(cycles_run, results)
    known_names.update(r.name for r in results)
    graph, watched, baseline = _refresh_watch_state(watched)
    prev_snap = baseline
    _adopt_new_files()

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if max_cycles is not None and cycles_run >= max_cycles:
            break

        _adopt_new_files()

        try:
            await asyncio.sleep(poll_interval)
        except KeyboardInterrupt:
            return _current_summary()

        snap = {path: _stat_sig(path) for path in baseline}
        if snap != prev_snap:
            last_change = time.monotonic()
        prev_snap = snap

        if last_change is None:
            continue
        if time.monotonic() - last_change < debounce_seconds:
            continue

        # Stable for the debounce window — recompile the invalidation set.
        last_change = None
        changed_paths = [path for path in baseline if snap[path] != baseline[path]]
        changed_names = {name for name, path in watched.items() if path in changed_paths}
        names = _transitive_dependents(changed_names, graph)
        if pending_adopt:
            # Fold adopted new modules into this cycle's compile. They never
            # participated in the last graph, so no reverse edges can
            # invalidate anything that imports them (nothing does yet) — the
            # stems are compiled as-is and their dependencies resolve through
            # the normal compile pipeline.
            names |= set(pending_adopt)
            pending_adopt.clear()
        if not names:
            # A watched file no longer maps to a known module (e.g. renamed
            # between polls) — nothing to recompile this cycle.
            continue

        try:
            results = await compile_cycle(sorted(names))
        except KeyboardInterrupt:
            return _current_summary()
        cycles_run += 1
        modules_recompiled += len(names)
        if on_cycle is not None:
            on_cycle(cycles_run, results)
        known_names.update(r.name for r in results)
        graph, watched, baseline = _refresh_watch_state(watched)
        prev_snap = baseline
        _adopt_new_files()
        if any(path in snap and snap[path] != baseline[path] for path in baseline):
            # The source kept changing while we were compiling — re-arm the
            # debounce so the newest state still gets compiled.
            last_change = time.monotonic()

    return _current_summary()
