"""Version helpers for runtime metadata."""

from __future__ import annotations

# Source-controlled producer version used by emitted artifacts.
VERSION = "0.4.0"


def get_producer_version() -> str:
    """Return the producer version embedded in emitted artifacts."""
    return VERSION
