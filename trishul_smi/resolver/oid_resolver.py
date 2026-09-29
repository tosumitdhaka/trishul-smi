"""Full OID resolution: rewrite MibObject.oid / .oid_path to absolute numeric paths.

After MibResolver returns a topologically-ordered list of MibModule objects,
this module walks that list in order (dependencies before dependents) and
resolves every object's OID to its full dotted-decimal path by following the
parent-name chain.

The transformer stores only the local numeric arcs in oid_path and captures
the leading name arc (e.g. 'ifMIB' in { ifMIB 1 }) in oid_parent.  Symbolic
parents are looked up in the importing module's *namespace*, in this order:

1. the module's own definitions,
2. the module explicitly named in IMPORTS as the provider of that symbol
   (through the requested→declared alias map, issue #39),
3. well-known SNMP roots,
4. a unique provider already in the resolved closure (legal unimported
   references).

A symbol exported by several modules with no explicit import is ambiguous and
is left unresolved.  Mutation is in-place so that formatters always receive
fully-resolved modules without needing access to the full module set.
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
    # Requested name -> declared name for misnamed files, gathered from each
    # module's own record (set by MibResolver._reconcile_name). Lets an
    # import that names a misnamed provider by its *requested* name resolve
    # against the module actually present in the closure (issue #39).
    aliases: dict[str, str] = {}
    for module in modules:
        aliases.update(module.aliases)

    # Module-scoped symbol table: declared module name -> {symbol: abs_path}
    # for every module already processed (topological order → dependencies
    # first). Well-known roots are consulted per-lookup rather than seeded so
    # a module-defined symbol can shadow them per the documented order.
    closure_paths: dict[str, dict[str, list[int]]] = {}

    for module in modules:
        pending: list[MibObject] = [
            *module.objects.values(),
            *module.notifications.values(),
        ]
        module_paths: dict[str, list[int]] = {}
        while pending:
            progressed = False
            next_pending: list[MibObject] = []
            for obj in pending:
                abs_path = _resolve_one(obj, module, closure_paths, module_paths, aliases)
                if abs_path is not None:
                    obj.oid_path = abs_path
                    obj.oid = ".".join(str(n) for n in abs_path)
                    obj.oid_parent = None  # mark resolved; makes re-runs idempotent
                    module_paths[obj.name] = abs_path
                    progressed = True
                else:
                    next_pending.append(obj)

            if not progressed:
                break
            pending = next_pending

        # Register only resolved symbols; unresolved local arcs are not safe
        # for dependents to consume as absolute paths.
        closure_paths[module.name] = module_paths


def _resolve_one(
    obj: MibObject,
    module: MibModule,
    closure_paths: dict[str, dict[str, list[int]]],
    module_paths: dict[str, list[int]],
    aliases: dict[str, str],
) -> list[int] | None:
    """Return absolute int path for *obj*, or None if it cannot be resolved."""
    if obj.oid_parent is None:
        # All arcs are already numeric (e.g. { 1 3 6 1 2 1 2 }).
        return obj.oid_path if obj.oid_path else None

    parent_path = _lookup_parent(obj.oid_parent, module, closure_paths, module_paths, aliases)
    if parent_path is None:
        return None  # parent not yet known — leave for caller to handle

    return parent_path + obj.oid_path


def _lookup_parent(
    name: str,
    module: MibModule,
    closure_paths: dict[str, dict[str, list[int]]],
    module_paths: dict[str, list[int]],
    aliases: dict[str, str],
) -> list[int] | None:
    """Resolve a symbolic OID parent within *module*'s namespace.

    Lookup order (issue #39): own module definitions → the explicitly
    imported provider for the symbol (through the alias map) → well-known
    roots → a unique provider in the resolved closure. Returns None when the
    symbol is ambiguous (several providers, none imported) or unknown.
    """
    own = module_paths.get(name)
    if own is not None:
        return own

    provider = module.import_reverse_map().get(name)
    if provider is not None:
        provider = aliases.get(provider, provider)
        provider_paths = closure_paths.get(provider)
        if provider_paths is not None and name in provider_paths:
            return provider_paths[name]

    well_known = WELL_KNOWN_OIDS.get(name)
    if well_known is not None:
        return well_known

    candidates: list[list[int]] = [paths[name] for paths in closure_paths.values() if name in paths]
    if len(candidates) == 1:
        return candidates[0]
    return None
