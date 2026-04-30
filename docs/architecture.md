# trishul-smi — Architecture

> **Status:** v0.2 — review findings applied, in sync with plan.md v0.3  
> **Author:** GhaatakJi  
> **Last updated:** 2026-04-30

---

## 1. Overview

`trishul-smi` is a pipeline-based MIB compiler. Raw ASN.1 source text enters one end; structured JSON (and optionally PySNMP `.py` modules) exits the other. Every stage in the pipeline is a **distinct, independently testable module** with a clean abstract interface.

```
┌─────────────────────────────────────────────────────────────────┐
│                        MibCompiler                              │
│                        (orchestrator)                           │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────┐  │
│  │  Reader  │──▶│  Parser  │──▶│ Resolver │──▶│  CodeGen   │  │
│  └──────────┘   └──────────┘   └──────────┘   └─────┬──────┘  │
│                                                       │         │
│                                               ┌───────▼──────┐ │
│                                               │    Writer    │ │
│                                               └──────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Package Structure

```
trishul_smi/
├── compiler.py            ← MibCompiler: pipeline orchestrator
├── config.py              ← CompilerConfig dataclass
├── errors.py              ← exception hierarchy
│
├── models/
│   ├── __init__.py
│   ├── mib_module.py      ← MibModule dataclass
│   ├── mib_object.py      ← MibObject dataclass
│   ├── mib_type.py        ← MibType dataclass
│   └── result.py          ← CompileResult dataclass
│
├── parser/
│   ├── __init__.py
│   ├── grammar/
│   │   ├── smiv2.lark     ← complete SMIv2 grammar (RFC 2578)
│   │   └── smiv1.lark     ← independent SMIv1 grammar (RFC 1155)
│   ├── transformer.py     ← Lark Transformer → MibModule
│   └── smi_parser.py      ← public API: parse(text) → MibModule
│
├── reader/
│   ├── __init__.py
│   ├── base.py            ← AbstractReader ABC
│   ├── localfile.py       ← FileReader (enforces max_mib_size)
│   ├── httpclient.py      ← HttpReader (enforces max_mib_size, ETag caching, TTL)
│   ├── zipreader.py       ← ZipReader
│   └── chain.py           ← ReaderChain
│
├── resolver/
│   ├── __init__.py
│   ├── resolver.py        ← DependencyResolver (Kahn’s + asyncio.gather)
│   └── cache.py           ← MibCache (memory + orjson disk cache)
│
├── codegen/
│   ├── __init__.py
│   ├── base.py            ← AbstractCodeGen ABC
│   ├── json_codegen.py    ← MibModule → JSON            [PRIMARY]
│   ├── pysnmp_codegen.py  ← MibModule → PySNMP .py     [SECONDARY, Jinja2]
│   ├── pysnmp_reader.py   ← PySNMP .py → MibModule     [UTILITY]
│   └── templates/
│       └── pysnmp_module.j2
│
├── writer/
│   ├── __init__.py
│   ├── base.py            ← AbstractWriter ABC
│   ├── file_writer.py
│   ├── stdout_writer.py
│   └── callback_writer.py
│
└── cli/
    ├── __init__.py
    ├── main.py            ← typer app (compile + convert)
    └── display.py         ← rich helpers

tests/
├── fixtures/              ← sample .mib and .py files
├── test_models.py
├── test_parser.py
├── test_readers.py
├── test_resolver.py
├── test_codegen.py
├── test_writer.py
└── test_compiler.py

docs/
├── plan.md
└── architecture.md
```

---

## 3. Module Contracts

### 3.1 `models/`

All pipeline stages communicate via these dataclasses. No business logic — pure data.

```python
# models/mib_module.py
@dataclass
class MibModule:
    name: str
    language: Literal["SMIv1", "SMIv2"]
    imports: dict[str, list[str]]        # {"SNMPv2-SMI": ["OBJECT-TYPE", ...]}
    objects: dict[str, MibObject]
    types: dict[str, MibType]
    notifications: dict[str, MibObject]
    source_text: str | None = None       # original raw ASN.1 (for debugging)

    def all_imports(self) -> list[str]:
        """Return flat list of all imported MIB module names."""
        return list(self.imports.keys())

