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

## v0.3.0 — shipped 2026-05-06

JSON output completeness, pysnmp correctness, grammar coverage.

### JSON output

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | NOTIFICATION-TYPE `members` list in JSON | done | `OBJECTS` clause wired through transformer; resolved to `{module, object}` dicts. |
| 2 | `--no-texts` flag for JSON | done | `JsonFormatter` honours the flag; suppresses description/organization/contactinfo/revision descriptions. |
| 3 | Module-identity metadata in JSON | done | `module_metadata` block always emitted; `lastupdated` (ISO 8601), `revisions`, text fields conditional on `--no-texts`. |
| 4 | TC `display_hint` and `status` in JSON | done | Both fields now emitted in the `types` section. |
| 5 | Conformance group member lists in JSON | done | `OBJECT-GROUP`, `NOTIFICATION-GROUP`, `MODULE-COMPLIANCE` carry `members` as `{module, object}` dicts. |
| 6 | `BASE_MIBS` explicit-request bypass | done | `pending = set(mib_names)` — explicit requests compile normally; filter only applies to transitive deps. |

### pysnmp output

| # | Item | Status | Notes |
|---|------|--------|-------|
| 7 | Standard `mibBuilder` injection | done | Jinja2 template uses `if 'mibBuilder' not in globals()` guard. |
| 8 | `.setObjects()` on notifications | done | `OBJECTS` clause wired through transformer → `MibObject.members` → template. |

### Grammar and parser (bonus)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 9 | MACRO body preprocessing | done | `_strip_macro_bodies()` keeps Lark in LALR for all tested MIBs; eliminates ~10× cold-parse slowdown. |
| 10 | SMIv1 grammar gaps | done | EXPORTS clause, CHOICE type, BITS-in-SEQUENCE, DEFVAL variants, INTEGER range in type assignments. 35 → 6 corpus failures. |
| 11 | `"missing"` compile status | done | `MibNotFoundError` → `status="missing"`, separate from `"failed"`. |

---

## v0.3.1

Implementation is complete locally; release/tagging is still pending.

Correctness, status semantics, and CLI/runtime contract fixes.

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | CLI exits non-zero when modules are `missing` | done | GitHub `#1`. CLI now exits `1` when any result is `missing` or `failed`. |
| 2 | CLI compile path applies configured reader options | done | GitHub `#2`. `_compile_async()` now passes size, timeout, retry, cache dir, and cache TTL settings into `FileReader` / `HttpReader`. |
| 3 | Block compilation when non-base imports fail to resolve | done | GitHub `#3`. Blocked dependents are marked `failed`, skipped from output emission, and preserve the unresolved dependency result separately. |
| 4 | Distinguish HTTP/network failure from true not-found | done | GitHub `#4`. True miss outcomes stay `missing`; transport/server failures now surface via `NetworkError` and become `failed`. |

---

## v0.4.0

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | Define runtime bundle contract: module JSON is atomic, sidecars optional | planned | GitHub `#8`. Module JSON is the source of truth; `manifest.json` and `oid_index.json` are additive sidecars. |
| 2 | Version the JSON IR for downstream consumers | planned | GitHub `#7`. Add `schema_version` and producer metadata so downstream runtimes can detect compatibility explicitly. |
| 3 | Emit a bundle manifest for compiled JSON output | planned | GitHub `#6`. Add `manifest.json` as optional bundle metadata for deterministic discovery. |
| 4 | Generate `oid_index.json` for fast reverse OID lookup | planned | GitHub `#5`. Flat JSON lookup artifact for OID → module/object resolution; optional accelerator, not source of truth. |
| 5 | MIB validation / lint mode | planned | `tsmi lint IF-MIB` — report missing imports, undefined types, etc. |
| 6 | Watch mode | planned | Recompile on file change for local MIB development workflows. |
| 7 | Plugin system for custom formatters | planned | Allow third-party output formats without forking. |
| 8 | MIB borrowing (pre-compiled fallback) | planned | Download pre-compiled MIBs from a remote registry as fallback. |
