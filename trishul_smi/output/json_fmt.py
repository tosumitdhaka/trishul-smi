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
from trishul_smi.output.json_contract import derive_nodetypes, object_class, runtime_oid
from trishul_smi.output.json_ir import JsonArtifactMetadata, make_json_artifact_metadata

# Collapse multi-line integer arrays: [ \n  1,\n  3,\n  ... \n] → [1, 3, ...]
_INT_ARRAY_RE = re.compile(r"\[\n(?:\s+\d+,?\n)+\s*\]")

# SMIv2 revision dates: "200003060000Z" or "9912160000Z" → ISO 8601.
# Format: YYYYMMDDHHmmZ (12 chars, century present) or YYMMDDHHmmZ (11 chars).
_SMI_DATE_RE = re.compile(r"^(\d{2,4})(\d{2})(\d{2})\d{4}Z$")


def _norm_desc(text: str | None) -> str | None:
    """Normalize MIB description text: collapse whitespace into single spaces."""
    if text is None:
        return None
    normalized = " ".join(text.split())
    return normalized or None


def _smi_date_to_iso(smi_date: str | None) -> str | None:
    """Convert SMIv2 date string to ISO 8601 date (YYYY-MM-DD), or return as-is."""
    if not smi_date:
        return smi_date
    m = _SMI_DATE_RE.match(smi_date.strip())
    if not m:
        return smi_date
    year_raw, month, day = m.group(1), m.group(2), m.group(3)
    # Two-digit year: 0–49 → 2000s, 50–99 → 1900s (RFC 2578 convention)
    if len(year_raw) == 2:
        y = int(year_raw)
        year = str(2000 + y if y < 50 else 1900 + y)
    else:
        year = year_raw
    return f"{year}-{month}-{day}"


def _compact_int_arrays(data: bytes) -> bytes:
    if b"[\n" not in data:
        return data

    def _collapse(m: re.Match[str]) -> str:
        nums = re.findall(r"\d+", m.group())
        return "[" + ", ".join(nums) + "]"

    return _INT_ARRAY_RE.sub(_collapse, data.decode()).encode()


def _resolve_member(name: str, this_module: str, name_to_mod: dict[str, str]) -> dict[str, str]:
    mod = name_to_mod.get(name, this_module)
    return {"module": mod, "object": name}


def _obj_dict(
    o: MibObject,
    no_texts: bool,
    nodetypes: dict[str, str] | None,
    name_to_mod: dict[str, str],
    module_name: str,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "oid_path": o.oid_path,
        "object_type": o.object_type,
        "class": object_class(o.object_type),
        "syntax": o.syntax,
        "max_access": o.max_access,
        "status": o.status,
        "index": o.index,
        "augments": o.augments,
    }
    oid = runtime_oid(o)
    if oid is not None:
        d["oid"] = oid
    if nodetypes is not None and o.object_type == "OBJECT-TYPE":
        d["nodetype"] = nodetypes.get(o.name, "scalar")
    if o.constraints is not None:
        d["constraints"] = o.constraints
    if o.members is not None:
        d["members"] = [_resolve_member(m, module_name, name_to_mod) for m in o.members]
    if not no_texts:
        d["description"] = _norm_desc(o.description)
    return d


def _type_dict(t: MibType, no_texts: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {
        "class": "textualconvention",
        "base_type": t.base_type,
        "constraints": t.constraints,
        "display_hint": t.display_hint,
        "status": t.status,
    }
    if not no_texts:
        d["description"] = _norm_desc(t.description)
    return d


class JsonFormatter:
    """Serialises a MibModule to indented JSON bytes."""

    FILE_SUFFIX = ".json"

    def __init__(
        self,
        no_texts: bool = False,
        artifact_metadata: JsonArtifactMetadata | None = None,
    ) -> None:
        self._no_texts = no_texts
        self._artifact_metadata = artifact_metadata or make_json_artifact_metadata()

    def set_artifact_metadata(self, artifact_metadata: JsonArtifactMetadata) -> None:
        """Refresh the shared metadata used for subsequent JSON renders."""
        self._artifact_metadata = artifact_metadata

    def format(self, module: MibModule) -> bytes:
        """Return UTF-8 JSON bytes for *module*."""
        no_texts = self._no_texts
        nodetypes = derive_nodetypes(module)
        name_to_mod = module.import_reverse_map()
        artifact_metadata = self._artifact_metadata

        payload: dict[str, Any] = {
            "module": module.name,
            "language": module.language,
            "schema_version": artifact_metadata.schema_version,
            "producer_version": artifact_metadata.producer_version,
            "generated_by": artifact_metadata.generated_by,
            "generated_at": artifact_metadata.generated_at,
            "imports": module.imports,
            "objects": {
                k: _obj_dict(v, no_texts, nodetypes, name_to_mod, module.name)
                for k, v in module.objects.items()
            },
            "types": {k: _type_dict(v, no_texts) for k, v in module.types.items()},
            "notifications": {
                k: _obj_dict(v, no_texts, None, name_to_mod, module.name)
                for k, v in module.notifications.items()
            },
        }

        meta: dict[str, Any] = {
            "lastupdated": _smi_date_to_iso(module.lastupdated),
            "revisions": [{"date": _smi_date_to_iso(r.get("date"))} for r in module.revisions],
        }
        if not no_texts:
            meta["organization"] = module.organization
            meta["contactinfo"] = module.contactinfo
            meta["description"] = _norm_desc(module.description)
            for rev_out, rev_in in zip(meta["revisions"], module.revisions, strict=True):
                rev_out["description"] = rev_in.get("description")
        payload["module_metadata"] = meta

        return _compact_int_arrays(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
