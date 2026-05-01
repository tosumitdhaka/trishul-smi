# trishul-smi — Design Notes

> **Status:** v0.2.0 — implementation complete; v0.2.0 shipped 2026-05-01  
> **Author:** GhaatakJi  
> **Last updated:** 2026-05-01

---

## 1. Aim

Build a **clean, modern, pure-Python SMI/MIB compiler** that:

- Parses ASN.1 SMI MIB files (SMIv1 and SMIv2)
- Converts them to structured **JSON** (primary output)
- Optionally outputs **PySNMP-compatible `.py` modules** (secondary output, for compat)
- Converts existing **PySNMP `.py` MIB modules** to JSON (`tsmi convert FILE.py` — shipped in v0.2.0)
- Resolves MIB dependencies **automatically** (parallel async fetching)
- Downloads missing MIBs from the web **on demand**
- Exposes a simple **CLI** and a **Python API**

---

## 2. Motivation

The existing reference implementation — [pysmi](https://github.com/lextudio/pysmi) — works but carries significant technical debt:

| Problem in pysmi | Impact |
|---|---|
| `dataObj` used before assignment in nested ZIPs | Runtime `NameError` crash |
| `UnboundLocalError` on empty `refs` | Silent failure on edge cases |
| Misplaced `raise` after successful file read | Incorrect control flow |
| No HTTP timeout implemented | Process hangs indefinitely |
| `requests.Session` never closed | Resource leak in long-running apps |
| File handles without `with` statements | Leak on exception |
| Circular imports in `error.py` | `ImportError` on some environments |
| `**options` kwargs with no type safety | Opaque, fragile API |
| Mixed concerns across modules | Hard to test in isolation |
| Built on PLY (aging lex/yacc port) | Verbose, manual AST construction |

Rather than patching pysmi incrementally, `trishul-smi` is a **ground-up rewrite** with correctness, testability, and clean design as first-class goals.

---

## 3. Goals

### Must Have (v1.0)
- [ ] Parse SMIv2 MIB files (RFC 2578, 2579, 2580)
- [ ] Parse SMIv1 MIB files (RFC 1155, 1212, 1215)
- [ ] Handle common vendor dialect quirks (Cisco, HP, NET-SNMP)
- [ ] Output clean, structured **JSON** per MIB module (primary)
- [ ] Output **PySNMP-compatible `.py` modules** (secondary, `--format pysnmp`)
- [ ] Support **both outputs simultaneously** (`--format json pysnmp`)
- [x] Convert existing PySNMP `.py` MIB modules → JSON (`tsmi convert` — shipped v0.2.0)
- [ ] Automatic dependency resolution with **parallel async fetching**
- [ ] Fetch missing MIBs from HTTP sources with retry + timeout
- [ ] Read MIBs from local filesystem and ZIP archives
- [ ] CLI: `trishul-smi compile <MIB-NAME>`
- [ ] Python API: `MibCompiler` class
- [ ] Full type annotations (mypy strict)
- [ ] Test coverage ≥ 95%
- [ ] Published to PyPI as `0.1.0` (unstable marker)

### Nice to Have (v1.x)
- [ ] OID index file generation
- [ ] MIB validation / lint mode
- [ ] Plugin system for custom code generators
- [ ] Watch mode (recompile on file change)
- [ ] MIB borrowing (pre-compiled fallback from remote) — *see DD-5*

### Non-Goals
- Not a full SNMP agent or manager
- Not a replacement for PySNMP runtime
- Not a GUI tool
- No support for Python < 3.10

---

## 4. Why Build This?

### Why not just fix pysmi?

pysmi’s core issues are **architectural**, not just bugs. The parser is built on PLY (an aging lex/yacc port), the pipeline has tight coupling between reader/parser/codegen, and the public API uses `**kwargs` throughout — making it hard to add type safety without a near-total rewrite. Fixing it means owning their architecture.

### Why JSON as primary output?

JSON is universally consumable. Every language, framework, and tool can read JSON. A MIB compiled to JSON can be used in:
- Network management dashboards
- REST APIs
- Monitoring tools (Prometheus exporters, Grafana)
- AI/ML pipelines processing network telemetry
- Any language without a native MIB parser

PySNMP `.py` output is a **walled garden** — only useful inside the PySNMP ecosystem. JSON breaks MIB data free for any consumer.

### Why also support PySNMP `.py` output?

- Existing PySNMP-based tooling still needs `.py` format
- The `AbstractCodeGen` architecture makes adding a second codegen **zero-cost to the pipeline**
- Makes `trishul-smi` a **complete drop-in replacement** for pysmi, not just a partial tool
- Users can generate both formats in a single run

### Why async + httpx?

Fetching MIBs from the web is I/O bound. Async allows **parallel fetching** of independent MIB dependencies without blocking on each HTTP request. `httpx` is the modern replacement for `requests` — async-native, timeout-safe, and easier to mock in tests.

---

## 5. Design Decisions

### DD-1: Lark over PLY for parsing

| Concern | PLY | Lark |
|---|---|---|
| Grammar style | Verbose BNF, C-like | Clean EBNF |
| AST construction | Manual `p_rule()` for every rule | Auto-built from grammar structure |
| Ambiguity handling | ❌ LALR(1) only | ✅ Earley algorithm for ambiguous grammars |
| Python 3 support | Legacy, Python 2 roots | Native Python 3, typed since v1.0 |
| Maintenance | Largely inactive | Actively maintained |
| Relevance to SMI | Vendor dialect quirks cause ambiguity | Earley handles this gracefully |

**Decision:** Use `lark-parser` with LALR(1) for standard SMIv2; fall back to Earley for ambiguous vendor dialects.

---

### DD-2: JSON as primary, PySNMP `.py` as secondary output

**Decision:** JSON is the default and primary output format. PySNMP `.py` is supported as an optional secondary format via `--format pysnmp`. Both can be generated in a single run.

```bash
trishul-smi compile IF-MIB                        # JSON only (default)
trishul-smi compile IF-MIB --format pysnmp        # PySNMP .py only
trishul-smi compile IF-MIB --format json pysnmp   # both simultaneously
```

---

### DD-3: PySNMP `.py` → JSON as a separate utility command

**Decision:** Implemented as `tsmi convert FILE.py` in v0.2.0. Uses Python's `ast` module — no SMI grammar required. Extracts OIDs, object types, syntax (resolving `_Name_Type` wrapper classes to their base type), `max_access`, `status`, and description from `setMaxAccess`/`setStatus`/`setDescription` calls. Emits JSON via `JsonFormatter` — same schema as the compile path.

---

### DD-4: Jinja2 for PySNMP `.py` output from v1.0

**Decision:** Use Jinja2 for `PysnmpFormatter` from day one. Avoids two code paths (manual string building vs. template). Templates live in `output/templates/pysnmp_module.j2`.

---

### DD-5: No MIB borrowing in v1.0

**Decision:** Deferred to v1.x. In v1.0 a failed fetch/parse raises `MibNotFoundError` or `ParseError`. `CompileResult.status` is `Literal["compiled", "cached", "failed"]`.

---

### DD-6: Parallel async fetching of independent dependencies

**Decision:** Yes, from v1.0. Resolver uses `asyncio.gather()` per BFS level. Architecturally cheap; deferring would make the resolver hard to parallelize later.

---

### DD-7: orjson for disk cache serialization (not pickle)

**Decision:** Disk cache uses `orjson` JSON serialization. Pickle banned — silently breaks on model changes, security risk.

---

### DD-8: PysnmpFormatter covers scalars, tables, notifications (minimum subset)

**Decision (v0.1.0):** `PysnmpFormatter` targets the **common subset** sufficient for 95% of MIBs:
- Scalar `OBJECT-TYPE` definitions
- Table + row `OBJECT-TYPE` (with INDEX / AUGMENTS)
- `NOTIFICATION-TYPE` definitions
- `TEXTUAL-CONVENTION` types
- `MODULE-IDENTITY` metadata

**Extended in v0.2.0:**
- `MibTableColumn` correctly classified (two-pass OID tree walk after full OID resolution)
- Full TC class generation with `subtypeSpec`, `displayHint`, `status`, `description`
- Per-OBJECT-TYPE `_Name_Type` wrapper classes for inline constraints
- `setIndexNames` / `AUGMENTS → getIndexNames()`
- `setOrganization`, `setRevisions`, `setDescription` on `MODULE-IDENTITY`
- `setDescription` on `NOTIFICATION-TYPE`, `OBJECT-GROUP`, `MODULE-COMPLIANCE`, `AGENT-CAPABILITIES`
- `exportSymbols` single-dict format
- `--no-texts` flag to suppress all text fields

---

### DD-9: Publish to PyPI as `0.1.0` from day one

**Decision:** Publish early with `Development Status :: 3 - Alpha` classifier. Costs nothing; enables `pip install trishul-smi` from day one and creates accountability. Version `0.1.0` published when end-to-end compile of `IF-MIB` works.

---

## 6. How — High-Level Approach

### 6.1 Pipeline

```
[Source: file / zip / http]
        ↓  Reader
[Raw ASN.1 text]
        ↓  Parser  (lark EBNF grammar, run via asyncio.to_thread)
[AST]
        ↓  Transformer
[MibModule dataclass]
        ↓  Dependency Resolver  (Kahn’s algorithm + asyncio.gather for parallel fetch)
[Ordered MibModule list]
        ↓  CodeGen  (json_codegen / pysnmp_codegen — one or both)
[dict / .py string]
        ↓  Writer  (file / stdout / callback)
[Output artifacts]
```

Each stage is **independently testable** with clean interfaces.

### 6.2 Key Technology Choices

| Concern | Choice | Reason |
|---|---|---|
| ASN.1 parsing | `lark-parser` | Clean EBNF, auto AST, Earley for ambiguity |
| HTTP client | `httpx` | Async, timeout-safe, easy to mock |
| Retry logic | `tenacity` | Exponential backoff, clean decorator API |
| JSON output + cache | `orjson` | Fast, compact; also used for disk cache (replaces pickle) |
| PySNMP codegen | `jinja2` | Template-based, testable, single code path from v1.0 |
| CLI | `typer` | Type-annotated, auto `--help`, built on Click |
| Terminal output | `rich` | Pretty tables, progress bars |
| Linting | `ruff` | Replaces flake8 + black + isort in one tool |
| Type checking | `mypy` (strict) | Catches bugs at dev time |
| Testing | `pytest` + `pytest-httpx` | Async support, HTTP mocking |
| Packaging | `hatchling` + `pyproject.toml` | Modern Python packaging standard |

### 6.3 Core Modules (Planned)

```
trishul_smi/
├── compiler.py           ← orchestrator (MibCompiler class)
├── config.py             ← CompilerConfig dataclass + VALID_FORMATS
├── errors.py             ← exception hierarchy (no circular imports)
├── models/               ← MibModule, MibObject, MibType, CompileResult
├── parser/
│   ├── grammar/
│   │   ├── common.lark   ← shared terminals (string literals, OIDs, comments)
│   │   ├── smiv2.lark    ← complete SMIv2 grammar (RFC 2578)
│   │   └── smiv1.lark    ← complete SMIv1 grammar (RFC 1155)
│   ├── transformer.py    ← Lark tree → MibModule
│   ├── smi_parser.py     ← public parse(text) → MibModule
│   └── _constants.py     ← BASE_MIBS skip list
├── reader/
│   ├── localfile.py      ← filesystem reader (enforces max_mib_size)
│   ├── httpclient.py     ← async HTTP reader (enforces max_mib_size, ETag + TTL)
│   ├── zipreader.py      ← ZIP archive reader
│   └── chain.py          ← ReaderChain
├── resolver/
│   ├── resolver.py       ← MibResolver (Kahn’s + asyncio.gather)
│   ├── dependency.py     ← topological sort
│   └── cache.py          ← MibCache (orjson disk cache, atomic writes)
├── output/
│   ├── json_fmt.py       ← MibModule → JSON          [PRIMARY]
│   └── pysnmp_fmt.py     ← MibModule → PySNMP .py   [SECONDARY, Jinja2 inline template]
├── convert/
│   └── pysnmp_reader.py  ← compiled .py → MibModule  [ast-based, no grammar]
└── cli/
    └── main.py           ← typer app (compile + convert + version commands)
```

### 6.4 Build Order

1. `models/` — pure data structures, no deps
2. `config.py` — `CompilerConfig` dataclass (needed by reader, compiler, cli)
3. `errors.py` — exception hierarchy
4. `reader/` — fetch raw MIB text
5. `parser/grammar/smiv2.lark` — hardest piece, SMIv2 first
6. `parser/transformer.py` + `smi_parser.py`
7. `resolver/` — Kahn’s algorithm + parallel fetch
8. `output/json_fmt.py` — primary output
9. `output/pysnmp_fmt.py` + `templates/pysnmp_module.j2`
10. `compiler.py` — wire everything
11. `cli/` — last, always backed by real logic

---

## 7. Output Formats

### 7.1 JSON Schema (Target)

```json
{
  "module": "IF-MIB",
  "language": "SMIv2",
  "generated_by": "trishul-smi",
  "imports": {
    "SNMPv2-SMI": ["MODULE-IDENTITY", "OBJECT-TYPE", "Integer32"],
    "SNMPv2-TC": ["DisplayString", "PhysAddress", "TruthValue"]
  },
  "objects": {
    "ifDescr": {
      "oid": "1.3.6.1.2.1.2.2.1.2",
      "oid_path": [1, 3, 6, 1, 2, 1, 2, 2, 1, 2],
      "object_type": "OBJECT-TYPE",
      "syntax": "DisplayString",
      "max_access": "read-only",
      "status": "current",
      "description": "A textual string containing information about the interface.",
      "index": null,
      "augments": null
    }
  },
  "types": {},
  "notifications": {}
}
```

### 7.2 PySNMP `.py` Output

Generated from an inline Jinja2 template in `output/pysnmp_fmt.py`. Covers the full set defined in DD-8 (as extended in v0.2.0): scalars, tables, table columns (with INDEX/AUGMENTS), notifications, textual-conventions with full subtypeSpec, module-identity with organization/revisions/description.

---

## 8. Success Criteria

The project is considered v1.0 ready when:

- `trishul-smi compile IF-MIB` works end-to-end from a clean environment
- `trishul-smi compile IF-MIB --format json pysnmp` produces both output formats correctly
- All standard RFC MIBs (SNMPv2-SMI, SNMPv2-TC, IF-MIB, IP-MIB, etc.) compile without error
- Test suite passes with ≥ 95% coverage
- `mypy --strict` passes with zero errors
- `ruff check` passes with zero warnings
- Package published to PyPI as `trishul-smi 0.1.0`
