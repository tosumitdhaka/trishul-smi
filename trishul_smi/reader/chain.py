from __future__ import annotations

from trishul_smi.errors import MibNotFoundError
from trishul_smi.reader.base import AbstractReader


class ReaderChain:
    """Tries each reader in order, returns the first successful result.

    If all readers raise MibNotFoundError, re-raises with a combined message.
    Any other exception (MibSizeLimitError, network error) propagates immediately.
    """

    def __init__(self, readers: list[AbstractReader]) -> None:
        if not readers:
            raise ValueError("ReaderChain requires at least one reader")
        self._readers = readers

    async def fetch(self, mib_name: str) -> str:
        not_found_msgs: list[str] = []
        for reader in self._readers:
            try:
                return await reader.fetch(mib_name)
            except MibNotFoundError as exc:
                not_found_msgs.append(str(exc))
                continue
            # All other exceptions propagate immediately
        raise MibNotFoundError(
            f"MIB '{mib_name}' not found in any reader.\n"
            + "\n".join(f"  - {msg}" for msg in not_found_msgs)
        )
