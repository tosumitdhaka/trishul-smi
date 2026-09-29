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
    # Non-fatal parser warnings (e.g. non-standard vendor syntax accepted leniently).
    warnings: list[str] = field(default_factory=list)
    # Requested name -> declared name for this module when its file was
    # fetched under a different name (misnamed MIB files, issue #21).
    # Recorded by MibResolver._reconcile_name and consumed by
    # oid_resolver.resolve_oids to map an importer's alias-named provider
    # back to the module actually present in the resolved closure (issue #39).
    aliases: dict[str, str] = field(default_factory=dict)
    # Identifier tokens harvested from MACRO-definition bodies (issue #37).
    # Macro bodies are stripped before parsing (free-form ASN.1), so symbols
    # that are used ONLY inside them (e.g. `ObjectName` inside SNMPv2-CONF's
    # MODULE-COMPLIANCE body) never appear as structural references. The lint
    # engine counts these tokens as uses — restricted to symbols already in
    # the module's import map — so such imports stop firing `unused-import`.
    # Harvested from quote/comment-masked body spans, so string/comment
    # content can never leak in. Never emitted to JSON (schema_version 1.1).
    macro_body_symbols: list[str] = field(default_factory=list)

    def all_imports(self) -> list[str]:
        """Return flat list of all imported MIB module names."""
        return list(self.imports.keys())

    def import_reverse_map(self) -> dict[str, str]:
        """Return a reverse map: imported symbol name → source MIB module name."""
        return {sym: mod for mod, syms in self.imports.items() for sym in syms}
