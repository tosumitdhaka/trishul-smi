"""Integration tests for MibCompiler, ReaderChain, and output formatters."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.models import CompileResult
from trishul_smi.models.mib_module import MibModule
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.output.pysnmp_fmt import PysnmpFormatter
from trishul_smi.reader.base import AbstractReader
from trishul_smi.reader.chain import ReaderChain


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_V2 = """
TEST-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, Integer32 FROM SNMPv2-SMI ;
testMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Test Org"
    CONTACT-INFO "test@example.com"
    DESCRIPTION  "Minimal test MIB."
    ::= { 1 3 }
END
"""

OBJECT_V2 = """
OBJECT-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32 FROM SNMPv2-SMI ;
objectMIB MODULE-IDENTITY
    LAST-UPDATED "200001010000Z"
    ORGANIZATION "Obj Org"
    CONTACT-INFO "obj@example.com"
    DESCRIPTION  "MIB with an OBJECT-TYPE."
    ::= { 1 7 }
foo OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A test object."
    ::= { objectMIB 1 }
END
"""


class MockReader(AbstractReader):
    def __init__(
        self,
        texts: dict[str, str],
        size_limit_names: set[str] | None = None,
    ) -> None:
        self._texts = texts
        self._size_limit_names = size_limit_names or set()

    async def fetch(self, mib_name: str) -> str:
        if mib_name in self._size_limit_names:
            raise MibSizeLimitError(f"{mib_name} exceeds limit")
        if mib_name not in self._texts:
            raise MibNotFoundError(mib_name)
        return self._texts[mib_name]


# ---------------------------------------------------------------------------
# ReaderChain
# ---------------------------------------------------------------------------

class TestReaderChain:
    @pytest.mark.asyncio
    async def test_returns_first_reader_hit(self):
        r1 = MockReader({"IF-MIB": "content-from-r1"})
        r2 = MockReader({"IF-MIB": "content-from-r2"})
        chain = ReaderChain(r1, r2)
        assert await chain.fetch("IF-MIB") == "content-from-r1"

    @pytest.mark.asyncio
    async def test_falls_back_to_second_reader(self):
        r1 = MockReader({})
        r2 = MockReader({"IF-MIB": "content-from-r2"})
        chain = ReaderChain(r1, r2)
        assert await chain.fetch("IF-MIB") == "content-from-r2"

    @pytest.mark.asyncio
    async def test_raises_if_all_miss(self):
        chain = ReaderChain(MockReader({}), MockReader({}))
        with pytest.raises(MibNotFoundError):
            await chain.fetch("MISSING")

    @pytest.mark.asyncio
    async def test_non_notfound_error_propagates_immediately(self):
        """MibSizeLimitError must not be swallowed by the fallback logic."""
        r1 = MockReader({}, size_limit_names={"BIG-MIB"})
        r2 = MockReader({"BIG-MIB": "small copy"})
        chain = ReaderChain(r1, r2)
        with pytest.raises(MibSizeLimitError):
            await chain.fetch("BIG-MIB")

    def test_empty_readers_raises(self):
        with pytest.raises(ValueError, match="at least one reader"):
            ReaderChain()

    @pytest.mark.asyncio
    async def test_append_adds_reader(self):
        chain = ReaderChain(MockReader({}))
        chain.append(MockReader({"X": "found"}))
        assert await chain.fetch("X") == "found"


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------

class TestJsonFormatter:
    def test_output_is_valid_json(self):
        m = MibModule(name="IF-MIB", language="SMIv2",
                      imports={"SNMPv2-SMI": ["OBJECT-TYPE"]})
        data = json.loads(JsonFormatter().format(m))
        assert data["module"] == "IF-MIB"
        assert data["language"] == "SMIv2"
        assert "generated_by" in data

    def test_objects_serialised(self):
        from trishul_smi.models.mib_object import MibObject
        obj = MibObject(name="ifIndex", oid="1.3.6.1",
                        oid_path=[1, 3, 6, 1], object_type="OBJECT-TYPE",
                        syntax="Integer32", max_access="read-only",
                        status="current")
        m = MibModule(name="IF-MIB", language="SMIv2",
                      objects={"ifIndex": obj})
        data = json.loads(JsonFormatter().format(m))
        assert "ifIndex" in data["objects"]
        assert data["objects"]["ifIndex"]["syntax"] == "Integer32"

    def test_empty_module_serialises(self):
        data = json.loads(JsonFormatter().format(
            MibModule(name="EMPTY-MIB", language="SMIv1")
        ))
        assert data["objects"] == {}
        assert data["types"] == {}
        assert data["notifications"] == {}


# ---------------------------------------------------------------------------
# PysnmpFormatter
# ---------------------------------------------------------------------------

class TestPysnmpFormatter:
    def test_output_is_python_source(self):
        m = MibModule(name="IF-MIB", language="SMIv2")
        src = PysnmpFormatter().format(m)
        assert "mibBuilder" in src
        assert "IF-MIB" in src

    def test_hyphens_replaced_in_identifiers(self):
        from trishul_smi.models.mib_object import MibObject
        obj = MibObject(name="if-mib-obj", oid="1.3", oid_path=[1, 3],
                        object_type="OBJECT-TYPE", syntax="Integer32")
        m = MibModule(name="IF-MIB", language="SMIv2",
                      objects={"if-mib-obj": obj})
        src = PysnmpFormatter().format(m)
        assert "if_mib_obj" in src
        assert "if-mib-obj =" not in src  # not a valid Python identifier

    def test_imports_rendered(self):
        m = MibModule(name="IF-MIB", language="SMIv2",
                      imports={"SNMPv2-SMI": ["ModuleIdentity", "Integer32"]})
        src = PysnmpFormatter().format(m)
        assert "importSymbols" in src
        assert "SNMPv2-SMI" in src


# ---------------------------------------------------------------------------
# MibCompiler (integration)
# ---------------------------------------------------------------------------

class TestMibCompiler:
    def test_no_readers_raises(self):
        compiler = MibCompiler()
        with pytest.raises(RuntimeError, match="No readers"):
            import asyncio
            asyncio.run(compiler.compile("TEST-MIB"))

    @pytest.mark.asyncio
    async def test_compile_writes_json(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path / "out",
            formats=["json"],
            cache_dir=None,
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"TEST-MIB": MINIMAL_V2})
        )
        results = await compiler.compile("TEST-MIB")
        compiled = [r for r in results if r.status == "compiled"]
        assert any(r.name == "TEST-MIB" for r in compiled)
        # JSON file written to disk
        json_file = tmp_path / "out" / "TEST-MIB.json"
        assert json_file.exists()
        data = json.loads(json_file.read_bytes())
        assert data["module"] == "TEST-MIB"

    @pytest.mark.asyncio
    async def test_compile_writes_pysnmp(self, tmp_path: Path):
        config = CompilerConfig(
            output_dir=tmp_path / "out",
            formats=["pysnmp"],
            cache_dir=None,
        )
        compiler = MibCompiler(config).add_reader(
            MockReader({"TEST-MIB": MINIMAL_V2})
        )
        results = await compiler.compile("TEST-MIB")
        compiled = [r for r in results if r.status == "compiled"]
        assert any(r.name == "TEST-MIB" for r in compiled)
        py_file = tmp_path / "out" / "TEST-MIB.py"
        assert py_file.exists()
        assert "mibBuilder" in py_file.read_text()

    @pytest.mark.asyncio
    async def test_missing_mib_status_failed(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({}))
        results = await compiler.compile("MISSING-MIB")
        failed = [r for r in results if r.status == "failed"]
        assert any(r.name == "MISSING-MIB" for r in failed)

    @pytest.mark.asyncio
    async def test_fluent_add_reader(self, tmp_path: Path):
        """add_reader() returns self so calls can be chained."""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None,
                                formats=["json"])
        compiler = (
            MibCompiler(config)
            .add_reader(MockReader({}))
            .add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        )
        results = await compiler.compile("TEST-MIB")
        assert any(r.status == "compiled" and r.name == "TEST-MIB"
                   for r in results)

    @pytest.mark.asyncio
    async def test_output_dir_created(self, tmp_path: Path):
        out = tmp_path / "deep" / "nested" / "out"
        config = CompilerConfig(output_dir=out, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(
            MockReader({"TEST-MIB": MINIMAL_V2})
        )
        await compiler.compile("TEST-MIB")
        assert out.is_dir()

    @pytest.mark.asyncio
    async def test_compile_result_has_output_paths(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None,
                                formats=["json"])
        compiler = MibCompiler(config).add_reader(
            MockReader({"TEST-MIB": MINIMAL_V2})
        )
        results = await compiler.compile("TEST-MIB")
        compiled = next(r for r in results if r.name == "TEST-MIB")
        assert len(compiled.output_paths) == 1
        assert compiled.output_paths[0].suffix == ".json"

    @pytest.mark.asyncio
    async def test_compile_object_mib(self, tmp_path: Path):
        """Compile a MIB with an OBJECT-TYPE and verify the JSON output."""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None,
                                formats=["json"])
        compiler = MibCompiler(config).add_reader(
            MockReader({"OBJECT-MIB": OBJECT_V2})
        )
        results = await compiler.compile("OBJECT-MIB")
        compiled = next((r for r in results if r.name == "OBJECT-MIB"), None)
        assert compiled is not None
        assert compiled.status == "compiled"
        data = json.loads((tmp_path / "OBJECT-MIB.json").read_bytes())
        assert "foo" in data["objects"]
        assert data["objects"]["foo"]["syntax"] == "Integer32"
        assert data["objects"]["foo"]["max_access"] == "read-only"
