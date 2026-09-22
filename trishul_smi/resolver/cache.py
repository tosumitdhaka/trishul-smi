"""MibModule-level compiled cache.

Stores fully-parsed MibModule objects as orjson on disk so the parse step can
be skipped on repeated runs.

Cache layout:
    {cache_dir}/compiled/{mib_name}.json

Invalidation:
    File mtime vs CompilerConfig.cache_ttl_days. When cache_ttl_days=0
    entries never expire. Note: since the fetch-first fingerprint design
    (issue #12), a warm cache alone no longer serves a compile when the
    source is unreachable — the source is always fetched first so its
    fingerprint can be checked.

Fingerprint invalidation (issue #12):
    Each entry additionally records ``source_fingerprint`` — the sha256 hex
    digest of the raw ASN.1 source text the module was parsed from.
    ``MibResolver`` always fetches the source first and passes the fingerprint
    to ``get()``; a mismatch is a miss, so an updated source file can never
    serve a stale entry.

    Trade-off: the cache cannot avoid fetches — it saves parsing only.
    Fetch-avoidance on cache hits was abandoned so the content fingerprint is
    always available; local reads are cheap, and the HTTP freshness machinery
    that previously justified the old name-keyed design was deleted in v0.4.8
    as dead code.
"""

from __future__ import annotations

import os
import tempfile
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


def _module_to_bytes(module: MibModule, source_fingerprint: str | None = None) -> bytes:
    """Serialise MibModule to orjson bytes. source_text is intentionally
    excluded from the cache to keep files small."""

    def _obj(o: MibObject) -> dict[str, Any]:
        return {
            "name": o.name,
            "oid": o.oid,
            "oid_path": o.oid_path,
            "oid_parent": o.oid_parent,
            "object_type": o.object_type,
            "syntax": o.syntax,
            "max_access": o.max_access,
            "status": o.status,
            "description": o.description,
            "index": o.index,
            "augments": o.augments,
            "constraints": o.constraints,
            "members": o.members,
            "enterprise": o.enterprise,
            "trap_number": o.trap_number,
        }

    def _typ(t: MibType) -> dict[str, Any]:
        return {
            "name": t.name,
            "base_type": t.base_type,
            "constraints": t.constraints,
            "description": t.description,
            "display_hint": t.display_hint,
            "status": t.status,
        }

    payload: dict[str, Any] = {
        "name": module.name,
        "language": module.language,
        "imports": module.imports,
        "objects": {k: _obj(v) for k, v in module.objects.items()},
        "types": {k: _typ(v) for k, v in module.types.items()},
        "notifications": {k: _obj(v) for k, v in module.notifications.items()},
        "organization": module.organization,
        "contactinfo": module.contactinfo,
        "lastupdated": module.lastupdated,
        "revisions": module.revisions,
        "description": module.description,
        "warnings": module.warnings,
    }
    if source_fingerprint is not None:
        payload["source_fingerprint"] = source_fingerprint
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2)


