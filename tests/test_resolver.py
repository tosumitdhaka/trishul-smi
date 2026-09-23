"""Unit tests for resolver/ — cache, dependency sort, and MibResolver."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from trishul_smi.errors import (
    CircularDependencyError,
    MibNotFoundError,
    MibSizeLimitError,
    NetworkError,
)
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import AbstractReader
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.dependency import build_dependency_graph, topological_sort
from trishul_smi.resolver.resolver import MibResolver

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_module(name: str, imports: dict[str, list[str]] | None = None) -> MibModule:
    return MibModule(name=name, language="SMIv2", imports=imports or {})


def _source_fingerprint(text: str) -> str:
    """sha256 hex digest, mirroring the resolver's fingerprint helper (issue #12)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CountingParser:
    """Wraps SmiParser and records every parse() call so tests can prove a
    cache hit skipped parsing."""

    def __init__(self) -> None:
        self._inner = SmiParser()
        self.parsed: list[str] = []

    def parse(self, text: str) -> MibModule:
        self.parsed.append(text)
        return self._inner.parse(text)


MINIMAL_V2 = """
TEST-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI ;
testMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Minimal."
    ::= { 1 3 }
END
"""

DEP_MIB = """
DEP-MIB DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM SNMPv2-SMI ;
depObj MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Dep Org"
    CONTACT-INFO "dep@example.com"
    DESCRIPTION  "Dependency MIB."
    ::= { 1 4 }
END
"""

MIB_C = """
MIB-C DEFINITIONS ::= BEGIN
IMPORTS
    Integer32 FROM SNMPv2-SMI ;
cObj MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "C Org"
    CONTACT-INFO "c@example.com"
    DESCRIPTION  "MIB C (root dep)."
    ::= { 1 5 }
END
"""


class MockReader(AbstractReader):
    """Returns pre-loaded text; raises MibNotFoundError for unknown names.
    Raises MibSizeLimitError for names registered in size_limit_names.
    """

    def __init__(
        self,
        texts: dict[str, str],
        size_limit_names: set[str] | None = None,
    ) -> None:
        self._texts = texts
        self._size_limit_names = size_limit_names or set()

    async def fetch(self, mib_name: str) -> str:
        from trishul_smi.errors import MibNotFoundError

        if mib_name in self._size_limit_names:
            raise MibSizeLimitError(f"{mib_name} exceeds size limit")
        if mib_name not in self._texts:
            raise MibNotFoundError(mib_name)
        return self._texts[mib_name]


class NetworkErrorReader(AbstractReader):
    """Raises NetworkError for every request — simulates a reachable but
    broken source (transport failure), which must NOT trigger the offline
    cache fallback (only a true MibNotFoundError may)."""

    async def fetch(self, mib_name: str) -> str:
        raise NetworkError(f"GET {mib_name} failed")


# ---------------------------------------------------------------------------
# MibCache
# ---------------------------------------------------------------------------


