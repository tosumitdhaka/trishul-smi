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
| `--reproducible` | off | Pin `generated_at` to a fixed epoch so repeated compiles of the same source produce byte-identical output files. |
| `--list-formats` | — | Print built-in and discovered plugin output formats and exit 0. No MIB names or `--mib-dir` required. |
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
  surfaces as a `failed` result row without stopping the watcher.
- **Dependency recovery:** a module that failed on an unresolved dependency
  keeps its missing-dependency edges. When a newly adopted or restored file
  supplies that dependency, the failed module **and its transitive
  dependents** are recompiled in the next debounced cycle — without editing
  the dependents.
- **Deletion:** removing a watched source file invalidates the removed module
  and its dependents. The compile pipeline retries every remaining reader; if
  none can supply the module, the affected output is marked **stale** and its
  path is printed — the offline compiled-module cache fallback is never
  treated as a successful watch compile for a confirmed local deletion.
  Existing output files are **never deleted**. Recreating the file re-adopts
  it and clears the stale state.
- Misnamed source files (file stem ≠ declared module name) are watched at
  their **actual path**: the watcher uses the path the compile pipeline
  reports for each module (never a name-based reconstruction), so editing a
  misnamed file recompiles it and its dependents like any other.
- **Exit contract:** the exit code reflects the state at stop, not history.
  A normal stop (Ctrl-C, or any other clean stop) exits `0` when every
  watched module's latest result is compiled or cached, and exits `1` when
  any module is still failed, missing, or stale at stop. A successful
  recovery clears the module's state, so a module that failed mid-session and
  recovered does not keep the run at exit 1.
- **Ctrl-C** stops the session cleanly: a short summary (cycles run, modules
  recompiled) is printed and the process exits according to the contract
  above (exit `1` if any module is still failed, missing, or stale).

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
reports findings; **no output files are written** unless `--fix` is given.
When no `MIB` names are given, the whole `--mib-dir` discovery set is linted
— the same discovery semantics as `compile` (file stems, deduplicated,
first `--mib-dir` wins).

With `--fix`, mechanical fixes are applied to the **local `--mib-dir`
source files** for the two fixable check kinds (see below); all other
findings are report-only. `--fix --diff` is a dry-run: it prints the
planned unified diffs and writes nothing.

**Check set (v1 + v0.5.1)**

| Check | Severity | Meaning |
|---|---|---|
| `missing-import` | error / warning | A referenced symbol is neither imported nor defined in-module and is not a base type or well-known OID root. Error when the reference is in a TYPE position (SYNTAX / base type); warning when it is in a MEMBER/OID position (notification OBJECTS members, INDEX, AUGMENTS, OID parent, TRAP-TYPE ENTERPRISE) |
| `undefined-type` | error | A SYNTAX/base-type reference has no TEXTUAL-CONVENTION or base type anywhere in the closure |
| `unresolvable-oid` | error | An object's OID parent chain dead-ends |
| `unused-import` | warning | An IMPORTS symbol is never referenced by the module |
| `duplicate-oid-arc` | warning | Two or more objects resolve to the same absolute OID |
| `missing-status` | warning | A construct whose SMI macro mandates a STATUS clause lacks one (SMIv2 macros with STATUS: OBJECT-TYPE, OBJECT-IDENTITY, NOTIFICATION-TYPE, OBJECT-GROUP, NOTIFICATION-GROUP, MODULE-COMPLIANCE, AGENT-CAPABILITIES; TEXTUAL-CONVENTION) |
| `missing-description` | warning | A construct whose SMI macro carries a DESCRIPTION clause lacks one (the STATUS-bearing macros above plus TRAP-TYPE, and the module-level description of an SMIv2 module that declares actual object instances — OBJECT-TYPE assignments or notifications). Modules without object instances — TC-only modules (`SNMPv2-TC`, `IPV6-TC`) and OID-registry/root modules (`SNMPv2-SMI`, `JUNIPER-EXPERIMENT-MIB`) — conventionally carry no MODULE-IDENTITY and are not flagged at the module level |

**Fix mode (`--fix`)**

The v1 fixable set is exactly two check kinds, and only these ever modify a
file:

- **`missing-import` (TYPE-role only)** — the missing `symbol FROM provider`
  import is added when the symbol resolves to exactly one provider module in
  the loaded closure. Ambiguous (two or more providers) or unresolvable (no
  provider) symbols are reported, not fixed. Member/OID-role
  `missing-import` findings are NOT fixable (they are legal unimported OID
  references, not defects).
- **`unused-import`** — the unused symbol is removed from the IMPORTS
  clause; the clause is removed entirely when it empties.

Safety rules:

- Only the two fixable check kinds above ever modify a file; every other
  finding is report-only.
- Idempotency: a second `--fix` run is a no-op.
- Line endings and trailing whitespace of untouched lines are preserved
  byte-for-byte (the fixer edits only the IMPORTS block).
- A fix that would leave the file unparseable is detected (re-parse after
  edit) and rolled back with an error.
- No fix is attempted on modules fetched over HTTP or from ZIP sources —
  local `--mib-dir` files only; out-of-scope sources are report-only.
- **Source-identity guard:** a local file is edited only when its decoded
  content fingerprint matches the fingerprint of the source that produced
  the resolved module. A same-named local file whose content differs from
  the resolved source (e.g. a caller-supplied reader serving a different
  file) is left untouched, as is any module served from the offline
  compiled-module cache fallback (no live source was fetched).
