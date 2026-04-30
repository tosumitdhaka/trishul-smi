"""Unit tests for models/ — pure instantiation and field validation."""
from pathlib import Path

import pytest

from trishul_smi.models import MibModule, MibObject, MibType, CompileResult


class TestMibModule:
    def test_minimal_construction(self):
        m = MibModule(name="IF-MIB", language="SMIv2")
        assert m.name == "IF-MIB"
        assert m.language == "SMIv2"
        assert m.imports == {}
        assert m.objects == {}
        assert m.types == {}
        assert m.notifications == {}
        assert m.source_text is None

    def test_all_imports_empty(self):
        m = MibModule(name="X", language="SMIv2")
        assert m.all_imports() == []

    def test_all_imports_returns_module_names(self):
        m = MibModule(
            name="IF-MIB",
            language="SMIv2",
            imports={"SNMPv2-SMI": ["OBJECT-TYPE"], "SNMPv2-TC": ["DisplayString"]},
        )
        assert set(m.all_imports()) == {"SNMPv2-SMI", "SNMPv2-TC"}

    def test_independent_default_dicts(self):
        a = MibModule(name="A", language="SMIv2")
        b = MibModule(name="B", language="SMIv2")
        a.objects["x"] = None  # type: ignore[assignment]
        assert "x" not in b.objects  # no shared mutable default


class TestMibObject:
    def test_minimal_construction(self):
        obj = MibObject(name="ifDescr", oid="1.3.6.1.2.1.2.2.1.2")
        assert obj.name == "ifDescr"
        assert obj.oid == "1.3.6.1.2.1.2.2.1.2"
        assert obj.oid_path == []
        assert obj.object_type == ""
        assert obj.syntax is None

    def test_full_construction(self):
        obj = MibObject(
            name="ifDescr",
            oid="1.3.6.1.2.1.2.2.1.2",
            oid_path=[1, 3, 6, 1, 2, 1, 2, 2, 1, 2],
            object_type="OBJECT-TYPE",
            syntax="DisplayString",
            max_access="read-only",
            status="current",
            description="Interface description.",
        )
        assert obj.oid_path == [1, 3, 6, 1, 2, 1, 2, 2, 1, 2]
        assert obj.max_access == "read-only"


class TestMibType:
    def test_minimal_construction(self):
        t = MibType(name="DisplayString", base_type="OCTET STRING")
        assert t.name == "DisplayString"
        assert t.base_type == "OCTET STRING"
        assert t.constraints is None
        assert t.description is None


class TestCompileResult:
    def test_compiled_status(self):
        r = CompileResult(
            name="IF-MIB",
            status="compiled",
            output_paths=[Path("./out/IF-MIB.json")],
        )
        assert r.status == "compiled"
        assert r.error is None
        assert r.warnings == []

    def test_failed_status(self):
        r = CompileResult(name="BAD-MIB", status="failed", error="ParseError at line 5")
        assert r.status == "failed"
        assert r.error == "ParseError at line 5"
        assert r.output_paths == []

    def test_no_borrowed_status(self):
        """DD-5: 'borrowed' is not a valid status in v1.0."""
        with pytest.raises(Exception):
            # mypy would catch this at type-check time;
            # this test documents the intent at runtime via Literal enforcement
            _ = CompileResult(name="X", status="borrowed")  # type: ignore[arg-type]
            # Literal doesn't raise at runtime — this is a mypy/static check.
            # The test is intentionally a no-op at runtime; kept as living docs.
