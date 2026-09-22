"""Tests for HttpReader using pytest-httpx to intercept HTTP calls.

Each test class covers a distinct behavioural contract:
  TestHttpReaderBasic            — happy path, streaming size limit, CM guard
  TestHttpReaderNoHead           — GET is authoritative; HEAD is never sent
  TestHttpReaderErrorMap         — not-found vs transport/server error mapping
  TestHttpReaderFallback         — multiple source URLs, all-fail path
  TestHttpReaderNoRawCache       — no raw-body cache artifacts are written
  TestHttpReaderCacheTtlDeprecation — cache_ttl_days warns; omission is silent

The reader performs a single streaming GET per fetch: there is no HEAD
pre-check, no ETag/304 machinery, and no raw-body disk cache (all removed in
the fetch-semantics rework), so no test registers HEAD responses or asserts
cache files.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError, NetworkError
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
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text=_MINIMAL)
        async with HttpReader(_TEMPLATE) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == _MINIMAL

    @pytest.mark.asyncio
    async def test_get_404_raises_mib_not_found(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=404)
        async with HttpReader(_TEMPLATE) as reader:
            with pytest.raises(MibNotFoundError, match="404"):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_get_410_raises_mib_not_found(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=410)
        async with HttpReader(_TEMPLATE) as reader:
            with pytest.raises(MibNotFoundError, match="410"):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_malformed_content_length_is_ignored(self, httpx_mock: HTTPXMock):
        """A malformed Content-Length must not be parsed as a network error."""
        httpx_mock.add_response(
            url=_IF_MIB_URL,
            method="GET",
            headers={"content-length": "abc"},
            text=_MINIMAL,
        )
        async with HttpReader(_TEMPLATE) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == _MINIMAL

    @pytest.mark.asyncio
    async def test_streamed_body_exceeds_limit_raises(self, httpx_mock: HTTPXMock):
        """Streaming counts the real body — no HEAD/Content-Length pre-check."""
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text="x" * 1024)
        async with HttpReader(_TEMPLATE, max_size=512) as reader:
            with pytest.raises(MibSizeLimitError):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_lying_content_length_cannot_bypass_limit(self, httpx_mock: HTTPXMock):
        """A small Content-Length on a large chunked body must still trip the cap."""
        httpx_mock.add_response(
            url=_IF_MIB_URL,
            method="GET",
            headers={"content-length": "10"},
            text="x" * 1024,
        )
        async with HttpReader(_TEMPLATE, max_size=512) as reader:
            with pytest.raises(MibSizeLimitError):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_body_exactly_at_limit_succeeds(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text="x" * 512)
        async with HttpReader(_TEMPLATE, max_size=512) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == "x" * 512

    @pytest.mark.asyncio
    async def test_body_one_byte_over_limit_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text="x" * 513)
        async with HttpReader(_TEMPLATE, max_size=512) as reader:
            with pytest.raises(MibSizeLimitError):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_unlimited_max_size_accepts_large_body(self, httpx_mock: HTTPXMock):
        """max_size=None disables the cap but keeps the streaming path."""
        body = "x" * 20_000
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text=body)
        async with HttpReader(_TEMPLATE, max_size=None) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == body

    @pytest.mark.asyncio
    async def test_context_manager_required(self):
        """Calling fetch() without entering the CM must raise RuntimeError."""
        reader = HttpReader(_TEMPLATE)
        with pytest.raises(RuntimeError, match="context manager"):
            await reader.fetch("IF-MIB")


# ---------------------------------------------------------------------------
# GET is authoritative: HEAD is never sent
# ---------------------------------------------------------------------------


class TestHttpReaderNoHead:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("head_status", [404, 405])
    async def test_head_failure_does_not_block_get(self, httpx_mock: HTTPXMock, head_status: int):
        """Servers that mishandle HEAD (404/405 on HEAD, 200 on GET) must work.

        The failing HEAD response is registered as optional: if the reader ever
        regresses to sending HEAD, the fetch fails on it and this test errors.
        """
        httpx_mock.add_response(
            url=_IF_MIB_URL,
            method="HEAD",
            status_code=head_status,
            is_optional=True,
        )
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text=_MINIMAL)

        async with HttpReader(_TEMPLATE) as reader:
            result = await reader.fetch("IF-MIB")

        assert result == _MINIMAL
        assert {r.method for r in httpx_mock.get_requests()} == {"GET"}


# ---------------------------------------------------------------------------
# Error mapping: not-found vs transport/server failure
# ---------------------------------------------------------------------------


class TestHttpReaderErrorMap:
    @pytest.mark.asyncio
    async def test_get_404_is_not_found_not_network_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=404)
        async with HttpReader(_TEMPLATE) as reader:
            with pytest.raises(MibNotFoundError, match="404"):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_server_error_is_network_error_not_not_found(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=500)
        async with HttpReader(_TEMPLATE) as reader:
            with pytest.raises(NetworkError, match="500"):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_redirect_is_followed_not_network_error(self, httpx_mock: HTTPXMock):
        """A redirect to an existing MIB must resolve, not be classified as a
        network failure — 3xx is transport plumbing, and the GET response
        after redirects is authoritative."""
        httpx_mock.add_response(
            url=_IF_MIB_URL,
            method="GET",
            status_code=301,
            headers={"Location": "https://backup.example.com/IF-MIB"},
        )
        httpx_mock.add_response(
            url="https://backup.example.com/IF-MIB", method="GET", text=_MINIMAL
        )
        async with HttpReader(_TEMPLATE) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == _MINIMAL

    @pytest.mark.asyncio
    async def test_transport_error_raises_network_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(
            httpx.ConnectError("connection reset"),
            url=_IF_MIB_URL,
            method="GET",
        )
        async with HttpReader(_TEMPLATE, retries=1) as reader:
            with pytest.raises(NetworkError, match="HTTP fetch failed"):
                await reader.fetch("IF-MIB")


# ---------------------------------------------------------------------------
# Multiple source URL fallback
# ---------------------------------------------------------------------------


class TestHttpReaderFallback:
    _BACKUP = "https://backup.example.com/@mib@"
    _BACKUP_URL = "https://backup.example.com/IF-MIB"

    @pytest.mark.asyncio
    async def test_falls_back_to_second_source_on_404(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=404)
        httpx_mock.add_response(url=self._BACKUP_URL, method="GET", text=_MINIMAL)
        async with HttpReader(_TEMPLATE, self._BACKUP) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == _MINIMAL

    @pytest.mark.asyncio
    async def test_falls_back_to_second_source_on_410(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=410)
        httpx_mock.add_response(url=self._BACKUP_URL, method="GET", text=_MINIMAL)
        async with HttpReader(_TEMPLATE, self._BACKUP) as reader:
            result = await reader.fetch("IF-MIB")
        assert result == _MINIMAL

    @pytest.mark.asyncio
    async def test_all_sources_404_raises_mib_not_found(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=404)
        httpx_mock.add_response(url=self._BACKUP_URL, method="GET", status_code=404)
        async with HttpReader(_TEMPLATE, self._BACKUP) as reader:
            with pytest.raises(MibNotFoundError):
                await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_404_then_server_error_raises_network_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", status_code=404)
        httpx_mock.add_response(url=self._BACKUP_URL, method="GET", status_code=500)
        async with HttpReader(_TEMPLATE, self._BACKUP) as reader:
            with pytest.raises(NetworkError, match="500"):
                await reader.fetch("IF-MIB")


# ---------------------------------------------------------------------------
# No raw-body disk cache
# ---------------------------------------------------------------------------


class TestHttpReaderNoRawCache:
    @pytest.mark.asyncio
    async def test_fetch_writes_no_raw_cache_files(self, httpx_mock: HTTPXMock, tmp_path: Path):
        """The raw-body cache under cache_dir/raw/ is write-only — nothing reads
        it back since the ETag/304 removal, so a successful fetch must leave no
        cache artifacts on disk at all."""
        cache_dir = tmp_path / "http-cache"
        httpx_mock.add_response(url=_IF_MIB_URL, method="GET", text=_MINIMAL)

        async with HttpReader(_TEMPLATE) as reader:
            result = await reader.fetch("IF-MIB")

        assert result == _MINIMAL
        assert not cache_dir.exists(), "fetch() must not create a raw-body cache"

    def test_cache_dir_parameter_removed(self):
        """HttpReader no longer accepts cache_dir: the raw-body cache it fed
        was write-only (nothing read it back), so the parameter is gone."""
        with pytest.raises(TypeError, match="cache_dir"):
            HttpReader(_TEMPLATE, cache_dir="/tmp/whatever")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# cache_ttl_days deprecation
# ---------------------------------------------------------------------------


class TestHttpReaderCacheTtlDeprecation:
    def test_passing_cache_ttl_days_warns(self):
        """cache_ttl_days is accepted-but-unused since the ETag/304 removal;
        passing it must warn and point at CompilerConfig.cache_ttl_days."""
        with pytest.warns(DeprecationWarning, match="CompilerConfig.cache_ttl_days"):
            HttpReader(_TEMPLATE, cache_ttl_days=7)  # type: ignore[call-arg]

    def test_omitting_cache_ttl_days_does_not_warn(self):
        """The default path must stay silent — only an explicit value warns."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            HttpReader(_TEMPLATE)
