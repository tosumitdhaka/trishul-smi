"""JSON output formatter.

Produces a single structured JSON file per MIB module containing all
parsed metadata: imports, objects, types, notifications.

Output file: {output_dir}/{ModuleName}.json
"""

from __future__ import annotations

from typing import Any

import orjson

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.models.mib_type import MibType


def _obj_dict(o: MibObject) -> dict[str, Any]:
    return {
        "oid": o.oid,
        "oid_path": o.oid_path,
        "object_type": o.object_type,
        "syntax": o.syntax,
        "max_access": o.max_access,
        "status": o.status,
        "description": o.description,
        "index": o.index,
        "augments": o.augments,
    }


def _type_dict(t: MibType) -> dict[str, Any]:
    return {
        "base_type": t.base_type,
        "constraints": t.constraints,
        "description": t.description,
    }


class JsonFormatter:
    """Serialises a MibModule to indented JSON bytes."""

    FILE_SUFFIX = ".json"

    def format(self, module: MibModule) -> bytes:
        """Return UTF-8 JSON bytes for *module*."""
        payload: dict[str, Any] = {
            "module": module.name,
            "language": module.language,
            "generated_by": "trishul-smi",
            "imports": module.imports,
            "objects": {k: _obj_dict(v) for k, v in module.objects.items()},
            "types": {k: _type_dict(v) for k, v in module.types.items()},
            "notifications": {k: _obj_dict(v) for k, v in module.notifications.items()},
        }
        return orjson.dumps(payload, option=orjson.OPT_INDENT_2)
