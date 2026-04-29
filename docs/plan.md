# trishul-smi — Project Plan

> **Status:** Draft v0.1 — under review  
> **Author:** GhaatakJi  
> **Last updated:** 2026-04-29

---

## 1. Aim

Build a **clean, modern, pure-Python SMI/MIB compiler** that:

- Parses ASN.1 SMI MIB files (SMIv1 and SMIv2)
- Converts them to structured **JSON**
- Converts existing **PySNMP `.py` MIB modules** to JSON
- Resolves MIB dependencies **automatically**
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

Rather than patching pysmi incrementally, `trishul-smi` is a **ground-up rewrite** with correctness, testability, and clean design as first-class goals.

---

## 3. Goals

### Must Have (v1.0)
- [ ] Parse SMIv2 MIB files (RFC 2578, 2579, 2580)
- [ ] Parse SMIv1 MIB files (RFC 1155, 1212, 1215)
- [ ] Handle common vendor dialect quirks (Cisco, HP, NET-SNMP)
- [ ] Output clean, structured JSON per MIB module
- [ ] Convert PySNMP `.py` MIB modules → JSON
- [ ] Automatic dependency resolution (BFS, cycle detection)
- [ ] Fetch missing MIBs from HTTP sources with retry + timeout
- [ ] Read MIBs from local filesystem and ZIP archives
- [ ] CLI: `trishul-smi compile <MIB-NAME>`
- [ ] Python API: `MibCompiler` class
- [ ] Full type annotations (mypy strict)
- [ ] Test coverage ≥ 80%

### Nice to Have (v1.x)
- [ ] Async batch compilation
- [ ] OID index file generation
- [ ] MIB validation / lint mode
- [ ] Custom Jinja2 output templates
- [ ] Plugin system for custom code generators
- [ ] Watch mode (recompile on file change)

### Non-Goals
- Not a full SNMP agent or manager
- Not a replacement for PySNMP runtime
- Not a GUI tool
- No support for Python < 3.10

---

## 4. Why Build This?

### Why not just fix pysmi?

pysmi's core issues are **architectural**, not just bugs. The parser is built on PLY (an aging lex/yacc port), the pipeline has tight coupling between reader/parser/codegen, and the public API uses `**kwargs` throughout — making it hard to add type safety without a near-total rewrite. Fixing it means owning their architecture.

### Why JSON output?

JSON is universally consumable. Every language, framework, and tool can read JSON. A MIB compiled to JSON can be used in:
- Network management dashboards
- REST APIs
- Monitoring tools (Prometheus exporters, Grafana)
- AI/ML pipelines processing network telemetry
- Any language without a native MIB parser

### Why async + httpx?

Fetching MIBs from the web is I/O bound. Async allows batch compilation of many MIBs (and their dependencies) without blocking on each HTTP request. `httpx` is the modern replacement for `requests` — async-native, timeout-safe, and easier to mock in tests.

---

## 5. How — High-Level Approach

### 5.1 Pipeline

```
[Source: file / zip / http]
        ↓  Reader
[Raw ASN.1 text]
        ↓  Parser  (lark EBNF grammar)
[AST]
        ↓  Transformer
[MibModule dataclass]
        ↓  Dependency Resolver  (BFS queue)
[Ordered MibModule list]
        ↓  CodeGen  (json / pysnmp→json)
[dict]
        ↓  Writer  (file / stdout / callback)
[Output JSON file]
```

Each stage is **independently testable** with clean interfaces.

### 5.2 Key Technology Choices

| Concern | Choice | Reason |
|---|---|---|
| ASN.1 parsing | `lark-parser` | Clean EBNF grammar, readable, great error messages |
| HTTP client | `httpx` | Async, timeout-safe, easy to mock |
| Retry logic | `tenacity` | Exponential backoff, clean decorator API |
| JSON output | `orjson` | Fast, compact, handles bytes |
| CLI | `typer` | Type-annotated, auto `--help`, built on Click |
| Terminal output | `rich` | Pretty tables, progress bars |
| Linting | `ruff` | Replaces flake8 + black + isort in one tool |
| Type checking | `mypy` (strict) | Catches bugs at dev time |
| Testing | `pytest` + `pytest-httpx` | Async support, HTTP mocking |
| Packaging | `hatchling` + `pyproject.toml` | Modern Python packaging standard |

