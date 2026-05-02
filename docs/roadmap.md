# Roadmap

Tracks planned features, known limitations, and deferred work.
Status: `planned` | `in progress` | `done` | `deferred`

---

## v0.2.0

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | Full OID resolution to absolute numeric paths | done | `oid_resolver.py` walks the full chain across all loaded modules in topological order. |
| 2 | `MibTableColumn` detection in `PysnmpFormatter` | done | Two-pass OID tree walk; parent OID → row class lookup. |
| 3 | `setIndexNames` / `setAugmentation` in pysnmp output | done | `INDEX` → `setIndexNames`; `AUGMENTS` → `getIndexNames()`. |
| 4 | `ModuleIdentity.setRevisions()` in pysnmp output | done | Revision date + description extracted from transformer and stored on `MibModule`. |
| 5 | Full TEXTUAL-CONVENTION class generation | done | Proper subclasses with `subtypeSpec`, `displayHint`, `status`, `description`. Constraint expressions for size/range/enum/bits/union. |
| 6 | Write all compiled dependencies to disk | done | All transitively compiled modules written to output directory. |
| 7 | `exportSymbols` single-dict format | done | Single `exportSymbols()` call with all symbols in one merged dict. |
| 8 | TC description as class attribute in pysnmp output | done | `description = """..."""` inside TC class body. |
| 9 | `setOrganization` on MODULE-IDENTITY in pysnmp output | done | `setOrganization` and `setDescription` emitted for MODULE-IDENTITY. |
| 10 | `--no-texts` flag to suppress descriptions | done | Suppresses `setDescription`/`setOrganization`/`setRevisions` and TC description. |
| 11 | Vendor dialect quirks (Cisco, HP, NET-SNMP) | done | Hex range bounds case-insensitive (`'ff'h`); GROUP/COMPLIANCE status+description; SNMPv2-CONF symbol name mapping. |
| 12 | PySNMP `.py` → JSON reverse conversion | done | `tsmi convert FILE.py` — ast-based reader, no grammar required. |

---

## v0.3.0

JSON output completeness and pysnmp output correctness.

### JSON output

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | NOTIFICATION-TYPE objects in JSON | planned | `linkDown`, `linkUp`, and all `NOTIFICATION-TYPE` definitions absent from `objects` dict. Need transformer coverage + JsonFormatter emission including bound OBJECTS list. |
| 2 | `--no-texts` flag for JSON | planned | `JsonFormatter` currently ignores the flag — descriptions always written. SC1 and SC2 JSON output is byte-for-byte identical. Should suppress `description`, `organization`, `contactinfo`, `revisions` when set. |
| 3 | Module-identity metadata in JSON | planned | `organization`, `contactinfo`, `lastupdated`, `revisions` not emitted. Transformer parses them (available on `MibModule`); `JsonFormatter` needs to surface them. |
| 4 | TC `displayhint` and `status` in JSON | planned | Both fields parsed and stored on `MibType`; `JsonFormatter` not emitting them in the `types` section. |
| 5 | Conformance group member lists in JSON | planned | `OBJECT-GROUP` and `NOTIFICATION-GROUP` entries emit only OID/status/description — member object list dropped. Transformer + JsonFormatter change. |
| 6 | SNMPv2-MIB silently skipped | planned | In `BASE_MIBS` frozenset; never compiled even when explicitly requested. Should honour explicit requests regardless of base-MIB status. |

### pysnmp output

| # | Item | Status | Notes |
|---|------|--------|-------|
| 7 | Standard `mibBuilder` injection | planned | Compiled `.py` modules create their own `MibBuilder()` instead of accepting the one injected by the runtime. Fix the Jinja2 template to use the standard `if 'mibBuilder' not in globals()` guard. |
| 8 | `.setObjects()` on notifications | planned | Acknowledged `# TODO` in the Jinja2 template. Notifications carry no varbind definitions — need to wire the `OBJECTS` clause through the formatter and emit `.setObjects()`. |

---

## v0.4.0

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | OID index file generation | planned | Flat JSON file mapping OID → module/object for fast reverse lookup. |
| 2 | MIB validation / lint mode | planned | `tsmi lint IF-MIB` — report missing imports, undefined types, etc. |
| 3 | Watch mode | planned | Recompile on file change for local MIB development workflows. |
| 4 | Plugin system for custom formatters | planned | Allow third-party output formats without forking. |
| 5 | MIB borrowing (pre-compiled fallback) | planned | Download pre-compiled MIBs from a remote registry as fallback. |
