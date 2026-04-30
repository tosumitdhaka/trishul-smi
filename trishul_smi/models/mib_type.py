from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MibType:
    """A TEXTUAL-CONVENTION or derived type defined in a MIB module."""

    name: str
    base_type: str  # "OCTET STRING", "Integer32", "DisplayString", ...
    constraints: dict | None = None  # range / size / enum constraints
    description: str | None = None
