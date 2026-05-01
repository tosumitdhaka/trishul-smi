"""PySNMPReader: convert a compiled PySNMP .py MIB module back to a MibModule.

Uses Python's ast module to parse the .py file — not the SMI grammar.  The
patterns recognised are those produced by pysmi and by trishul-smi's own
PysnmpFormatter:

    ifMIB = ModuleIdentity((1, 3, 6, 1, 2, 1, 31,))
    ifDescr = MibScalar((1, 3, 6, 1, 2, 1, 2, 2, 1, 2,), DisplayString())
    mibBuilder.exportSymbols('IF-MIB', **{'ifMIB': ifMIB, ...})

Limitations
-----------
- Imports and TEXTUAL-CONVENTION class definitions are not reconstructed.
- Only objects whose OID tuple is literally in the source are extracted.
- The language field is always set to SMIv2 since compiled .py files don't
  preserve the original dialect.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

from trishul_smi.errors import ParseError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject

# Map pysnmp constructor names → MIB object_type strings.
_CLASS_TO_OBJECT_TYPE: dict[str, str] = {
    "ModuleIdentity": "MODULE-IDENTITY",
    "ObjectIdentity": "OBJECT-IDENTITY",
    "MibIdentifier": "OBJECT IDENTIFIER",
    "MibScalar": "OBJECT-TYPE",
    "MibTable": "OBJECT-TYPE",
    "MibTableRow": "OBJECT-TYPE",
    "MibTableColumn": "OBJECT-TYPE",
    "NotificationType": "NOTIFICATION-TYPE",
    "ObjectType": "OBJECT-TYPE",
}


def _extract_oid_tuple(node: ast.expr) -> list[int] | None:
    """Extract a list of ints from an ast.Tuple of integer constants."""
    if not isinstance(node, ast.Tuple):
        return None
    result: list[int] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
            result.append(elt.value)
        else:
            return None
    return result


def _module_name_from_export(tree: ast.Module) -> str | None:
    """Find 'MOD-NAME' from mibBuilder.exportSymbols('MOD-NAME', ...)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "exportSymbols"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ):
            continue
        return str(call.args[0].value)
    return None


