from typing import TYPE_CHECKING, Any

from trishul_smi.reader.base import AbstractReader
from trishul_smi.reader.chain import ReaderChain
from trishul_smi.reader.localfile import FileReader
from trishul_smi.reader.zipreader import ZipReader

if TYPE_CHECKING:
    from trishul_smi.reader.httpclient import HttpReader

__all__ = ["AbstractReader", "ReaderChain", "FileReader", "HttpReader", "ZipReader"]


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute access for ``HttpReader``.

    httpclient.py imports httpx, which is heavyweight; keep it out of the
    import graph until a consumer actually asks for ``HttpReader``.
    """
    if name == "HttpReader":
        from trishul_smi.reader.httpclient import HttpReader

        return HttpReader
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
