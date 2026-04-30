"""Unit tests for models/ — instantiation, field validation, and composition."""
from pathlib import Path

from trishul_smi.models import CompileResult, MibModule, MibObject, MibType


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
        assert "x" not in b.objects

    def test_composed_module_with_objects_and_types(self):
        """Issue #11: composed MibModule + MibObject + MibType test."""
        obj = MibObject(
            name="ifDescr",
            oid="1.3.6.1.2.1.2.2.1.2",
            oid_path=[1, 3, 6, 1, 2, 1, 2, 2, 1, 2],
            object_type="OBJECT-TYPE",
            syntax="DisplayString",
            max_access="read-only",
            status="current",
            description="A textual string containing information about the interface.",
            index=["ifIndex"],
        )
        tc = MibType(
            name="DisplayString",
            base_type="OCTET STRING",
            description="Represents textual information.",
        )
        notif = MibObject(
            name="linkDown",
            oid="1.3.6.1.6.3.1.1.5.3",
            object_type="NOTIFICATION-TYPE",
            status="current",
        )
        m = MibModule(
            name="IF-MIB",
            language="SMIv2",
            imports={"SNMPv2-SMI": ["OBJECT-TYPE"], "SNMPv2-TC": ["DisplayString"]},
            objects={"ifDescr": obj},
            types={"DisplayString": tc},
            notifications={"linkDown": notif},
        )
        # Structure
        assert m.name == "IF-MIB"
        assert "ifDescr" in m.objects
        assert "DisplayString" in m.types
        assert "linkDown" in m.notifications
        # Cross-references
        assert m.objects["ifDescr"].syntax == "DisplayString"
        assert m.types["DisplayString"].base_type == "OCTET STRING"
        assert m.objects["ifDescr"].index == ["ifIndex"]
        # all_imports
        assert set(m.all_imports()) == {"SNMPv2-SMI", "SNMPv2-TC"}
        # Notifications are separate from objects
        assert "linkDown" not in m.objects
        assert m.notifications["linkDown"].object_type == "NOTIFICATION-TYPE"


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

    def test_cached_status(self):
        r = CompileResult(name="IF-MIB", status="cached")
        assert r.status == "cached"

    # DD-5: "borrowed" is not a valid Literal value. mypy catches this
    # at type-check time. No runtime enforcement for Literal exists.