def _build_type_name_map(tree: ast.Module) -> dict[str, str]:
    """Build map from _Name_Type → real base type name.

    Handles two patterns:
      1. class _Foo_Type(BaseClass): ...   → _Foo_Type → 'BaseClass'
      2. _Foo_Type.__name__ = "BaseClass"  → _Foo_Type → 'BaseClass'
    """
    name_map: dict[str, str] = {}
    for node in ast.walk(tree):
        # Pattern 1: class definition
        if (
            isinstance(node, ast.ClassDef)
            and node.name.startswith("_")
            and node.name.endswith("_Type")
        ):
            if node.bases:
                base = node.bases[0]
                if isinstance(base, ast.Name):
                    name_map[node.name] = base.id
                elif isinstance(base, ast.Attribute):
                    name_map[node.name] = base.attr
        # Pattern 2: __name__ assignment
        elif isinstance(node, ast.Assign):
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Attribute)
                and node.targets[0].attr == "__name__"
                and isinstance(node.targets[0].value, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                name_map[node.targets[0].value.id] = node.value.value
    return name_map


def _call_name(call: ast.Call) -> str | None:
    """Return the function/constructor name from a Call node."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _extract_string_arg(call: ast.Call) -> str | None:
    """Return the first string argument of a call, or None."""
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return str(call.args[0].value)
    return None


def _collect_set_calls(tree: ast.Module) -> dict[str, dict[str, str]]:
    """Collect setStatus/setMaxAccess/setDescription calls per object name.

    Handles two forms:
      obj.setMaxAccess('read-only')            (chained on assignment RHS)
      if mibBuilder.loadTexts: obj.setStatus(...)
    """
    attrs: dict[str, dict[str, str]] = {}

    def _record(obj_name: str, attr: str, value: str) -> None:
        attrs.setdefault(obj_name, {})[attr] = value

    for node in ast.walk(tree):
        # Top-level expression: obj.setXxx(...) or if ...: obj.setXxx(...)
        if isinstance(node, ast.If):
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    _process_call_stmt(stmt.value, _record)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            _process_call_stmt(node.value, _record)

    return attrs


def _process_call_stmt(
    call: ast.Call,
    record: Callable[[str, str, str], None],
) -> None:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return
    obj_node = func.value
    if not isinstance(obj_node, ast.Name):
        return
    obj_name = obj_node.id
    attr = func.attr
    if attr in ("setStatus", "setMaxAccess"):
        val = _extract_string_arg(call)
        if val:
            record(obj_name, attr, val)
    elif attr == "setDescription":
        arg0 = call.args[0] if call.args else None
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            record(obj_name, attr, " ".join(arg0.value.split()))


def _objects_from_assignments(
    tree: ast.Module,
    type_name_map: dict[str, str],
    set_calls: dict[str, dict[str, str]],
) -> dict[str, MibObject]:
    """Extract MibObject entries from top-level Name = Constructor(...) assignments."""
    objects: dict[str, MibObject] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        value = node.value

        # Collect .setMaxAccess from chained calls on the assignment RHS
        max_access_inline: str | None = None
        inner: ast.expr = value
        while isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
            if inner.func.attr == "setMaxAccess":
                val = _extract_string_arg(inner)
                if val:
                    max_access_inline = val
            inner = inner.func.value

        # inner should now be the Constructor(...) call
        call: ast.Call | None = inner if isinstance(inner, ast.Call) else None
        if call is None:
            continue

        cls_name = _call_name(call)
        if cls_name is None:
            continue

        object_type = _CLASS_TO_OBJECT_TYPE.get(cls_name)
        if object_type is None:
            continue

        # First positional arg must be the OID tuple
        if not call.args:
            continue
        oid_path = _extract_oid_tuple(call.args[0])
        if oid_path is None:
            continue

        oid_str = ".".join(str(n) for n in oid_path)

        # Extract syntax from second arg (e.g. DisplayString() or _ifDescr_Type())
        syntax: str | None = None
        if len(call.args) >= 2:
            arg1 = call.args[1]
            if isinstance(arg1, ast.Call):
                raw = _call_name(arg1)
                if raw:
                    # Resolve _Name_Type wrappers to their real base name
                    syntax = type_name_map.get(raw, raw)

        # Merge set-calls from loadTexts blocks
        extra = set_calls.get(name, {})
        status = extra.get("setStatus")
        max_access = max_access_inline or extra.get("setMaxAccess")
        description = extra.get("setDescription")

        objects[name] = MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type=object_type,
            syntax=syntax,
            max_access=max_access,
            status=status,
            description=description,
        )

    return objects


class PySNMPReader:
    """Convert a compiled PySNMP .py MIB file to a MibModule."""

    def read(self, path: Path) -> MibModule:
        """Parse *path* and return a MibModule.

        Raises:
            ParseError: if the file cannot be parsed as a Python module or
                if the module name cannot be extracted from exportSymbols.
        """
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise ParseError(f"Cannot parse {path}: {exc}") from exc

        module_name = _module_name_from_export(tree)
        if module_name is None:
            module_name = path.stem.replace("_", "-")

        type_name_map = _build_type_name_map(tree)
        set_calls = _collect_set_calls(tree)
        all_objects = _objects_from_assignments(tree, type_name_map, set_calls)

        objects: dict[str, MibObject] = {}
        notifications: dict[str, MibObject] = {}
        for obj_name, obj in all_objects.items():
            if obj.object_type == "NOTIFICATION-TYPE":
                notifications[obj_name] = obj
            else:
                objects[obj_name] = obj

        return MibModule(
            name=module_name,
            language="SMIv2",
            imports={},
            objects=objects,
            notifications=notifications,
        )

    def read_text(self, source: str, module_name: str = "UNKNOWN") -> MibModule:
        """Parse *source* string and return a MibModule. Useful for testing."""
        try:
            tree = ast.parse(source, filename="<string>")
        except SyntaxError as exc:
            raise ParseError(f"Cannot parse source: {exc}") from exc

        detected_name = _module_name_from_export(tree)
        name = detected_name or module_name

        type_name_map = _build_type_name_map(tree)
        set_calls = _collect_set_calls(tree)
        all_objects = _objects_from_assignments(tree, type_name_map, set_calls)
        objects: dict[str, MibObject] = {}
        notifications: dict[str, MibObject] = {}
        for obj_name, obj in all_objects.items():
            if obj.object_type == "NOTIFICATION-TYPE":
                notifications[obj_name] = obj
            else:
                objects[obj_name] = obj

        return MibModule(
            name=name,
            language="SMIv2",
            imports={},
            objects=objects,
            notifications=notifications,
        )
