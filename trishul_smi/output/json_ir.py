"""Shared metadata for JSON IR artifacts.

The same metadata contract applies to module JSON now, and will later be
reused by optional bundle sidecars such as ``manifest.json`` and
``oid_index.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from trishul_smi.version import get_producer_version

JSON_IR_SCHEMA_VERSION = "1.1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class JsonArtifactMetadata:
    """Metadata shared by every JSON artifact emitted in one compile run."""

    schema_version: str
    producer_version: str
    generated_by: str
    generated_at: str


def make_json_artifact_metadata(
    *,
    schema_version: str = JSON_IR_SCHEMA_VERSION,
    producer_version: str | None = None,
    generated_by: str = "trishul-smi",
    generated_at: str | None = None,
) -> JsonArtifactMetadata:
    """Build the shared JSON metadata block for one compile run."""
    return JsonArtifactMetadata(
        schema_version=schema_version,
        producer_version=(get_producer_version() if producer_version is None else producer_version),
        generated_by=generated_by,
        generated_at=_utc_now_iso() if generated_at is None else generated_at,
    )
