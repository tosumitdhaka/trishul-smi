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
    contactinfo: str | None = None
    lastupdated: str | None = None
    revisions: list[dict[str, str]] = field(default_factory=list)
    description: str | None = None
    source_text: str | None = None  # original raw ASN.1, kept for debugging
    # Non-fatal parser warnings (e.g. non-standard vendor syntax accepted leniently).
    warnings: list[str] = field(default_factory=list)

    def all_imports(self) -> list[str]:
        """Return flat list of all imported MIB module names."""
        return list(self.imports.keys())

    def import_reverse_map(self) -> dict[str, str]:
        """Return a reverse map: imported symbol name → source MIB module name."""
        return {sym: mod for mod, syms in self.imports.items() for sym in syms}
