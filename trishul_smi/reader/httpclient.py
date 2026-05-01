from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
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
    - httpx.AsyncClient with explicit timeout (from constructor)
    - Exponential backoff: retry count from ``self._retries``, NOT hardcoded
    - ETag caching: skips re-download when server returns 304
    - TTL: forces re-fetch after cache_ttl_days days regardless of ETag
    - Content-Length pre-check against max_size before downloading body

    Fallback behaviour
    ------------------
    When multiple ``url_templates`` are provided, fetch() tries each in order.
    MibNotFoundError (404 or no match) continues to the next template.
    MibSizeLimitError propagates immediately — it is a configuration error,
    not a per-source failure.
    RuntimeError (context-manager guard) propagates immediately — it is a
    programming error that must never be silently swallowed.

    304 edge case
    -------------
    If the server returns 304 but the local cache file is missing (cache_dir
    is None, the file was deleted, or the process restarted without cache),
    we fall back to an unconditional GET without If-None-Match rather than
    calling raise_for_status() on the 304 (which would raise HTTPStatusError
    since 304 is not a 2xx). This matters for long-running daemon deployments
    where the on-disk cache can be cleared independently of the ETag store.
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
        self._etags: dict[str, str] = {}
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
        if self._cache_ttl_seconds <= 0:
            return False
        fetched = self._fetched_at.get(url)
        return fetched is None or (time.monotonic() - fetched) > self._cache_ttl_seconds

    async def fetch(self, mib_name: str) -> str:
        """Try each URL template in order; return the first success.

        Exception policy:
        - ``RuntimeError``: re-raise immediately (programming error — no CM).
        - ``MibSizeLimitError``: re-raise immediately (config error).
        - ``MibNotFoundError``: continue to next template (per-source 404).
        - All other exceptions: continue to next template (transient errors).
        """
        last_exc: Exception = MibNotFoundError(mib_name)
        for template in self._templates:
            url = template.replace(_PLACEHOLDER, mib_name)
            try:
                return await self._fetch_url_with_retry(url)
            except (RuntimeError, MibSizeLimitError):
                raise  # programming / config errors — never swallow
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        raise MibNotFoundError(
            f"MIB '{mib_name}' not found at any HTTP source. Last error: {last_exc}"
        )

    async def _fetch_url_with_retry(self, url: str) -> str:
        """Retry _fetch_url using self._retries (NOT a hardcoded constant)."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(httpx.TransportError),
            stop=stop_after_attempt(self._retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        ):
            with attempt:
                return await self._fetch_url(url)
        raise MibNotFoundError(url)  # unreachable; satisfies type checker

    async def _fetch_url(self, url: str) -> str:
        client = self._client_or_raise()
        headers: dict[str, str] = {}

        if not self._is_stale(url) and url in self._etags:
            headers["If-None-Match"] = self._etags[url]

        # HEAD pre-check: bail early if Content-Length already exceeds limit
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
            cached = self._read_cache(url)
            if cached is not None:
                return cached
            # Cache miss despite 304: clear stale ETag, retry through the full
            # retry wrapper so transient errors are handled by the retry policy.
            self._etags.pop(url, None)
            return await self._fetch_url_with_retry(url)

        if response.status_code == 404:
            raise MibNotFoundError(f"HTTP 404: {url}")

        response.raise_for_status()

        if len(response.content) > self._max_size:
            raise MibSizeLimitError(
                f"{url} response body {len(response.content)} bytes exceeds limit"
            )

        text = response.text
        etag = response.headers.get("etag")
        if etag:
            self._etags[url] = etag
        self._fetched_at[url] = time.monotonic()
        self._write_cache(url, text)
        return text

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
        """Atomically write *text* to the cache file for *url*."""
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)  # atomic on POSIX; best-effort on Windows