# models/mib_object.py
@dataclass
class MibObject:
    name: str
    oid: str                             # dotted string: "1.3.6.1.2.1.2.2.1.2"
    oid_path: list[int]
    object_type: str                     # "OBJECT-TYPE", "MODULE-IDENTITY", etc.
    syntax: str | None = None
    max_access: str | None = None
    status: str | None = None
    description: str | None = None
    index: list[str] | None = None       # for table rows
    augments: str | None = None

# models/mib_type.py
@dataclass
class MibType:
    name: str
    base_type: str                       # "OCTET STRING", "Integer32", etc.
    constraints: dict | None = None
    description: str | None = None

# models/result.py
@dataclass
class CompileResult:
    name: str
    status: Literal["compiled", "cached", "failed"]   # "borrowed" deferred to v1.x
    output_paths: list[Path]
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
```

---

### 3.2 `reader/`

Responsible for fetching raw ASN.1 MIB text from a source. Stateless per-call.

```python
# reader/base.py
class AbstractReader(ABC):
    @abstractmethod
    async def fetch(self, mib_name: str) -> str:
        """Fetch raw ASN.1 text for mib_name. Raises MibNotFoundError if not found."""

# reader/chain.py
class ReaderChain:
    """Tries each reader in order, returns first successful result."""
    def __init__(self, readers: list[AbstractReader]) -> None: ...
    async def fetch(self, mib_name: str) -> str: ...
```

**Key contracts:**
- `FileReader`: uses `with open(...)`, enforces `max_mib_size` on read
- `HttpReader`:
  - `httpx.AsyncClient` with explicit `timeout` from `CompilerConfig`
  - `tenacity` retry with exponential backoff
  - `async with` context manager to close session cleanly
  - **ETag caching**: stores `ETag` header per MIB; sends `If-None-Match` on next fetch — skips re-download if unchanged
  - **TTL**: remote MIBs cached for 7 days by default; configurable via `CompilerConfig.cache_ttl_days`
  - Enforces `max_mib_size` via `Content-Length` check before download
- `ZipReader`: seeds `data: bytes = b""` before loop — no `NameError` on nested ZIPs

---

### 3.3 `parser/`

Converts raw ASN.1 text into a `MibModule` dataclass via a Lark grammar + Transformer.

```python
# parser/smi_parser.py
class SmiParser:
    def __init__(self, dialect: Literal["smiv2", "smiv1"] = "smiv2") -> None: ...
    def parse(self, text: str) -> MibModule:
        """Parse raw ASN.1 text. Raises ParseError on invalid input."""
```

**Grammar strategy — two independent files (no inheritance):**

Lark does not support grammar rule overriding via file imports. Both grammars are **standalone complete files**:
- `smiv2.lark` — complete SMIv2 grammar (RFC 2578/2579/2580), LALR(1)
- `smiv1.lark` — complete SMIv1 grammar (RFC 1155/1212/1215), LALR(1)

Dialect is auto-detected from the MIB source (`DEFINITIONS ::= BEGIN` preamble patterns). `SmiParser` picks the appropriate grammar file.

Both grammars share a common lexer terminal file (`grammar/common.lark`) for tokens that are identical in both (string literals, OID notation, comments).

**Earley fallback for vendor quirks:**
```python
try:
    tree = Lark(grammar, parser="lalr").parse(text)
except UnexpectedInput:
    tree = Lark(grammar, parser="earley").parse(text)  # slower, handles ambiguity
```

**Parser → async boundary:**

`SmiParser.parse()` is CPU-bound sync code. Called from async context via:
```python
mib = await asyncio.to_thread(parser.parse, raw_text)
```
This offloads parsing to a thread pool, keeping the event loop unblocked.

**Parser pipeline:**
```
raw text
  → asyncio.to_thread(Lark.parse)
  → Tree
  → MibTransformer().transform(tree)
  → MibModule
