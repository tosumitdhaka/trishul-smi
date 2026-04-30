from __future__ import annotations

from pathlib import Path

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.base import AbstractReader

_EXTENSIONS = ["", ".mib", ".txt", ".my"]


class FileReader(AbstractReader):
    """Reads MIB files from one or more local filesystem directories."""

    def __init__(self, *dirs: str | Path, max_size: int = 10 * 1024 * 1024) -> None:
        self._dirs: list[Path] = [Path(d) for d in dirs]
        self._max_size = max_size

    async def fetch(self, mib_name: str) -> str:
        for directory in self._dirs:
            for ext in _EXTENSIONS:
                candidate = directory / f"{mib_name}{ext}"
                if not candidate.is_file():
                    continue
                # Open first, check size after read to avoid TOCTOU race
                # between stat() and open(). Read max_size+1 bytes so we can
                # detect an overrun without reading the entire file.
                with open(candidate, "rb") as fh:
                    data = fh.read(self._max_size + 1)
                if len(data) > self._max_size:
                    raise MibSizeLimitError(
                        f"{candidate} exceeds size limit of {self._max_size} bytes"
                    )
                return data.decode("utf-8", errors="replace")
        raise MibNotFoundError(
            f"MIB '{mib_name}' not found in directories: "
            + ", ".join(str(d) for d in self._dirs)
        )
