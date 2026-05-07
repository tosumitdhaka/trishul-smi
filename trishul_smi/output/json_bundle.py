"""Helpers for optional JSON bundle sidecars.

For v0.4.0, module JSON remains the source of truth. Sidecars such as
``manifest.json`` are additive metadata derived from the emitted module files.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import orjson

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.output.json_contract import derive_nodetypes, object_class
from trishul_smi.output.json_ir import JsonArtifactMetadata

MANIFEST_FILENAME = "manifest.json"
OID_INDEX_FILENAME = "oid_index.json"


@dataclass(frozen=True, slots=True)
class JsonModuleArtifact:
    """A successfully emitted JSON module file inside a bundle directory."""

    module: str
    file: str
    module_data: MibModule


def build_manifest_bytes(
    artifact_metadata: JsonArtifactMetadata,
    modules: list[JsonModuleArtifact],
    *,
    oid_index_filename: str | None = None,
) -> bytes:
    """Return the deterministic ``manifest.json`` payload."""
    sidecars: dict[str, str] = {}
    if oid_index_filename is not None:
        sidecars["oid_index"] = oid_index_filename

    payload: dict[str, Any] = {
        "schema_version": artifact_metadata.schema_version,
        "producer_version": artifact_metadata.producer_version,
        "generated_by": artifact_metadata.generated_by,
        "generated_at": artifact_metadata.generated_at,
        "modules": [
            {"module": artifact.module, "file": artifact.file}
            for artifact in sorted(modules, key=lambda artifact: (artifact.module, artifact.file))
        ],
    }
    if sidecars:
        payload["sidecars"] = sidecars
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2)


def _oid_sort_key(oid: str) -> tuple[tuple[int, int | str], ...]:
    parts: list[tuple[int, int | str]] = []
    for part in oid.split("."):
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part))
    return tuple(parts)


def _oid_index_entry(
    module_name: str,
    obj: MibObject,
    nodetypes: dict[str, str],
) -> dict[str, str]:
    entry: dict[str, str] = {
        "module": module_name,
        "object": obj.name,
        "class": object_class(obj.object_type),
        "object_type": obj.object_type,
    }
    if obj.object_type == "OBJECT-TYPE":
        entry["nodetype"] = nodetypes.get(obj.name, "scalar")
    return entry


def build_oid_index_bytes(
    artifact_metadata: JsonArtifactMetadata,
    modules: list[JsonModuleArtifact],
) -> bytes:
    """Return the deterministic ``oid_index.json`` payload."""
    oid_map: dict[str, list[dict[str, str]]] = defaultdict(list)

    for artifact in sorted(modules, key=lambda artifact: (artifact.module, artifact.file)):
        nodetypes = derive_nodetypes(artifact.module_data)
        for obj in artifact.module_data.objects.values():
            if obj.oid:
                oid_map[obj.oid].append(_oid_index_entry(artifact.module, obj, nodetypes))
        for notif in artifact.module_data.notifications.values():
            if notif.oid:
                oid_map[notif.oid].append(_oid_index_entry(artifact.module, notif, nodetypes))

    payload: dict[str, Any] = {
        "schema_version": artifact_metadata.schema_version,
        "producer_version": artifact_metadata.producer_version,
        "generated_by": artifact_metadata.generated_by,
        "generated_at": artifact_metadata.generated_at,
        "oids": {
            oid: sorted(
                entries,
                key=lambda entry: (
                    entry["module"],
                    entry["object"],
                    entry["class"],
                    entry["object_type"],
                ),
            )
            for oid, entries in sorted(oid_map.items(), key=lambda item: _oid_sort_key(item[0]))
        },
    }
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2)
