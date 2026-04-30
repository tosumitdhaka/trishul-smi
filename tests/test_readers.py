"""Unit tests for reader/ — FileReader, ZipReader, ReaderChain.
HttpReader integration tests use pytest-httpx and are in test_httpreader.py.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.reader.chain import ReaderChain
from trishul_smi.reader.localfile import FileReader
from trishul_smi.reader.zipreader import ZipReader

MINIMAL_MIB = """TEST-MIB DEFINITIONS ::= BEGIN
END
"""


# ---------------------------------------------------------------------------
# FileReader
# ---------------------------------------------------------------------------

class TestFileReader:
    @pytest.mark.asyncio
    async def test_reads_file_with_extension(self, tmp_path: Path):
        (tmp_path / "IF-MIB.mib").write_text(MINIMAL_MIB)
        reader = FileReader(tmp_path)
        text = await reader.fetch("IF-MIB")
        assert "TEST-MIB" in text

    @pytest.mark.asyncio
    async def test_reads_file_without_extension(self, tmp_path: Path):
        (tmp_path / "IF-MIB").write_text(MINIMAL_MIB)
        reader = FileReader(tmp_path)
        text = await reader.fetch("IF-MIB")
        assert "TEST-MIB" in text

    @pytest.mark.asyncio
    async def test_raises_mib_not_found(self, tmp_path: Path):
        reader = FileReader(tmp_path)
        with pytest.raises(MibNotFoundError, match="IF-MIB"):
            await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_raises_size_limit(self, tmp_path: Path):
        big = tmp_path / "BIG-MIB.mib"
        big.write_bytes(b"x" * 1024)
        reader = FileReader(tmp_path, max_size=512)
        with pytest.raises(MibSizeLimitError):
            await reader.fetch("BIG-MIB")

    @pytest.mark.asyncio
    async def test_searches_multiple_dirs(self, tmp_path: Path):
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d2 / "X-MIB.mib").write_text(MINIMAL_MIB)
        reader = FileReader(d1, d2)
        text = await reader.fetch("X-MIB")
        assert text


# ---------------------------------------------------------------------------
# ZipReader
# ---------------------------------------------------------------------------

class TestZipReader:
    def _make_zip(self, path: Path, filename: str, content: str) -> Path:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(filename, content)
        return path

    @pytest.mark.asyncio
    async def test_reads_mib_from_zip(self, tmp_path: Path):
        zp = self._make_zip(tmp_path / "mibs.zip", "IF-MIB.mib", MINIMAL_MIB)
        reader = ZipReader(zp)
        text = await reader.fetch("IF-MIB")
        assert "TEST-MIB" in text

    @pytest.mark.asyncio
    async def test_raises_not_found(self, tmp_path: Path):
        zp = self._make_zip(tmp_path / "mibs.zip", "OTHER.mib", MINIMAL_MIB)
        reader = ZipReader(zp)
        with pytest.raises(MibNotFoundError):
            await reader.fetch("MISSING-MIB")

    @pytest.mark.asyncio
    async def test_raises_size_limit(self, tmp_path: Path):
        zp = tmp_path / "mibs.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("BIG-MIB.mib", "x" * 1024)
        reader = ZipReader(zp, max_size=512)
        with pytest.raises(MibSizeLimitError):
            await reader.fetch("BIG-MIB")

    # --- Nested ZIP tests (K) ---

    @pytest.mark.asyncio
    async def test_reads_mib_from_nested_zip(self, tmp_path: Path):
        """A MIB inside a ZIP that is itself inside another ZIP must be found."""
        inner = tmp_path / "inner.zip"
        with zipfile.ZipFile(inner, "w") as zf:
            zf.writestr("IF-MIB.mib", MINIMAL_MIB)
        outer = tmp_path / "outer.zip"
        with zipfile.ZipFile(outer, "w") as zf:
            zf.write(inner, "inner.zip")
        reader = ZipReader(outer)
        text = await reader.fetch("IF-MIB")
        assert "TEST-MIB" in text

    @pytest.mark.asyncio
    async def test_depth_limit_stops_recursion(self, tmp_path: Path):
        """A ZIP chain deeper than the depth guard must raise MibNotFoundError
        rather than recursing indefinitely or hitting Python\'s stack limit.
        The guard returns None at depth > limit, causing MibNotFoundError.
        We build 8 levels — well beyond any reasonable depth limit.
        """
        # Build leaf: deepest.zip contains IF-MIB.mib
        current = tmp_path / "level_7.zip"
        with zipfile.ZipFile(current, "w") as zf:
            zf.writestr("IF-MIB.mib", MINIMAL_MIB)
        # Wrap it 7 more times: level_6 -> level_5 -> ... -> level_0
        for i in range(6, -1, -1):
            outer = tmp_path / f"level_{i}.zip"
            with zipfile.ZipFile(outer, "w") as zf:
                zf.write(current, current.name)
            current = outer
        reader = ZipReader(current)  # current is now level_0.zip
        with pytest.raises(MibNotFoundError):
            await reader.fetch("IF-MIB")

    @pytest.mark.asyncio
    async def test_no_tmp_file_leftover_after_nested_fetch(self, tmp_path: Path, monkeypatch):
        """finally: unlink() in _search_zip must clean up temp files even
        when the fetch succeeds.
        """
        import tempfile as _tempfile

        created_temps: list[Path] = []
        _orig_ntf = _tempfile.NamedTemporaryFile

        def _tracking_ntf(*args, **kwargs):
            # Redirect all temp files into tmp_path so we can inspect them.
            kwargs["dir"] = str(tmp_path)
            f = _orig_ntf(*args, **kwargs)
            created_temps.append(Path(f.name))
            return f

        monkeypatch.setattr(_tempfile, "NamedTemporaryFile", _tracking_ntf)

        inner = tmp_path / "inner.zip"
        with zipfile.ZipFile(inner, "w") as zf:
            zf.writestr("IF-MIB.mib", MINIMAL_MIB)
        outer = tmp_path / "outer.zip"
        with zipfile.ZipFile(outer, "w") as zf:
            zf.write(inner, "inner.zip")

        reader = ZipReader(outer)
        await reader.fetch("IF-MIB")

        # Every temp file created during the nested fetch must have been unlinked.
        for p in created_temps:
            assert not p.exists(), (
                f"Temp file {p.name} was not cleaned up after nested ZIP fetch"
            )


# ---------------------------------------------------------------------------
# ReaderChain
# ---------------------------------------------------------------------------

class TestReaderChain:
    @pytest.mark.asyncio
    async def test_returns_first_successful(self, tmp_path: Path):
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d2 / "X-MIB.mib").write_text(MINIMAL_MIB)
        chain = ReaderChain(FileReader(d1), FileReader(d2))
        text = await chain.fetch("X-MIB")
        assert text

    @pytest.mark.asyncio
    async def test_raises_combined_not_found(self, tmp_path: Path):
        d1 = tmp_path / "d1"
        d1.mkdir()
        chain = ReaderChain(FileReader(d1))
        with pytest.raises(MibNotFoundError, match="MISSING"):
            await chain.fetch("MISSING")

    def test_raises_on_empty_readers(self):
        with pytest.raises(ValueError, match="at least one reader"):
            ReaderChain()

    @pytest.mark.asyncio
    async def test_size_limit_propagates_immediately(self, tmp_path: Path):
        """MibSizeLimitError should NOT be caught and swallowed by the chain."""
        d1 = tmp_path / "d1"
        d1.mkdir()
        big = d1 / "BIG-MIB.mib"
        big.write_bytes(b"x" * 2048)
        chain = ReaderChain(FileReader(d1, max_size=512))
        with pytest.raises(MibSizeLimitError):
            await chain.fetch("BIG-MIB")
