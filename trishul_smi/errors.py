"""Exception hierarchy for trishul-smi.

Design rules:
- Flat hierarchy: all exceptions inherit directly from TrishulError.
- No imports from other trishul_smi modules at module level.
- Forward references use TYPE_CHECKING guard to avoid circular imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # reserved for future typed forward refs


class TrishulError(Exception):
    """Base exception for all trishul-smi errors."""


class MibNotFoundError(TrishulError):
    """No reader in the chain could locate the requested MIB."""


class MibSizeLimitError(TrishulError):
    """MIB source exceeds CompilerConfig.max_mib_size."""


class ParseError(TrishulError):
    """Grammar or syntax error encountered while parsing ASN.1 source."""


class CircularDependencyError(TrishulError):
    """A circular import chain was detected during dependency resolution."""


class NetworkError(TrishulError):
    """HTTP/network failure that is not a simple 404 (MibNotFoundError).

    Raised by HttpReader when retries are exhausted and the underlying cause
    is a transport error, DNS failure, timeout, or a 5xx response.
    Wraps the original exception as ``__cause__`` so callers can inspect it.

    Example::

        raise NetworkError(f"GET {url} failed after {retries} retries") from exc
    """


class CodeGenError(TrishulError):
    """Code generation failed for a MibModule."""


class WriterError(TrishulError):
    """Output artifact could not be written to the target destination."""


class MibCacheError(TrishulError):
    """Cache read or write operation failed."""
