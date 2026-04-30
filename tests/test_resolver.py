"""Unit tests for resolver/ — cache, dependency sort, and MibResolver."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from trishul_smi.errors import CircularDependencyError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import AbstractReader
from trishul_smi.resolver.cache import MibCache, _module_to_bytes, _module_from_dict
from trishul_smi.resolver.dependency import topological_sort, build_dependency_graph
from trishul_smi.resolver.resolver import MibResolver, ResolveResult


# ---------------------------------------------------------------------------
# Fixtures
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


class MockReader(AbstractReader):
    """Returns pre-loaded text; raises MibNotFoundError for unknown names."""
    def __init__(self, texts: dict[str, str]) -> None:
        self._texts = texts

    async def fetch(self, mib_name: str) -> str:
        from trishul_smi.errors import MibNotFoundError
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
        m = _make_module("IF-MIB")
        cache.put("IF-MIB", m)
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
        m = _make_module("IF-MIB")
        cache.put("IF-MIB", m)
        # Backdate the file mtime by 365 days
        path = tmp_path / "compiled" / "IF-MIB.json"
        old_time = time.time() - 365 * 86_400
        import os
        os.utime(path, (old_time, old_time))
        assert cache.get("IF-MIB") is not None  # should NOT be evicted

    def test_stale_entry_returns_none(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=1)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        path = tmp_path / "compiled" / "IF-MIB.json"
        old_time = time.time() - 2 * 86_400  # 2 days old
        import os
        os.utime(path, (old_time, old_time))
        assert cache.get("IF-MIB") is None

    def test_roundtrip_preserves_objects(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        obj = MibObject(
            name="ifDescr", oid="1.3.6.1.2.1.2.2.1.2",
            oid_path=[1,3,6,1,2,1,2,2,1,2],
            object_type="OBJECT-TYPE", syntax="DisplayString",
            max_access="read-only", status="current",
            index=["ifIndex"],
        )
        m = MibModule(
            name="IF-MIB", language="SMIv2",
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


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------

class TestTopologicalSort:
    def test_single_module_no_deps(self):
        modules = {"A": _make_module("A")}
        assert topological_sort(modules) == ["A"]

    def test_linear_chain(self):
        # B imports A → order should be [A, B]
        modules = {
            "A": _make_module("A"),
            "B": _make_module("B", imports={"A": ["x"]}),
        }
        result = topological_sort(modules)
        assert result.index("A") < result.index("B")

    def test_diamond_dependency(self):
        # A ← B, A ← C, B ← D, C ← D  (D imports B and C, which both import A)
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
        # SNMPv2-SMI is not in the modules dict — should not raise
        modules = {
            "MY-MIB": _make_module("MY-MIB", imports={"SNMPv2-SMI": ["OBJECT-TYPE"]}),
        }
        result = topological_sort(modules)
        assert result == ["MY-MIB"]

    def test_cycle_raises(self):
        modules = {
            "A": _make_module("A", imports={"B": ["x"]}),
            "B": _make_module("B", imports={"A": ["y"]}),
        }
        with pytest.raises(CircularDependencyError):
            topological_sort(modules)

    def test_deterministic_order(self):
        # Multiple valid orderings exist; we require alphabetical within layers
        modules = {
            "C": _make_module("C"),
            "A": _make_module("A"),
            "B": _make_module("B"),
        }
        result = topological_sort(modules)
        assert result == ["A", "B", "C"]

    def test_build_dependency_graph(self):
        modules = {
            "A": _make_module("A"),
            "B": _make_module("B", imports={"A": ["x"]}),
        }
        graph = build_dependency_graph(modules)
        assert "B" in graph["A"]  # A is depended on by B
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
        names = [m.name for m in result.modules]
        assert "TEST-MIB" in names

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

        # Reader would raise if called — cache hit must prevent the call
        reader = MockReader({})
        parser = SmiParser()
        resolver = MibResolver(reader, parser, cache=cache)
        result = await resolver.resolve(["IF-MIB"])
        assert result.ok
        assert result.modules[0].name == "IF-MIB"

    @pytest.mark.asyncio
    async def test_result_ok_property(self):
        reader = MockReader({"TEST-MIB": MINIMAL_V2})
        parser = SmiParser()
        resolver = MibResolver(reader, parser)
        result = await resolver.resolve(["TEST-MIB"])
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_result_not_ok_on_error(self):
        reader = MockReader({})
        parser = SmiParser()
        resolver = MibResolver(reader, parser)
        result = await resolver.resolve(["MISSING"])
        assert result.ok is False
