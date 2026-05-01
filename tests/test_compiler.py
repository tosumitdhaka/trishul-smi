"""Integration tests for MibCompiler, ReaderChain, and output formatters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers import MockReader
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.errors import MibNotFoundError, MibSizeLimitError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.output.json_fmt import JsonFormatter
from trishul_smi.output.pysnmp_fmt import PysnmpFormatter, _pysnmp_obj_class
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
        m = MibModule(name="IF-MIB", language="SMIv2", imports={"SNMPv2-SMI": ["OBJECT-TYPE"]})
        data = json.loads(JsonFormatter().format(m))
        assert data["module"] == "IF-MIB"
        assert data["language"] == "SMIv2"
        assert "generated_by" in data

    def test_objects_serialised(self):
        obj = MibObject(
            name="ifIndex",
            oid="1.3.6.1",
            oid_path=[1, 3, 6, 1],
            object_type="OBJECT-TYPE",
            syntax="Integer32",
            max_access="read-only",
            status="current",
        )
        m = MibModule(name="IF-MIB", language="SMIv2", objects={"ifIndex": obj})
        data = json.loads(JsonFormatter().format(m))
        assert "ifIndex" in data["objects"]
        assert data["objects"]["ifIndex"]["syntax"] == "Integer32"

    def test_empty_module_serialises(self):
        data = json.loads(JsonFormatter().format(MibModule(name="EMPTY-MIB", language="SMIv1")))
        assert data["objects"] == {}
        assert data["types"] == {}
        assert data["notifications"] == {}


# ---------------------------------------------------------------------------
# PysnmpFormatter + object-class detection
# ---------------------------------------------------------------------------


class TestPysnmpFormatter:
    def test_output_is_python_source(self):
        m = MibModule(name="IF-MIB", language="SMIv2")
        src = PysnmpFormatter().format(m)
        assert "mibBuilder" in src
        assert "IF-MIB" in src

    def test_preamble_always_imports_notification_type(self):
        """NotificationType and TextualConvention must appear even with no IMPORTS."""
        m = MibModule(name="IF-MIB", language="SMIv2")
        src = PysnmpFormatter().format(m)
        assert "NotificationType" in src
        assert "TextualConvention" in src

    def test_preamble_imports_object_identifier_from_asn1(self):
        """ObjectIdentifier (ASN.1 value type) must be imported so that
        SYNTAX OBJECT IDENTIFIER objects don't raise NameError at load time.
        """
        m = MibModule(name="IF-MIB", language="SMIv2")
        src = PysnmpFormatter().format(m)
        assert "'ASN1'" in src
        assert "ObjectIdentifier" in src

    def test_hyphens_replaced_in_identifiers(self):
        obj = MibObject(
            name="if-mib-obj",
            oid="1.3",
            oid_path=[1, 3],
            object_type="OBJECT-TYPE",
            syntax="Integer32",
        )
        m = MibModule(name="IF-MIB", language="SMIv2", objects={"if-mib-obj": obj})
        src = PysnmpFormatter().format(m)
        assert "if_mib_obj" in src
        assert "if-mib-obj =" not in src

    def test_imports_rendered(self):
        m = MibModule(
            name="IF-MIB", language="SMIv2", imports={"SNMPv2-SMI": ["ModuleIdentity", "Integer32"]}
        )
        src = PysnmpFormatter().format(m)
        assert "importSymbols" in src
        assert "SNMPv2-SMI" in src

    def test_notifications_exported(self):
        """Notifications must appear in mibBuilder.exportSymbols(), not just be defined."""
        from trishul_smi.models.mib_object import MibObject

        notif = MibObject(
            name="linkDown",
            oid="1.3.6.1.6.3.1.1.5.3",
            oid_path=[1, 3, 6, 1, 6, 3, 1, 1, 5, 3],
            object_type="NOTIFICATION-TYPE",
            status="current",
        )
        m = MibModule(
            name="IF-MIB",
            language="SMIv2",
            notifications={"linkDown": notif},
        )
        src = PysnmpFormatter().format(m)
        export_block = src[src.index("exportSymbols") :]
        assert "linkDown" in export_block

    def test_spaced_syntax_emits_valid_python(self):
        """'OCTET STRING' and 'SEQUENCE OF X' must not emit broken Python."""
        obj_octet = MibObject(
            name="rawBytes",
            oid="1.3",
            oid_path=[1, 3],
            object_type="OBJECT-TYPE",
            syntax="OCTET STRING",
        )
        obj_seq = MibObject(
            name="ifTable",
            oid="1.4",
            oid_path=[1, 4],
            object_type="OBJECT-TYPE",
            syntax="SEQUENCE OF IfEntry",
        )
        m = MibModule(
            name="IF-MIB", language="SMIv2", objects={"rawBytes": obj_octet, "ifTable": obj_seq}
        )
        src = PysnmpFormatter().format(m)
        assert "OCTET STRING()" not in src
        assert "SEQUENCE OF IfEntry()" not in src
        assert "OctetString()" in src


class TestPysnmpObjClass:
    """Unit tests for _pysnmp_obj_class object-type detection."""

    def _module(self, **types):
        from trishul_smi.models.mib_type import MibType

        return MibModule(
            name="TEST-MIB",
            language="SMIv2",
            types={k: MibType(name=k, base_type=v) for k, v in types.items()},
        )

    def test_sequence_of_is_mib_table(self):
        obj = MibObject(
            name="ifTable", oid="1", object_type="OBJECT-TYPE", syntax="SEQUENCE OF IfEntry"
        )
        assert _pysnmp_obj_class(obj, MibModule(name="X", language="SMIv2")) == "MibTable"

    def test_named_type_resolving_to_sequence_is_mib_table_row(self):
        obj = MibObject(name="ifEntry", oid="1", object_type="OBJECT-TYPE", syntax="IfEntry")
        m = self._module(IfEntry="SEQUENCE { ifIndex Integer32 }")
        assert _pysnmp_obj_class(obj, m) == "MibTableRow"

    def test_scalar_syntax_is_mib_scalar(self):
        obj = MibObject(name="ifIndex", oid="1", object_type="OBJECT-TYPE", syntax="Integer32")
        assert _pysnmp_obj_class(obj, MibModule(name="X", language="SMIv2")) == "MibScalar"

    def test_none_syntax_is_mib_scalar(self):
        obj = MibObject(name="foo", oid="1", object_type="OBJECT-TYPE", syntax=None)
        assert _pysnmp_obj_class(obj, MibModule(name="X", language="SMIv2")) == "MibScalar"


# ---------------------------------------------------------------------------
# MibCompiler (integration)
# ---------------------------------------------------------------------------


class TestMibCompiler:
    def test_default_config_created_when_none_given(self):
        """MibCompiler() with no args must create a default CompilerConfig."""
        compiler = MibCompiler()
        assert compiler._config is not None
        assert "json" in compiler._config.formats

    def test_unknown_format_raises_at_construction(self):
        with pytest.raises(ValueError, match="Unknown output format"):
            MibCompiler(CompilerConfig(formats=["invalid-fmt"], cache_dir=None))

    def test_no_readers_raises(self):
        compiler = MibCompiler(CompilerConfig(cache_dir=None, formats=["json"]))
        with pytest.raises(RuntimeError, match="No readers"):
            import asyncio

            asyncio.run(compiler.compile("TEST-MIB"))

    @pytest.mark.asyncio
    async def test_compile_writes_json(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path / "out", formats=["json"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        compiled = [r for r in results if r.status == "compiled"]
        assert any(r.name == "TEST-MIB" for r in compiled)
        data = json.loads((tmp_path / "out" / "TEST-MIB.json").read_bytes())
        assert data["module"] == "TEST-MIB"

    @pytest.mark.asyncio
    async def test_compile_writes_pysnmp(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path / "out", formats=["pysnmp"], cache_dir=None)
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        assert any(r.status == "compiled" and r.name == "TEST-MIB" for r in results)
        py_file = tmp_path / "out" / "TEST-MIB.py"
        assert py_file.exists()
        assert "mibBuilder" in py_file.read_text()

    @pytest.mark.asyncio
    async def test_missing_mib_status_failed(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({}))
        results = await compiler.compile("MISSING-MIB")
        assert any(r.name == "MISSING-MIB" and r.status == "failed" for r in results)

    @pytest.mark.asyncio
    async def test_fluent_add_reader(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = (
            MibCompiler(config)
            .add_reader(MockReader({}))
            .add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        )
        results = await compiler.compile("TEST-MIB")
        assert any(r.status == "compiled" and r.name == "TEST-MIB" for r in results)

    @pytest.mark.asyncio
    async def test_output_dir_created(self, tmp_path: Path):
        out = tmp_path / "deep" / "nested" / "out"
        config = CompilerConfig(output_dir=out, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        await compiler.compile("TEST-MIB")
        assert out.is_dir()

    @pytest.mark.asyncio
    async def test_compile_result_has_output_paths(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        results = await compiler.compile("TEST-MIB")
        compiled = next(r for r in results if r.name == "TEST-MIB")
        assert len(compiled.output_paths) == 1
        assert compiled.output_paths[0].suffix == ".json"

    @pytest.mark.asyncio
    async def test_compile_object_mib(self, tmp_path: Path):
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"OBJECT-MIB": OBJECT_V2}))
        results = await compiler.compile("OBJECT-MIB")
        compiled = next((r for r in results if r.name == "OBJECT-MIB"), None)
        assert compiled is not None and compiled.status == "compiled"
        data = json.loads((tmp_path / "OBJECT-MIB.json").read_bytes())
        assert "foo" in data["objects"]
        assert data["objects"]["foo"]["syntax"] == "Integer32"

    @pytest.mark.asyncio
    async def test_formatter_error_captured_in_warnings_not_raised(self, tmp_path: Path):
        """A formatter that raises must not abort the compile run."""
        config = CompilerConfig(output_dir=tmp_path, cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))

        def _raise(_):
            raise RuntimeError("simulated crash")

        broken_formatter = JsonFormatter()
        broken_formatter.format = _raise  # type: ignore[method-assign]
        compiler._formatters = {"json": broken_formatter}

        results = await compiler.compile("TEST-MIB")
        compiled = next((r for r in results if r.name == "TEST-MIB"), None)
        assert compiled is not None
        assert compiled.status == "compiled"
        assert len(compiled.output_paths) == 0
        assert any("formatter error" in w for w in compiled.warnings)
        assert any("simulated crash" in w for w in compiled.warnings)

    @pytest.mark.asyncio
    async def test_writer_error_raised_on_unwritable_output_dir(self, tmp_path: Path):
        """If the output directory cannot be created, WriterError is raised."""
        from unittest.mock import patch

        from trishul_smi.errors import WriterError

        config = CompilerConfig(output_dir=tmp_path / "out", cache_dir=None, formats=["json"])
        compiler = MibCompiler(config).add_reader(MockReader({"TEST-MIB": MINIMAL_V2}))
        with patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
            with pytest.raises(WriterError, match="permission denied"):
                await compiler.compile("TEST-MIB")


# ---------------------------------------------------------------------------
# JsonFormatter — types and notifications serialisation
# ---------------------------------------------------------------------------


class TestJsonFormatterExtended:
    def test_types_serialised(self):
        from trishul_smi.models.mib_type import MibType

        tc = MibType(name="DisplayString", base_type="OCTET STRING", description="A display string")
        m = MibModule(name="TC-MIB", language="SMIv2", types={"DisplayString": tc})
        import json

        data = json.loads(JsonFormatter().format(m))
        assert "DisplayString" in data["types"]
        assert data["types"]["DisplayString"]["base_type"] == "OCTET STRING"
        assert data["types"]["DisplayString"]["description"] == "A display string"

    def test_notifications_serialised(self):
        notif = MibObject(
            name="linkDown",
            oid="1.3.6.1.6.3.1.1.5.3",
            oid_path=[1, 3, 6, 1, 6, 3, 1, 1, 5, 3],
            object_type="NOTIFICATION-TYPE",
            status="current",
        )
        m = MibModule(name="IF-MIB", language="SMIv2", notifications={"linkDown": notif})
        import json

        data = json.loads(JsonFormatter().format(m))
        assert "linkDown" in data["notifications"]
        assert data["notifications"]["linkDown"]["object_type"] == "NOTIFICATION-TYPE"


# ---------------------------------------------------------------------------
# PysnmpFormatter — syntax edge cases
# ---------------------------------------------------------------------------


class TestPysnmpHelpers:
    def test_oid_tuple_empty(self):
        from trishul_smi.output.pysnmp_fmt import _oid_tuple

        assert _oid_tuple([]) == "()"

    def test_map_pysnmp_assign_empty_symbols(self):
        from trishul_smi.output.pysnmp_fmt import _map_pysnmp_assign

        assert _map_pysnmp_assign([], "SNMPv2-SMI") == ""


class TestPysnmpSyntaxEdgeCases:
    def _obj(self, name: str, syntax: str | None) -> MibObject:
        return MibObject(
            name=name, oid="1.3", oid_path=[1, 3], object_type="OBJECT-TYPE", syntax=syntax
        )

    def test_none_syntax_fallback(self):
        m = MibModule(name="X", language="SMIv2", objects={"o": self._obj("o", None)})
        src = PysnmpFormatter().format(m)
        assert "OctetString()  # unknown syntax" in src

    def test_spaced_syntax_fallback(self):
        m = MibModule(
            name="X", language="SMIv2", objects={"o": self._obj("o", "SEQUENCE OF Entry")}
        )
        src = PysnmpFormatter().format(m)
        assert "TODO: map syntax" in src

    def test_named_syntax_with_hyphen(self):
        m = MibModule(name="X", language="SMIv2", objects={"o": self._obj("o", "Display-String")})
        src = PysnmpFormatter().format(m)
        assert "Display_String()" in src

    def test_known_counter64_syntax(self):
        m = MibModule(name="X", language="SMIv2", objects={"o": self._obj("o", "Counter64")})
        src = PysnmpFormatter().format(m)
        assert "Counter64()" in src

    def test_textual_convention_rendered(self):
        from trishul_smi.models.mib_type import MibType

        tc = MibType(name="TruthValue", base_type="INTEGER")
        m = MibModule(name="X", language="SMIv2", types={"TruthValue": tc})
        src = PysnmpFormatter().format(m)
        assert "TruthValue" in src
        assert "TextualConvention" in src

    def test_mib_table_row_class(self):
        from trishul_smi.models.mib_type import MibType

        row_type = MibType(name="IfEntry", base_type="SEQUENCE")
        table_obj = MibObject(
            name="ifTable",
            oid="1.2",
            oid_path=[1, 2],
            object_type="OBJECT-TYPE",
            syntax="SEQUENCE OF IfEntry",
        )
        row_obj = MibObject(
            name="ifEntry",
            oid="1.2.1",
            oid_path=[1, 2, 1],
            object_type="OBJECT-TYPE",
            syntax="IfEntry",
        )
        m = MibModule(
            name="IF-MIB",
            language="SMIv2",
            objects={"ifTable": table_obj, "ifEntry": row_obj},
            types={"IfEntry": row_type},
        )
        src = PysnmpFormatter().format(m)
        assert "MibTable(" in src
        assert "MibTableRow(" in src