- **Trailing-comment retention:** deleting a sole-line import clause that
  carries a trailing comment (`    Integer32 FROM OTHER-REF  -- note`)
  keeps the comment as a standalone comment line — the comment documents
  the removed import and survives the fix.
- A per-module fix failure (e.g. an overlapping-edit invariant) degrades to
  `left` entries for that module's findings; the other modules are still
  fixed.
- Files are rewritten atomically (temp-file rename, same convention as the
  compiled-module cache).

`--diff` requires `--fix` (it is the dry-run form) and exits 2 without it.

**Arguments**

| Argument | Description |
|---|---|
| `MIB ...` | One or more MIB names to lint (e.g. `IF-MIB IP-MIB`). Omit to lint every MIB discovered in `--mib-dir` directories. |

**Options**

| Option | Default | Description |
|---|---|---|
| `-f` / `--format` | `text` | Output format: `text` (human-readable) or `json` (stable machine-readable document, for CI) |
| `--fail-level` | `all` | Exit-1 threshold: `all` (any finding or unresolved module) or `error` (only error-severity findings and unresolved modules; warnings report but exit 0) |
| `--fix` | off | Apply mechanical fixes for the two fixable check kinds (above) to local `--mib-dir` source files |
| `--diff` | off | Dry-run: print unified diffs of what `--fix` would change and write nothing. Requires `--fix` |
| `-d` / `--mib-dir` | — | Local MIB directory. Repeat for multiple. Searched before HTTP. |
| `--online` | off | Fetch missing MIBs from HTTP sources (pysnmp.com + mibbrowser.online). Off by default. |
| `-s` / `--source` | — | Custom HTTP URL template (`@mib@` replaced with MIB name). Implies `--online`. Repeat for multiple. |
| `--cache-dir` | `~/.cache/trishul-smi` | Compiled-module cache directory. Pass `""` to disable. |
| `--cache-ttl-days` | `7` | Cache TTL in days. `0` = never expire. |
| `--max-mib-size` | `10485760` | Maximum MIB source size in bytes. |
| `--timeout` | `30.0` | HTTP timeout in seconds. |
| `--retries` | `3` | HTTP retry count on transient failure. |
| `--help` | — | Show help and exit. |

**Exit codes:** `0` no findings and no unresolved modules (with `--fail-level
error`: no error-severity findings or unresolved modules; with `--fix`:
nothing to fix or all fixable findings fixed with none remaining) — `1` one
or more findings or unresolved modules remain (with `--fail-level error`:
error-severity findings or unresolved modules; with `--fix`: unfixed
findings remain) — `2` bad option, no source configured, invalid MIB name,
or `--diff` without `--fix`.

Modules that cannot be fetched or parsed are reported as *unresolved* in the
output (they cannot be inspected) and count toward exit code `1` under both
`--fail-level` values.

**`--fix --diff` exit codes.** In dry-run mode the fixer still moves
fixable findings out of `findings` and reports them as `fixed`, so the exit
code reflects the *hypothetical* outcome: `0` means everything fixable was
hypothetically fixed (or there was nothing to fix), `1` means unfixable or
unfixable-in-principle findings remain. The working tree is never modified.
CI gating must therefore use plain `tsmi lint` (or `--fail-level error`) as
the authoritative check — not `--fix --diff`, whose exit `0` does not mean
"the tree is clean", only "the fix would have cleaned the fixable part".

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

# Fix fixable findings in local source files (unused imports; uniquely
# resolvable type-role missing imports)
tsmi lint -d /usr/share/snmp/mibs --fix

# Dry-run: show the unified diffs the fix would make, write nothing
tsmi lint -d /usr/share/snmp/mibs --fix --diff
```

**Sample text output**

```
Errors:
  [A-MIB] MysteryType (missing-import): 'MysteryType' is referenced as a SYNTAX/base type but is neither imported nor defined in module 'A-MIB'
Warnings:
  [D-MIB] Integer32 (unused-import): imported symbol 'Integer32' from 'SNMPv2-SMI' is never referenced by module 'D-MIB'
Summary: 2 modules checked, 1 error, 1 warning
```

With `--fix`, the report grows a `Fixed:` section (what was fixed) and a
`Left (not fixed):` section (fixable findings that were left, with a reason):

```
Fixed:
  [JUNIPER-MIB] DisplayString (missing-import): added import 'DisplayString' FROM 'SNMPv2-TC' to IMPORTS
Left (not fixed):
  [A-MIB] MysteryType (missing-import): symbol 'MysteryType' does not resolve to exactly one provider module in the loaded closure
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
  "resolve_errors": {"NO-SUCH-MIB": "MIB 'NO-SUCH-MIB' not found"},
  "fixed": [
    {
      "check": "unused-import",
      "severity": "warning",
      "module": "D-MIB",
      "symbol": "Integer32",
      "file": "/mibs/D-MIB",
      "status": "fixed",
      "message": "removed unused import 'Integer32' from IMPORTS"
    }
  ],
  "diffs": {"/mibs/JUNIPER-MIB": "--- /mibs/JUNIPER-MIB\n+++ /mibs/JUNIPER-MIB\n@@"}
}
```

`severity` is `error` or `warning`; `check` is one of the stable check ids
above; `symbol` is `null` when a finding has no symbol; `resolve_errors`
maps each module that could not be fetched or parsed to its error message.
`fixed` lists the `--fix` outcomes (one entry per fixable finding: `status`
`fixed` or `left`, `file` is the local source path when one exists,
`message` describes the fix or the reason it was left; empty on plain lint
runs). `diffs` maps each local file the fixer would change to its unified
diff — populated only in `--fix --diff` dry-run mode.

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
