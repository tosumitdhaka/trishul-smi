# CLI Reference

---

## `tsmi compile` / `trishul-smi compile`

```
tsmi compile [OPTIONS] MIB [MIB ...]
```

Compile one or more MIBs and all their transitive dependencies.

**Arguments**

| Argument | Description |
|---|---|
| `MIB ...` | One or more MIB names (e.g. `IF-MIB IP-MIB`). Omit to auto-discover every MIB file found in `--mib-dir` directories. |

**Options**

| Option | Default | Description |
|---|---|---|
| `-o` / `--output-dir` | `./mibs-output` | Directory to write output files |
| `-f` / `--format` | `json` | Output format: `json`. |
| `--emit-manifest` | off | Emit optional `manifest.json` bundle metadata alongside JSON output. Requires `json` output. |
| `--emit-oid-index` | off | Emit optional `oid_index.json` reverse-lookup metadata alongside JSON output. Requires `json` output. |
| `-d` / `--mib-dir` | — | Local MIB directory. Repeat for multiple. Searched before HTTP. |
| `--online` | off | Fetch missing MIBs from HTTP sources (pysnmp.com + mibbrowser.online). Off by default. |
| `-s` / `--source` | — | Custom HTTP URL template (`@mib@` replaced with MIB name). Implies `--online`. Repeat for multiple. |
| `--cache-dir` | `~/.cache/trishul-smi` | Compiled-module cache directory. Pass `""` to disable. |
| `--cache-ttl-days` | `7` | Cache TTL in days. `0` = never expire. |
| `--max-mib-size` | `10485760` | Maximum MIB source size in bytes. |
| `--timeout` | `30.0` | HTTP timeout in seconds. |
| `--retries` | `3` | HTTP retry count on transient failure. |
| `--no-texts` | off | Omit description, organization, and contact text from output for leaner files. Structural metadata (OIDs, dates, types) is always preserved. |
| `-v` / `--verbose` | — | Show output file paths per module. |
| `--watch` | off | Watch MIB source files for changes and recompile only the changed module and its dependents. Requires explicit MIB names or at least one `--mib-dir`. |
| `--help` | — | Show help and exit. |

**Exit codes:** `0` all compiled — `1` any failure — `2` bad option or no source configured.

**Examples**

```bash
# Compile from a local directory (no HTTP)
tsmi compile IF-MIB -d /usr/share/snmp/mibs

# Fetch from the internet
tsmi compile IF-MIB --online

# Custom output directory
tsmi compile IF-MIB IP-MIB -f json --online -o ./out

# Emit optional JSON bundle sidecars
tsmi compile IF-MIB --online --emit-manifest --emit-oid-index

# Local directory first, fall back to HTTP
tsmi compile IF-MIB -d /usr/share/snmp/mibs --online

# Compile every MIB found in a directory (no explicit names)
tsmi compile -d /usr/share/snmp/mibs -f json

# Disable the disk cache
tsmi compile IF-MIB --online --cache-dir ""

# Show per-module output paths
tsmi compile IF-MIB --online --verbose

# Lean output without description text
tsmi compile IF-MIB --online --no-texts

# Watch a local MIB directory and recompile on every change
tsmi compile IF-MIB -d /usr/share/snmp/mibs --watch
```

**Sample output**

```
Compiling IF-MIB → ./mibs-output (json)
Status    Module
✅        IANAifType-MIB
✅        IF-MIB

2 compiled
```

---

## Watch mode (`--watch`)

`tsmi compile --watch` keeps the compile running and recompiles automatically
when a MIB source file changes. It requires explicit MIB names or at least one
`--mib-dir` (no watchable sources otherwise → exit 2).

**How it works**

- The initial compile runs normally; afterwards the CLI polls the source files
  that participated in the resolved closure (the watched set) for changes.
- Changes are **debounced (~300 ms)** — a burst of rapid writes triggers a
  single recompile once the files stop changing.
- On a change, only the **changed module and its transitive dependents** are
  recompiled (the dependency graph is rebuilt from the last resolve's emitted
  JSON `imports`). Unchanged modules are served by the fingerprinted
  compiled-module cache and never re-parsed; modules outside the invalidation
  set are not re-requested, so their output files are left untouched.
- The same per-module results table is printed after every cycle.
- New MIB-looking files appearing in `--mib-dir` mid-watch are **adopted**:
  the new module joins the watch set, gets an initial compile folded into the
  next debounced cycle, and is watched like any other from then on (first
  `--mib-dir` wins on stem collisions). A new file that fails to parse
  surfaces as a `failed` result row without stopping the watcher. Removed
  files are reported with a one-line notice only.
- **Ctrl-C** stops the session cleanly: a short summary (cycles run, modules
  recompiled) is printed and the process exits `0`.

**Example**

