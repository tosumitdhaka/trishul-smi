"""MibResolver: fetch, parse, and order MIB modules by dependency.

Architecture
------------
The resolver performs a BFS over the MIB import graph:

    1. Start with the requested MIB name(s).
    2. Check the compiled cache (MibCache) — skip fetch+parse on hit.
    3. Fetch all cache-missing MIBs *concurrently* via asyncio.gather.
    4. Parse each fetched text synchronously after the fetch wave completes.
       This keeps the CLI's asyncio.run() path reliable on real MIBs while
       still allowing concurrent I/O for remote readers.
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
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from trishul_smi.errors import MibSizeLimitError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.parser._constants import BASE_MIBS
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import FetchProtocol
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.dependency import topological_sort


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
                from / written to disk, skipping fetch+parse on subsequent runs.
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
            ResolveResult with .modules in topological order and .errors
            for anything that failed.
        """
        fetched: dict[str, MibModule] = {}
        errors: dict[str, Exception] = {}
        # Explicit requests are always honoured; BASE_MIBS filter applies only
        # to transitive dependency resolution (line 146) so that well-known
        # infrastructure MIBs are skipped when pulled in as deps but still
        # compiled when the user explicitly asks for them.
        pending: set[str] = set(mib_names)
        # requested name -> declared name for misnamed MIB files. `fetched` is
        # keyed by DECLARED name so imports of the declared name match; this
        # map preserves the requested name for is_dependency / cache aliasing.
        aliases: dict[str, str] = {}

        while pending:
            # --- Cache check (synchronous, cheap) ---
            resolved_this_wave: set[str] = set()
            still_pending: set[str] = set()
            for name in sorted(pending):
                if self._cache is not None:
                    cached = self._cache.get(name)
                    if cached is not None:
                        # Cache entries carry the module's declared name inside
                        # the serialised payload, so a hit (under either the
                        # requested or the declared name) is re-keyed here.
                        fetched[cached.name] = cached
                        resolved_this_wave.add(cached.name)
                        self._reconcile_name(aliases, name, cached)
                        continue
                still_pending.add(name)

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
                        errors[name] = result
                    elif isinstance(result, BaseException):
                        # KeyboardInterrupt / SystemExit must not be silently
                        # collected — re-raise so the process can exit
                        # cleanly.
                        raise result
                    else:
                        try:
                            module = self._parser.parse(result)
                        except Exception as exc:  # noqa: BLE001
                            errors[name] = exc
                            continue
                        # Key by the module's DECLARED name so dependents that
                        # import it (by its real name) resolve against this
                        # entry instead of triggering a phantom fetch.
                        fetched[module.name] = module
                        resolved_this_wave.add(module.name)
                        self._reconcile_name(aliases, name, module)
                        if self._cache is not None:
                            self._cache.put(module.name, module)
                            if module.name != name:
                                # Also cache under the requested name so a
                                # later run still asking for the misnamed file
                                # gets a cache hit under the declared name.
                                self._cache.put(name, module)

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
        )
