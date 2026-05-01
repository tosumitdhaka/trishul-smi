"""Tests for trishul_smi.convert.pysnmp_reader — PySNMPReader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from trishul_smi.convert.pysnmp_reader import PySNMPReader
from trishul_smi.errors import ParseError

_reader = PySNMPReader()


def _read(source: str, name: str = "TEST-MIB"):
    return _reader.read_text(textwrap.dedent(source), name)


# ---------------------------------------------------------------------------
# Module name extraction
# ---------------------------------------------------------------------------


class TestModuleName:
    def test_name_from_export_symbols(self):
        src = """
        foo = MibScalar((1, 3, 6, 1,), Integer32())
        mibBuilder.exportSymbols('MY-MIB', **{'foo': foo})
        """
        m = _read(src)
        assert m.name == "MY-MIB"

    def test_fallback_to_provided_name_when_no_export(self):
        src = "foo = MibScalar((1,), Integer32())\n"
        m = _read(src, name="FALLBACK-MIB")
        assert m.name == "FALLBACK-MIB"

    def test_read_from_file_uses_stem(self, tmp_path: Path):
        py_file = tmp_path / "IF_MIB.py"
        py_file.write_text(
            "foo = MibScalar((1,), Integer32())\n",
            encoding="utf-8",
        )
        m = _reader.read(py_file)
        assert m.name == "IF-MIB"  # underscores replaced with hyphens

    def test_read_from_file_with_export(self, tmp_path: Path):
        py_file = tmp_path / "if_mib.py"
        py_file.write_text(
            "ifMIB = ModuleIdentity((1, 3, 6, 1, 2, 1, 31,))\n"
            "mibBuilder.exportSymbols('IF-MIB', **{'ifMIB': ifMIB})\n",
            encoding="utf-8",
        )
        m = _reader.read(py_file)
        assert m.name == "IF-MIB"

    def test_syntax_error_raises_parse_error(self, tmp_path: Path):
        py_file = tmp_path / "bad.py"
        py_file.write_text("def (broken syntax\n", encoding="utf-8")
        with pytest.raises(ParseError, match="Cannot parse"):
            _reader.read(py_file)

    def test_read_text_syntax_error_raises_parse_error(self):
        with pytest.raises(ParseError, match="Cannot parse"):
            _reader.read_text("def (broken", "X")


# ---------------------------------------------------------------------------
# Object extraction
# ---------------------------------------------------------------------------


class TestObjectExtraction:
    def test_module_identity_extracted(self):
        src = "ifMIB = ModuleIdentity((1, 3, 6, 1, 2, 1, 31,))\n"
        m = _read(src)
        assert "ifMIB" in m.objects
        obj = m.objects["ifMIB"]
        assert obj.object_type == "MODULE-IDENTITY"
        assert obj.oid_path == [1, 3, 6, 1, 2, 1, 31]
        assert obj.oid == "1.3.6.1.2.1.31"

    def test_mib_scalar_extracted(self):
        src = "ifDescr = MibScalar((1, 3, 6, 1, 2, 1, 2, 2, 1, 2,), DisplayString())\n"
        m = _read(src)
        assert "ifDescr" in m.objects
        obj = m.objects["ifDescr"]
        assert obj.object_type == "OBJECT-TYPE"
        assert obj.syntax == "DisplayString"

    def test_object_identity_extracted(self):
        src = "zeroDotZero = ObjectIdentity((0, 0,))\n"
        m = _read(src)
        assert "zeroDotZero" in m.objects
        assert m.objects["zeroDotZero"].object_type == "OBJECT-IDENTITY"

    def test_mib_identifier_extracted(self):
        src = "mib2 = MibIdentifier((1, 3, 6, 1, 2, 1,))\n"
        m = _read(src)
        assert "mib2" in m.objects
        assert m.objects["mib2"].object_type == "OBJECT IDENTIFIER"

    def test_notification_type_in_notifications(self):
        src = "linkDown = NotificationType((1, 3, 6, 1, 6, 3, 1, 1, 5, 3,))\n"
        m = _read(src)
        assert "linkDown" in m.notifications
        assert "linkDown" not in m.objects
        assert m.notifications["linkDown"].object_type == "NOTIFICATION-TYPE"

    def test_mib_table_extracted(self):
        src = "ifTable = MibTable((1, 3, 6, 1, 2, 1, 2, 2,))\n"
        m = _read(src)
        assert "ifTable" in m.objects
        assert m.objects["ifTable"].object_type == "OBJECT-TYPE"

    def test_mib_table_row_extracted(self):
        src = "ifEntry = MibTableRow((1, 3, 6, 1, 2, 1, 2, 2, 1,))\n"
        m = _read(src)
        assert "ifEntry" in m.objects

    def test_mib_table_column_extracted(self):
        src = "ifIndex = MibTableColumn((1, 3, 6, 1, 2, 1, 2, 2, 1, 1,), Integer32())\n"
        m = _read(src)
        assert "ifIndex" in m.objects
        obj = m.objects["ifIndex"]
        assert obj.syntax == "Integer32"

    def test_chained_set_calls_unwrapped(self):
        """Name = Constructor(...).setMaxAccess(...).setStatus(...) must be extracted."""
        src = (
            "ifDescr = MibScalar((1, 3, 6, 1, 2, 1, 2, 2, 1, 2,), DisplayString())"
            ".setMaxAccess('read-only').setStatus('current')\n"
        )
        m = _read(src)
        assert "ifDescr" in m.objects
        assert m.objects["ifDescr"].oid_path == [1, 3, 6, 1, 2, 1, 2, 2, 1, 2]

    def test_unknown_constructor_ignored(self):
        src = "foo = SomeUnknownClass((1, 2, 3,))\n"
        m = _read(src)
        assert "foo" not in m.objects

    def test_object_without_oid_tuple_ignored(self):
        src = "foo = MibScalar('string_not_tuple')\n"
        m = _read(src)
        assert "foo" not in m.objects

    def test_no_objects_returns_empty_module(self):
        src = "x = 42\n"
        m = _read(src, name="EMPTY-MIB")
        assert m.name == "EMPTY-MIB"
        assert m.objects == {}
        assert m.notifications == {}

    def test_syntax_none_when_no_second_arg(self):
        src = "ifMIB = ModuleIdentity((1, 3, 6, 1, 2, 1, 31,))\n"
        m = _read(src)
        assert m.objects["ifMIB"].syntax is None

    def test_syntax_from_attribute_call(self):
        """Constructor syntax like mod.DisplayString() — dot-attribute form."""
        src = "foo = MibScalar((1,), mymod.DisplayString())\n"
        m = _read(src)
        assert m.objects["foo"].syntax == "DisplayString"

    def test_oid_tuple_with_non_int_element_ignored(self):
        """A tuple containing a non-integer (e.g. a name) is not a valid OID — skip."""
        src = "foo = MibScalar((1, 'bad', 3,), Integer32())\n"
        m = _read(src)
        assert "foo" not in m.objects

    def test_export_call_without_string_first_arg_ignored(self):
        """exportSymbols(someVar, ...) — no literal module name — falls back to stem."""
        src = (
            "foo = MibScalar((1,), Integer32())\n"
            "mibBuilder.exportSymbols(moduleName, **{'foo': foo})\n"
        )
        m = _reader.read_text(src, module_name="FALLBACK")
        assert m.name == "FALLBACK"

    def test_multi_target_assignment_ignored(self):
        """a = b = Constructor(...) has two targets — must not crash, just skip."""
        src = "a = b = MibScalar((1, 2,), Integer32())\n"
        m = _read(src)
        assert "a" not in m.objects
        assert "b" not in m.objects

    def test_constructor_via_attribute_without_args_ignored(self):
        """mod.MibScalar() with no positional args — no OID, skip."""
        src = "foo = mymod.MibScalar()\n"
        m = _read(src)
        assert "foo" not in m.objects

    def test_func_not_name_or_attribute_ignored(self):
        """Constructor form foo()() — func is a Call, not Name/Attribute — skip."""
        src = "foo = MibScalar((1,))((2,))\n"
        m = _read(src)
        assert "foo" not in m.objects


# ---------------------------------------------------------------------------
# Full round-trip: read → JSON
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_read_text_uses_detected_name_over_provided(self):
        """read_text with exportSymbols present uses the detected name, not module_name arg."""
        src = (
            "foo = MibScalar((1,), Integer32())\n"
            "mibBuilder.exportSymbols('DETECTED-MIB', **{'foo': foo})\n"
        )
        m = _reader.read_text(src, module_name="IGNORED-MIB")
        assert m.name == "DETECTED-MIB"

    def test_read_then_json_format(self):
        import json

        from trishul_smi.output.json_fmt import JsonFormatter

        src = (
            "ifMIB = ModuleIdentity((1, 3, 6, 1, 2, 1, 31,))\n"
            "ifDescr = MibScalar((1, 3, 6, 1, 2, 1, 2, 2, 1, 2,), DisplayString())\n"
            "mibBuilder.exportSymbols('IF-MIB', **{'ifMIB': ifMIB, 'ifDescr': ifDescr})\n"
        )
        module = _reader.read_text(src, "IF-MIB")
        data = json.loads(JsonFormatter().format(module))
        assert data["module"] == "IF-MIB"
        assert "ifMIB" in data["objects"]
        assert "ifDescr" in data["objects"]
        assert data["objects"]["ifDescr"]["syntax"] == "DisplayString"