```
$ tsmi compile IF-MIB -d /usr/share/snmp/mibs --watch
Watching IF-MIB → ./mibs-output (json, Ctrl-C to stop)

  Status   Module
  ✅       IANAifType-MIB
  ✅       IF-MIB

2 compiled

Cycle 2:
  Status   Module
  ✅       IANAifType-MIB
  ♻        IF-MIB

1 compiled  1 cached
Watch stopped: 2 cycle(s) run, 1 module(s) recompiled.
```

Notes:

- Dependents tracking relies on the emitted JSON module files (`imports`
  section); with a non-json `--format` the invalidation set falls back to a
  source-text `FROM` scan, so dependent tracking is best-effort.
- Misnamed source files (file stem ≠ declared module name) are tracked under
  their declared name; if no file matches, that module is not polled.
- A same-size rewrite landing on the same coarse filesystem timestamp tick as
  the last poll may be missed (mtime polling limitation).

---

## `tsmi lint` / `trishul-smi lint`

```
tsmi lint [MIB ...] [OPTIONS]
```

Validate one or more MIBs and all their transitive dependencies against the
v1 lint check set (plus the two v0.5.1 additions below). Runs the same
resolve pipeline as `compile` (fetch → parse → cache → OID resolution) and
reports findings; **no output files are written**. When no `MIB` names are
given, the whole `--mib-dir` discovery set is linted — the same discovery
semantics as `compile` (file stems, deduplicated, first `--mib-dir` wins).

**Check set (v1 + v0.5.1)**

| Check | Severity | Meaning |
|---|---|---|
| `missing-import` | error / warning | A referenced symbol is neither imported nor defined in-module and is not a base type or well-known OID root. Error when the reference is in a TYPE position (SYNTAX / base type); warning when it is in a MEMBER/OID position (notification OBJECTS members, INDEX, AUGMENTS, OID parent, TRAP-TYPE ENTERPRISE) |
| `undefined-type` | error | A SYNTAX/base-type reference has no TEXTUAL-CONVENTION or base type anywhere in the closure |
| `unresolvable-oid` | error | An object's OID parent chain dead-ends |
| `unused-import` | warning | An IMPORTS symbol is never referenced by the module |
| `duplicate-oid-arc` | warning | Two or more objects resolve to the same absolute OID |
| `missing-status` | warning | A construct whose SMI macro mandates a STATUS clause lacks one (SMIv2 macros with STATUS: OBJECT-TYPE, OBJECT-IDENTITY, NOTIFICATION-TYPE, OBJECT-GROUP, NOTIFICATION-GROUP, MODULE-COMPLIANCE, AGENT-CAPABILITIES; TEXTUAL-CONVENTION) |
| `missing-description` | warning | A construct whose SMI macro carries a DESCRIPTION clause lacks one (the STATUS-bearing macros above plus TRAP-TYPE, and the module-level description of a SMIv2 module) |

**Arguments**

| Argument | Description |
|---|---|
| `MIB ...` | One or more MIB names to lint (e.g. `IF-MIB IP-MIB`). Omit to lint every MIB discovered in `--mib-dir` directories. |

**Options**

| Option | Default | Description |
|---|---|---|
| `-f` / `--format` | `text` | Output format: `text` (human-readable) or `json` (stable machine-readable document, for CI) |
| `--fail-level` | `all` | Exit-1 threshold: `all` (any finding or unresolved module) or `error` (only error-severity findings and unresolved modules; warnings report but exit 0) |
| `-d` / `--mib-dir` | — | Local MIB directory. Repeat for multiple. Searched before HTTP. |
| `--online` | off | Fetch missing MIBs from HTTP sources (pysnmp.com + mibbrowser.online). Off by default. |
| `-s` / `--source` | — | Custom HTTP URL template (`@mib@` replaced with MIB name). Implies `--online`. Repeat for multiple. |
| `--cache-dir` | `~/.cache/trishul-smi` | Compiled-module cache directory. Pass `""` to disable. |
| `--cache-ttl-days` | `7` | Cache TTL in days. `0` = never expire. |
| `--max-mib-size` | `10485760` | Maximum MIB source size in bytes. |
| `--timeout` | `30.0` | HTTP timeout in seconds. |
| `--retries` | `3` | HTTP retry count on transient failure. |
| `--help` | — | Show help and exit. |

**Exit codes:** `0` no findings and no unresolved modules — `1` one or more
findings or unresolved modules (with `--fail-level error`: one or more
error-severity findings or unresolved modules) — `2` bad option, no source
configured, or invalid MIB name.

Modules that cannot be fetched or parsed are reported as *unresolved* in the
output (they cannot be inspected) and count toward exit code `1` under both
`--fail-level` values.

**Examples**