```

---

### 3.4 `resolver/`

Reads `MibModule.imports`, fetches + parses all dependencies in parallel, returns a topologically ordered list.

```python
# resolver/resolver.py
class DependencyResolver:
    def __init__(self, reader: ReaderChain, parser: SmiParser, cache: MibCache) -> None: ...

    async def resolve(self, root: MibModule) -> list[MibModule]:
        """
        Kahn’s algorithm. Returns list ordered: dependencies first, root last.
        Raises CircularDependencyError on cycles.
        Independent deps at each level are fetched in parallel via asyncio.gather.
        """
```

**Algorithm — Kahn’s (correct topological sort with cycle detection):**
```python
# Phase 1: fetch all transitive deps (parallel per BFS level)
all_mibs: dict[str, MibModule] = {}
queue = deque([root.name])
seen: set[str] = set()

while queue:
    level = list(queue)             # all names at this BFS level
    queue.clear()
    # parallel fetch+parse for all unseen names at this level
    results = await asyncio.gather(*[
        fetch_and_parse(name) for name in level if name not in seen
    ])
    for mib in results:
        seen.add(mib.name)
        all_mibs[mib.name] = mib
        queue.extend(mib.all_imports())

# Phase 2: Kahn’s topological sort
in_degree = {name: 0 for name in all_mibs}
for mib in all_mibs.values():
    for dep in mib.all_imports():
        if dep in in_degree:
            in_degree[mib.name] += 1

ready = deque([n for n, d in in_degree.items() if d == 0])
ordered: list[MibModule] = []
while ready:
    name = ready.popleft()
    ordered.append(all_mibs[name])
    for mib in all_mibs.values():
        if name in mib.all_imports():
            in_degree[mib.name] -= 1
            if in_degree[mib.name] == 0:
                ready.append(mib.name)

if len(ordered) != len(all_mibs):
    raise CircularDependencyError(...)

return ordered
```

**`MibCache` — two layers:**

| Layer | Storage | Key | Invalidation |
|---|---|---|---|
| L1 Memory | `dict[str, MibModule]` | `mib_name` | Per-process lifetime |
| L2 Disk | `~/.cache/trishul-smi/<mib>.json` | `mib_name` | File mtime (local); TTL + ETag (remote) |

**Disk format:** `orjson`-serialised `MibModule` dataclass to JSON. **No pickle** — pickle silently breaks on model changes between versions.

---

### 3.5 `codegen/`

Transforms a `MibModule` into an output artifact. Multiple codegens can run on the same module.

```python
# codegen/base.py
class AbstractCodeGen(ABC):
    suffix: str                          # file extension: ".json" or ".py"

    @abstractmethod
    def generate(self, mib: MibModule) -> str:
        """Generate output string from a MibModule."""
```

| Class | Input | Output | Method |
|---|---|---|---|
| `JsonCodeGen` | `MibModule` | JSON string | Walks dataclass, serialises via `orjson` |
| `PySnmpCodeGen` | `MibModule` | PySNMP `.py` string | **Jinja2 template** from v1.0 |
| `PySnmpReader` | PySNMP `.py` path | `MibModule` | Python `ast` module — no regex |

**`PySnmpCodeGen` uses Jinja2 from day one** — single code path, no manual string building:
```python
from jinja2 import Environment, PackageLoader

class PySnmpCodeGen(AbstractCodeGen):
    suffix = ".py"
    _env = Environment(loader=PackageLoader("trishul_smi", "codegen/templates"))
    _tmpl = _env.get_template("pysnmp_module.j2")

    def generate(self, mib: MibModule) -> str:
        return self._tmpl.render(mib=mib)
```

**`PySnmpReader` — how it works:**
```
PySNMP .py file
  → ast.parse(source)
  → walk ast.Assign nodes
  → extract OID tuples, syntax class names, access strings
  → construct MibObject instances
  → return MibModule
```

---

### 3.6 `writer/`

Persists the generated output string to a destination.

```python
# writer/base.py
class AbstractWriter(ABC):
    @abstractmethod
    def write(self, name: str, content: str, suffix: str) -> Path | None:
        """Write content. Returns output path or None (e.g. stdout)."""