def _module_from_dict(d: dict[str, Any]) -> MibModule:
    """Reconstruct a MibModule from a deserialised dict."""

    def _obj(o: dict[str, Any]) -> MibObject:
        return MibObject(
            name=o["name"],
            oid=o["oid"],
            oid_path=o.get("oid_path") or [],
            oid_parent=o.get("oid_parent"),
            object_type=o.get("object_type", ""),
            syntax=o.get("syntax"),
            max_access=o.get("max_access"),
            status=o.get("status"),
            description=o.get("description"),
            index=o.get("index"),
            augments=o.get("augments"),
            constraints=o.get("constraints"),
            members=o.get("members"),
            enterprise=o.get("enterprise"),
            trap_number=o.get("trap_number"),
        )

    def _typ(t: dict[str, Any]) -> MibType:
        return MibType(
            name=t["name"],
            base_type=t.get("base_type", ""),
            constraints=t.get("constraints"),
            description=t.get("description"),
            display_hint=t.get("display_hint"),
            status=t.get("status"),
        )

    return MibModule(
        name=d["name"],
        language=d["language"],
        imports=d.get("imports", {}),
        objects={k: _obj(v) for k, v in d.get("objects", {}).items()},
        types={k: _typ(v) for k, v in d.get("types", {}).items()},
        notifications={k: _obj(v) for k, v in d.get("notifications", {}).items()},
        organization=d.get("organization"),
        contactinfo=d.get("contactinfo"),
        lastupdated=d.get("lastupdated"),
        revisions=d.get("revisions") or [],
        description=d.get("description"),
        warnings=d.get("warnings") or [],
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

    Fingerprinting (issue #12):
        ``put()`` records the ``source_fingerprint`` (sha256 hex of the raw
        source text) passed by the caller; ``get()`` treats a supplied
        fingerprint that differs from the recorded one as a miss. Because the
        fingerprint can only be computed from the fetched source, the cache
        saves parsing only — it never avoids the fetch.

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
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            # stat() failed (TOCTOU removal between is_file() and the TTL
            # check, or unreadable metadata) — treat as stale so get()
            # surfaces a miss rather than propagating.
            return True
        return age > self._ttl_seconds

    def get(self, mib_name: str, fingerprint: str | None = None) -> MibModule | None:
        """Return cached MibModule or None if absent / stale / fingerprint-mismatched."""
        path = self._path(mib_name)
        if not path.is_file():
            return None
        if self._is_stale(path):
            path.unlink(missing_ok=True)
            return None
        try:
            data = orjson.loads(path.read_bytes())
            module = _module_from_dict(data)
        except (OSError, orjson.JSONDecodeError, KeyError):
            # Unreadable or corrupted cache file — delete and signal miss.
            # OSError covers TOCTOU (the file vanished between is_file() and
            # read_bytes()) and permission errors: a cache is an optimization,
            # so one unreadable entry must never kill a compile.
            path.unlink(missing_ok=True)
            return None
        if fingerprint is not None and data.get("source_fingerprint") != fingerprint:
            # The raw source this entry was parsed from has changed since it
            # was cached — treat as miss (issue #12). Entries written before
            # fingerprinting (no recorded fingerprint) also miss.
            path.unlink(missing_ok=True)
            return None
        return module

    def put(self, mib_name: str, module: MibModule, source_fingerprint: str | None = None) -> None:
        """Persist a compiled MibModule to disk atomically.

        Writes to a uniquely-named temp file first (``tempfile.mkstemp``),
        then renames to the final path. ``Path.replace()`` is atomic on POSIX
        (rename(2) syscall) and best-effort on Windows. This prevents
        partially-written files if the process is killed mid-write — and
        ``mkstemp`` (unlike a predictable ``{name}.tmp`` sibling path) keeps
        two concurrent processes writing the same entry from interleaving
        bytes in one temp file before the rename (issue #20).

        ``source_fingerprint`` (sha256 hex of the raw source text, issue #12)
        is recorded in the payload so a later ``get()`` can refuse the entry
        once the source changes.
        """
        path = self._path(mib_name)
        fd: int | None = None
        tmp_name: str | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(dir=self._dir, prefix=f"{mib_name}.", suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fd = None  # fd is now owned by the buffered writer
                fh.write(_module_to_bytes(module, source_fingerprint))
            Path(tmp_name).replace(path)  # atomic on POSIX
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            if tmp_name is not None:
                Path(tmp_name).unlink(missing_ok=True)
            raise MibCacheError(f"Cannot write cache for {mib_name}: {exc}") from exc

    def invalidate(self, mib_name: str) -> None:
        """Remove a single cached entry."""
        self._path(mib_name).unlink(missing_ok=True)

    def clear(self) -> None:
        """Delete all compiled cache files."""
        for f in self._dir.glob("*.json"):
            f.unlink(missing_ok=True)
