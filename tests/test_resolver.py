"""Unit tests for resolver/ — cache, dependency sort, and MibResolver."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from trishul_smi.errors import CircularDependencyError, MibSizeLimitError
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
        """OSError during put() must be wrapped in MibCacheError, not leak raw."""
        from unittest.mock import patch

        from trishul_smi.errors import MibCacheError

        cache = MibCache(tmp_path, ttl_days=7)
        with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
            with pytest.raises(MibCacheError, match="disk full"):
                cache.put("IF-MIB", _make_module("IF-MIB"))


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
    async def test_cache_hit_skips_fetch(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        m = _make_module("IF-MIB")
        cache.put("IF-MIB", m)
        reader = MockReader({})  # raises for any fetch call
        parser = SmiParser()
        resolver = MibResolver(reader, parser, cache=cache)
        result = await resolver.resolve(["IF-MIB"])
        assert result.ok
        assert result.modules[0].name == "IF-MIB"

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