### 5.3 Core Modules (Planned)

```
trishul_smi/
├── compiler.py          ← orchestrator (MibCompiler class)
├── config.py            ← CompilerConfig dataclass
├── errors.py            ← exception hierarchy (no circular imports)
├── models/              ← MibModule, MibObject, MibType, CompileResult
├── parser/
│   ├── grammar/         ← smiv2.lark, smiv1.lark
│   ├── transformer.py   ← Lark tree → MibModule
│   └── smi_parser.py    ← public parse(text) → MibModule
├── reader/
│   ├── base.py          ← AbstractReader ABC
│   ├── localfile.py     ← filesystem reader
│   ├── httpclient.py    ← async HTTP reader
│   ├── zipreader.py     ← ZIP archive reader
│   └── chain.py         ← ReaderChain (tries readers in order)
├── resolver/
│   ├── resolver.py      ← DependencyResolver (BFS + cycle detection)
│   └── cache.py         ← MibCache (memory + optional disk)
├── codegen/
│   ├── base.py          ← AbstractCodeGen ABC
│   ├── json_codegen.py  ← MibModule → JSON dict
│   └── pysnmp_codegen.py ← PySNMP .py → JSON (via ast module)
├── writer/
│   ├── base.py          ← AbstractWriter ABC
│   ├── file_writer.py
│   ├── stdout_writer.py
│   └── callback_writer.py
└── cli/
    ├── main.py          ← typer app
    └── display.py       ← rich output helpers
```

### 5.4 Build Order

1. `models/` — data structures, no deps
2. `errors.py` — exception hierarchy
3. `reader/` — fetch raw MIB text
4. `parser/grammar/smiv2.lark` — hardest piece
5. `parser/transformer.py` + `smi_parser.py`
6. `resolver/` — dependency BFS
7. `codegen/json_codegen.py`
8. `codegen/pysnmp_codegen.py`
9. `writer/`
10. `compiler.py` — wire everything
11. `cli/` — last, always backed by real logic

---

## 6. JSON Output Schema (Target)

```json
{
  "name": "IF-MIB",
  "language": "SMIv2",
  "imports": {
    "SNMPv2-SMI": ["MODULE-IDENTITY", "OBJECT-TYPE", "Integer32"],
    "SNMPv2-TC": ["DisplayString", "PhysAddress", "TruthValue"]
  },
  "objects": {
    "ifDescr": {
      "oid": "1.3.6.1.2.1.2.2.1.2",
      "oid_path": [1, 3, 6, 1, 2, 1, 2, 2, 1, 2],
      "type": "OBJECT-TYPE",
      "syntax": "DisplayString",
      "max_access": "read-only",
      "status": "current",
      "description": "A textual string containing information about the interface."
    }
  },
  "types": {},
  "notifications": {}
}
```

---

## 7. Open Questions

- [ ] Should the resolver support **parallel async fetching** of independent deps?
- [ ] Should we support **MIB borrowing** (pre-compiled fallback) like pysmi does?
- [ ] What is the right disk cache strategy — per-session or persistent?
- [ ] Should `pysnmp_codegen` support the full PySNMP module format or just the common subset?
- [ ] Should we publish to PyPI from day one or only after v1.0 is stable?

---

## 8. Success Criteria

The project is considered v1.0 ready when:

- `trishul-smi compile IF-MIB` works end-to-end from a clean environment
- All standard RFC MIBs (SNMPv2-SMI, SNMPv2-TC, IF-MIB, IP-MIB, etc.) compile without error
- Test suite passes with ≥ 80% coverage
- `mypy --strict` passes with zero errors
- `ruff check` passes with zero warnings
