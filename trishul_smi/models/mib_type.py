from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MibType:
    """A TEXTUAL-CONVENTION or derived type defined in a MIB module."""

    name: str
    base_type: str  # "OCTET STRING", "Integer32", "DisplayString", ...
    constraints: dict[str, Any] | None = None  # range / size / enum constraints
    description: str | None = None
