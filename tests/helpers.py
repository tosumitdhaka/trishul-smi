"""Shared test helpers for the trishul-smi test suite.

Kept in a separate module (not conftest.py) so that test files can import
directly with ``from tests.helpers import MockReader`` regardless of whether
tests/ is treated as a package (has __init__.py) or a rootdir collection.
"""

from __future__ import annotations

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.base import AbstractReader


class MockReader(AbstractReader):
    """In-memory reader for unit tests.

    Parameters
    ----------
    texts:
        Mapping of MIB name → source text returned by fetch().
    size_limit_names:
        MIB names that raise MibSizeLimitError instead of returning text.
    """

    def __init__(
        self,
        texts: dict[str, str],
        size_limit_names: set[str] | None = None,
    ) -> None:
        self._texts = texts
        self._size_limit_names = size_limit_names or set()

    async def fetch(self, mib_name: str) -> str:
        if mib_name in self._size_limit_names:
            raise MibSizeLimitError(f"{mib_name} exceeds size limit")
        if mib_name not in self._texts:
            raise MibNotFoundError(mib_name)
        return self._texts[mib_name]
