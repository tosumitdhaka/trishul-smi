"""Concurrency and cache-safety regression tests (issues #19, #20).

Covers:
- #19: parse() runs off the event-loop thread (asyncio.to_thread).
- #20a: concurrent compile() calls on one MibCompiler each emit their own
        artifact metadata (per-run formatter instances, no shared mutation).
- #20b: same-name cache writes from concurrent writers cannot interleave into
        a corrupt entry (tempfile.mkstemp instead of a predictable .tmp path).
- #20c: an unreadable cache entry (PermissionError / TOCTOU FileNotFoundError,
        stat failure in the TTL check) degrades to a miss — never a crash.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import MockReader
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.models.mib_module import MibModule
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.parser.smi_parser import SmiParser
from trishul_smi.resolver.cache import MibCache
from trishul_smi.resolver.resolver import MibResolver

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


def _make_module(name: str) -> MibModule:
    return MibModule(name=name, language="SMIv2")


class TestOffLoopParsing:
    """Issue #19 — parse() must run on a worker thread, never the event loop."""

    @pytest.mark.asyncio
    async def test_parse_runs_off_event_loop_thread(self):
        loop_thread = threading.get_ident()
        parse_threads: list[int] = []

        class ThreadCapturingParser:
            def parse(self, text: str) -> MibModule:
                parse_threads.append(threading.get_ident())
                return SmiParser().parse(text)

        reader = MockReader({"TEST-MIB": MINIMAL_V2})
        resolver = MibResolver(reader, ThreadCapturingParser())
        result = await resolver.resolve(["TEST-MIB"])

        assert result.ok
        assert parse_threads, "the parser was never invoked"
        assert all(tid != loop_thread for tid in parse_threads)


class TestConcurrentCompileMetadata:
    """Issue #20a — per-run formatter instances, no shared-state race."""

    def test_concurrent_compiles_each_use_own_metadata(self, tmp_path: Path):
        """Two threads, two event loops, one shared MibCompiler — the actual
        race scenario for the old shared-JsonFormatter mutation. Every
        format() call inside one run must use that run's own metadata."""
        out_dir = tmp_path / "out"
        config = CompilerConfig(output_dir=out_dir, formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))

        errors: list[BaseException] = []
        used_metadata: dict[int, list[str]] = {}
        barrier = threading.Barrier(2)
        original_format = JsonFormatter.format

        def _wrapped_format(self, module):
            used_metadata.setdefault(threading.get_ident(), []).append(
                self._artifact_metadata.generated_at
            )
            return original_format(self, module)

        def _run() -> None:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                barrier.wait()
                loop.run_until_complete(compiler.compile("TEST-MIB"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with patch.object(JsonFormatter, "format", _wrapped_format):
            threads = [threading.Thread(target=_run) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors
        assert len(used_metadata) == 2, "both runs must have emitted JSON"
        for tid, metas in used_metadata.items():
            # Every format() call within one run used that run's metadata —
            # never another run's (the shared-instance race would mix them).
            assert len(set(metas)) == 1, f"run on thread {tid} used another run's metadata"

    def test_compile_does_not_mutate_shared_formatter(self, tmp_path: Path):
        """The per-run JsonFormatter instances leave the shared registry alone."""
        config = CompilerConfig(output_dir=tmp_path / "out", formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        shared = compiler._formatters["json"]
        original = shared._artifact_metadata

        asyncio.run(compiler.compile("TEST-MIB"))

        assert shared._artifact_metadata is original


class TestInterleavedCacheWrites:
    """Issue #20b — unique mkstemp names keep same-name writers apart."""

    def test_same_name_concurrent_writes_leave_valid_entry(self, tmp_path: Path):
        cache_dir = tmp_path / "cache"
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def _writer(prefix: str) -> None:
            try:
                cache = MibCache(cache_dir, ttl_days=7)
                barrier.wait()
                for i in range(50):
                    cache.put("IF-MIB", _make_module("IF-MIB"), f"{prefix}-{i}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(prefix,)) for prefix in ("aaa", "bbb")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Whichever write landed last, the entry must be a complete, parseable
        # payload from one writer — never an interleaved half of both.
        path = cache_dir / "compiled" / "IF-MIB.json"
        data = json.loads(path.read_bytes())
        assert data["name"] == "IF-MIB"
        assert data["source_fingerprint"].startswith(("aaa-", "bbb-"))
        # No stray temp files survive a put.
        assert list((cache_dir / "compiled").glob("*.tmp")) == []


class TestUnreadableCacheEntries:
    """Issue #20c — cache read failures degrade to a miss, never a crash."""

    def test_permission_error_on_read_is_a_miss(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError("denied")):
            assert cache.get("IF-MIB") is None

    def test_toctou_file_not_found_on_read_is_a_miss(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        with patch("pathlib.Path.read_bytes", side_effect=FileNotFoundError("gone")):
            assert cache.get("IF-MIB") is None

    def test_stat_failure_in_ttl_check_treated_as_stale(self, tmp_path: Path):
        """A stat() failure inside the TTL check (e.g. TOCTOU removal, or
        unreadable metadata) must surface as a miss, not propagate."""
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        real_stat = Path.stat
        calls = {"n": 0}

        def _flaky_stat(self):
            calls["n"] += 1
            if calls["n"] == 2:  # the stat inside _is_stale (1st is is_file)
                raise PermissionError("denied")
            return real_stat(self)

        with patch("pathlib.Path.stat", _flaky_stat):
            assert cache.get("IF-MIB") is None

    @pytest.mark.asyncio
    async def test_compile_succeeds_when_cache_unreadable(self, tmp_path: Path):
        """A PermissionError on the cache read-path must not kill a compile."""
        config = CompilerConfig(
            output_dir=tmp_path / "out", cache_dir=tmp_path / "cache", formats=["json"]
        )
        # Warm the cache so the entry exists and the read path is exercised.
        await (
            MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2})).compile("TEST-MIB")
        )

        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError("denied")):
            results = await compiler.compile("TEST-MIB")
        # Miss → re-parsed → compiled, not failed.
        assert any(r.name == "TEST-MIB" and r.status == "compiled" for r in results)
