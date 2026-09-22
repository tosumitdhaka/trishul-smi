"""Issue #17: ZipReader must bound nested-archive reads by max_mib_size.

A small outer ZIP containing a highly-compressed nested archive must not be
fully decompressed into memory before the limit check runs (zip-bomb DoS).
Both MIB entries and nested-ZIP entries are read with a bounded
``max_size + 1`` read at every recursion depth, and oversized content raises
MibSizeLimitError. The limit is always a positive int (CompilerConfig
validates ``max_mib_size > 0``); there is no unlimited/None mode to mirror.

On top of the per-entry bound, the *aggregate* nested-archive bytes examined
per ``fetch()`` call is capped at ``4 x max_size``: an archive holding many
small nested zips must trip the aggregate guard rather than causing unbounded
time/temp-file churn (bounded memory, unbounded work). MIB-entry (leaf)
reads never count toward the aggregate.

Aggregate-cap trips are RECOVERABLE (v0.4.9-review L4): the budget raises an
internal sentinel and ``fetch()`` surfaces ``MibNotFoundError`` so the
ReaderChain falls through to the next reader/source. Only per-entry
``max_mib_size`` overruns remain fatal (``MibSizeLimitError``).
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import pytest

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.zipreader import ZipReader

MINIMAL_MIB = """TEST-MIB DEFINITIONS ::= BEGIN
END
"""


def _zip_bytes(entries: dict[str, bytes], compress_type: int = zipfile.ZIP_DEFLATED) -> bytes:
    """Build an in-memory ZIP archive (no temp files needed)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compress_type) as zf:
        for name, content in entries.items():
            zf.writestr(name, content, compress_type=compress_type)
    return buf.getvalue()


