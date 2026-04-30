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
        # varargs — NOT ReaderChain([reader1, reader2])
        chain = ReaderChain(FileReader(d1), FileReader(d2))
        text = await chain.fetch("X-MIB")
        assert text

    @pytest.mark.asyncio
    async def test_raises_combined_not_found(self, tmp_path: Path):
        d1 = tmp_path / "d1"
        d1.mkdir()
        chain = ReaderChain(FileReader(d1))
        with pytest.raises(MibNotFoundError, match="not found in any reader"):
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
