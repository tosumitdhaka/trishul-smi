"""Abstract base for all MIB readers, plus the FetchProtocol for typing.

FetchProtocol is a structural (Protocol) type used wherever a reader is
accepted as a parameter — resolver.py, compiler.py — so that mypy can
verify the .fetch() contract without requiring inheritance from AbstractReader.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class FetchProtocol(Protocol):
    """Structural type for any object that can fetch a MIB by name.

    Implemented by AbstractReader subclasses and ReaderChain.
    Using a Protocol (not ABC) lets resolver.py and compiler.py accept
    mock readers in tests without any inheritance boilerplate.
    """
    async def fetch(self, mib_name: str) -> str:
        """Fetch raw ASN.1 text for *mib_name*.

        Raises:
            MibNotFoundError: if the MIB cannot be located.
            MibSizeLimitError: if the MIB exceeds the configured size limit.
        """
        ...


class AbstractReader(ABC):
    """Base class for concrete readers (FileReader, HttpReader, ZipReader)."""

    @abstractmethod
    async def fetch(self, mib_name: str) -> str:
        """Fetch raw ASN.1 text for *mib_name*."""
