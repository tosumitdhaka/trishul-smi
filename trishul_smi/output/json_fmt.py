"""JSON output formatter.

Produces a single structured JSON file per MIB module containing all
parsed metadata: imports, objects, types, notifications.

Output file: {output_dir}/{ModuleName}.json
"""

from __future__ import annotations

import re
from typing import Any

import orjson

from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.models.mib_type import MibType

# Collapse multi-line integer arrays: [ \n  1,\n  3,\n  ... \n] → [1, 3, ...]
_INT_ARRAY_RE = re.compile(r"\[\n(?:\s+\d+,?\n)+\s*\]")


def _norm_desc(text: str | None) -> str | None:
    """Normalize MIB description text: collapse whitespace into single spaces."""
    if text is None:
        return None
    normalized = " ".join(text.split())
    return normalized or None


def _compact_int_arrays(data: bytes) -> bytes:
    def _collapse(m: re.Match[str]) -> str:
        nums = re.findall(r"\d+", m.group())
        return "[" + ", ".join(nums) + "]"

    return _INT_ARRAY_RE.sub(_collapse, data.decode()).encode()


def _obj_dict(o: MibObject) -> dict[str, Any]:
    return {
        "oid": o.oid,
        "oid_path": o.oid_path,
        "object_type": o.object_type,
        "syntax": o.syntax,
        "max_access": o.max_access,
        "status": o.status,
        "description": _norm_desc(o.description),
        "index": o.index,
        "augments": o.augments,
    }


def _type_dict(t: MibType) -> dict[str, Any]:
    return {
        "base_type": t.base_type,
        "constraints": t.constraints,
        "description": _norm_desc(t.description),
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
        return _compact_int_arrays(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
