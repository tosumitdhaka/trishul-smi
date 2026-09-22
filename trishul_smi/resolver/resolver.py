"""MibResolver: fetch, parse, and order MIB modules by dependency.

Architecture
------------
The resolver performs a BFS over the MIB import graph:

    1. Start with the requested MIB name(s).
    2. Fetch the raw text for every pending MIB *concurrently* via
       asyncio.gather.
    3. Check the compiled cache (MibCache) with the sha256 fingerprint of the
       fetched text — a hit skips parsing. The source is always fetched so an
       updated file can never serve a stale entry; the cache saves parsing
       only (issue #12).
    4. Parse each cache-missing text off the event-loop thread via
       ``asyncio.to_thread`` (issue #19; the per-thread Lark parser cache in
       smi_parser.py exists for exactly this).
    5. Collect imports from every module resolved in the current wave
       (cache hits and newly fetched modules); add unseen names to the next
       wave.
    6. Repeat until the import closure is complete.
    7. Topological-sort (Kahn's) the full set and return in order.

Error handling
--------------
- Fetch/parse failures are collected per-module and reported together
  as .errors on ResolveResult rather than aborting mid-run.
- MibSizeLimitError propagates immediately (it is a configuration error).
- CircularDependencyError propagates immediately (uncaught from
  topological_sort — no try/except wrapper needed).
- Failed modules' transitive dependencies are not explored: if module A
  fails to fetch or parse, A's imports are never queued. Callers should
  not assume all reachable dependencies will appear in ResolveResult.errors.
- A true source not-found (MibNotFoundError) first checks the disk cache:
  a warm (non-expired) entry is served with a "source unavailable" warning,
  so offline/air-gapped compiles keep working (L1). Only genuine not-found
  gets this fallback — transport failures (NetworkError and friends) always
  surface as per-module errors and never mask stale cache behind a
  reachable-but-broken source.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.parser._constants import BASE_MIBS
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import FetchProtocol
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.dependency import topological_sort


def _source_fingerprint(text: str) -> str:
    """sha256 hex digest of the raw source text (issue #12).

    The digest is the cache's second key factor: entries are name-keyed but
    additionally record the fingerprint of the text they were parsed from, so
    an updated MIB file can never be served stale content.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ResolveResult:
    """Returned by MibResolver.resolve()."""

    modules: list[MibModule] = field(default_factory=list)
    """All successfully resolved modules in dependency order."""
    errors: dict[str, Exception] = field(default_factory=dict)
    """Modules that failed to fetch or parse: {mib_name: exception}."""
    aliases: dict[str, str] = field(default_factory=dict)
    """Requested name -> declared name for misnamed MIB files (issue #21).

    ``modules`` is keyed (conceptually) by declared name; entries here map a
    name the caller/reader used to the declared name the module actually
    carries, so callers can tell explicit requests from transitive deps even
    when the requested name and declared name differ.
    """
    cached: set[str] = field(default_factory=set)
    """Declared names served from the disk cache this run (issue #15).

    These modules were loaded from ``MibCache`` rather than re-parsed, so
    callers can surface a distinct ``cached`` status instead of ``compiled``.
    Includes modules served by the offline fallback (a warm cache entry used
    because the source was unreachable — L1).
    """

    @property
    def ok(self) -> bool:
        """True when no errors occurred."""
        return not self.errors


class MibResolver:
    """Resolves a set of MIB names to a topologically-ordered list of
    MibModule objects, fetching transitive dependencies automatically.

    Args:
        reader: Any object satisfying FetchProtocol (AbstractReader subclass
                or ReaderChain). Typed as FetchProtocol so mypy validates the
                .fetch() contract without requiring inheritance.
        parser: A SmiParser instance (create once, reuse — grammar is cached).
        cache:  Optional MibCache. When provided, compiled modules are read
                from / written to disk, skipping re-parse on subsequent runs
                (the source is still fetched so its content fingerprint can
                be checked — issue #12). When the source is unreachable
                (MibNotFoundError), a warm cache entry is served instead of
                failing the compile (offline fallback, L1).
    """

    def __init__(
        self,
        reader: FetchProtocol,
        parser: SmiParser,
        cache: MibCache | None = None,
    ) -> None:
        self._reader = reader
        self._parser = parser
        self._cache = cache

    def _record_module(
        self,
        fetched: dict[str, MibModule],
        declared_by: dict[str, str],
        resolved_this_wave: set[str],
        requested: str,
        module: MibModule,
    ) -> None:
        """Key a resolved module by its DECLARED name, warning on duplicates.

        Two requested files can declare the same module name (issue #25): the
        later one silently wins today, dropping the first file's content.
        Emit a collision warning on the surviving module (same style as the
        ``_reconcile_name`` mismatch warning), naming the discarded requested
        file, and record which requested file filled each declared name.
        """
        if module.name in fetched:
            discarded = declared_by.get(module.name, "another requested file")
            warning = (
                f"Module name collision: both {discarded!r} and {requested!r} "
                f"declare {module.name!r}; using {requested!r} and discarding "
                f"{discarded!r}."
            )
            if warning not in module.warnings:
                module.warnings.append(warning)
        fetched[module.name] = module
        declared_by[module.name] = requested
        resolved_this_wave.add(module.name)

    def _reconcile_name(
        self,
        aliases: dict[str, str],
        requested: str,
        module: MibModule,
    ) -> None:
        """Record a requested->declared alias when a file is misnamed.

        Misnamed MIB files (file fetched as ``requested`` whose ASN.1 header
        declares ``module.name``) are the root cause of issue #21: dependents
        importing the *declared* name miss the module in ``fetched`` and cause
        a phantom fetch. Recording the alias here lets dependency discovery,
        cache keying, output naming, and ``is_dependency`` all agree on the
        declared name.

        The mismatch warning is appended to ``module.warnings`` so it surfaces
        through CompileResult.warnings in the CLI (MibModule.warnings is
        round-tripped through MibCache, and the dedupe guard keeps a cached
        module from accumulating a duplicate warning on a later run).
        """
        if module.name == requested:
            return
        aliases[requested] = module.name
        warning = (
            f"Module requested as {requested!r} but declares itself as "
            f"{module.name!r}; using the declared name."
        )
        if warning not in module.warnings:
            module.warnings.append(warning)

    async def resolve(self, mib_names: list[str]) -> ResolveResult:
        """Fetch and parse ``mib_names`` and all transitive dependencies.

        Returns:
            ResolveResult with .modules in topological order, .errors
            for anything that failed, and .cached for names served from the
            disk cache.
        """
        fetched: dict[str, MibModule] = {}
        errors: dict[str, Exception] = {}
        cached: set[str] = set()
        # Explicit requests are always honoured; BASE_MIBS filter applies only
        # to transitive dependency resolution (line 146) so that well-known
        # infrastructure MIBs are skipped when pulled in as deps but still
        # compiled when the user explicitly asks for them.
        pending: set[str] = set(mib_names)
        # requested name -> declared name for misnamed MIB files. `fetched` is
        # keyed by DECLARED name so imports of the declared name match; this
        # map preserves the requested name for is_dependency / cache aliasing.
        aliases: dict[str, str] = {}
        # declared module name -> requested file that supplied it (issue #25).
        declared_by: dict[str, str] = {}

        while pending:
            # --- Queue the wave: fetch raw text first (issue #12) ---
            resolved_this_wave: set[str] = set()
            still_pending: set[str] = set(pending)

            if still_pending:
                # --- Concurrent fetch, then parse deterministically ---
                names_ordered = sorted(still_pending)
                fetch_results = await asyncio.gather(
                    *[self._reader.fetch(name) for name in names_ordered],
                    return_exceptions=True,
                )

                # strict=True: asyncio.gather always returns exactly one
                # result per coroutine, so a length mismatch would be a bug —
                # fail loudly.
                for name, result in zip(names_ordered, fetch_results, strict=True):
                    if name in resolved_this_wave:
                        # A file earlier in this wave declared this requested
                        # name (e.g. "A" declares B-MIB while B-MIB was also
                        # requested and cannot be fetched). The declared-name
                        # copy is authoritative — skip so we do not report the
                        # module as both compiled and missing (issue #25).
                        # A successfully-fetched file skipped here would
                        # otherwise have its content silently discarded
                        # (first-wins, no warning) — inconsistent with the
                        # parse path, which warns and is last-wins. Surface a
                        # collision warning on the surviving module (L2).
                        if isinstance(result, str):
                            survivor = fetched[name]
                            supplier = declared_by.get(name, "another requested file")
                            warning = (
                                f"Module requested as {name!r} was fetched but "
                                f"discarded; {name!r} was already supplied by "
                                f"{supplier!r}."
                            )
                            if warning not in survivor.warnings:
                                survivor.warnings.append(warning)
                        continue
                    if isinstance(result, MibSizeLimitError):
                        # Propagate immediately — size limit is a config
                        # error, not a per-module failure. Use `raise result`
                        # (not bare `raise`) because
                        # asyncio.gather(return_exceptions=True) returns
                        # exceptions as *values*, not as the active exception
                        # — bare `raise` would hit RuntimeError:
                        # "No active exception to re-raise".
                        raise result
                    elif isinstance(result, Exception):
                        # A genuine not-found first checks the disk cache:
                        # when the source is unreachable but a warm (non-
                        # expired) entry exists, serve it rather than failing
                        # the compile (offline fallback, L1). The fingerprint
                        # check is intentionally skipped — there is no fetched
                        # source to fingerprint, and the TTL still applies.
                        # Transport failures (NetworkError and friends) are
                        # NOT not-found: a reachable-but-broken source must
                        # never be masked by stale cache, so they fall through
                        # to the per-module error collection unchanged.
                        if isinstance(result, MibNotFoundError) and self._cache is not None:
                            cached_module = self._cache.get(name)
                            if cached_module is not None:
                                warning = f"serving cached {name!r}; source unavailable"
                                if warning not in cached_module.warnings:
                                    cached_module.warnings.append(warning)
                                self._record_module(
                                    fetched,
                                    declared_by,
                                    resolved_this_wave,
                                    name,
                                    cached_module,
                                )
                                self._reconcile_name(aliases, name, cached_module)
                                cached.add(cached_module.name)
                                continue
                        errors[name] = result
                    elif isinstance(result, BaseException):
                        # KeyboardInterrupt / SystemExit must not be silently
                        # collected — re-raise so the process can exit
                        # cleanly.
                        raise result
                    else:
                        # Fingerprint the fetched text and consult the cache
                        # BEFORE parsing: a hit (fingerprint matches) skips the
                        # expensive parse. The source is always fetched so an
                        # updated file can never serve a stale entry.
                        fingerprint = _source_fingerprint(result)
                        if self._cache is not None:
                            cached_module = self._cache.get(name, fingerprint)
                            if cached_module is not None:
                                # Cache entries carry the module's declared
                                # name inside the serialised payload, so a hit
                                # (under either the requested or the declared
                                # name) is re-keyed here.
                                self._record_module(
                                    fetched, declared_by, resolved_this_wave, name, cached_module
                                )
                                self._reconcile_name(aliases, name, cached_module)
                                cached.add(cached_module.name)
                                continue
                        try:
                            # CPU-bound Lark parse — off the event-loop thread
                            # (issue #19). SmiParser caches compiled Lark
                            # parsers per thread, so concurrent to_thread
                            # calls do not share mutable parser state.
                            module = await asyncio.to_thread(self._parser.parse, result)
                        except Exception as exc:  # noqa: BLE001
                            errors[name] = exc
                            continue
                        # Key by the module's DECLARED name so dependents that
                        # import it (by its real name) resolve against this
                        # entry instead of triggering a phantom fetch.
                        self._record_module(fetched, declared_by, resolved_this_wave, name, module)
                        self._reconcile_name(aliases, name, module)
                        if self._cache is not None:
                            self._cache.put(module.name, module, fingerprint)
                            if module.name != name:
                                # Also cache under the requested name so a
                                # later run still asking for the misnamed file
                                # gets a cache hit under the declared name.
                                self._cache.put(name, module, fingerprint)

            # --- Discover new transitive dependencies ---
            pending = set()
            for name in resolved_this_wave:
                for dep in fetched[name].all_imports():
                    # `dep in aliases` covers deps that reference a misnamed
                    # file by its *requested* name — the module is already
                    # present under the declared name, so fetching it again
                    # would be a phantom fetch.
                    if (
                        dep not in fetched
                        and dep not in aliases
                        and dep not in errors
                        and dep not in BASE_MIBS
                    ):
                        pending.add(dep)

        # Raises CircularDependencyError on cycle — propagates uncaught.
        # No try/except wrapper needed: catching and immediately re-raising
        # is a no-op that only adds noise.
        order = topological_sort(fetched)

        return ResolveResult(
            modules=[fetched[name] for name in order],
            errors=errors,
            aliases=aliases,
            cached=cached,
        )