class TestNestedArchiveSizeLimit:
    @pytest.mark.asyncio
    async def test_oversized_nested_archive_raises_size_limit(self, tmp_path: Path):
        """A nested archive whose *uncompressed* bytes exceed the limit must
        raise MibSizeLimitError even though it compresses to a tiny blob in
        the outer ZIP — the exact zip-bomb shape from issue #17.
        """
        max_size = 512
        blob = b"\x00" * (max_size * 2)
        # Stored (not deflated) so the inner archive's file bytes stay large.
        inner_zip = _zip_bytes({"BIG-MIB.mib": blob}, compress_type=zipfile.ZIP_STORED)
        assert len(inner_zip) > max_size  # decompressed nested archive exceeds limit
        outer = tmp_path / "outer.zip"
        outer.write_bytes(_zip_bytes({"inner.zip": inner_zip}))  # deflates to a tiny outer

        reader = ZipReader(outer, max_size=max_size)
        with pytest.raises(MibSizeLimitError):
            await reader.fetch("BIG-MIB")

    @pytest.mark.asyncio
    async def test_oversized_mib_entry_inside_nested_archive(self, tmp_path: Path):
        """An oversized MIB entry *inside* a nested archive must hit the same
        per-entry size check as a top-level one.
        """
        max_size = 512
        big_mib = b"x" * 1024
        inner_zip = _zip_bytes({"BIG-MIB.mib": big_mib})
        assert len(inner_zip) <= max_size  # the archive itself passes; the entry trips
        outer = tmp_path / "outer.zip"
        outer.write_bytes(_zip_bytes({"inner.zip": inner_zip}))

        reader = ZipReader(outer, max_size=max_size)
        with pytest.raises(MibSizeLimitError):
            await reader.fetch("BIG-MIB")

    @pytest.mark.asyncio
    async def test_nested_archive_under_limit_extracts(self, tmp_path: Path):
        """Regression: a nested archive within the limit still extracts and
        decodes a valid MIB.
        """
        inner_zip = _zip_bytes({"IF-MIB.mib": MINIMAL_MIB.encode()})
        outer = tmp_path / "outer.zip"
        outer.write_bytes(_zip_bytes({"inner.zip": inner_zip}))

        reader = ZipReader(outer, max_size=1024 * 1024)
        text = await reader.fetch("IF-MIB")
        assert "TEST-MIB" in text

    @pytest.mark.asyncio
    async def test_many_small_nested_zips_trip_aggregate_cap(self, tmp_path: Path, caplog):
        """Per-entry bounds alone leave an archive of many small nested zips
        unbounded in aggregate work; the 4 x max_size per-fetch cap must trip.
        The trip is a recoverable miss (MibNotFoundError + warning), NOT a
        fatal MibSizeLimitError — the 4x aggregate is a heuristic, so a
        legitimate bundle must degrade to the ReaderChain fall-through (L4).
        """
        max_size = 512
        inner_zip = _zip_bytes({"UNRELATED-MIB.mib": b""})
        assert 0 < len(inner_zip) < max_size  # each nested archive is tiny alone
        assert 20 * len(inner_zip) > 4 * max_size  # aggregate would breach cap

        outer = tmp_path / "outer.zip"
        outer.write_bytes(_zip_bytes({f"inner{i}.zip": inner_zip for i in range(20)}))

        reader = ZipReader(outer, max_size=max_size)
        with caplog.at_level(logging.WARNING, logger="trishul_smi.reader.zipreader"):
            with pytest.raises(MibNotFoundError):
                await reader.fetch("IF-MIB")
        # The warning names the budget and the archive being scanned.
        assert any("aggregate" in r.message and "inner" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_aggregate_cap_falls_through_reader_chain(self, tmp_path: Path):
        """An aggregate-cap trip must surface as MibNotFoundError so the
        ReaderChain falls through to the next reader/source (L4)."""
        from trishul_smi.reader.chain import ReaderChain
        from trishul_smi.reader.localfile import FileReader

        max_size = 512
        inner_zip = _zip_bytes({"UNRELATED-MIB.mib": b""})
        outer = tmp_path / "outer.zip"
        outer.write_bytes(_zip_bytes({f"inner{i}.zip": inner_zip for i in range(20)}))

        fallback = tmp_path / "fallback"
        fallback.mkdir()
        (fallback / "IF-MIB.mib").write_text(MINIMAL_MIB)

        chain = ReaderChain(ZipReader(outer, max_size=max_size), FileReader(fallback))
        text = await chain.fetch("IF-MIB")
        assert "TEST-MIB" in text

    @pytest.mark.asyncio
    async def test_handful_of_nested_zips_stays_under_aggregate_cap(self, tmp_path: Path):
        """A corpus-typical archive with a handful of small nested zips stays
        comfortably under the aggregate cap and still extracts the target."""
        inner_zip = _zip_bytes({"IF-MIB.mib": MINIMAL_MIB.encode()})
        decoy_zip = _zip_bytes({"DECOY-MIB.mib": MINIMAL_MIB.encode()})
        outer = tmp_path / "outer.zip"
        outer.write_bytes(
            _zip_bytes({"decoy0.zip": decoy_zip, "decoy1.zip": decoy_zip, "inner.zip": inner_zip})
        )

        reader = ZipReader(outer, max_size=1024 * 1024)
        text = await reader.fetch("IF-MIB")
        assert "TEST-MIB" in text

    @pytest.mark.asyncio
    async def test_oversized_archive_at_max_depth_still_bounded(self, tmp_path: Path):
        """An oversized nested archive encountered at the deepest recursion
        level (depth 4) is still caught by the bounded read — it raises
        MibSizeLimitError instead of recursing into the unbounded read or
        being silently dropped by the depth guard.
        """
        max_size = 512
        blob = b"\x00" * (max_size * 2)
        leaf_zip = _zip_bytes({"payload.bin": blob}, compress_type=zipfile.ZIP_STORED)
        assert len(leaf_zip) > max_size

        # Wrap the oversized archive in 5 ZIP levels so the bounded read that
        # trips the limit happens at _depth == 4 (the max level that reads).
        current = leaf_zip
        for _ in range(5):
            current = _zip_bytes({"nested.zip": current})
        outer = tmp_path / "outer.zip"
        outer.write_bytes(current)

        reader = ZipReader(outer, max_size=max_size)
        with pytest.raises(MibSizeLimitError):
            await reader.fetch("IF-MIB")
