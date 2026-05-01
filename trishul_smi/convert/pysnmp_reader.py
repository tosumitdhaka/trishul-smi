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


def _objects_from_assignments(tree: ast.Module) -> dict[str, MibObject]:
    """Extract MibObject entries from top-level Name = Constructor(...) assignments."""
    objects: dict[str, MibObject] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        value = node.value

        # Handle Name = Constructor(oid_tuple, ...) or
        #        Name = Constructor(oid_tuple, ...).setMaxAccess(...)
        call: ast.Call | None = None
        if isinstance(value, ast.Call):
            call = value
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
            call = value
        # Unwrap chained .setMaxAccess(...) / .setStatus(...) calls
        inner: ast.expr = value
        while isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
            inner = inner.func.value
        if isinstance(inner, ast.Call):
            call = inner

        if call is None:
            continue

        # Get constructor name
        func = call.func
        if isinstance(func, ast.Name):
            cls_name = func.id
        elif isinstance(func, ast.Attribute):
            cls_name = func.attr
        else:
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

        # Extract syntax from second arg if present (e.g. DisplayString())
        syntax: str | None = None
        if len(call.args) >= 2:
            arg1 = call.args[1]
            if isinstance(arg1, ast.Call):
                s_func = arg1.func
                if isinstance(s_func, ast.Name):
                    syntax = s_func.id
                elif isinstance(s_func, ast.Attribute):
                    syntax = s_func.attr

        objects[name] = MibObject(
            name=name,
            oid=oid_str,
            oid_path=oid_path,
            object_type=object_type,
            syntax=syntax,
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
            # Fall back to the file stem (without .py)
            module_name = path.stem.replace("_", "-")

        all_objects = _objects_from_assignments(tree)

        objects: dict[str, MibObject] = {}
        notifications: dict[str, MibObject] = {}
        for name, obj in all_objects.items():
            if obj.object_type == "NOTIFICATION-TYPE":
                notifications[name] = obj
            else:
                objects[name] = obj

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

        all_objects = _objects_from_assignments(tree)
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
