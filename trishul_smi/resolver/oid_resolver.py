"""Full OID resolution: rewrite MibObject.oid / .oid_path to absolute numeric paths.

After MibResolver returns a topologically-ordered list of MibModule objects,
this module walks that list in order (dependencies before dependents) and
resolves every object's OID to its full dotted-decimal path by following the
parent-name chain.

The transformer stores only the local numeric arcs in oid_path and captures
the leading name arc (e.g. 'ifMIB' in { ifMIB 1 }) in oid_parent.  This
module looks up oid_parent in a growing name→absolute-path map seeded with
well-known SNMP roots, then concatenates parent path + local arcs.

Mutation is in-place so that formatters always receive fully-resolved modules
without needing access to the full module set themselves.
"""

from __future__ import annotations

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject

# Well-known OID roots that are built into the SNMP tree.  Modules that
# reference these names (e.g. { mib-2 2 }) never import them explicitly.
WELL_KNOWN_OIDS: dict[str, list[int]] = {
    "iso": [1],
    "ccitt": [0],
    "joint-iso-ccitt": [2],
    "org": [1, 3],
    "dod": [1, 3, 6],
    "internet": [1, 3, 6, 1],
    "directory": [1, 3, 6, 1, 1],
    "mgmt": [1, 3, 6, 1, 2],
    "mib-2": [1, 3, 6, 1, 2, 1],
    "transmission": [1, 3, 6, 1, 2, 1, 10],
    "experimental": [1, 3, 6, 1, 3],
    "private": [1, 3, 6, 1, 4],
    "enterprises": [1, 3, 6, 1, 4, 1],
    "security": [1, 3, 6, 1, 5],
    "snmpV2": [1, 3, 6, 1, 6],
    "snmpDomains": [1, 3, 6, 1, 6, 1],
    "snmpProxys": [1, 3, 6, 1, 6, 2],
    "snmpModules": [1, 3, 6, 1, 6, 3],
    # SNMPv2-MIB well-known nodes (needed to resolve NOTIFICATION-TYPEs that
    # reference snmpTraps without SNMPv2-MIB being in the compiled module set)
    "snmpMIB": [1, 3, 6, 1, 6, 3, 1],
    "snmpMIBObjects": [1, 3, 6, 1, 6, 3, 1, 1],
    "snmpTraps": [1, 3, 6, 1, 6, 3, 1, 1, 5],
}


def resolve_oids(modules: list[MibModule]) -> None:
    """Mutate every MibObject in *modules* to hold absolute OID paths.

    Modules must be in topological order (dependencies before dependents).
    Objects whose parent cannot be resolved are left unchanged.
    """
    name_map: dict[str, list[int]] = dict(WELL_KNOWN_OIDS)

    for module in modules:
        pending: list[MibObject] = [
            *module.objects.values(),
            *module.notifications.values(),
        ]
        while pending:
            progressed = False
            next_pending: list[MibObject] = []
            for obj in pending:
                abs_path = _resolve_one(obj, name_map)
                if abs_path is not None:
                    obj.oid_path = abs_path
                    obj.oid = ".".join(str(n) for n in abs_path)
                    obj.oid_parent = None  # mark resolved; makes re-runs idempotent
                    progressed = True

                # Register only fully resolved objects; unresolved local arcs are
                # not safe for dependents to consume as absolute paths.
                if obj.oid_parent is None and obj.oid_path:
                    name_map[obj.name] = obj.oid_path
                else:
                    next_pending.append(obj)

            if not progressed:
                break
            pending = next_pending


def _resolve_one(obj: MibObject, name_map: dict[str, list[int]]) -> list[int] | None:
    """Return absolute int path for *obj*, or None if it cannot be resolved."""
    if obj.oid_parent is None:
        # All arcs are already numeric (e.g. { 1 3 6 1 2 1 2 }).
        return obj.oid_path if obj.oid_path else None

    parent_path = name_map.get(obj.oid_parent)
    if parent_path is None:
        return None  # parent not yet known — leave for caller to handle

    return parent_path + obj.oid_path
