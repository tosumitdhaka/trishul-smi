"""ReaderChain: tries a sequence of readers in priority order.

ReaderChain satisfies FetchProtocol so it can be passed directly to
MibResolver and MibCompiler without any special handling.

Fallback semantics:
- MibNotFoundError from a reader → try next reader.
- Any other exception (MibSizeLimitError, NetworkError after retries, …)
  propagates immediately without trying further readers.

This prevents a size-limit hit on reader 1 from silently falling through
to a second reader that might return a truncated or stale copy.
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
            http_reader,
        )
        text = await chain.fetch("IF-MIB")
    """

    def __init__(self, *readers: FetchProtocol) -> None:
        if not readers:
            raise ValueError("ReaderChain requires at least one reader")
        self._readers: list[FetchProtocol] = list(readers)

    def append(self, reader: FetchProtocol) -> None:
        """Add a reader to the end of the chain at runtime.

        Note:
            ``MibCompiler`` does **not** call this method internally — it
            always constructs a fresh ``ReaderChain(*self._readers)`` at the
            start of each ``compile()`` call. This method is provided for
            library consumers who manage a ``ReaderChain`` directly and need
            to add readers after construction (e.g. in a long-running service
            that registers sources lazily). It is not used by the CLI or the
            compiler pipeline in v0.1.
        """
        self._readers.append(reader)

    async def fetch(self, mib_name: str) -> str:
        """Try each reader in order; raise the last MibNotFoundError if all fail."""
        # last_exc is MibNotFoundError | None rather than narrowing to
        # MibNotFoundError upfront, which would require a dummy construction.
        # We assert before re-raise so mypy is satisfied and the intent is clear.
        last_exc: MibNotFoundError | None = None
        for reader in self._readers:
            try:
                return await reader.fetch(mib_name)
            except MibNotFoundError as exc:
                last_exc = exc
                # continue to next reader
        assert last_exc is not None  # guaranteed: loop ran ≥1 iteration
        raise last_exc