```bash
# Lint a MIB from a local directory (no HTTP)
tsmi lint IF-MIB -d /usr/share/snmp/mibs

# Lint every MIB discovered in --mib-dir (no names needed)
tsmi lint -d /usr/share/snmp/mibs

# Gate CI on errors only; warnings report but do not fail the run
tsmi lint -d /usr/share/snmp/mibs --fail-level error

# Lint several MIBs, fetching missing dependencies from the internet
tsmi lint IF-MIB IP-MIB --online

# JSON output for CI
tsmi lint IF-MIB -d /usr/share/snmp/mibs --format json

# Disable the disk cache
tsmi lint IF-MIB --online --cache-dir ""
```

**Sample text output**

```
Errors:
  [A-MIB] MysteryType (missing-import): 'MysteryType' is referenced as a SYNTAX/base type but is neither imported nor defined in module 'A-MIB'
Warnings:
  [D-MIB] Integer32 (unused-import): imported symbol 'Integer32' from 'SNMPv2-SMI' is never referenced by module 'D-MIB'
Summary: 2 modules checked, 1 error, 1 warning
```

**JSON output (`--format json`)**

The document shape is stable and is the CI contract:

```json
{
  "findings": [
    {
      "check": "missing-import",
      "severity": "error",
      "module": "A-MIB",
      "symbol": "MysteryType",
      "message": "'MysteryType' is referenced as a SYNTAX/base type but is neither imported nor defined in module 'A-MIB'"
    }
  ],
  "summary": {"modules_checked": 2, "errors": 1, "warnings": 0},
  "resolve_errors": {"NO-SUCH-MIB": "MIB 'NO-SUCH-MIB' not found"}
}
```

`severity` is `error` or `warning`; `check` is one of the stable check ids
above; `symbol` is `null` when a finding has no symbol; `resolve_errors`
maps each module that could not be fetched or parsed to its error message.

---

## `tsmi convert` / `trishul-smi convert`

```
tsmi convert FILE.py [OPTIONS]
```

Reverse-convert a compiled PySNMP `.py` MIB module back to JSON using Python's `ast` module — no SMI grammar required.

**Arguments**

| Argument | Description |
|---|---|
| `FILE.py` | Path to a compiled PySNMP `.py` MIB file |

**Options**

| Option | Default | Description |
|---|---|---|
| `-o` / `--output-dir` | `./mibs-output` | Directory to write the JSON output file |
| `--help` | — | Show help and exit. |

**Examples**

```bash
# Convert a compiled IF-MIB.py back to JSON
tsmi convert IF-MIB.py

# Write to a custom directory
tsmi convert IF-MIB.py -o ./converted
```

**What is extracted:** OID paths, object types, syntax (resolving `_Name_Type` wrappers to their base class), `max_access`, `status`, and description text. Imports and TEXTUAL-CONVENTION class bodies are not reconstructed.

---

## `tsmi version` / `trishul-smi version`

```
tsmi version
```

Print the installed version and exit.

---

## Output Formats

### JSON (`-f json`)

One `.json` file per MIB module:

Each module JSON file is individually usable. `manifest.json` and `oid_index.json` are
optional additive sidecars. From the CLI, enable them with `--emit-manifest` and
`--emit-oid-index`; from the library API, use `CompilerConfig.emit_manifest` and
`CompilerConfig.emit_oid_index`. When emitted, they describe the final emitted JSON file
set for that compile run.

```json
{
  "module": "IF-MIB",
  "language": "SMIv2",
  "schema_version": "1.1",
  "producer_version": "<installed trishul-smi version>",
  "generated_by": "trishul-smi",
  "generated_at": "2026-05-06T12:00:00Z",
  "imports": {
    "SNMPv2-SMI": ["MODULE-IDENTITY", "OBJECT-TYPE"]
  },
  "objects": {
    "ifIndex": {
      "oid": "1.3.6.1.2.1.2.2.1.1",
      "oid_path": [1, 3, 6, 1, 2, 1, 2, 2, 1, 1],
      "object_type": "OBJECT-TYPE",
      "class": "objecttype",
      "nodetype": "column",
      "syntax": "InterfaceIndex",
      "max_access": "read-only",
      "status": "current",
      "description": "A unique value ..."
    }
  },
  "types": {
    "InterfaceIndex": {
      "class": "textualconvention",
      "base_type": "Integer32",
      "display_hint": "d",
      "status": "current",
      "description": "..."
    }
  },
  "notifications": {},
  "module_metadata": {
    "lastupdated": "2000-06-14",
    "revisions": [{"date": "2000-06-14", "description": "..."}],
    "organization": "IETF Interfaces MIB Working Group",
    "contactinfo": "...",
    "description": "..."
  }
}
```

`producer_version` reflects the installed package version that produced the artifact.
`oid_path` is the canonical runtime OID representation, and `oid` is emitted only when the
matching numeric dotted string can be derived from it.