class TestMibCache:
    def test_put_and_get(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        m = _make_module("IF-MIB")
        cache.put("IF-MIB", m)
        result = cache.get("IF-MIB")
        assert result is not None
        assert result.name == "IF-MIB"

    def test_miss_returns_none(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        assert cache.get("MISSING-MIB") is None

    def test_invalidate(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        cache.invalidate("IF-MIB")
        assert cache.get("IF-MIB") is None

    def test_clear(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        cache.put("IP-MIB", _make_module("IP-MIB"))
        cache.clear()
        assert cache.get("IF-MIB") is None
        assert cache.get("IP-MIB") is None

    def test_ttl_zero_never_expires(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=0)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        path = tmp_path / "compiled" / "IF-MIB.json"
        old_time = time.time() - 365 * 86_400
        os.utime(path, (old_time, old_time))
        assert cache.get("IF-MIB") is not None

    def test_stale_entry_returns_none(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=1)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        path = tmp_path / "compiled" / "IF-MIB.json"
        old_time = time.time() - 2 * 86_400
        os.utime(path, (old_time, old_time))
        assert cache.get("IF-MIB") is None

    def test_roundtrip_preserves_objects(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        obj = MibObject(
            name="ifDescr",
            oid="1.3.6.1.2.1.2.2.1.2",
            oid_path=[1, 3, 6, 1, 2, 1, 2, 2, 1, 2],
            object_type="OBJECT-TYPE",
            syntax="DisplayString",
            max_access="read-only",
            status="current",
            index=["ifIndex"],
        )
        m = MibModule(
            name="IF-MIB",
            language="SMIv2",
            imports={"SNMPv2-SMI": ["OBJECT-TYPE"]},
            objects={"ifDescr": obj},
        )
        cache.put("IF-MIB", m)
        result = cache.get("IF-MIB")
        assert result is not None
        assert "ifDescr" in result.objects
        assert result.objects["ifDescr"].index == ["ifIndex"]
        assert result.objects["ifDescr"].syntax == "DisplayString"

    def test_corrupted_cache_returns_none(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        path = tmp_path / "compiled" / "BAD-MIB.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json{{{")
        assert cache.get("BAD-MIB") is None

    def test_put_is_atomic_no_tmp_leftover(self, tmp_path: Path):
        """put() writes via .tmp then renames; no .tmp file should remain."""
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        tmp = tmp_path / "compiled" / "IF-MIB.tmp"
        assert not tmp.exists()

    def test_put_oserror_raises_mib_cache_error(self, tmp_path: Path):
        """OSError during put() must be wrapped in MibCacheError, not leak raw.

        put() writes via tempfile.mkstemp + os.fdopen (issue #20), so an
        OSError must surface from the write path rather than Path.write_bytes.
        """
        from unittest.mock import patch

        from trishul_smi.errors import MibCacheError

        cache = MibCache(tmp_path, ttl_days=7)
        with patch("os.fdopen", side_effect=OSError("disk full")):
            with pytest.raises(MibCacheError, match="disk full"):
                cache.put("IF-MIB", _make_module("IF-MIB"))

    def test_roundtrip_preserves_types(self, tmp_path: Path):
        """MibType entries must survive a put/get round-trip through the cache."""
        from trishul_smi.models.mib_type import MibType

        tc = MibType(name="TruthValue", base_type="INTEGER", description="Boolean type")
        m = MibModule(name="SNMPv2-TC", language="SMIv2", types={"TruthValue": tc})
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("SNMPv2-TC", m)
        result = cache.get("SNMPv2-TC")
        assert result is not None
        assert "TruthValue" in result.types
        assert result.types["TruthValue"].base_type == "INTEGER"
        assert result.types["TruthValue"].description == "Boolean type"

    def test_roundtrip_preserves_notifications(self, tmp_path: Path):
        """Notifications must survive a put/get round-trip through the cache."""
        notif = MibObject(
            name="linkDown",
            oid="1.3.6.1.6.3.1.1.5.3",
            oid_path=[1, 3, 6, 1, 6, 3, 1, 1, 5, 3],
            object_type="NOTIFICATION-TYPE",
            status="current",
        )
        m = MibModule(
            name="IF-MIB",
            language="SMIv2",
            notifications={"linkDown": notif},
        )
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", m)
        result = cache.get("IF-MIB")
        assert result is not None
        assert "linkDown" in result.notifications
        assert result.notifications["linkDown"].status == "current"

    def test_init_oserror_raises_mib_cache_error(self, tmp_path: Path):
        """OSError creating the cache directory must raise MibCacheError."""
        from unittest.mock import patch

        from trishul_smi.errors import MibCacheError

        with patch("pathlib.Path.mkdir", side_effect=OSError("read-only fs")):
            with pytest.raises(MibCacheError, match="read-only fs"):
                MibCache(tmp_path, ttl_days=7)


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    def test_single_module_no_deps(self):
        modules = {"A": _make_module("A")}
        assert topological_sort(modules) == ["A"]

    def test_linear_chain(self):
        modules = {
            "A": _make_module("A"),
            "B": _make_module("B", imports={"A": ["x"]}),
        }
        result = topological_sort(modules)
        assert result.index("A") < result.index("B")

    def test_diamond_dependency(self):
        modules = {
            "A": _make_module("A"),
            "B": _make_module("B", imports={"A": ["x"]}),
            "C": _make_module("C", imports={"A": ["y"]}),
            "D": _make_module("D", imports={"B": ["p"], "C": ["q"]}),
        }
        result = topological_sort(modules)
        assert result.index("A") < result.index("B")
        assert result.index("A") < result.index("C")
        assert result.index("B") < result.index("D")
        assert result.index("C") < result.index("D")

    def test_external_imports_ignored(self):
        modules = {
            "MY-MIB": _make_module("MY-MIB", imports={"SNMPv2-SMI": ["OBJECT-TYPE"]}),
        }
        assert topological_sort(modules) == ["MY-MIB"]

    def test_cycle_raises(self):
        modules = {
            "A": _make_module("A", imports={"B": ["x"]}),
            "B": _make_module("B", imports={"A": ["y"]}),
        }
        with pytest.raises(CircularDependencyError):
            topological_sort(modules)

    def test_deterministic_order(self):
        modules = {
            "C": _make_module("C"),
            "A": _make_module("A"),
            "B": _make_module("B"),
        }
        assert topological_sort(modules) == ["A", "B", "C"]

    def test_build_dependency_graph(self):
        modules = {
            "A": _make_module("A"),
            "B": _make_module("B", imports={"A": ["x"]}),
        }
        graph = build_dependency_graph(modules)
        assert "B" in graph["A"]
        assert graph["B"] == []


# ---------------------------------------------------------------------------
# MibResolver
# ---------------------------------------------------------------------------


class TestMibResolver:
    @pytest.mark.asyncio
    async def test_resolves_single_mib(self):
        reader = MockReader({"TEST-MIB": MINIMAL_V2})
        parser = SmiParser()
        resolver = MibResolver(reader, parser)
        result = await resolver.resolve(["TEST-MIB"])
        assert result.ok
        assert any(m.name == "TEST-MIB" for m in result.modules)

    @pytest.mark.asyncio
    async def test_missing_mib_reported_in_errors(self):
        reader = MockReader({})
        parser = SmiParser()
        resolver = MibResolver(reader, parser)
        result = await resolver.resolve(["MISSING-MIB"])
        assert "MISSING-MIB" in result.errors
        assert result.modules == []

    @pytest.mark.asyncio
    async def test_cache_hit_skips_parse(self, tmp_path: Path):
        """Fetch-first design (issue #12): the source is fetched to compute
        its fingerprint, but a matching entry skips the parse entirely."""
        text = "IF-MIB source v1"
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"), _source_fingerprint(text))
        reader = MockReader({"IF-MIB": text})
        parser = CountingParser()
        resolver = MibResolver(reader, parser, cache=cache)
        result = await resolver.resolve(["IF-MIB"])
        assert result.ok
        assert result.modules[0].name == "IF-MIB"
        assert parser.parsed == []  # served from cache — never re-parsed
        assert result.cached == {"IF-MIB"}

    @pytest.mark.asyncio
    async def test_cache_hit_discovers_transitive_dependencies(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        root_text = "ROOT-MIB source v1"
        cache.put(
            "ROOT-MIB",
            _make_module("ROOT-MIB", imports={"DEP-MIB": ["depObj"]}),
            _source_fingerprint(root_text),
        )
        reader = MockReader({"ROOT-MIB": root_text, "DEP-MIB": DEP_MIB})
        resolver = MibResolver(reader, SmiParser(), cache=cache)

        result = await resolver.resolve(["ROOT-MIB"])

        assert result.ok
        names = [m.name for m in result.modules]
        assert "ROOT-MIB" in names
        assert "DEP-MIB" in names
        assert names.index("DEP-MIB") < names.index("ROOT-MIB")
        assert result.cached == {"ROOT-MIB"}

    @pytest.mark.asyncio
    async def test_result_ok_property(self):
        reader = MockReader({"TEST-MIB": MINIMAL_V2})
        resolver = MibResolver(reader, SmiParser())
        result = await resolver.resolve(["TEST-MIB"])
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_result_not_ok_on_error(self):
        reader = MockReader({})
        resolver = MibResolver(reader, SmiParser())
        result = await resolver.resolve(["MISSING"])
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_transitive_dependency_fetched(self):
        """If A imports B which imports C, resolving [A] should fetch all
        three and return them in dependency order: C before B before A.
        External base-MIB imports (SNMPv2-SMI etc.) are silently skipped.
        """
        mib_a = """
MIB-A DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    depObj          FROM DEP-MIB ;
aObj MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "A Org"
    CONTACT-INFO "a@example.com"
    DESCRIPTION  "MIB A imports DEP-MIB."
    ::= { 1 6 }
END
"""
        reader = MockReader(
            {
                "MIB-A": mib_a,
                "DEP-MIB": DEP_MIB,
            }
        )
        resolver = MibResolver(reader, SmiParser())
        result = await resolver.resolve(["MIB-A"])
        assert result.ok
        names = [m.name for m in result.modules]
        assert "MIB-A" in names
        assert "DEP-MIB" in names
        # DEP-MIB (the dependency) must appear before MIB-A
        assert names.index("DEP-MIB") < names.index("MIB-A")

    @pytest.mark.asyncio
    async def test_base_exception_propagates_not_collected(self):
        """BaseException subclasses from asyncio.gather must propagate, not be stored in errors.
        Uses a custom BaseException subclass instead of KeyboardInterrupt to avoid
        confusing pytest's own interrupt handling.
        """

        class _FakeInterrupt(BaseException):
            pass

        class InterruptReader(AbstractReader):
            async def fetch(self, mib_name: str) -> str:
                raise _FakeInterrupt("simulated interrupt")

        resolver = MibResolver(InterruptReader(), SmiParser())
        with pytest.raises(_FakeInterrupt):
            await resolver.resolve(["ANY-MIB"])

    @pytest.mark.asyncio
    async def test_size_limit_propagates_immediately(self):
        """MibSizeLimitError must propagate out of resolve() immediately
        (not be collected in .errors), because it is a configuration error
        rather than a recoverable per-module failure.
        The fix in resolver.py uses `raise result` (the exception value)
        rather than bare `raise` which would hit RuntimeError outside an
        except block, since asyncio.gather(return_exceptions=True) returns
        exceptions as plain values.
        """
        reader = MockReader({}, size_limit_names={"BIG-MIB"})
        resolver = MibResolver(reader, SmiParser())
        with pytest.raises(MibSizeLimitError):
            await resolver.resolve(["BIG-MIB"])


class TestAliasEdgeCases:
    """Issue #25 — declared-name collisions and explicit-alias requests."""

    @pytest.mark.asyncio
    async def test_duplicate_declared_names_emit_collision_warning(self):
        """Two requested files declaring the same module name must emit a
        collision warning on the surviving (last-wins) module, naming the
        discarded requested file — no silent content drop (issue #25)."""
        text_a = MINIMAL_V2.replace("TEST-MIB", "SHARED-MIB").replace("testMIB", "sharedMIB")
        text_b = text_a.replace('LAST-UPDATED "200001010000Z"', 'LAST-UPDATED "200101010000Z"')
        reader = MockReader({"A": text_a, "B": text_b})
        resolver = MibResolver(reader, SmiParser())

        result = await resolver.resolve(["A", "B"])

        assert result.ok
        # Last-wins: a single surviving module, its content from the later file.
        assert [m.name for m in result.modules] == ["SHARED-MIB"]
        module = result.modules[0]
        assert module.lastupdated == "200101010000Z"
        assert any("collision" in w and "'A'" in w and "'B'" in w for w in module.warnings)

    @pytest.mark.asyncio
    async def test_explicit_alias_request_yields_single_consistent_result(self):
        """compile("A", "B-MIB") where A declares B-MIB and B-MIB cannot be
        fetched must not report B-MIB as both compiled and missing (issue #25)."""
        misnamed = MINIMAL_V2.replace("TEST-MIB", "B-MIB").replace("testMIB", "bMIB")
        reader = MockReader({"A": misnamed})
        resolver = MibResolver(reader, SmiParser())

        result = await resolver.resolve(["A", "B-MIB"])

        assert result.ok
        assert result.errors == {}
        assert [m.name for m in result.modules] == ["B-MIB"]

    @pytest.mark.asyncio
    async def test_alias_skip_path_warns_instead_of_silent_discard(self):
        """v0.4.9-review L2: when a genuinely-fetchable requested file is
        skipped because an earlier file in the wave already declared its name,
        the discard must surface a collision warning on the surviving module —
        the parse path is last-wins + warns, the skip path was silent
        first-wins. The discarded file's fetch must still have happened."""
        misnamed = MINIMAL_V2.replace("TEST-MIB", "B-MIB").replace("testMIB", "bMIB")

        class CountingReader(MockReader):
            def __init__(self, texts: dict[str, str]) -> None:
                super().__init__(texts)
                self.fetched: list[str] = []

            async def fetch(self, mib_name: str) -> str:
                self.fetched.append(mib_name)
                return await super().fetch(mib_name)

        reader = CountingReader({"A": misnamed, "B-MIB": misnamed})
        resolver = MibResolver(reader, SmiParser())

        result = await resolver.resolve(["A", "B-MIB"])

        assert result.ok
        assert result.errors == {}
        assert [m.name for m in result.modules] == ["B-MIB"]
        # B-MIB was genuinely fetched (its content discarded, not skipped).
        assert reader.fetched == ["A", "B-MIB"]
        module = result.modules[0]
        assert any(
            w == "Module requested as 'B-MIB' was fetched but discarded; "
            "'B-MIB' was already supplied by 'A'."
            for w in module.warnings
        )


class TestOfflineCacheFallback:
    """v0.4.9-review M1: a warm cache must serve a compile when the source is
    unreachable, without weakening freshness when the source is reachable."""

    @pytest.mark.asyncio
    async def test_warm_cache_serves_unreachable_source(self, tmp_path: Path):
        """MibNotFoundError from the source + a warm cache entry → the cached
        module is served with status 'cached' and a source-unavailable
        warning."""
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        reader = MockReader({})  # IF-MIB genuinely unreachable
        resolver = MibResolver(reader, SmiParser(), cache=cache)

        result = await resolver.resolve(["IF-MIB"])

        assert result.ok
        assert result.errors == {}
        assert result.cached == {"IF-MIB"}
        assert [m.name for m in result.modules] == ["IF-MIB"]
        assert any(
            w == "serving cached 'IF-MIB'; source unavailable" for w in result.modules[0].warnings
        )

    @pytest.mark.asyncio
    async def test_network_error_does_not_trigger_fallback(self, tmp_path: Path):
        """A transport failure (NetworkError) must NOT fall back to cache —
        only a true not-found (MibNotFoundError) may. A reachable-but-broken
        source must never be masked by stale cache (v0.3.1 #4 semantics)."""
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        resolver = MibResolver(NetworkErrorReader(), SmiParser(), cache=cache)

        result = await resolver.resolve(["IF-MIB"])

        assert not result.ok
        assert isinstance(result.errors["IF-MIB"], NetworkError)
        assert result.cached == set()
        assert result.modules == []

    @pytest.mark.asyncio
    async def test_cold_cache_unreachable_still_missing(self, tmp_path: Path):
        """Cold cache + unreachable source → unchanged missing behavior."""
        cache = MibCache(tmp_path, ttl_days=7)  # empty
        resolver = MibResolver(MockReader({}), SmiParser(), cache=cache)

        result = await resolver.resolve(["MISSING-MIB"])

        assert "MISSING-MIB" in result.errors
        assert isinstance(result.errors["MISSING-MIB"], MibNotFoundError)
        assert result.modules == []
        assert result.cached == set()

    @pytest.mark.asyncio
    async def test_expired_entry_not_resurrected(self, tmp_path: Path):
        """A TTL-expired cache entry must NOT be resurrected by the offline
        fallback — the TTL still applies to fingerprint-less gets."""
        cache = MibCache(tmp_path, ttl_days=1)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        path = tmp_path / "compiled" / "IF-MIB.json"
        old_time = time.time() - 2 * 86_400
        os.utime(path, (old_time, old_time))

        resolver = MibResolver(MockReader({}), SmiParser(), cache=cache)

        result = await resolver.resolve(["IF-MIB"])

        assert "IF-MIB" in result.errors
        assert isinstance(result.errors["IF-MIB"], MibNotFoundError)
        assert result.modules == []
        assert result.cached == set()

    @pytest.mark.asyncio
    async def test_fresh_fetch_wins_over_stale_offline_fallback(self, tmp_path: Path):
        """v0.5.0 M1: when the offline fallback serves a stale cached misnamed
        alias for a declared name whose genuine source is fetchable in the
        same wave, the fresh source must win. The skip guard must re-record
        the fresh content through _record_module (collision warning emitted)
        instead of silently keeping the stale fallback-served entry.

        Review repro: cached alias 'ALIAS' declaring B-MIB with LAST-UPDATED
        2000; ALIAS is unreachable (fallback), but the genuine B-MIB source is
        fetchable in the same wave with LAST-UPDATED 2003 → 2003 wins.
        """
        stale_alias_text = MINIMAL_V2.replace("TEST-MIB", "B-MIB").replace("testMIB", "bMIB")
        fresh_source = stale_alias_text.replace(
            'LAST-UPDATED "200001010000Z"', 'LAST-UPDATED "200301010000Z"'
        )

        cache = MibCache(tmp_path, ttl_days=7)
        # Cached misnamed alias: requested "ALIAS" carried a module declaring
        # B-MIB with old content (LAST-UPDATED 2000).
        stale_cached = _make_module("B-MIB")
        stale_cached.lastupdated = "200001010000Z"
        cache.put("ALIAS", stale_cached)

        # ALIAS is genuinely unreachable (→ offline fallback); B-MIB is
        # fetchable from a live source with newer content.
        reader = MockReader({"B-MIB": fresh_source})
        resolver = MibResolver(reader, SmiParser(), cache=cache)

        result = await resolver.resolve(["ALIAS", "B-MIB"])

        assert result.ok
        assert result.errors == {}
        assert [m.name for m in result.modules] == ["B-MIB"]
        module = result.modules[0]
        # Fresh content (2003) replaces the stale fallback-served entry (2000).
        assert module.lastupdated == "200301010000Z"
        # Re-recorded via _record_module → the collision warning is emitted.
        assert any("collision" in w and "'ALIAS'" in w and "'B-MIB'" in w for w in module.warnings)
        # The surviving module was compiled from source, not served from cache.
        assert result.cached == set()

    @pytest.mark.asyncio
    async def test_unparseable_fresh_fetch_keeps_fallback_entry(self, tmp_path: Path):
        """v0.5.0 M1 regression: when the offline fallback serves a cached
        entry for a declared name whose fresh source is fetchable in the same
        wave but does NOT parse, the fallback-served entry must be kept and
        the parse failure surfaced as a warning on it — NOT an errors[name]
        entry. Recording the failure in .errors would report the same module
        as both cached and failed (issue #25 contradiction shape) and exit 1.
        """
        cache = MibCache(tmp_path, ttl_days=7)
        # Cached alias: requested "ALIAS" carries a module declaring B-MIB.
        stale_cached = _make_module("B-MIB")
        stale_cached.lastupdated = "200001010000Z"
        cache.put("ALIAS", stale_cached)

        # ALIAS is genuinely unreachable (→ offline fallback); the genuine
        # B-MIB source is fetchable but its content is unparseable garbage.
        reader = MockReader({"B-MIB": "this is not a MIB at all !!!"})
        resolver = MibResolver(reader, SmiParser(), cache=cache)

        result = await resolver.resolve(["ALIAS", "B-MIB"])

        # The module appears exactly once, served from the fallback entry.
        assert result.ok
        assert result.errors == {}
        assert result.cached == {"B-MIB"}
        assert [m.name for m in result.modules] == ["B-MIB"]
        module = result.modules[0]
        # Stale fallback content is kept (fresh source could not be parsed).
        assert module.lastupdated == "200001010000Z"
        # The parse failure is reported as a warning on the kept module.
        assert any(
            "failed to parse" in w and "keeping the cached fallback" in w for w in module.warnings
        )
