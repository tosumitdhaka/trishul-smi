"""Tests for issue #21: declared module name vs requested name mismatch.

Misnamed MIB files (requested under one name, declaring another) must be
re-keyed by their DECLARED name so that dependency discovery, topological
sort, cache keying, output naming, and ``is_dependency`` all agree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import MockReader
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.errors import MibNotFoundError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.reader.base import AbstractReader
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.resolver import MibResolver

# The misnamed file lives in the reader under the requested name "A" but its
# ASN.1 header declares itself as B-MIB.
MISNAMED_TEXT = """
B-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
bMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "B Org"
    CONTACT-INFO "b@example.com"
    DESCRIPTION  "File requested as A, declares B-MIB."
    ::= { 1 50 }
END
"""

# A dependent that imports the DECLARED name (B-MIB).
DEPENDENT_TEXT = """
C-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    bMIB FROM B-MIB ;
cMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "C Org"
    CONTACT-INFO "c@example.com"
    DESCRIPTION  "Imports the declared name of the misnamed file."
    ::= { 1 51 }
END
"""

# A dependent that imports the REQUESTED name (A) of the misnamed file.
DEPENDENT_IMPORTING_REQUESTED_TEXT = """
D-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI
    bMIB FROM A ;
dMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "D Org"
    CONTACT-INFO "d@example.com"
    DESCRIPTION  "Imports the requested name of the misnamed file."
    ::= { 1 52 }
END
"""

NORMAL_TEXT = """
TEST-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY FROM SNMPv2-SMI ;
testMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Normally named module."
    ::= { 1 53 }
END
"""


class CountingReader(AbstractReader):
    """MockReader that also records every name it was asked to fetch."""

    def __init__(self, texts: dict[str, str]) -> None:
        self._texts = texts
        self.fetched: list[str] = []

    async def fetch(self, mib_name: str) -> str:
        self.fetched.append(mib_name)
        if mib_name not in self._texts:
            raise MibNotFoundError(mib_name)
        return self._texts[mib_name]


def _b_mib_warning() -> str:
    return "Module requested as 'A' but declares itself as 'B-MIB'; using the declared name."


class TestMisnamedModuleResolver:
    """Issue #21 consequence 1: dependents importing the declared name resolve."""

    @pytest.mark.asyncio
    async def test_dependent_importing_declared_name_resolves(self):
        reader = CountingReader({"A": MISNAMED_TEXT, "C-MIB": DEPENDENT_TEXT})
        resolver = MibResolver(reader, SmiParser())

        result = await resolver.resolve(["A", "C-MIB"])

        assert result.ok
        assert result.errors == {}
        assert [m.name for m in result.modules] == ["B-MIB", "C-MIB"]
        # No phantom fetch of the declared name: the misnamed file was fetched
        # once under its requested name only.
        assert reader.fetched == ["A", "C-MIB"]
        assert result.aliases == {"A": "B-MIB"}

    @pytest.mark.asyncio
    async def test_dependent_importing_requested_name_resolves(self):
        """A dep that names the misnamed file by its requested name (A) must
        also resolve — the alias guard prevents a phantom fetch."""
        reader = CountingReader({"A": MISNAMED_TEXT, "D-MIB": DEPENDENT_IMPORTING_REQUESTED_TEXT})
        resolver = MibResolver(reader, SmiParser())

        result = await resolver.resolve(["A", "D-MIB"])

        assert result.ok
        assert result.errors == {}
        assert [m.name for m in result.modules] == ["B-MIB", "D-MIB"]
        assert reader.fetched == ["A", "D-MIB"]
        assert result.aliases == {"A": "B-MIB"}

    @pytest.mark.asyncio
    async def test_warning_surfaced_on_module(self):
        reader = MockReader({"A": MISNAMED_TEXT})
        resolver = MibResolver(reader, SmiParser())

        result = await resolver.resolve(["A"])

        assert result.ok
        module = result.modules[0]
        assert module.name == "B-MIB"
        assert _b_mib_warning() in module.warnings

    @pytest.mark.asyncio
    async def test_misnamed_cache_hit_records_alias(self, tmp_path: Path):
        """A cache hit under the requested name must re-key to the declared
        name and record the alias so is_dependency stays correct on later runs."""
        cache = MibCache(tmp_path, ttl_days=7)
        resolver = MibResolver(CountingReader({"A": MISNAMED_TEXT}), SmiParser(), cache=cache)
        first = await resolver.resolve(["A"])
        assert first.ok

        # Second run: fetch-first (issue #12) — the source is fetched again to
        # compute its fingerprint, but the matching alias entry is a cache hit.
        second = await MibResolver(
            CountingReader({"A": MISNAMED_TEXT}), SmiParser(), cache=cache
        ).resolve(["A"])

        assert second.ok
        assert second.errors == {}
        assert [m.name for m in second.modules] == ["B-MIB"]
        assert second.aliases == {"A": "B-MIB"}
        assert second.cached == {"B-MIB"}
        # The warning is persisted in the cache and not duplicated on reload.
        assert second.modules[0].warnings.count(_b_mib_warning()) == 1


