"""Shared pytest fixtures and test helpers.

MockReader is defined here (not in individual test files) so that
test_compiler.py and any future test file can reuse the same implementation
without drift.
"""
from __future__ import annotations

import pytest

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.base import AbstractReader


class MockReader(AbstractReader):
    """In-memory reader for unit tests.

    Parameters
    ----------
    texts:
        Mapping of MIB name → source text returned by fetch().
    size_limit_names:
        MIB names that should raise MibSizeLimitError instead of returning text.
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


@pytest.fixture
def mock_reader_factory() -> type[MockReader]:
    """Return the MockReader class so tests can construct instances directly.

    Usage::

        def test_something(mock_reader_factory):
            reader = mock_reader_factory({"IF-MIB": "..."})
    """
    return MockReader
