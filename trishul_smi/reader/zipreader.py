from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.base import AbstractReader

# Extensions tried when looking for a MIB entry inside a ZIP.
_MIB_SUFFIXES = {"", ".mib", ".txt", ".my"}

# Aggregate cap on nested-archive extraction per top-level fetch(): the sum of
# nested-archive bytes examined must not exceed this multiple of max_size.
# Per-entry reads are already bounded by max_size, but an archive holding
# arbitrarily many small nested zips would otherwise cause unbounded
# time/temp-file churn (bounded memory, unbounded work). MIB-entry (leaf)
# reads are deliberately excluded from the aggregate — only nested-archive
# extraction bytes count.
_NESTED_AGGREGATE_MULTIPLIER = 4


class _NestedScanBudget:
    """Tracks the aggregate nested-archive bytes examined in one fetch() call.

    Threaded through ``_search_zip`` recursion rather than stored on the
    reader: ``fetch()`` may be invoked concurrently for different MIB names
    (resolver uses asyncio.gather), so the budget must be per-call.
    """

    __slots__ = ("_limit", "_used")

    def __init__(self, max_size: int) -> None:
        self._limit = _NESTED_AGGREGATE_MULTIPLIER * max_size
        self._used = 0

    def charge(self, size: int) -> None:
        """Account for *size* nested-archive bytes; raise past the cap."""
        self._used += size
        if self._used > self._limit:
            raise MibSizeLimitError(
                "nested-archive scan exceeds aggregate limit "
                f"{self._limit} bytes "
                f"({_NESTED_AGGREGATE_MULTIPLIER} x max_size)"
            )


class ZipReader(AbstractReader):
    """Reads MIB files from one or more ZIP archives.

    Handles nested ZIPs — `data = b""` is initialised before the read loop,
    fixing the pysmi NameError-on-nested-ZIP bug.

    Size guard (issue #17): every read — MIB entries and nested ZIP entries,
    at every recursion depth — is bounded to ``max_size + 1`` bytes, and
    oversized content raises MibSizeLimitError, so a highly-compressed nested
    archive cannot be fully decompressed into memory (zip-bomb DoS).

    Aggregate guard (issue #17 follow-up): the *sum* of nested-archive bytes
    examined in one ``fetch()`` is additionally capped at
    ``4 x max_size`` (``_NESTED_AGGREGATE_MULTIPLIER``) — per-entry bounds
    alone leave unbounded time/temp-file churn for archives holding many small
    nested zips. MIB-entry (leaf) reads do not count toward the aggregate.
    """

    def __init__(self, *zip_paths: str | Path, max_size: int = 10 * 1024 * 1024) -> None:
        self._zip_paths: list[Path] = [Path(p) for p in zip_paths]
        self._max_size = max_size

    async def fetch(self, mib_name: str) -> str:
        budget = _NestedScanBudget(self._max_size)
        for zip_path in self._zip_paths:
            result = self._search_zip(zip_path, mib_name, budget)
            if result is not None:
                return result
        raise MibNotFoundError(
            f"MIB '{mib_name}' not found in ZIP archives: "
            + ", ".join(str(p) for p in self._zip_paths)
        )

    def _search_zip(
        self,
        zip_path: Path,
        mib_name: str,
        budget: _NestedScanBudget,
        _depth: int = 0,
    ) -> str | None:
        if _depth > 4:
            return None
        if not zip_path.is_file():
            return None

        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()

                for entry in names:
                    p = Path(entry)
                    # suffix check uses _MIB_SUFFIXES set — no redundant condition
                    if p.stem == mib_name and p.suffix.lower() in _MIB_SUFFIXES:
                        data: bytes = b""  # initialised before read — no NameError
                        with zf.open(entry) as fh:
                            data = fh.read(self._max_size + 1)
                        if len(data) > self._max_size:
                            raise MibSizeLimitError(
                                f"{entry} in {zip_path} exceeds limit {self._max_size}"
                            )
                        return data.decode("utf-8", errors="replace")

                for entry in names:
                    if not entry.lower().endswith(".zip"):
                        continue
                    data = b""  # reset before each nested read
                    with zf.open(entry) as fh:
                        data = fh.read(self._max_size + 1)
                    if len(data) > self._max_size:
                        raise MibSizeLimitError(
                            f"{entry} in {zip_path} exceeds limit {self._max_size}"
                        )
                    # Nested-archive extraction bytes count toward the aggregate
                    # cap (per-entry bound alone leaves many-small-zips churn).
                    budget.charge(len(data))
                    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                        tmp.write(data)
                        tmp_path = Path(tmp.name)
                    try:
                        result = self._search_zip(tmp_path, mib_name, budget, _depth + 1)
                        if result is not None:
                            return result
                    finally:
                        tmp_path.unlink(missing_ok=True)

        except zipfile.BadZipFile:
            return None

        return None
