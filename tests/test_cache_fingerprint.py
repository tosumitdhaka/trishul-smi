"""Content-fingerprint cache tests (issue #12).

The compiled-module cache is name-keyed; without a content fingerprint an
updated MIB file would serve stale entries. The resolver now always fetches
first and fingerprints the source; a fingerprint mismatch is a miss. The
mtime TTL remains an additional staleness layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from tests.helpers import MockReader
from trishul_smi.models.mib_module import MibModule
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


def _fp(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_module(name: str) -> MibModule:
    return MibModule(name=name, language="SMIv2")


class TestMibCacheFingerprint:
    def test_put_records_fingerprint_in_payload(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"), _fp("raw v1"))
        data = json.loads((tmp_path / "compiled" / "IF-MIB.json").read_bytes())
        assert data["source_fingerprint"] == _fp("raw v1")

    def test_matching_fingerprint_hits(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"), _fp("raw v1"))
        assert cache.get("IF-MIB", _fp("raw v1")) is not None

    def test_changed_fingerprint_is_miss(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"), _fp("raw v1"))
        assert cache.get("IF-MIB", _fp("raw v2")) is None

    def test_changed_fingerprint_removes_entry(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"), _fp("raw v1"))
        cache.get("IF-MIB", _fp("raw v2"))
        assert not (tmp_path / "compiled" / "IF-MIB.json").exists()

    def test_entry_without_fingerprint_misses_when_checked(self, tmp_path: Path):
        """Pre-fingerprint entries (no recorded fingerprint) miss on a
        fingerprinted lookup — the safe upgrade behaviour."""
        cache = MibCache(tmp_path, ttl_days=7)
        cache.put("IF-MIB", _make_module("IF-MIB"))
        # A fingerprintless lookup still hits (backward compatible).
        assert cache.get("IF-MIB") is not None
        # A fingerprinted lookup misses (and removes) the legacy entry.
        assert cache.get("IF-MIB", _fp("raw v1")) is None

    def test_ttl_still_applies_with_fingerprint(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=1)
        cache.put("IF-MIB", _make_module("IF-MIB"), _fp("raw v1"))
        path = tmp_path / "compiled" / "IF-MIB.json"
        old = time.time() - 2 * 86_400
        os.utime(path, (old, old))
        assert cache.get("IF-MIB", _fp("raw v1")) is None

    def test_ttl_zero_never_expires_with_fingerprint(self, tmp_path: Path):
        cache = MibCache(tmp_path, ttl_days=0)
        cache.put("IF-MIB", _make_module("IF-MIB"), _fp("raw v1"))
        path = tmp_path / "compiled" / "IF-MIB.json"
        old = time.time() - 365 * 86_400
        os.utime(path, (old, old))
        assert cache.get("IF-MIB", _fp("raw v1")) is not None


class TestResolverFingerprintIntegration:
    @pytest.mark.asyncio
    async def test_source_change_invalidates_cache_and_reparses(self, tmp_path: Path):
        """Same module name, changed source content → cache miss + re-parse."""
        cache = MibCache(tmp_path, ttl_days=7)
        text_v2 = MINIMAL_V2.replace('LAST-UPDATED "200001010000Z"', 'LAST-UPDATED "200101010000Z"')
        reader = MockReader({"TEST-MIB": MINIMAL_V2})
        first = await MibResolver(reader, SmiParser(), cache=cache).resolve(["TEST-MIB"])
        assert first.ok
        assert first.modules[0].lastupdated == "200001010000Z"
        assert first.cached == set()

        reader = MockReader({"TEST-MIB": text_v2})
        second = await MibResolver(reader, SmiParser(), cache=cache).resolve(["TEST-MIB"])
        assert second.ok
        # Re-parsed from the updated source — not a cache hit.
        assert second.modules[0].lastupdated == "200101010000Z"
        assert second.cached == set()

    @pytest.mark.asyncio
    async def test_identical_source_is_cache_hit_without_reparse(self, tmp_path: Path):
        """Same name, identical content → cache hit, no re-parse."""
        cache = MibCache(tmp_path, ttl_days=7)
        reader = MockReader({"TEST-MIB": MINIMAL_V2})
        first = await MibResolver(reader, SmiParser(), cache=cache).resolve(["TEST-MIB"])
        assert first.ok
        assert first.cached == set()

        second = await MibResolver(reader, SmiParser(), cache=cache).resolve(["TEST-MIB"])
        assert second.ok
        assert second.modules[0].name == "TEST-MIB"
        assert second.cached == {"TEST-MIB"}
