"""ReaderChain: tries a sequence of readers in priority order.

ReaderChain satisfies FetchProtocol so it can be passed directly to
MibResolver and MibCompiler without any special handling.

Fallback semantics:
- MibNotFoundError from a reader → try next reader.
- Any other exception (MibSizeLimitError, httpx.TransportError after
  retries exhausted, …) → propagates immediately without trying further.

This means a size-limit hit on the first reader will NOT silently fall
through to a second reader that might return a truncated copy.
"""
from __future__ import annotations

from trishul_smi.errors import MibNotFoundError
from trishul_smi.reader.base import FetchProtocol


class ReaderChain:
    """Try a list of readers in order; return the first successful result.

    Example::

        chain = ReaderChain(
            FileReader("/usr/share/snmp/mibs"),
            ZipReader("/opt/vendor-mibs.zip"),
            http_reader,            # HttpReader used as async context manager
        )
        text = await chain.fetch("IF-MIB")
    """

    def __init__(self, *readers: FetchProtocol) -> None:
        if not readers:
            raise ValueError("ReaderChain requires at least one reader")
        self._readers: list[FetchProtocol] = list(readers)

    def append(self, reader: FetchProtocol) -> None:
        """Add a reader to the end of the chain at runtime."""
        self._readers.append(reader)

    async def fetch(self, mib_name: str) -> str:
        """Try each reader in order; raise the last MibNotFoundError if all fail."""
        last_exc: MibNotFoundError = MibNotFoundError(mib_name)
        for reader in self._readers:
            try:
                return await reader.fetch(mib_name)
            except MibNotFoundError as exc:
                last_exc = exc
                # continue to next reader
        raise last_exc
