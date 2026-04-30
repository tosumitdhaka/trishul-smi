"""MibModule-level compiled cache.

Stores fully-parsed MibModule objects as orjson on disk so the fetch+parse
step can be skipped on repeated runs. This is separate from HttpReader's
raw-text cache (reader/httpclient.py) which caches ASN.1 source bytes.

Cache layout:
    {cache_dir}/compiled/{mib_name}.json

Invalidation:
    File mtime vs CompilerConfig.cache_ttl_days. When cache_ttl_days=0
    entries never expire (useful for offline/air-gapped workflows).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import orjson

from trishul_smi.errors import MibCacheError
from trishul_smi.models.mib_module import MibModule
from trishul_smi.models.mib_object import MibObject
from trishul_smi.models.mib_type import MibType

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _module_to_bytes(module: MibModule) -> bytes:
    """Serialise MibModule to orjson bytes. source_text is intentionally
    excluded from the cache to keep files small."""

    def _obj(o: MibObject) -> dict[str, Any]:
        return {
            "name": o.name,
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

    def _typ(t: MibType) -> dict[str, Any]:
        return {
            "name": t.name,
            "base_type": t.base_type,
            "constraints": t.constraints,
            "description": t.description,
        }

    payload: dict[str, Any] = {
        "name": module.name,
        "language": module.language,
        "imports": module.imports,
        "objects": {k: _obj(v) for k, v in module.objects.items()},
        "types": {k: _typ(v) for k, v in module.types.items()},
        "notifications": {k: _obj(v) for k, v in module.notifications.items()},
    }
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2)


def _module_from_dict(d: dict[str, Any]) -> MibModule:
    """Reconstruct a MibModule from a deserialised dict."""

    def _obj(o: dict[str, Any]) -> MibObject:
        return MibObject(
            name=o["name"],
            oid=o["oid"],
            oid_path=o.get("oid_path") or [],
            object_type=o.get("object_type", ""),
            syntax=o.get("syntax"),
            max_access=o.get("max_access"),
            status=o.get("status"),
            description=o.get("description"),
            index=o.get("index"),
            augments=o.get("augments"),
        )

    def _typ(t: dict[str, Any]) -> MibType:
        return MibType(
            name=t["name"],
            base_type=t.get("base_type", ""),
            constraints=t.get("constraints"),
            description=t.get("description"),
        )

    return MibModule(
        name=d["name"],
        language=d["language"],
        imports=d.get("imports", {}),
        objects={k: _obj(v) for k, v in d.get("objects", {}).items()},
        types={k: _typ(v) for k, v in d.get("types", {}).items()},
        notifications={k: _obj(v) for k, v in d.get("notifications", {}).items()},
    )


# ---------------------------------------------------------------------------
# MibCache
# ---------------------------------------------------------------------------


class MibCache:
    """Disk-backed cache for compiled MibModule objects.

    Args:
        cache_dir: Root directory for cached files.
            Compiled modules go in ``{cache_dir}/compiled/``.
        ttl_days: Entries older than this many days are treated as stale
            and re-fetched. ``0`` means never expire.

    Raises:
        MibCacheError: if the cache directory cannot be created (e.g.
            permission denied). Raised at construction time so the caller
            learns immediately rather than on the first cache write.
    """

    _SUBDIR = "compiled"

    def __init__(self, cache_dir: Path, ttl_days: int = 7) -> None:
        self._dir = cache_dir / self._SUBDIR
        self._ttl_seconds = ttl_days * 86_400  # 0 → never expire
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MibCacheError(f"Cannot create cache directory {self._dir}: {exc}") from exc

    def _path(self, mib_name: str) -> Path:
        return self._dir / f"{mib_name}.json"

    def _is_stale(self, path: Path) -> bool:
        if self._ttl_seconds == 0:
            return False
        # st_mtime is a wall-clock timestamp, so time.time() is correct here —
        # unlike in-memory TTL checks (httpclient.py) where monotonic is safer
        # because monotonic is immune to NTP slew and VM clock jumps.
        age = time.time() - path.stat().st_mtime
        return age > self._ttl_seconds

    def get(self, mib_name: str) -> MibModule | None:
        """Return cached MibModule or None if absent / stale."""
        path = self._path(mib_name)
        if not path.is_file():
            return None
        if self._is_stale(path):
            path.unlink(missing_ok=True)
            return None
        try:
            data = orjson.loads(path.read_bytes())
            return _module_from_dict(data)
        except (orjson.JSONDecodeError, KeyError):
            # Corrupted cache file — delete and signal miss
            path.unlink(missing_ok=True)
            return None

    def put(self, mib_name: str, module: MibModule) -> None:
        """Persist a compiled MibModule to disk atomically.

        Writes to a sibling ``.tmp`` file first, then renames to the final
        path. ``Path.replace()`` is atomic on POSIX (rename(2) syscall) and
        best-effort on Windows. This prevents partially-written files if the
        process is killed mid-write, or if two concurrent asyncio.gather tasks
        write different MIBs whose paths collide on a race.
        """
        path = self._path(mib_name)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(_module_to_bytes(module))
        tmp.replace(path)  # atomic on POSIX

    def invalidate(self, mib_name: str) -> None:
        """Remove a single cached entry."""
        self._path(mib_name).unlink(missing_ok=True)

    def clear(self) -> None:
        """Delete all compiled cache files."""
        for f in self._dir.glob("*.json"):
            f.unlink(missing_ok=True)
