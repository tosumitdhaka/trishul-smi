"""Dependency graph and topological sort for MIB modules.

Uses Kahn\'s algorithm (BFS-based) which:
- Runs in O(V + E)
- Detects cycles by checking if the result length < number of modules
- Produces a deterministic order (alphabetical within each BFS layer)
  which makes test output stable and diffs readable.
"""

from __future__ import annotations

from collections import deque

from trishul_smi.errors import CircularDependencyError
from trishul_smi.models.mib_module import MibModule


def topological_sort(
    modules: dict[str, MibModule],
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Return MIB names in dependency order (dependencies before dependents).

    Edges run: dependency → dependent  (compile the dependency first).
    Only edges whose source is present in ``modules`` are counted —
    imports of well-known base MIBs that were not fetched (e.g. SNMPv2-SMI)
    are silently skipped rather than causing an error.

    Args:
        modules: All fully-parsed MibModule objects keyed by name.
        aliases: Requested name -> declared name for misnamed MIB files
            (issue #21). Each import edge is mapped through this map before
            edge counting and cycle detection, so an importer that names a
            misnamed provider by its *requested* name (``IMPORTS z FROM
            ALIAS`` where the file fetched as ``ALIAS`` declares ``Z-MIB``)
            still contributes the ``Z-MIB`` edge — otherwise the provider can
            sort after its importer and its OIDs stay unresolved (issue #39).

    Returns:
        List of module names in safe compilation order.

    Raises:
        CircularDependencyError: If the import graph contains a cycle.
    """
    aliases = aliases or {}
    # in_degree[name] = number of imports that are present in `modules`
    in_degree: dict[str, int] = {name: 0 for name in modules}
    # dependents[dep] = list of module names that import `dep`
    dependents: dict[str, list[str]] = {name: [] for name in modules}

    for name, module in modules.items():
        for dep in sorted(module.all_imports()):  # sorted → deterministic
            dep = aliases.get(dep, dep)
            if dep in modules:
                in_degree[name] += 1
                dependents[dep].append(name)

    # Seed queue with nodes that have no unresolved deps (alphabetical order)
    queue: deque[str] = deque(sorted(name for name, deg in in_degree.items() if deg == 0))
    result: list[str] = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for dependent in sorted(dependents[node]):  # sorted → deterministic
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(modules):
        cycle_members = sorted(n for n in modules if n not in result)
        raise CircularDependencyError(
            f"Circular dependency detected among: {cycle_members}. "
            f"Resolved {len(result)}/{len(modules)} modules before deadlock."
        )

    return result


def build_dependency_graph(
    modules: dict[str, MibModule],
    aliases: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Return a plain adjacency dict for inspection / debugging.

    Keys are module names; values are the names of modules that import the key.
    Only edges within the provided ``modules`` dict are included. Import
    edges are mapped through ``aliases`` (requested -> declared name) the same
    way ``topological_sort`` does, so the graph matches the sort's view.
    """
    aliases = aliases or {}
    graph: dict[str, list[str]] = {name: [] for name in modules}
    for name, module in modules.items():
        for dep in module.all_imports():
            dep = aliases.get(dep, dep)
            if dep in modules:
                graph[dep].append(name)
    return graph
