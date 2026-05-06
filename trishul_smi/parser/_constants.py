"""Parser-level shared constants.

Defined once here so that dialect detection is consistent between
smi_parser.py (which uses it to pre-detect before parsing) and
transformer.py (which uses it to set MibModule.language after parsing).

Issue #4: previously each file had its own copy of the SMIv2 marker set,
which could silently drift out of sync.
"""

from __future__ import annotations

# Module names that, when present in IMPORTS, confirm a MIB is SMIv2.
SMIv2_MARKERS: frozenset[str] = frozenset(
    {
        "SNMPv2-SMI",
        "SNMPv2-TC",
        "SNMPv2-CONF",
        "SNMPv2-MIB",
    }
)

# Well-known SNMP infrastructure MIBs skipped during *transitive* dependency
# resolution — they are not distributed as standalone ASN.1 files and will
# 404 from MIB repositories when chased as deps. Explicit compile requests
# for these names are still honoured (resolver.py initial pending set).
BASE_MIBS: frozenset[str] = frozenset(
    {
        "SNMPv2-SMI",
        "SNMPv2-TC",
        "SNMPv2-CONF",
        "SNMPv2-MIB",
        # SMIv1 compatibility shims used by some vendor MIB converters
        "SNMPv2-SMI-v1",
        "SNMPv2-TC-v1",
        "RFC1155-SMI",
        "RFC1213-MIB",
        "RFC-1212",
        "RFC-1215",
    }
)