```

| Class | Behaviour |
|---|---|
| `FileWriter` | Writes `<output_dir>/<name><suffix>` (e.g. `IF-MIB.json`) |
| `StdoutWriter` | Streams to stdout, returns `None` |
| `CallbackWriter` | Calls `on_write(name, content)` — for programmatic use |

---

### 3.7 `compiler.py` — Orchestrator

The only module that knows about all other modules. Everything else is decoupled.

```python
class MibCompiler:
    def __init__(
        self,
        reader: ReaderChain,
        writer: AbstractWriter,
        codegens: list[AbstractCodeGen],
        cache: MibCache | None = None,
    ) -> None: ...

    async def compile(
        self,
        *mib_names: str,
        rebuild: bool = False,
        dry_run: bool = False,
        no_deps: bool = False,
        ignore_errors: bool = False,
    ) -> list[CompileResult]:
        """Compile one or more MIBs. All options are explicit typed parameters."""
```

**Compile flow per MIB:**
```
1. reader.fetch(name)                    → raw ASN.1 text
2. await asyncio.to_thread(parser.parse) → MibModule          [offloads CPU to thread]
3. resolver.resolve(mib)                 → [dep1, dep2, ..., mib]  (parallel + ordered)
4. for each mib in ordered:
     for codegen in codegens:
       content = codegen.generate(mib)   (sync, fast)
       writer.write(mib.name, content, codegen.suffix)
5. return list[CompileResult]
```

---

### 3.8 `config.py`

```python
@dataclass
class CompilerConfig:
    sources: list[str] = field(default_factory=lambda: [
        "https://mibs.pysnmp.com/asn1/@mib@",
        "https://www.circitor.fr/Mibs/Mib/@mib@.mib",
    ])
    output_dir: Path = Path("./mibs-output")
    formats: list[Literal["json", "pysnmp"]] = field(default_factory=lambda: ["json"])
    http_timeout: float = 30.0
    http_retries: int = 3
    cache_dir: Path | None = Path.home() / ".cache" / "trishul-smi"
    cache_ttl_days: int = 7              # TTL for HTTP-fetched MIBs
    max_mib_size: int = 10 * 1024 * 1024 # enforced by FileReader + HttpReader
```

---

### 3.9 `errors.py`

Flat hierarchy — no circular imports. All annotations use `TYPE_CHECKING` guard.

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from trishul_smi.models.mib_module import MibModule

class TrishulError(Exception):               """Base"""
class MibNotFoundError(TrishulError):        """Reader could not locate MIB"""
class ParseError(TrishulError):              """Grammar/syntax error in ASN.1 source"""
class CircularDependencyError(TrishulError): """Import cycle detected"""
class CodeGenError(TrishulError):            """Output generation failed"""
class WriterError(TrishulError):             """Could not write output artifact"""
class MibCacheError(TrishulError):           """Cache read/write failure"""
class MibSizeLimitError(TrishulError):       """MIB exceeds max_mib_size limit"""
```

---

### 3.10 `cli/`

Two top-level commands, backed entirely by `MibCompiler` and `PySnmpReader`.

```
trishul-smi compile IF-MIB SNMPv2-MIB
            --source https://mibs.pysnmp.com/asn1/@mib@
            --source ./local-mibs/
            --format json
            --format pysnmp
            --output ./out/
            --no-deps
            --rebuild
            --dry-run

trishul-smi convert ./IF_MIB.py
            --output ./out/
```

CLI constructs a `CompilerConfig` from flags → builds `MibCompiler` → calls `compile()` → displays results via `rich` table.

---

## 4. Data Flow — End to End

