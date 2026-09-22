from __future__ import annotations

import warnings
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError, NetworkError
from trishul_smi.reader.base import AbstractReader

_PLACEHOLDER = "@mib@"

# Sentinel default for the deprecated ``cache_ttl_days`` parameter: lets
# __init__ distinguish "caller passed it" from "caller left the default",
# so the DeprecationWarning fires only when the value is actually supplied.
_UNSET: object = object()

# Status codes that unambiguously mean "this MIB does not exist at this
# source" (as opposed to a server-side failure). Every other non-2xx status
# is treated as a transport/server failure and classified as NetworkError.
_NOT_FOUND_STATUSES = frozenset({404, 410})


class HttpReader(AbstractReader):
    """Fetches MIBs from HTTP(S) sources.

    Features:
    - httpx.AsyncClient with explicit timeout (from constructor)
    - Exponential backoff: retry count from ``self._retries``, NOT hardcoded
    - Streaming GET: the body is consumed in chunks and the request is aborted
      as soon as the accumulated size exceeds ``max_size`` — no HEAD pre-check,
      no Content-Length parsing, so a lying or absent Content-Length cannot
      force a full download into memory

    Fallback behaviour
    ------------------
    When multiple ``url_templates`` are provided, fetch() tries each in order.
    MibNotFoundError (404/410) continues to the next template.
    MibSizeLimitError propagates immediately — it is a configuration error,
    not a per-source failure.
    RuntimeError (context-manager guard) propagates immediately — it is a
    programming error that must never be silently swallowed.

    Error mapping (GET is authoritative)
    ------------------------------------
    - 404 Not Found / 410 Gone → MibNotFoundError (MIB absent; try next source)
    - Any other non-2xx status → NetworkError (server-side failure)
    - Transport errors (connection reset, DNS failure, timeout) are retried,
      then classified as NetworkError — a transport failure is NOT a missing MIB
    """

    def __init__(
        self,
        *url_templates: str,
        timeout: float = 30.0,
        retries: int = 3,
        max_size: int | None = 10 * 1024 * 1024,
        cache_ttl_days: object = _UNSET,
    ) -> None:
        self._templates = list(url_templates)
        self._timeout = timeout
        self._retries = retries
        # None means unlimited: streaming still applies, just without the byte cap.
        self._max_size = max_size
        if cache_ttl_days is not _UNSET:
            warnings.warn(
                "HttpReader.cache_ttl_days is deprecated and unused: HttpReader no "
                "longer maintains a raw-body cache. Set CompilerConfig.cache_ttl_days "
                "to control the compiled-module MibCache instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HttpReader:
        # follow_redirects: a 301/302 to an existing MIB is transport plumbing,
        # not a fetch failure — the GET response after redirects is authoritative.
        self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
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

    async def fetch(self, mib_name: str) -> str:
        """Try each URL template in order; return the first success.

        Exception policy:
        - ``RuntimeError``: re-raise immediately (programming error — no CM).
        - ``MibSizeLimitError``: re-raise immediately (config error).
        - ``MibNotFoundError``: continue to next template (per-source 404/410).
        - All other exceptions: continue to next template and classify as
          ``NetworkError`` if no source ultimately succeeds.
        """
        last_not_found: MibNotFoundError = MibNotFoundError(mib_name)
        last_network: Exception | None = None
        for template in self._templates:
            url = template.replace(_PLACEHOLDER, mib_name)
            try:
                return await self._fetch_url_with_retry(url)
            except (RuntimeError, MibSizeLimitError):
                raise  # programming / config errors — never swallow
            except MibNotFoundError as exc:
                last_not_found = exc
                continue
            except Exception as exc:  # noqa: BLE001
                last_network = exc
                continue
        if last_network is not None:
            raise NetworkError(
                f"HTTP fetch failed for MIB '{mib_name}' from all configured sources. "
                f"Last error: {last_network}"
            ) from last_network
        raise MibNotFoundError(
            f"MIB '{mib_name}' not found at any HTTP source. Last error: {last_not_found}"
        )

    async def _fetch_url_with_retry(self, url: str) -> str:
        """Retry _fetch_url using self._retries (NOT a hardcoded constant).

        The retryable call wraps the full consume-or-abort in ``_fetch_url``:
        a transport error raised mid-body exits the ``client.stream`` context
        manager (which closes the half-read stream), so a retried request
        never leaks the previous attempt's stream.
        """
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
        """Streaming GET: status-check, then consume-or-abort the body.

        The whole exchange runs inside ``async with client.stream(...)``, so
        raising here (not-found, non-2xx, size-limit, or mid-body transport
        error) exits the context manager and closes the response stream.
        """
        client = self._client_or_raise()
        async with client.stream("GET", url) as response:
            if response.status_code in _NOT_FOUND_STATUSES:
                raise MibNotFoundError(f"HTTP {response.status_code}: {url}")
            response.raise_for_status()
            return await self._consume_body(response, url)

    async def _consume_body(self, response: httpx.Response, url: str) -> str:
        """Read *response*'s body in chunks, aborting at the size cap.

        ``max_size`` is applied to the *accumulated* byte count; the chunk
        that would breach the limit is never buffered. Raising propagates out
        of the caller's ``client.stream`` context manager, which closes the
        connection — the oversized download is stopped, not completed.

        Content-Length is deliberately ignored: with streaming the count is
        authoritative, and a malformed header cannot cause a misreport.
        """
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            if self._max_size is not None and total + len(chunk) > self._max_size:
                raise MibSizeLimitError(f"{url} response body exceeds limit {self._max_size} bytes")
            chunks.append(chunk)
            total += len(chunk)
        text = b"".join(chunks).decode(response.encoding or "utf-8")
        return text