class TestMisnamedModuleCache:
    """Issue #21 consequence 3: the declared name is the cache key."""

    @pytest.mark.asyncio
    async def test_second_compile_hits_cache_skips_parse(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        parser = SmiParser()
        reader = CountingReader({"A": MISNAMED_TEXT})
        resolver = MibResolver(reader, parser, cache=cache)
        first = await resolver.resolve(["A"])
        assert first.ok
        assert first.modules[0].name == "B-MIB"

        # First run must have cached under the DECLARED name (primary key) and
        # the requested name (alias entry so misnamed requests still hit).
        assert cache.get("B-MIB") is not None
        assert cache.get("B-MIB").name == "B-MIB"
        assert cache.get("A") is not None

        # Second compile: fetch-first (issue #12) — the source is fetched
        # again to compute its fingerprint, but the parse is skipped.
        fresh_reader = CountingReader({"A": MISNAMED_TEXT})
        second = await MibResolver(fresh_reader, parser, cache=cache).resolve(["A"])
        assert second.ok
        assert [m.name for m in second.modules] == ["B-MIB"]
        assert fresh_reader.fetched == ["A"]  # fetched for the fingerprint
        assert second.cached == {"B-MIB"}  # served from cache — no re-parse

    @pytest.mark.asyncio
    async def test_cache_hit_under_declared_name(self, tmp_path: Path):
        """Requesting the declared name directly on a later run must also hit."""
        cache = MibCache(tmp_path, ttl_days=7)
        parser = SmiParser()
        first = await MibResolver(
            CountingReader({"A": MISNAMED_TEXT}), parser, cache=cache
        ).resolve(["A"])
        assert first.ok

        # Fetch-first (issue #12): the declared-name request must be fetchable
        # — serve the same source text under B-MIB; its fingerprint matches
        # the cached entry and the parse is skipped.
        second = await MibResolver(
            CountingReader({"B-MIB": MISNAMED_TEXT}), parser, cache=cache
        ).resolve(["B-MIB"])
        assert second.ok
        assert [m.name for m in second.modules] == ["B-MIB"]
        assert second.aliases == {}  # requested name matched declared name


class TestMisnamedModuleCompiler:
    """Issue #21 consequence 2: CompileResult naming and is_dependency agree."""

    @pytest.mark.asyncio
    async def test_compile_result_naming_and_is_dependency(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path / "out",
            cache_dir=None,
            formats=["json"],
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"A": MISNAMED_TEXT, "C-MIB": DEPENDENT_TEXT})
        )

        results = await compiler.compile("A", "C-MIB")
        by_name = {r.name: r for r in results}

        assert set(by_name) == {"B-MIB", "C-MIB"}
        # The misnamed module is compiled under its declared name, is an
        # explicit request (not a dependency), and carries the warning.
        b = by_name["B-MIB"]
        assert b.status == "compiled"
        assert b.is_dependency is False
        assert _b_mib_warning() in b.warnings
        # Output file named after the declared name, not the requested one.
        assert (tmp_path / "out" / "B-MIB.json").is_file()
        assert not (tmp_path / "out" / "A.json").exists()

        # The dependent resolved against the declared name — no phantom
        # "B-MIB missing" result, no false blocked status.
        assert by_name["C-MIB"].status == "compiled"
        assert by_name["C-MIB"].is_dependency is False

    @pytest.mark.asyncio
    async def test_compile_dependent_importing_requested_name(self, tmp_path: Path):
        """Compiler-level twin of the resolver test: a dependent that imports
        the misnamed file by its REQUESTED name (A) must not be falsely
        blocked at the compile stage (review H1 — the blocked-deps predicate
        must consult the alias table, not just resolved module names)."""
        config = CompilerConfig(
            output_dir=tmp_path / "out",
            cache_dir=None,
            formats=["json"],
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"A": MISNAMED_TEXT, "D-MIB": DEPENDENT_IMPORTING_REQUESTED_TEXT})
        )

        results = await compiler.compile("A", "D-MIB")
        by_name = {r.name: r for r in results}

        assert set(by_name) == {"B-MIB", "D-MIB"}
        assert by_name["B-MIB"].status == "compiled"
        assert by_name["D-MIB"].status == "compiled"
        assert by_name["D-MIB"].missing_dependencies == []
        assert (tmp_path / "out" / "D-MIB.json").is_file()

    @pytest.mark.asyncio
    async def test_compile_result_transitive_dep_still_dependency(self, tmp_path: Path):
        """A module pulled in transitively (not requested) stays a dependency,
        even when the requested module is a misnamed file."""
        config = CompilerConfig(
            output_dir=tmp_path / "out",
            cache_dir=None,
            formats=["json"],
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"A": MISNAMED_TEXT, "C-MIB": DEPENDENT_TEXT})
        )

        results = await compiler.compile("A")
        by_name = {r.name: r for r in results}

        assert set(by_name) == {"B-MIB"}
        assert by_name["B-MIB"].is_dependency is False


