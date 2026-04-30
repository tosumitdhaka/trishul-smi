from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.base import AbstractReader


class ZipReader(AbstractReader):
    """Reads MIB files from one or more ZIP archives.

    Handles nested ZIPs correctly — seeds `data = b""` before the loop,
    fixing the pysmi NameError-on-nested-ZIP bug (see motivation in plan.md).
    """

    def __init__(self, *zip_paths: str | Path, max_size: int = 10 * 1024 * 1024) -> None:
        self._zip_paths: list[Path] = [Path(p) for p in zip_paths]
        self._max_size = max_size

    async def fetch(self, mib_name: str) -> str:
        for zip_path in self._zip_paths:
            result = self._search_zip(zip_path, mib_name)
            if result is not None:
                return result
        raise MibNotFoundError(
            f"MIB '{mib_name}' not found in ZIP archives: "
            + ", ".join(str(p) for p in self._zip_paths)
        )

    def _search_zip(
        self, zip_path: Path, mib_name: str, _depth: int = 0
    ) -> str | None:
        """Recursively search zip_path for mib_name. Returns text or None."""
        if _depth > 4:
            return None
        if not zip_path.is_file():
            return None

        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()

                for entry in names:
                    stem = Path(entry).stem
                    suffix = Path(entry).suffix.lower()
                    if stem == mib_name and suffix in ("", ".mib", ".txt", ".my"):
                        data: bytes = b""  # initialised before read — no NameError
                        with zf.open(entry) as fh:
                            data = fh.read()
                        if len(data) > self._max_size:
                            raise MibSizeLimitError(
                                f"{entry} in {zip_path} exceeds size limit {self._max_size}"
                            )
                        return data.decode("utf-8", errors="replace")

                for entry in names:
                    if entry.lower().endswith(".zip"):
                        data = b""  # reset before each nested read
                        with zf.open(entry) as fh:
                            data = fh.read()
                        with tempfile.NamedTemporaryFile(
                            suffix=".zip", delete=False
                        ) as tmp:
                            tmp.write(data)
                            tmp_path = Path(tmp.name)
                        try:
                            result = self._search_zip(tmp_path, mib_name, _depth + 1)
                            if result is not None:
                                return result
                        finally:
                            tmp_path.unlink(missing_ok=True)

        except zipfile.BadZipFile:
            return None

        return None
