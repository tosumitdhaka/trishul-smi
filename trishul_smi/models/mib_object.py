from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MibObject:
    """A single named object inside a MIB module (OBJECT-TYPE, MODULE-IDENTITY, etc.)."""

    name: str
    oid: str  # dotted-decimal string: "1.3.6.1.2.1.2.2.1.2"
    oid_path: list[int] = field(default_factory=list)
    object_type: str = ""  # "OBJECT-TYPE", "MODULE-IDENTITY", "NOTIFICATION-TYPE", ...
    syntax: str | None = None
    max_access: str | None = None
    status: str | None = None
    description: str | None = None
    index: list[str] | None = None   # columnar objects: INDEX { ... }
    augments: str | None = None      # columnar objects: AUGMENTS { <row> }
