"""Tests for HttpReader using pytest-httpx to intercept HTTP calls.

Each test class covers a distinct behavioural contract:
  TestHttpReaderBasic     — happy path, 404, size limits, CM guard
  TestHttpReaderETag      — ETag / 304 / disk-cache interaction
  TestHttpReaderFallback  — multiple source URLs, all-fail path
  TestHttpReaderCache     — atomic write, no stale .tmp files
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.httpclient import HttpReader

_TEMPLATE = "https://mibs.example.com/@mib@"
_IF_MIB_URL = "https://mibs.example.com/IF-MIB"
_MINIMAL = "IF-MIB DEFINITIONS ::= BEGIN\nEND\n"


# ---------------------------------------------------------------------------
# Basic fetch
# ---------------------------------------------------------------------------

class TestHttpReaderBasic:
    @pytest.mark.asyncio
    async def test_successful_fetch_returns_text(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text=_MINIMAL)
        async with HttpReader(_TEMPLATE) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == _MINIMAL

    @pytest.mark.asyncio
    async def test_head_404_raises_mib_not_found(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=404)
        async with HttpReader(_TEMPLATE) as reader:
            with pytest.raises(MibNotFoundError, match="IF-MIB"):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_get_404_raises_mib_not_found(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=404)
        async with HttpReader(_TEMPLATE) as reader:
            with pytest.raises(MibNotFoundError, match="404"):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_content_length_exceeds_limit_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_IF_MIB_URL, method="HEAD", status_code=200,
            headers={"content-length": "2048"},
        )
        async with HttpReader(_TEMPLATE, max_size=512) as reader:
            with pytest.raises(MibSizeLimitError):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_response_body_exceeds_limit_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text="x" * 1024)
        async with HttpReader(_TEMPLATE, max_size=512) as reader:
            with pytest.raises(MibSizeLimitError):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_context_manager_required(self):
        """Calling fetch() without entering the CM must raise RuntimeError."""
        reader = HttpReader(_TEMPLATE)
        with pytest.raises(RuntimeError, match="context manager"):
            await reader.fetch("IF-MIB")


# ---------------------------------------------------------------------------
# ETag / 304 caching
# ---------------------------------------------------------------------------

class TestHttpReaderETag:
    @pytest.mark.asyncio
    async def test_304_with_disk_cache_returns_cached_content(
        self, httpx_mock: HTTPXMock, tmp_path: Path
    ):
        """304 response + matching disk cache → no second GET needed."""
        cache_dir = tmp_path / "cache"

        # First fetch: HEAD + GET — writes disk cache
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(
            url=_IF_MIB_URL, method="GET",
            text=_MINIMAL, headers={"etag": '"abc123"'},
        )
        # Second fetch (same reader, ETag in memory): HEAD + GET 304 — reads disk cache
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=304)

        async with HttpReader(_TEMPLATE, cache_dir=cache_dir, cache_ttl_days=0) as reader:
            first  = await reader.fetch("IF-MIB")  # populates ETag + disk cache
            second = await reader.fetch("IF-MIB")  # 304 → disk cache hit

        assert first == _MINIMAL
        assert second == _MINIMAL

    @pytest.mark.asyncio
    async def test_304_without_disk_cache_falls_back_to_unconditional_get(
        self, httpx_mock: HTTPXMock
    ):
        """304 + no cache_dir → stale ETag cleared, unconditional GET issued."""
        # First fetch: HEAD + GET (no disk cache_dir)
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(
            url=_IF_MIB_URL, method="GET",
            text=_MINIMAL, headers={"etag": '"abc123"'},
        )
        # Second fetch: HEAD + GET 304 + unconditional GET fallback
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=304)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text=_MINIMAL)

        async with HttpReader(_TEMPLATE, cache_ttl_days=0) as reader:  # no cache_dir
            await reader.fetch("IF-MIB")
            result = await reader.fetch("IF-MIB")

        assert result == _MINIMAL

    @pytest.mark.asyncio
    async def test_etag_header_sent_on_second_request(
        self, httpx_mock: HTTPXMock, tmp_path: Path
    ):
        """If-None-Match header must be present on the second GET."""
        cache_dir = tmp_path / "cache"
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(
            url=_IF_MIB_URL, method="GET",
            text=_MINIMAL, headers={"etag": '"abc123"'},
        )
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=304)

        async with HttpReader(_TEMPLATE, cache_dir=cache_dir, cache_ttl_days=0) as reader:
            await reader.fetch("IF-MIB")
            await reader.fetch("IF-MIB")

        requests = httpx_mock.get_requests()
        second_get = next(
            r for r in requests[2:] if r.method == "GET"
        )
        assert second_get.headers.get("if-none-match") == '"abc123"'


# ---------------------------------------------------------------------------
# Multiple source URL fallback
# ---------------------------------------------------------------------------

class TestHttpReaderFallback:
    _BACKUP = "https://backup.example.com/@mib@"
    _BACKUP_URL = "https://backup.example.com/IF-MIB"

    @pytest.mark.asyncio
    async def test_falls_back_to_second_source_on_404(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=404)
        httpx_mock.add_response(url=self._BACKUP_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=self._BACKUP_URL, method="GET", text=_MINIMAL)
        async with HttpReader(_TEMPLATE, self._BACKUP) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == _MINIMAL

    @pytest.mark.asyncio
    async def test_all_sources_404_raises_mib_not_found(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=404)
        httpx_mock.add_response(url=self._BACKUP_URL, method="HEAD", status_code=404)
        async with HttpReader(_TEMPLATE, self._BACKUP) as reader:
            with pytest.raises(MibNotFoundError):
                await reader.fetch("IF-MIB")


# ---------------------------------------------------------------------------
# Atomic cache write
# ---------------------------------------------------------------------------

class TestHttpReaderCache:
    @pytest.mark.asyncio
    async def test_no_stale_tmp_files_after_successful_fetch(
        self, httpx_mock: HTTPXMock, tmp_path: Path
    ):
        """_write_cache must not leave *.tmp files behind on success."""
        cache_dir = tmp_path / "http-cache"
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text=_MINIMAL)

        async with HttpReader(_TEMPLATE, cache_dir=cache_dir) as reader:
            await reader.fetch("IF-MIB")

        raw_dir = cache_dir / "raw"
        tmp_files = list(raw_dir.glob("*.tmp")) if raw_dir.exists() else []
        mib_files = list(raw_dir.glob("*.mib")) if raw_dir.exists() else []
        assert tmp_files == [], f"Stale .tmp files left behind: {tmp_files}"
        assert len(mib_files) == 1

    @pytest.mark.asyncio
    async def test_cache_content_matches_fetched_text(
        self, httpx_mock: HTTPXMock, tmp_path: Path
    ):
        cache_dir = tmp_path / "http-cache"
        httpx_mock.add_response(url=_IF_MIB_URL, method="HEAD", status_code=200)
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text=_MINIMAL)

        async with HttpReader(_TEMPLATE, cache_dir=cache_dir) as reader:
            result = await reader.fetch("IF-MIB")

        raw_dir = cache_dir / "raw"
        cached = next(raw_dir.glob("*.mib")).read_text(encoding="utf-8")
        assert cached == result == _MINIMAL
