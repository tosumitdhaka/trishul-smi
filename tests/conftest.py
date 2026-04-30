"""Shared pytest fixtures for the trishul-smi test suite.

MockReader lives in tests/helpers.py (importable module) rather than here
because conftest.py is not reliably importable when tests/ is a package
(has __init__.py). The fixture below exposes MockReader to tests that prefer
the fixture pattern; tests that instantiate it directly should import from
tests.helpers.
"""
from __future__ import annotations

import pytest

from tests.helpers import MockReader

__all__ = ["MockReader"]  # re-export so conftest itself is a valid import target


@pytest.fixture
def mock_reader_factory() -> type[MockReader]:
    """Return the MockReader class so tests can construct instances directly.

    Usage::

        def test_something(mock_reader_factory):
            reader = mock_reader_factory({"IF-MIB": "..."})
    """
    return MockReader
