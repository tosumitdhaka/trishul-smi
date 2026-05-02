# MIB Compiler Comparison — pysmi 1.5 · pysmi 2.0 · trishul-smi

> **Versions:** pysmi 1.5.11, pysmi 2.0.0, trishul-smi 0.2.0  
> **Python:** 3.12.3 · **Platform:** Linux (WSL2)  
> **Test MIBs:** 12 standard MIBs — IF-MIB, IP-MIB, SNMPv2-MIB, ENTITY-MIB, HOST-RESOURCES-MIB, TCP-MIB, UDP-MIB, IANAifType-MIB, INET-ADDRESS-MIB, UUID-TC-MIB, IANA-ENTITY-MIB, SNMP-FRAMEWORK-MIB  
> **Last updated:** 2026-05-02  
> **Process:** [comparison-process.md](comparison-process.md) · **Sample output:** [output-comparison.md](output-comparison.md)

---

## Contents

1. [Test setup](#1-test-setup)
2. [Code-level comparison](#2-code-level-comparison)
3. [Functional comparison](#3-functional-comparison)
4. [Output — JSON format](#4-output--json-format)
5. [Output — pysnmp format](#5-output--pysnmp-format)
6. [Scenario testing](#6-scenario-testing)
7. [Cache mechanics](#7-cache-mechanics)
8. [pysmi 1.5 vs 2.0 — what changed](#8-pysmi-15-vs-20--what-changed)
9. [Known gaps and bugs](#9-known-gaps-and-bugs)
10. [Summary verdicts](#10-summary-verdicts)

---

## 1. Test setup

```bash
# pysmi 2.0 + tsmi
python3 -m venv ~/trishul3/test-venv
pip install pysmi trishul-smi

# pysmi 1.5 + tsmi (separate venv to avoid dep conflicts)
python3 -m venv ~/trishul3/test-venv-v15
pip install "pysmi==1.5.11" trishul-smi
```

All runs use only local MIBs (`--mib-source file://...` / `--mib-dir ~/test/mibs/`). No network access. Both pysmi versions were tested against the same trishul-smi 0.2.0 on the same machine, so all numbers are directly comparable.

---

## 2. Code-level comparison

| Dimension | pysmi 1.5.11 | pysmi 2.0.0 | trishul-smi 0.2.0 |
|---|---|---|---|
| **Source size** | 50 files, ~8 054 lines | 52 files, ~7 819 lines | 34 files, ~3 500 lines |
| **Execution model** | Synchronous, blocking I/O | Synchronous, blocking I/O | `asyncio` + thread pool for parsing |
| **Parser engine** | **PLY** (`ply.lex` + `ply.yacc`); hand-written grammar in `parser/smi.py` (1 486 lines) + `lexer/smi.py` (551 lines) | **Lark** — single declarative grammar `lark_parser.py` (1 618 lines) handling both SMIv1 and SMIv2 | **Lark** — two separate `.lark` grammars (v1/v2); `MibTransformer` 671 lines |
| **Codegen pipeline** | Two-pass: `symtable.py` → `intermediate.py` → `pysnmp.py` / `jsondoc.py` | Same two-pass pipeline as 1.5 | Single-pass: transformer → `JsonFormatter` / `PysnmpFormatter` (Jinja2) |
| **SMIv1 support** | Dedicated `smiv1.py` + `smiv1compat.py` parsers; explicit RFC1155/RFC1213 symbol map | Single Lark grammar; same `base.py` SMIv1→SMIv2 symbol map | Separate SMIv1 grammar; less explicit compat shim |
| **HTTP client** | `requests` (sync) | `requests` (sync) | `httpx` (async) + `tenacity` |
| **Caching** | Output dir mtime-check only | Output dir mtime-check + Lark grammar pickle via `--cache-directory` | `orjson`-backed intermediate cache at `~/.cache/trishul-smi/`; atomic writes |
| **Extensibility** | Borrower/searcher plugin arch; `--destination-template` | Same as 1.5 | `FormatterProtocol`; no plugin system |
| **Dependencies** | `ply` (unmaintained), `requests`, `Jinja2`, `lark` | `requests`, `Jinja2`, `lark` | `httpx`, `tenacity`, `orjson`, `rich`, `typer`, `lark`, `Jinja2` |

**Key architectural points:**

- Both pysmi versions share an identical two-pass codegen (symtable → IR → output). This is why their generated JSON and pysnmp output are nearly byte-for-byte identical.
- The PLY→Lark migration in 2.0 made the grammar more maintainable but **doubled cold startup time** — PLY's compiled tables reload faster than Lark's grammar initialisation for this MIB set.
- trishul-smi's single-pass design is smaller and cleaner but limits resolution depth. Its core structural advantage — the async intermediate cache — is what neither pysmi version can match in repeated-run scenarios.

---

## 3. Functional comparison

### CLI

| Feature | pysmi 1.5 / 2.0 (`mibdump`) | trishul-smi (`tsmi compile`) |
|---|---|---|
| Run output | Dense ~20-line settings log on every invocation | Rich formatted per-module table (`✅` / `❌`) |
| Formats | `json`, `pysnmp`, `null` | `json`, `pysnmp` (repeat `-f` for both) |
| Local MIB dir | `--mib-source file:///path/` | `--mib-dir /path/` |
| Multiple sources | Repeat `--mib-source` | Repeat `--mib-dir` or `--source` |
| HTTP fetch | Always enabled; missing stubs borrowed from mibs.pysnmp.com | Off by default; opt-in via `--online` or `--source` |
| Texts flag | `--generate-mib-texts` (off by default) | Texts always written in JSON; `--no-texts` suppresses in pysnmp output only |
| Cache clear | `--rebuild` | `--cache-dir ""` |
| Dependency follow | Default on; `--no-dependencies` to suppress | Always resolves transitive deps; no flag to disable |
| Verbose mode | Always verbose (settings dump) | `--verbose` adds per-module output paths |
| Compile all in dir | Must list names explicitly | Omit MIB names to compile everything found in `--mib-dir` |

The CLI interface is **identical between pysmi 1.5 and 2.0** — no flags changed.

### MIBs compiled (same 12-name input)

| Tool | Output files | Notes |
|---|---|---|
| pysmi 1.5 | **12** created + 3 up-to-date (SNMPv2-*) | SNMPv2-SMI/TC/CONF treated as stubs; SNMPv2-MIB compiled |
| pysmi 2.0 | **12** created + 3 up-to-date (SNMPv2-*) | Same behaviour as 1.5 |
| trishul-smi | **11** compiled | SNMPv2-MIB silently skipped — in `BASE_MIBS` frozenset |

### SNMPv2 base MIB treatment

| MIB | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| SNMPv2-SMI | Stub; no output | Stub; no output | Skipped (`BASE_MIBS`) |
| SNMPv2-TC | Stub; no output | Stub; no output | Skipped |
| SNMPv2-CONF | Stub; no output | Stub; no output | Skipped |
| **SNMPv2-MIB** | **Compiled → output produced** | **Compiled → output produced** | **Skipped** (in `BASE_MIBS`) |

pysmi's stub list excludes `SNMPv2-MIB` so it compiles it. trishul-smi's `BASE_MIBS` includes it — the file is silently dropped even when explicitly requested on the command line.

### Error handling

All three tools handled CISCO-EPM-NOTIFICATION-MIB (vendor MIB with missing vendor deps) cleanly without errors.

---

## 4. Output — JSON format

### Schema structure

| Aspect | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| Top-level layout | Flat dict — all objects, TCs, conformance at root | Flat dict — identical to 1.5 | Structured: `module`, `language`, `generated_by`, `imports`, `objects`, `types` |
| TC placement | Root dict alongside objects (`"class": "textualconvention"`) | Same as 1.5 | Separate `"types": {}` section |
| `oid_path` integer array | Absent | Absent | Present, e.g. `[1,3,6,1,2,1,2,2,1,1]` |
| Import section | Present; has extra `"class": "imports"` wart | Same wart | Clean `{"module": ["symbols"]}` dict |
| Object discriminator | `"class": "objecttype"` | Same | `"object_type": "OBJECT-TYPE"` |

### Object-level fields

```jsonc
// pysmi 1.5 and 2.0 — identical output for standard objects
{
  "class": "objecttype",
  "description": "A unique value, greater than zero, for each interface...",
  "maxaccess": "read-only",
  "name": "ifIndex",
  "nodetype": "column",
  "oid": "1.3.6.1.2.1.2.2.1.1",
  "status": "current",
  "syntax": { "class": "type", "type": "InterfaceIndex" }
}

// trishul-smi
{
  "oid": "1.3.6.1.2.1.2.2.1.1",
  "oid_path": [1, 3, 6, 1, 2, 1, 2, 2, 1, 1],
  "object_type": "OBJECT-TYPE",
  "syntax": "InterfaceIndex",
  "max_access": "read-only",
  "status": "current",
  "description": "A unique value, greater than zero...",
  "index": null,
  "augments": null
}
```

Notable field differences:

| Field | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| `nodetype` (`column`/`table`/`row`) | Yes | Yes | No |
| `oid_path` integer array | No | No | Yes |
| `index` / `augments` | No (row entries have `"indices"` list) | No | Yes (always present, null for non-rows) |
| Row index structure | `"indices": [{"implied": 0, "module": "IF-MIB", "object": "ifIndex"}]` | Same | `"index": ["ifIndex"]` — `implied` flag lost |
| Table SEQUENCE syntax | No | No | Yes — `"syntax": "SEQUENCE OF IfEntry"` |

### Text and metadata fields (when texts enabled)

| Field | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| `description` per object | Yes | Yes | Yes (always, `--no-texts` has no effect on JSON) |
| `organization` on module-identity | Yes | Yes | **No** |
| `contactinfo` | Yes | Yes | **No** |
| `lastupdated` | Yes | Yes | **No** |
| `revisions` (array with per-revision descriptions) | Yes | Yes | **No** |
| TC `displayhint` | Yes | Yes | **No** |
| TC `status` | Yes | Yes | **No** |
| Description whitespace | Original `\n` indentation preserved | Same | Normalised to single spaces |

Both pysmi versions capture the same rich metadata. trishul-smi omits five of these fields entirely even with texts on.

### Object coverage (IF-MIB, 94 objects in source)

| Tool | Count | Missing |
|---|---|---|
| pysmi 1.5 | **94** | None |
| pysmi 2.0 | **94** | None |
| trishul-smi | **89** | `linkDown`, `linkUp` (NOTIFICATION-TYPE); `InterfaceIndex`, `InterfaceIndexOrZero`, `OwnerString` (TCs in objects dict) |

**NOTIFICATION-TYPE gap (trishul-smi):** `linkDown` and `linkUp` are absent from the `objects` dict entirely. pysmi emits them with full bound varbind lists:

```jsonc
// pysmi 1.5 and 2.0 — linkDown
{
  "class": "notificationtype",
  "description": "A linkDown trap signifies...",
  "name": "linkDown",
  "objects": [
    {"module": "IF-MIB", "object": "ifIndex"},
    {"module": "IF-MIB", "object": "ifAdminStatus"},
    {"module": "IF-MIB", "object": "ifOperStatus"}
  ],
  "oid": "1.3.6.1.6.3.1.1.5.3",
  "status": "current"
}

// trishul-smi — linkDown: null (absent from objects dict)
```

**Conformance group member lists:** pysmi (both versions) emits the full member object list per `OBJECT-GROUP`. trishul-smi emits only OID, status, and description — member lists dropped.

**Accuracy difference between pysmi versions:** pysmi 1.5 has two minor issues fixed in 2.0:
- Spurious unused imports (`Counter32`, `Counter64`, `Gauge32`, `NOTIFICATION-TYPE`) in some MIBs (ENTITY-MIB, IANA-ENTITY-MIB, INET-ADDRESS-MIB, HOST-RESOURCES-MIB)
- Missing conformance group members in HOST-RESOURCES-MIB compliance entries

### File sizes

```
Texts ON (SC1 — cold, --generate-mib-texts / always-on for tsmi)
MIB                  pysmi 1.5    pysmi 2.0    trishul-smi
IF-MIB               80,645       80,644       63,080
IP-MIB               226,548      226,503      182,239
ENTITY-MIB           70,950       70,894       56,848
HOST-RESOURCES-MIB   68,464       68,697       55,522
SNMP-FRAMEWORK-MIB   26,140       26,057       16,827
TCP-MIB              37,018       36,990       29,287
UDP-MIB              25,603       25,558       19,957
IANAifType-MIB       25,334       25,251       23,297
INET-ADDRESS-MIB     20,126       20,043       14,097
IANA-ENTITY-MIB      8,007        7,924        5,940
UUID-TC-MIB          4,420        4,337        1,995

Texts OFF (SC2 — --no texts / no flag)
MIB                  pysmi 1.5    pysmi 2.0    trishul-smi (!)
IF-MIB               37,401       37,400       63,080  ← unchanged
IP-MIB               110,866      110,821      182,239 ← unchanged
ENTITY-MIB           27,482       27,426       56,848  ← unchanged
SNMP-FRAMEWORK-MIB   7,475        7,392        16,827  ← unchanged
UUID-TC-MIB          2,006        1,923        1,995   ← unchanged
```

pysmi text stripping works and is effective (50–70% size reduction). trishul-smi JSON is the **same size in both scenarios** — `--no-texts` is a no-op for JSON output (see §9).

With texts on, pysmi produces larger files due to the extra metadata fields (organization, revisions, etc.). With texts off, trishul-smi JSON is consistently larger than pysmi's — it carries `oid_path` arrays, always-present `index`/`augments` fields, and always-on descriptions.

---

## 5. Output — pysnmp format

### MibBuilder injection

```python
# pysmi 1.5 and 2.0 — standard pysnmp idiom
if 'mibBuilder' not in globals():
    import sys
    sys.stderr.write(__doc__)
    sys.exit(1)
# mibBuilder is injected at load time by the pysnmp runtime

# trishul-smi — non-standard
from pysnmp.smi.builder import MibBuilder
mibBuilder = MibBuilder()
```

trishul-smi creates its own `MibBuilder()` at module level. This breaks pysnmp's normal `mibBuilder.loadModules()` path where the runtime injects a shared builder. Both pysmi versions use the correct idiom.

### Type definition style

```python
# pysmi 1.5 and 2.0 — per-object _Type subclass
class _IfDescr_Type(DisplayString):
    """Custom type ifDescr based on DisplayString"""
    subtypeSpec = DisplayString.subtypeSpec
    subtypeSpec += ConstraintsUnion(ValueSizeConstraint(0, 255),)

_IfDescr_Type.__name__ = "DisplayString"
_IfDescr_Object = MibTableColumn
ifDescr = _IfDescr_Object(
    (1, 3, 6, 1, 2, 1, 2, 2, 1, 2),
    _IfDescr_Type()
)
ifDescr.setMaxAccess("read-only")
if mibBuilder.loadTexts:
    ifDescr.setStatus("current")

# trishul-smi — direct instantiation; inline setDescription
class _ifDescr_Type(DisplayString):
    subtypeSpec = DisplayString.subtypeSpec
    subtypeSpec += ConstraintsUnion(ValueSizeConstraint(0, 255),)

_ifDescr_Type.__name__ = "DisplayString"
ifDescr = MibTableColumn(
    (1, 3, 6, 1, 2, 1, 2, 2, 1, 2,),
    _ifDescr_Type()
).setMaxAccess('read-only')
if mibBuilder.loadTexts: ifDescr.setStatus('current')
if mibBuilder.loadTexts: ifDescr.setDescription("""...""")
```

### Notification `.setObjects()` binding

```python
# pysmi 1.5 and 2.0 — correctly wired
linkDown = NotificationType((1, 3, 6, 1, 6, 3, 1, 1, 5, 3))
linkDown.setObjects(
    *(("IF-MIB", "ifIndex"),
      ("IF-MIB", "ifAdminStatus"),
      ("IF-MIB", "ifOperStatus"))
)
if mibBuilder.loadTexts:
    linkDown.setStatus("current")

# trishul-smi — .setObjects() missing; acknowledged TODO in template
linkDown = NotificationType((1, 3, 6, 1, 6, 3, 1, 1, 5, 3,))
# TODO: add .setObjects() from OBJECTS clause
if mibBuilder.loadTexts: linkDown.setStatus('current')
if mibBuilder.loadTexts: linkDown.setDescription("""...""")
```

The `TODO` comment is in trishul-smi's own Jinja2 template — `.setObjects()` is a known unimplemented feature in pysnmp output.

### pysnmp output comparison

| Aspect | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| IF-MIB.py lines | 1,346 | 1,346 | 2,077 |
| IP-MIB.py lines | 3,804 | 3,800 | 6,169 |
| Content diff (1.5 vs 2.0) | — | Version/timestamp comment only | — |
| `mibBuilder` idiom | ✅ Standard injection | ✅ Standard injection | ❌ Own instance |
| Type subclasses | ✅ Per-object `_Type` subclass | ✅ Same | ✅ Same (lowercase naming) |
| `.setObjects()` on notifications | ✅ Present | ✅ Present | ❌ Missing (TODO) |
| `setDescription()` in pysnmp | Only with `--generate-mib-texts` | Same | Always emitted (suppress with `--no-texts`) |
| TC names in `exportSymbols` | ❌ Not exported | ❌ Not exported | ✅ Exported |
| SNMPv2-MIB.py produced | ✅ | ✅ | ❌ Skipped |

pysmi 1.5 and 2.0 produce **byte-for-byte identical pysnmp output** (excluding the version/timestamp header comment).

---

## 6. Scenario testing

Two scenarios were run for all tools. See [comparison-process.md](comparison-process.md) for exact commands.

### Scenario 1: Cold cache, texts enabled

- pysmi: fresh output dir + `--rebuild` + `--generate-mib-texts --keep-texts-layout`
- trishul-smi: `--cache-dir ""` (cache disabled); texts always on in JSON

| Tool | Time | MIBs compiled |
|---|---|---|
| **pysmi 1.5** | **0.43 s** | 12 created, 3 up-to-date (SNMPv2-*) |
| pysmi 2.0 | 0.92 s | 12 created, 3 up-to-date (SNMPv2-*) |
| trishul-smi | 1.36 s | 11 compiled (SNMPv2-MIB skipped) |

pysmi 1.5 is the fastest cold by a wide margin — **2.1× faster than 2.0** and **3.2× faster than trishul-smi**. trishul-smi's asyncio startup overhead and full parse-from-scratch path make it the slowest cold start of the three.

### Scenario 2: Warm cache, texts suppressed

- pysmi: same destination dir from SC1 (output files act as cache); no `--generate-mib-texts`
- trishul-smi: `~/.cache/trishul-smi/` populated from SC1; `--no-texts`

| Tool | Run 1 | Run 2 | Status |
|---|---|---|---|
| pysmi 1.5 | 0.38 s | 0.38 s | 0 created, 15 up-to-date |
| pysmi 2.0 | 0.84 s | 0.84 s | 0 created, 15 up-to-date |
| **trishul-smi** | **0.32 s** | **0.31 s** | 11 compiled (cache hits) |

On warm runs, trishul-smi wins — but only just against pysmi 1.5 (0.31 s vs 0.38 s). Against pysmi 2.0, it wins by 2.7×.

The critical observation: **pysmi 2.0 has no warm-cache speedup** — it hits the same ~0.84 s floor on every run regardless of whether output files exist. The Lark grammar initialisation alone costs ~0.84 s. pysmi 1.5's PLY tables reload fast enough that its warm floor (~0.38 s) is competitive with tsmi's cache-hit path (~0.31 s).

---

## 7. Cache mechanics

| Aspect | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| Cache type | Output dir mtime-check | Output dir mtime-check | Parsed `MibModule` objects at `~/.cache/trishul-smi/` |
| What is cached | Output files (JSON / .py) | Same + Lark grammar pickle | Full parse result — format + write only on hit |
| Cold run | **0.43 s** | 0.92 s | 1.36 s |
| Warm run | 0.38 s | 0.84 s | **0.31 s** |
| Cold → warm speedup (within tool) | ~12% | ~9% | **4×** |
| Warm floor cause | PLY table reload (~0.38 s) | Lark grammar init (~0.84 s) | Asyncio startup + format/write only |
| Atomic writes | No | No | Yes (temp-file rename) |
| Cache TTL | None (mtime only) | None (mtime only) | 7 days default; `--cache-ttl-days 0` = never expire |
| Cache disable | `--rebuild` | `--rebuild` | `--cache-dir ""` |

The fundamental difference: both pysmi versions must re-parse source on every run — their "cache" only skips re-writing output files. trishul-smi stores the parsed intermediate representation so the entire parse stage is skipped on hit. This is where trishul-smi's 4× internal speedup comes from.

pysmi 2.0's Lark grammar initialisation is the single biggest performance bottleneck across all tools — it adds ~0.84 s of unrecoverable overhead per invocation. pysmi 1.5's PLY tables initialise in ~0.38 s; trishul-smi's Lark singleton is initialised once in-process.

---

## 8. pysmi 1.5 vs 2.0 — what changed

| Area | pysmi 1.5 | pysmi 2.0 | Direction |
|---|---|---|---|
| **Parser engine** | PLY — hand-written production rules, explicit lexer | Lark — declarative grammar, no explicit lexer | 2.0 more maintainable |
| **Cold speed** | **0.43 s** | 0.92 s | 1.5 is **2.1× faster** |
| **Warm floor** | **0.38 s** | 0.84 s | 1.5 is **2.2× faster** |
| **JSON accuracy** | Spurious unused imports in ~5 MIBs; missing group members in HOST-RESOURCES-MIB | Both fixed | 2.0 more accurate |
| **pysnmp output** | Byte-for-byte identical (excl. version comment) | Same | No change |
| **CLI interface** | Identical flags | Identical flags | No change |
| **Dependencies** | Requires `ply` (unmaintained since ~2017) | Dropped `ply` | 2.0 healthier |
| **Source size** | ~8 054 lines | ~7 819 lines | Slightly smaller in 2.0 |

**The speed regression from 1.5 → 2.0 is real and significant.** For most use cases pysmi 2.0 is the right choice (cleaner deps, more accurate output), but if cold-start latency is critical and you're stuck without trishul-smi's cache, pysmi 1.5 is measurably better.

---

## 9. Known gaps and bugs

> **Evaluation perspective:** trishul-smi's primary purpose is to produce rich, structured JSON as a pysmi replacement. pysnmp output is a secondary compatibility feature. Severity ratings reflect this: JSON gaps are rated on their impact to the core mission; pysnmp gaps are rated as secondary concerns.

### trishul-smi — JSON output (primary)

| # | Issue | Severity | Detail |
|---|---|---|---|
| 1 | **NOTIFICATION-TYPE missing from JSON** | High | `linkDown`, `linkUp`, and all `NOTIFICATION-TYPE` objects are absent from the `objects` dict. Only the `NOTIFICATION-GROUP` is emitted, without its member list. Any consumer building trap decoders, NMS integrations, or MIB browsers from tsmi JSON will find no notification entries. |
| 2 | **`--no-texts` no-op for JSON** | High | `JsonFormatter` ignores the flag entirely — descriptions are always written regardless. Only `PysnmpFormatter` respects it. SC1 and SC2 JSON files are byte-for-byte identical. Breaks the ability to produce lean JSON for size-constrained consumers. |
| 3 | **Module-identity metadata absent from JSON** | Medium | `organization`, `contactinfo`, `lastupdated`, `revisions`, TC `displayhint`, and TC `status` are not emitted even with texts on. pysmi captures all of these. Impacts documentation generators and MIB browsers that display module provenance. |
| 4 | **Conformance group members dropped in JSON** | Medium | `OBJECT-GROUP` and `NOTIFICATION-GROUP` entries carry only OID/status/description — the member object list is not emitted. Impacts compliance tooling and schema validators that walk conformance trees. |
| 5 | **SNMPv2-MIB silently skipped** | Medium | In `BASE_MIBS`; never compiled even when explicitly requested. Both pysmi versions produce it. JSON consumers that expect a complete set of output files per requested MIB name will silently get one fewer file. |
| 6 | **Index `implied` flag lost** | Low | Row index is `"index": ["ifIndex"]`; pysmi's `"indices": [{"implied": 0, ...}]` carries the `IMPLIED` keyword relevant to SNMP GET-NEXT/WALK OID ordering. |

### trishul-smi — pysnmp output (secondary)

| # | Issue | Severity | Detail |
|---|---|---|---|
| 1 | **pysnmp `MibBuilder` non-standard** | Medium | Compiled `.py` modules create their own `MibBuilder()` instead of accepting the one injected by the pysnmp runtime. Breaks `mibBuilder.loadModules()`. Needs fixing before pysnmp output can be used in production. |
| 2 | **`.setObjects()` missing from notifications** | Medium | trishul-smi's own Jinja2 template has a `# TODO: add .setObjects()` comment. Notifications carry no varbind definitions in compiled `.py` — pysnmp traps sent from these modules will have empty variable bindings. |
| 3 | **SNMPv2-MIB.py not produced** | Low | Follows from the `BASE_MIBS` skip above; same root cause. |

### pysmi 1.5

| # | Issue | Severity | Detail |
|---|---|---|---|
| 1 | **Spurious unused imports** | Low | ENTITY-MIB, IANA-ENTITY-MIB, INET-ADDRESS-MIB, HOST-RESOURCES-MIB emit unused symbols (`Counter32`, `Counter64`, `Gauge32`, `NOTIFICATION-TYPE`) in the imports section. Fixed in 2.0. |
| 2 | **Incomplete conformance group members** | Low | HOST-RESOURCES-MIB compliance group omits `hrSWRunGroup`, `hrSWRunPerfGroup`, `hrSWInstalledGroup`. Fixed in 2.0. |
| 3 | **`ply` dependency** | Low | Requires `ply` (Python Lex-Yacc), unmaintained since ~2017. pysmi 2.0 dropped it. |
| 4 | **No warm-cache speedup** | Low | PLY grammar tables reload on every run (~0.38 s floor). Effect smaller than 2.0's Lark floor but still present. |

### pysmi 2.0

| # | Issue | Severity | Detail |
|---|---|---|---|
| 1 | **Lark grammar cold-start cost** | Medium | ~0.84 s floor on every run, warm or cold. The single largest performance bottleneck across all three tools. |
| 2 | **Dense CLI output** | Low | ~20 lines of settings printed on every run; no `--quiet` flag for just the result summary. |
| 3 | **TC names not re-exported in pysnmp** | Low | `InterfaceIndex`, `OwnerString`, etc. defined but not added to `exportSymbols` — trishul-smi exports them. |

---

## 10. Summary verdicts

> trishul-smi's design goal is to replace pysmi as a JSON-first MIB compiler. pysnmp output is a secondary compatibility feature. The scoring and verdicts below reflect this — JSON quality is weighted as the primary axis.

### Timing — all scenarios

| Scenario | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| Cold, texts on | **0.43 s** | 0.92 s | 1.36 s |
| Warm, texts off (run 1) | 0.38 s | 0.84 s | **0.32 s** |
| Warm, texts off (run 2) | 0.38 s | 0.84 s | **0.31 s** |
| Internal cache speedup | ~12% | ~9% | **4×** (1.36 → 0.31 s) |

### Scoring — JSON output (primary evaluation axis)

| Category | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| **Cold compile speed** | ✅ Fastest (0.43 s) | ❌ 0.92 s | ❌ 1.36 s |
| **Warm compile speed** | ⚠️ 0.38 s (no true cache) | ❌ 0.84 s | ✅ 0.31 s (parse cache) |
| **NOTIFICATION-TYPE in JSON** | ✅ Present with varbind list | ✅ Present | ❌ Absent entirely |
| **`--no-texts` works for JSON** | ✅ 50–70% size reduction | ✅ Works | ❌ No-op |
| **Module-identity metadata** | ✅ org, contactinfo, revisions | ✅ Same | ❌ All absent |
| **TC displayhint + status** | ✅ Present | ✅ Present | ❌ Absent |
| **Conformance group members** | ⚠️ Incomplete in 1 MIB | ✅ Complete | ❌ Dropped |
| **JSON schema design** | ❌ Flat dict, mixed classes | ❌ Flat dict, mixed classes | ✅ Structured sections; `oid_path` |
| **Import accuracy** | ⚠️ Spurious unused imports in ~5 MIBs | ✅ Clean | ✅ Clean |
| **SMIv1 support** | ✅ Dedicated parsers + compat map | ✅ Lark + compat map | ⚠️ Grammar-level only |
| **Dependency health** | ❌ Unmaintained `ply` | ✅ No `ply` | ✅ All active |
| **Code size / clarity** | ❌ 8 054 lines | ⚠️ 7 819 lines | ✅ 3 500 lines |
| **Python async API** | ❌ Synchronous | ❌ Synchronous | ✅ Native asyncio |
| **CLI UX** | ❌ Verbose log every run | ❌ Verbose log every run | ✅ Rich table |

### Scoring — pysnmp output (secondary axis)

| Category | pysmi 1.5 | pysmi 2.0 | trishul-smi |
|---|---|---|---|
| **`mibBuilder` injection idiom** | ✅ Standard | ✅ Standard | ❌ Own instance (breaks runtime) |
| **Notification `.setObjects()`** | ✅ Present | ✅ Present | ❌ Missing (TODO in template) |
| **SNMPv2-MIB.py produced** | ✅ | ✅ | ❌ Skipped |
| **TC names exported** | ❌ Not re-exported | ❌ Not re-exported | ✅ Exported |
| **Type subclassing** | ✅ Per-object `_Type` subclass | ✅ Same | ✅ Same pattern |

### Where trishul-smi stands today

trishul-smi already wins on **schema design** — structured `objects`/`types` sections, `oid_path` arrays, always-present descriptions, clean imports, and a sane async API are all ahead of pysmi. It also wins on warm-run performance (4× internal cache speedup) and CLI UX.

The remaining gap against pysmi's JSON is concentrated in four areas that matter for the replacement goal:

1. **NOTIFICATION-TYPE objects missing** — the most impactful gap; any MIB browser or NMS tool built on tsmi JSON cannot see trap definitions
2. **`--no-texts` not working for JSON** — prevents producing lean output for size-constrained pipelines
3. **Module-identity metadata absent** — organization, revisions, contactinfo are standard fields MIB browsers and docs generators expect
4. **Conformance group members dropped** — compliance trees are incomplete

Once these four are addressed, tsmi's JSON output will be a strict superset of pysmi's — richer schema, better structured, and faster on repeated runs.

### When to use each

**trishul-smi** — the right choice for new JSON-centric tooling: MIB browsers, NMS integrations, documentation generators, schema validators, async Python pipelines. Fix the four JSON gaps above before claiming full pysmi replacement. The pysnmp output is usable for basic testing but not production pysnmp deployments until the `MibBuilder` idiom and `.setObjects()` are fixed.

**pysmi 2.0** — current best choice if you need complete JSON today (all object types, full module metadata, group members) or need pysnmp-compatible output. Healthiest deps. The cold-start cost (0.92 s) and lack of warm-cache speedup are its main weaknesses.

**pysmi 1.5** — only if cold-start latency is critical and pysmi 2.0 is too slow. Has minor JSON accuracy regressions and depends on unmaintained `ply`. No reason to choose it for new projects.
