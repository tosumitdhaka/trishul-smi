"""Shared JSON output semantics.

These helpers define the runtime-visible JSON meaning of object classes and
nodetypes so that module JSON and sidecars stay aligned.
"""

from __future__ import annotations

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject

OBJECT_TYPE_TO_CLASS: dict[str, str] = {
    "MODULE-IDENTITY": "moduleidentity",
    "OBJECT-IDENTITY": "objectidentity",
    "OBJECT-TYPE": "objecttype",
    "NOTIFICATION-TYPE": "notificationtype",
    "OBJECT IDENTIFIER": "objectidentifier",
    "OBJECT-GROUP": "objectgroup",
    "NOTIFICATION-GROUP": "notificationgroup",
    "MODULE-COMPLIANCE": "modulecompliance",
    "AGENT-CAPABILITIES": "agentcapabilities",
    "TRAP-TYPE": "traptype",
}


def object_class(object_type: str) -> str:
    """Return the JSON `class` value for an ASN.1 object type."""
    return OBJECT_TYPE_TO_CLASS.get(object_type, object_type.lower().replace("-", ""))


def runtime_oid(obj: MibObject) -> str | None:
    """Return the canonical runtime OID for *obj*, or ``None`` if unresolved.

    Runtime JSON treats ``oid_path`` as the authoritative OID representation.
    ``oid`` is emitted only when a fully resolved numeric dotted string can be
    derived from that path.
    """
    if obj.oid_parent is not None or not obj.oid_path:
        return None
    return ".".join(str(n) for n in obj.oid_path)


def derive_nodetypes(module: MibModule) -> dict[str, str]:
    """Compute structural nodetype for every OBJECT-TYPE in the module."""
    oid_to_role: dict[tuple[int, ...], str] = {}
    for obj in module.objects.values():
        if obj.object_type != "OBJECT-TYPE" or runtime_oid(obj) is None:
            continue
        key = tuple(obj.oid_path)
        syntax = (obj.syntax or "").strip()
        if syntax.upper().startswith("SEQUENCE OF"):
            oid_to_role[key] = "table"
        elif syntax and syntax in module.types:
            base = (module.types[syntax].base_type or "").upper()
            if base.startswith("SEQUENCE"):
                oid_to_role[key] = "row"

    for obj in module.objects.values():
        if obj.object_type != "OBJECT-TYPE" or runtime_oid(obj) is None:
            continue
        key = tuple(obj.oid_path)
        if key not in oid_to_role and oid_to_role.get(tuple(obj.oid_path[:-1])) == "row":
            oid_to_role[key] = "column"

    result: dict[str, str] = {}
    for name, obj in module.objects.items():
        if obj.object_type != "OBJECT-TYPE":
            continue
        key = tuple(obj.oid_path) if obj.oid_path else ()
        result[name] = oid_to_role.get(key, "scalar")
    return result
