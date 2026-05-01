from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from trishul_smi.models.mib_object import MibObject
    from trishul_smi.models.mib_type import MibType


@dataclass
class MibModule:
    """Parsed representation of a single ASN.1 MIB module."""

    name: str
    language: Literal["SMIv1", "SMIv2"]
    imports: dict[str, list[str]] = field(default_factory=dict)
    # {"SNMPv2-SMI": ["OBJECT-TYPE", "Integer32"], ...}
    objects: dict[str, MibObject] = field(default_factory=dict)
    types: dict[str, MibType] = field(default_factory=dict)
    notifications: dict[str, MibObject] = field(default_factory=dict)
    organization: str | None = None
    revisions: list[dict[str, str]] = field(default_factory=list)
    source_text: str | None = None  # original raw ASN.1, kept for debugging

    def all_imports(self) -> list[str]:
        """Return flat list of all imported MIB module names."""
        return list(self.imports.keys())
