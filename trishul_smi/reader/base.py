from __future__ import annotations

from abc import ABC, abstractmethod


class AbstractReader(ABC):
    """Fetches raw ASN.1 MIB text from a source. Stateless per-call."""

    @abstractmethod
    async def fetch(self, mib_name: str) -> str:
        """Return raw ASN.1 text for mib_name.

        Raises:
            MibNotFoundError: if the MIB cannot be located.
            MibSizeLimitError: if the source exceeds max_mib_size.
        """