```
$ trishul-smi compile IF-MIB --format json pysnmp

cli/main.py
  │
  ├─ builds CompilerConfig(formats=["json","pysnmp"], sources=[...])
  ├─ builds ReaderChain([FileReader(...), HttpReader(...), ZipReader(...)])
  ├─ builds codegens = [JsonCodeGen(), PySnmpCodeGen()]
  ├─ builds writer = FileWriter(output_dir)
  └─ awaits MibCompiler.compile("IF-MIB")
              │
              ├─ reader.fetch("IF-MIB")                        → raw ASN.1
              ├─ asyncio.to_thread(parser.parse, text)         → MibModule
              ├─ resolver.resolve(mib)                         → ordered list
              │     ├─ asyncio.gather(fetch SNMPv2-SMI, SNMPv2-TC)  [parallel]
              │     └─ Kahn’s sort → [SNMPv2-SMI, SNMPv2-TC, IF-MIB]
              │
              └─ for each mib in [SNMPv2-SMI, SNMPv2-TC, IF-MIB]:
                   JsonCodeGen.generate(mib)    → "{ ... }"
                   FileWriter.write(name, json) → ./out/IF-MIB.json
                   PySnmpCodeGen.generate(mib)  → "# PySNMP MIB module..."
                   FileWriter.write(name, py)   → ./out/IF_MIB.py

display.py renders:
  ┌───────────────┬──────────┬────────────────────────────────┐
  │ MIB           │ Status   │ Output                         │
  ├───────────────┼──────────┼────────────────────────────────┤
  │ SNMPv2-SMI    │ compiled │ out/SNMPv2-SMI.json, .py       │
  │ SNMPv2-TC     │ compiled │ out/SNMPv2-TC.json, .py        │
  │ IF-MIB        │ compiled │ out/IF-MIB.json, .py           │
  └───────────────┴──────────┴────────────────────────────────┘
```

---

## 5. Testing Strategy

| Layer | Tool | Approach |
|---|---|---|
| Models | `pytest` | Instantiation + field validation |
| Parser | `pytest` | Feed fixture `.mib` files, assert `MibModule` shape |
| Readers | `pytest` + `pytest-httpx` | Mock HTTP, tmp dirs for file/zip, size limit tests |
| Resolver | `pytest-asyncio` | Mock reader+parser, verify Kahn’s order + cycle detection |
| CodeGen | `pytest` | Known `MibModule` → assert JSON/py output structure |
| Writer | `pytest` | tmp dirs, assert files written correctly |
| Compiler | `pytest-asyncio` | Integration: full pipeline with fixture MIBs |
| CLI | `typer.testing.CliRunner` | Smoke test commands end-to-end |

**Fixtures** (`tests/fixtures/`):
- `minimal.mib` — smallest valid SMIv2 module (parser unit tests)
- `minimal_v1.mib` — smallest valid SMIv1 module
- `IF-MIB.mib` — real-world SMIv2 MIB (integration tests)
- `IF_MIB.py` — PySNMP compiled version (pysnmp_reader tests)
- `circular_a.mib` + `circular_b.mib` — cycle detection tests
- `oversized.mib` — file exceeding `max_mib_size` (size limit tests)

---

## 6. Dependency Graph (Internal)

```
cli
 └── compiler
      ├── reader (chain, file, http, zip)
      ├── parser (grammar, transformer)
      ├── resolver
      │    ├── reader
      │    ├── parser
      │    └── cache
      ├── codegen (json, pysnmp)
      └── writer

All modules → models
All modules → errors
No module → cli
No module → compiler  (except cli)
```

`models` and `errors` are the only true shared-leaf packages. Nothing in `reader`, `parser`, `resolver`, `codegen`, or `writer` imports from each other.

---

## 7. Key Design Principles

1. **No `**kwargs` in public APIs** — all options are explicit typed parameters
2. **No circular imports** — `TYPE_CHECKING` guard for forward references in `errors.py`
3. **No bare `open()`** — always `with open(...) as f:`
4. **No unguarded loops** — always initialise accumulator variables before loops
5. **Async I/O, sync logic** — readers/resolver are async; parser uses `asyncio.to_thread`; codegen/writer are sync
6. **No pickle** — disk cache uses `orjson` JSON serialization only
7. **One responsibility per module** — reader fetches, parser parses, resolver resolves
8. **Fail fast, fail clearly** — typed exceptions with descriptive messages, no silent swallowing
9. **Size limits enforced at source** — `FileReader` and `HttpReader` both enforce `max_mib_size`
