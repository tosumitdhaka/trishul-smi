# Roadmap

Tracks planned features, known limitations, and deferred work.
Status: `planned` | `in progress` | `done` | `deferred`

---

## v0.2.0

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | Full OID resolution to absolute numeric paths | planned | OIDs currently store only the local arc (`oid_path=[1]`). Need to walk the full chain across all loaded modules to emit absolute paths e.g. `(1, 3, 6, 1, 2, 1, 2, 2, 1, 1)`. Blocks items 2 and 3. |
| 2 | `MibTableColumn` detection in `PysnmpFormatter` | planned | Blocked on item 1. Currently all table columns are emitted as `MibScalar`. |
| 3 | `setIndexNames` / `setAugmentation` in pysnmp output | planned | Blocked on item 1. pysmi emits `ifEntry.setIndexNames((0, "IF-MIB", "ifIndex"))` and `ifXEntry.setIndexNames(*ifEntry.getIndexNames())`. tsmi emits nothing. |
| 4 | `ModuleIdentity.setRevisions()` in pysnmp output | planned | pysmi emits full revision history. tsmi omits it entirely. |
| 5 | Full TEXTUAL-CONVENTION class generation | planned | pysmi emits proper subclasses with `subtypeSpec`, `displayHint`, `ValueSizeConstraint`. tsmi emits `OwnerString = TextualConvention  # TODO` stubs. |
| 6 | Write all compiled dependencies to disk | planned | tsmi resolves and parses transitive dependencies but only writes the explicitly requested MIBs. pysmi writes all compiled modules (e.g. `IANAifType-MIB.py`, `SNMPv2-MIB.py`). |
| 7 | `exportSymbols` single-dict format | planned | pysmi emits one `exportSymbols()` call with all symbols in a single dict. tsmi emits one `**{name: obj}` per symbol — valid but non-standard. |
| 8 | TC description as class attribute in pysnmp output | planned | pysmi puts description inside the TC class body (`description = "..."`). tsmi uses `setDescription` outside the class. |
| 9 | `setOrganization` on MODULE-IDENTITY in pysnmp output | planned | pysmi emits `ifMIB.setOrganization("...")`. tsmi omits it. |
| 10 | `--no-texts` flag to suppress descriptions | planned | Skip all `setDescription` / `setOrganization` / `setRevisions` calls for lean output. Mirrors pysmi's default (no `--generate-mib-texts`) behaviour. |
| 11 | Vendor dialect quirks (Cisco, HP, NET-SNMP) | planned | Grammar covers common cases; edge cases driven by real-world MIB failures. |
| 12 | PySNMP `.py` → JSON reverse conversion | planned | `tsmi convert IF_MIB.py`. Uses Python `ast` module, not the SMI grammar parser. |

---

## v0.3.0

| # | Item | Status | Notes |
|---|------|--------|-------|
| 5 | OID index file generation | planned | Flat JSON file mapping OID → module/object for fast reverse lookup. |
| 6 | MIB validation / lint mode | planned | `tsmi lint IF-MIB` — report missing imports, undefined types, etc. |
| 7 | Watch mode | planned | Recompile on file change for local MIB development workflows. |

---

## v1.x (future)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 8 | Plugin system for custom formatters | planned | Allow third-party output formats without forking. |
| 9 | MIB borrowing (pre-compiled fallback) | planned | Download pre-compiled MIBs from a remote registry as fallback. |
