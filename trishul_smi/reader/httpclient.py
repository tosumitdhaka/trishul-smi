from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.base import AbstractReader

_PLACEHOLDER = "@mib@"


class HttpReader(AbstractReader):
    """Fetches MIBs from HTTP(S) sources.

    Features:
    - httpx.AsyncClient with explicit timeout
    - Exponential backoff via tenacity
    - ETag caching: skips re-download when server returns 304
    - TTL: forces re-fetch after cache_ttl_days days regardless of ETag
    - Content-Length pre-check against max_size before downloading body
    """

    def __init__(
        self,
        *url_templates: str,
        timeout: float = 30.0,
        retries: int = 3,
        max_size: int = 10 * 1024 * 1024,
        cache_dir: Path | None = None,
        cache_ttl_days: int = 7,
    ) -> None:
        self._templates = list(url_templates)
        self._timeout = timeout
        self._retries = retries
        self._max_size = max_size
        self._cache_dir = cache_dir
        self._cache_ttl_seconds = cache_ttl_days * 86_400
        # ETag store: {url: etag_string}
        self._etags: dict[str, str] = {}
        # Fetch timestamp store: {url: unix_timestamp}
        self._fetched_at: dict[str, float] = {}
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HttpReader:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _client_or_raise(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "HttpReader must be used as an async context manager: "
                "`async with HttpReader(...) as reader:`"
            )
        return self._client

    def _is_stale(self, url: str) -> bool:
        """True if the cached entry for url has exceeded TTL."""
        if self._cache_ttl_seconds <= 0:
            return False
        fetched = self._fetched_at.get(url)
        if fetched is None:
            return True
        return (time.monotonic() - fetched) > self._cache_ttl_seconds

    async def fetch(self, mib_name: str) -> str:
        last_exc: Exception = MibNotFoundError(mib_name)
        for template in self._templates:
            url = template.replace(_PLACEHOLDER, mib_name)
            try:
                text = await self._fetch_url(url)
                return text
            except (MibNotFoundError, MibSizeLimitError):
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        raise MibNotFoundError(
            f"MIB '{mib_name}' not found at any HTTP source. Last error: {last_exc}"
        )

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _fetch_url(self, url: str) -> str:
        client = self._client_or_raise()
        headers: dict[str, str] = {}

        # Send ETag if cached and not stale
        if not self._is_stale(url) and url in self._etags:
            headers["If-None-Match"] = self._etags[url]

        # HEAD pre-check for Content-Length
        head = await client.head(url)
        if head.status_code == 404:
            raise MibNotFoundError(f"HTTP 404: {url}")
        content_length = head.headers.get("content-length")
        if content_length and int(content_length) > self._max_size:
            raise MibSizeLimitError(
                f"{url} Content-Length {content_length} exceeds limit {self._max_size}"
            )

        response = await client.get(url, headers=headers)

        if response.status_code == 304:
            # Not modified — return from disk cache
            cached = self._read_cache(url)
            if cached is not None:
                return cached
            # Cache miss despite 304 (shouldn’t happen) — fall through to GET

        if response.status_code == 404:
            raise MibNotFoundError(f"HTTP 404: {url}")

        response.raise_for_status()

        if len(response.content) > self._max_size:
            raise MibSizeLimitError(
                f"{url} response body {len(response.content)} bytes exceeds limit"
            )

        text = response.text

        # Update ETag + timestamp
        etag = response.headers.get("etag")
        if etag:
            self._etags[url] = etag
        self._fetched_at[url] = time.monotonic()

        # Persist to disk cache
        self._write_cache(url, text)
        return text

    # ------------------------------------------------------------------
    # Simple disk cache helpers (raw text, not MibModule)
    # MibModule-level caching is handled by resolver/cache.py
    # ------------------------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        safe_name = url.replace("://", "_").replace("/", "_").replace(":", "_")
        return self._cache_dir / "raw" / f"{safe_name}.mib"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _write_cache(self, url: str, text: str) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
