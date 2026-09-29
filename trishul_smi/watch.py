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
  debounced cycle, and polled like any other module afterwards.
- Each module is polled at the ACTUAL local path that supplied it
  (``CompileResult.source_path``), never reconstructed from the declared
  name. A misnamed source file (file stem ≠ declared module name) is therefore
  watched and recompiled — with its dependents — like any other module (issue
  C2).
- Failed modules keep their reverse edges (``CompileResult.
  missing_dependencies``): when a newly adopted or restored file supplies a
  previously-missing dependency, the failed module and its transitive
  dependents are recompiled in the next debounced cycle (issue #38).
- Deleting a watched source file invalidates the removed module and its
  dependents. If no remaining reader can supply the module, its output is
  marked STALE and the path is reported via ``on_stale`` — the offline
  compiled-module cache fallback is never treated as a successful watch
  compile for a confirmed local deletion. Existing output files are never
  deleted. Recreating the file re-adopts it through the normal recovery path
  (issue #38).
- The latest result of every watched module is tracked; ``WatchSummary.failed``
  reports whether any module is still failed, missing, or stale when the
  session stops, so the CLI can exit 1 on a normal stop with outstanding
  failures (issue #40). A successful recovery clears the module's state.
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

# CompileResult statuses that count as a successful compile for recovery and
# for the exit contract.
_OK_STATUSES = {"compiled", "cached"}

# Statuses that count against the exit contract (WatchSummary.failed).
_BAD_STATUSES = {"failed", "missing", "stale"}


@dataclass(frozen=True)
class WatchSummary:
    """Result of a watch session."""

    cycles_run: int
    """Total compile cycles: the initial full compile plus every incremental one."""
    modules_recompiled: int
    """Total modules compiled in incremental cycles: change-triggered
    recompiles (invalidation-set sizes) plus adopted new files."""
    failed: bool = False
    """True when any watched module is still failed, missing, or stale at stop
    time (exit contract, issue #40). The CLI exits 1 on a normal stop with
    failures, 0 otherwise. State at stop, not history: a module that failed
    early and later recovered clears its entry, so it does NOT keep the
    session at exit 1."""


CompileCycle = Callable[[list[str]], Awaitable[list[CompileResult]]]
CycleHandler = Callable[[int, list[CompileResult]], None]
NewFilesHandler = Callable[[list[str]], None]
StaleHandler = Callable[[list[Path]], None]


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
    """Best-effort: locate the source file FileReader would serve for *name*.

    Used ONLY as a fallback for modules the compile pipeline could not
    attribute a path to (``CompileResult.source_path`` is None — e.g. a fake
    cycle or a module served by a non-local reader). Real modules always
    carry their actual source path, so a misnamed file (stem != declared
    name) is never reconstructed from its declared name here: the pipeline's
    reported path wins because this lookup only fills gaps.
    """
    for directory in mib_dirs:
        for ext in _EXTENSIONS:
            candidate = directory / f"{name}{ext}"
            if candidate.is_file():
                return candidate
    return None


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


def _imports_from_source_path(path: Path | None) -> set[str] | None:
    """Approximate a module's imports from its source text (non-JSON fallback)."""
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return set(_IMPORTS_FROM_RE.findall(text))


def _load_import_graph(
    output_dir: Path,
    watched: dict[str, Path],
    names: list[str],
) -> dict[str, set[str]]:
    """module name → set of imported module names, for the last resolved closure."""
    graph: dict[str, set[str]] = {}
    for name in names:
        imports = _imports_from_json(output_dir, name)
        if imports is None:
            imports = _imports_from_source_path(watched.get(name))
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


def _retry_set(supplied: set[str], failed_deps: dict[str, set[str]]) -> set[str]:
    """Failed modules that a successful compile now unblocks (issue #38).

    *supplied* is the set of modules that just compiled successfully. Every
    failed module that listed one of them in ``missing_dependencies`` can be
    retried, and the closure continues over the failed reverse edges: a
    retried module may itself unblock its own dependents, so they are pulled
    into the same retry batch.
    """
    retry: set[str] = set()
    queue = list(supplied)
    while queue:
        dep = queue.pop()
        for module, missing in failed_deps.items():
            if dep in missing and module not in retry:
                retry.add(module)
                queue.append(module)
    return retry


def _notice_new_files(
    mib_dirs: list[Path],
    watched_names: set[str],
    noticed: set[str],
    on_new_files: NewFilesHandler | None,
) -> list[str]:
    """Detect MIB-looking files that appeared in --mib-dir mid-watch.

    Returns the newly-seen stems (and adds them to *noticed* so a repeated
    scan never reports them again, even if adoption fails). *watched_names* is
    the set of modules already tracked with a source file — a file whose stem
    is already watched (an existing module, a misnamed file's alias, or a
    module whose recreation the poll already detects) is not a new arrival.
    A module that was MISSING in an earlier cycle has no watched file, so its
    file appearing mid-watch IS adopted — this is the issue #38 recovery
    path for a previously-missing dependency. The caller adopts the returned
    stems into the watch set; adoption compiles them through the normal cycle
    path.
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
            if stem not in watched_names and stem not in noticed:
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
    on_stale: StaleHandler | None = None,
) -> WatchSummary:
    """Run a watch session: initial compile, then debounced mtime polling over
    the resolved closure.

    The initial compile covers *initial_names*; afterwards the loop polls the
    source files of the cumulative closure (every module ever resolved, across
    all cycles — modules never leave the watch set). When the file set stops
    changing for *debounce_seconds*, the changed modules plus their transitive
    dependents are recompiled and *on_cycle* is invoked with the results.

    Recovery (issue #38): failed modules keep their missing-dependency reverse
    edges; a newly adopted or restored file that supplies a missing dependency
    schedules the failed module and its transitive dependents for the next
    debounced cycle. Deleting a watched file invalidates it and its dependents;
    if no reader supplies the removed module, its output is marked stale and
    reported via *on_stale* (never deleted).

    Stopping:
    - Ctrl-C (KeyboardInterrupt) returns normally with a ``WatchSummary``.
    - *max_cycles* bounds the total number of compile cycles (including the
      initial one).
    - Setting *stop_event* ends the session after the current cycle.
    - ``WatchSummary.failed`` is True when any module is still failed, missing,
      or stale at stop (exit contract, issue #40).
    """
    if not initial_names:
        raise ValueError("run_watch() requires at least one MIB name")
    if max_cycles is not None and max_cycles < 1:
        raise ValueError("max_cycles must be >= 1")

    cycles_run = 0
    modules_recompiled = 0
    graph: dict[str, set[str]] = {}
    # declared module name → ACTUAL local source path, from each cycle's
    # CompileResult.source_path (never reconstructed from the declared name —
    # issue C2). A path whose file is temporarily missing is retained so a
    # later recreation is still detected by polling.
    watched: dict[str, Path] = {}
    # Cumulative closure: every module name ever seen in any resolved closure
    # (initial full compile + every incremental cycle). The watch set and the
    # dependency graph are driven from this union — never from the LAST cycle's
    # results — so a module that one invalidation cycle happened not to
    # re-resolve stays polled and its dependents stay discoverable.
    known_names: set[str] = set(initial_names)
    # Stems that must never be announced as "new": every explicit request
    # (a requested name is never a new arrival, even when its file is
    # misnamed and the module tracks under another name) plus every stem
    # ever adopted. Prevents re-announcement loops for alias names.
    noticed: set[str] = set(initial_names)
    # Stems adopted mid-watch whose initial compile is still pending the
    # debounce window (folded into the next cycle's invalidation set).
    pending_adopt: list[str] = []
    # Failed module → the dependency names it could not resolve. Reverse edges
    # retained across cycles so a later-adopted dependency unblocks the module
    # and its transitive dependents (issue #38).
    failed_deps: dict[str, set[str]] = {}
    # Failed modules scheduled for a recompile because a dependency appeared.
    pending_retry: set[str] = set()
    # Latest result status per watched module: "compiled"/"cached"/"failed"/
    # "missing"/"stale". Drives the exit contract (WatchSummary.failed).
    latest: dict[str, str] = {}
    # Timestamp of the last detected change (file edit or new-file arrival);
    # a compile fires only after the debounce window has elapsed.
    last_change: float | None = None

    def _current_summary() -> WatchSummary:
        problems = {name for name, status in latest.items() if status in _BAD_STATUSES}
        # Names that entered the watch set but never produced a result (e.g. a
        # file adopted right before a stop, whose debounced initial compile
        # never ran) were never compiled — they count as outstanding failures.
        # Aliases of misnamed files (migrated to their declared name by
        # _process_cycle_results) are excluded: they are tracked under their
        # real name, and excluding them prevents a phantom exit-1.
        pending = {
            name
            for name in known_names - latest.keys()
            if name in watched or name in pending_adopt or name in pending_retry
        }
        problems.update(pending)
        return WatchSummary(
            cycles_run=cycles_run,
            modules_recompiled=modules_recompiled,
            failed=bool(problems),
        )

    def _process_cycle_results(results: list[CompileResult]) -> None:
        """Fold a cycle's results into the persistent watch state."""
        for r in results:
            known_names.add(r.name)
            if r.source_path is not None:
                # If this path is currently watched under a different name —
                # the requested/alias name of a misnamed file — migrate the
                # tracking to the declared name so the alias cannot linger as
                # a phantom failure in the exit contract.
                for other in [n for n, p in watched.items() if p == r.source_path and n != r.name]:
                    watched.pop(other, None)
                    latest.pop(other, None)
                    failed_deps.pop(other, None)
                watched[r.name] = r.source_path
            if r.status == "failed" and r.missing_dependencies:
                # Retain the reverse edges of failed modules (issue #38): a
                # later-adopted dependency unblocks them and their dependents.
                failed_deps[r.name] = set(r.missing_dependencies)
            elif r.status in _OK_STATUSES:
                failed_deps.pop(r.name, None)
            latest[r.name] = r.status

    def _refresh_watch_state() -> tuple[
        dict[str, set[str]], dict[str, Path], dict[Path, tuple[int, int] | None]
    ]:
        """Rebuild the graph / watched set / baseline over the cumulative
        closure. Cheap: graph edges come from the emitted JSON (or a source
        scan of the watched path), and baseline entries are plain stat
        signatures.

        Watched paths come from the compile pipeline's ``source_path``
        (never reconstructed from the declared name — issue C2). Names the
        pipeline could not attribute a path to (fake cycles, non-local
        readers) fall back to a best-effort name lookup so their files are
        still polled. The fallback skips a file already watched under
        another name (the alias of a misnamed module) — the real module owns
        that path.
        """
        graph = _load_import_graph(output_dir, watched, sorted(known_names))
        watched_paths = set(watched.values())
        for name in known_names:
            if name not in watched:
                path = _find_source_file(mib_dirs, name)
                if path is not None and path not in watched_paths:
                    watched[name] = path
                    watched_paths.add(path)
        baseline = {path: _stat_sig(path) for path in watched.values()}
        return graph, watched, baseline

    def _mark_stale(
        results_by_name: dict[str, CompileResult],
        deleted_paths: set[Path],
    ) -> None:
        """Mark outputs stale for watched files confirmed deleted.

        The offline compiled-module cache fallback must not silently turn a
        confirmed local deletion into a successful watch compile (issue #38):
        a deleted module served as ``cached`` without any live local source is
        stale, not compiled. Output files are never deleted — the stale marker
        only affects tracking and the exit contract.
        """
        stale_outputs: list[Path] = []
        for name, path in watched.items():
            if path not in deleted_paths:
                continue
            result = results_by_name.get(name)
            if result is None:
                # Not re-resolved this cycle (should not happen — deletion
                # invalidates the module) — conservatively mark stale.
                latest[name] = "stale"
                stale_outputs.append(output_dir / f"{name}.json")
                continue
            if result.source_path is not None:
                # A live local file now supplies the module (e.g. a second
                # --mib-dir) — the deletion is not a problem.
                latest[name] = result.status
                continue
            if result.status in {"cached", "missing"}:
                # Offline cache fallback (cached, no live source) or no source
                # at all: the output can no longer be refreshed.
                latest[name] = "stale"
                stale_outputs.append(output_dir / f"{name}.json")
            else:
                latest[name] = result.status
        if stale_outputs and on_stale is not None:
            on_stale(stale_outputs)

    def _adopt_new_files() -> None:
        """Scan for new MIB-looking files and adopt them into the watch set.

        Detection is idempotent: a stem joins ``known_names`` (and
        ``noticed``) the first time it is seen, so it is never announced or
        compiled twice. The initial compile of an adopted module is debounced
        like a file change — the stem is folded into the next cycle's
        invalidation set — so a half-written new file settles before its first
        parse. Adoption follows the same stem-dedup rule as --mib-dir
        discovery: the first directory that provides a source file for the
        stem wins (the compile pipeline's reader chain applies the same order).
        """
        new_stems = _notice_new_files(mib_dirs, set(watched), noticed, on_new_files)
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
    _process_cycle_results(results)
    graph, watched, baseline = _refresh_watch_state()
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
        deleted_paths = {
            path for path in baseline if snap[path] is None and baseline[path] is not None
        }
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
        if pending_retry:
            # Failed modules whose missing dependency just appeared — fold
            # them into this cycle (their dependents are transitively included
            # via _retry_set, so nothing is missed).
            names |= pending_retry
            pending_retry.clear()
        if not names:
            # A watched file no longer maps to a known module (e.g. renamed
            # between polls) — nothing to recompile this cycle.
            continue

        # Request each module by the STEM of its watched source file: the
        # compile pipeline fetches by name, so a misnamed file (stem !=
        # declared name) must be requested under its real stem or the fetch
        # misses it. For a normally-named file the stem equals the declared
        # name. Names without a watched path (missing modules, adopted stems
        # awaiting their first compile) are requested as-is.
        request_names = sorted(watched[name].stem if name in watched else name for name in names)

        try:
            results = await compile_cycle(request_names)
        except KeyboardInterrupt:
            return _current_summary()
        cycles_run += 1
        modules_recompiled += len(names)
        if on_cycle is not None:
            on_cycle(cycles_run, results)
        _process_cycle_results(results)
        if deleted_paths:
            _mark_stale({r.name: r for r in results}, deleted_paths)
        graph, watched, baseline = _refresh_watch_state()
        prev_snap = baseline
        _adopt_new_files()
        # Recovery (issue #38): a module that just compiled may supply a
        # previously-missing dependency — retry the failed modules and their
        # transitive dependents in the next debounced cycle.
        supplied = {r.name for r in results if r.status in _OK_STATUSES}
        retry = _retry_set(supplied, failed_deps)
        if retry:
            pending_retry.update(retry)
            last_change = time.monotonic()
        if any(path in snap and snap[path] != baseline[path] for path in baseline):
            # The source kept changing while we were compiling — re-arm the
            # debounce so the newest state still gets compiled.
            last_change = time.monotonic()

    return _current_summary()