class TestNormallyNamedRegression:
    """Issue #21 regression: request name == declared name behaves as before."""

    @pytest.mark.asyncio
    async def test_resolver_no_alias_no_warning(self):
        reader = MockReader({"TEST-MIB": NORMAL_TEXT})
        result = await MibResolver(reader, SmiParser()).resolve(["TEST-MIB"])

        assert result.ok
        assert result.aliases == {}
        assert [m.name for m in result.modules] == ["TEST-MIB"]
        assert result.modules[0].warnings == []

    @pytest.mark.asyncio
    async def test_compiler_no_alias_no_warning(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path / "out",
            cache_dir=None,
            formats=["json"],
        )
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": NORMAL_TEXT}))

        results = await compiler.compile("TEST-MIB")
        by_name = {r.name: r for r in results}

        assert set(by_name) == {"TEST-MIB"}
        assert by_name["TEST-MIB"].status == "compiled"
        assert by_name["TEST-MIB"].is_dependency is False
        assert by_name["TEST-MIB"].warnings == []
        assert (tmp_path / "out" / "TEST-MIB.json").is_file()

    @pytest.mark.asyncio
    async def test_module_carries_no_alias_field_on_plain_modules(self):
        """Sanity check: MibModule dataclass is untouched by the alias logic."""
        m = MibModule(name="PLAIN-MIB", language="SMIv2")
        assert m.name == "PLAIN-MIB"
        assert m.warnings == []


class TestExplicitAliasRequest:
    """Issue #25 — requesting both a misnamed file and its declared name."""

    @pytest.mark.asyncio
    async def test_explicit_alias_request_compiles_once(self, tmp_path: Path):
        """compile("A", "B-MIB") where A declares B-MIB and B-MIB cannot be
        fetched: B-MIB must be compiled once from A and never reported as
        missing — which keeps the CLI exit code at 0."""
        config = CompilerConfig(output_dir=tmp_path / "out", cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"A": MISNAMED_TEXT}))

        results = await compiler.compile("A", "B-MIB")
        by_name = {r.name: r for r in results}

        assert set(by_name) == {"B-MIB"}
        assert by_name["B-MIB"].status == "compiled"
        # No contradictory "B-MIB missing" entry — the missing/failed set that
        # drives the CLI's exit-1 contract is empty.
        assert not any(r.status in {"missing", "failed"} for r in results)
        assert (tmp_path / "out" / "B-MIB.json").is_file()
