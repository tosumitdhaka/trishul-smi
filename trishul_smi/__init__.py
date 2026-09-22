"""trishul-smi — A clean, modern SMI/MIB compiler."""

from typing import TYPE_CHECKING, Any

from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.errors import (
    CircularDependencyError,
    MibCacheError,
    MibNotFoundError,
    ParseError,
    TrishulError,
    WriterError,
)
from trishul_smi.models import CompileResult
from trishul_smi.reader.localfile import FileReader
from trishul_smi.reader.zipreader import ZipReader
from trishul_smi.version import VERSION

if TYPE_CHECKING:
    from trishul_smi.reader.httpclient import HttpReader

__version__ = VERSION

__all__ = [
    "__version__",
    "MibCompiler",
    "CompilerConfig",
    "CompileResult",
    "FileReader",
    "HttpReader",
    "ZipReader",
    "TrishulError",
    "MibNotFoundError",
    "ParseError",
    "CircularDependencyError",
    "WriterError",
    "MibCacheError",
]


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute access.

    ``HttpReader`` lives behind an httpx import, so it is resolved lazily to
    keep ``import trishul_smi`` free of heavy HTTP dependencies (issue #23).
    Everything else in ``__all__`` is imported eagerly above.
    """
    if name == "HttpReader":
        from trishul_smi.reader.httpclient import HttpReader

        return HttpReader
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
