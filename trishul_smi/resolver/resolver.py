"""MibResolver: fetch, parse, and order MIB modules by dependency.

Architecture
------------
The resolver performs a BFS over the MIB import graph:

    1. Start with the requested MIB name(s).
    2. Check the compiled cache (MibCache) — skip fetch+parse on hit.
    3. Fetch all cache-missing MIBs *concurrently* via asyncio.gather.
    4. Parse each fetched text in a thread pool (asyncio.to_thread) so
       the event loop stays unblocked during CPU-bound Lark parsing.
    5. Collect newly-discovered imports; add unseen names to the next wave.
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

    async def resolve(self, mib_names: list[str]) -> ResolveResult:
        """Fetch and parse ``mib_names`` and all transitive dependencies.

        Returns:
            ResolveResult with .modules in topological order and .errors
            for anything that failed.
        """
        fetched: dict[str, MibModule] = {}
        errors: dict[str, Exception] = {}
        pending: set[str] = set(mib_names)

        while pending:
            # --- Cache check (synchronous, cheap) ---
            still_pending: set[str] = set()
            for name in sorted(pending):
                if self._cache is not None:
                    cached = self._cache.get(name)
                    if cached is not None:
                        fetched[name] = cached
                        continue
                still_pending.add(name)

            if not still_pending:
                pending.clear()
                break

            # --- Concurrent fetch + parse ---
            names_ordered = sorted(still_pending)
            results = await asyncio.gather(
                *[self._fetch_and_parse(name) for name in names_ordered],
                return_exceptions=True,
            )

            newly_fetched: set[str] = set()
            # strict=True: asyncio.gather always returns exactly one result per
            # coroutine, so a length mismatch would be a bug — fail loudly.
            for name, result in zip(names_ordered, results, strict=True):
                if isinstance(result, MibSizeLimitError):
                    # Propagate immediately — size limit is a config error,
                    # not a per-module failure. Use `raise result` (not bare
                    # `raise`) because asyncio.gather(return_exceptions=True)
                    # returns exceptions as *values*, not as the active
                    # exception — bare `raise` would hit RuntimeError:
                    # "No active exception to re-raise".
                    raise result
                elif isinstance(result, BaseException):
                    # BaseException (not just Exception) so mypy can narrow
                    # the else branch to MibModule cleanly. Covers KeyboardInterrupt
                    # and SystemExit that gather may also return as values.
                    errors[name] = result
                else:
                    # mypy now knows: not BaseException → must be MibModule
                    module: MibModule = result
                    fetched[name] = module
                    if self._cache is not None:
                        self._cache.put(name, module)
                    newly_fetched.add(name)

            # --- Discover new transitive dependencies ---
            pending = set()
            for name in newly_fetched:
                for dep in fetched[name].all_imports():
                    if dep not in fetched and dep not in errors:
                        pending.add(dep)

        # Raises CircularDependencyError on cycle — propagates uncaught.
        # No try/except wrapper needed: catching and immediately re-raising
        # is a no-op that only adds noise.
        order = topological_sort(fetched)

        return ResolveResult(
            modules=[fetched[name] for name in order],
            errors=errors,
        )

    async def _fetch_and_parse(self, mib_name: str) -> MibModule:
        """Fetch raw text (I/O-bound, async) then parse in a thread
        (CPU-bound, off the event loop)."""
        text: str = await self._reader.fetch(mib_name)
        return await asyncio.to_thread(self._parser.parse, text)
