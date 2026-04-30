# trishul-smi — Architecture

> **Status:** v0.1 — in sync with plan.md Draft v0.2  
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
│   │   ├── smiv2.lark     ← SMIv2 EBNF grammar (RFC 2578)
│   │   └── smiv1.lark     ← SMIv1 extensions (RFC 1155)
│   ├── transformer.py     ← Lark Transformer → MibModule
│   └── smi_parser.py      ← public API: parse(text) → MibModule
│
├── reader/
│   ├── __init__.py
│   ├── base.py            ← AbstractReader ABC
│   ├── localfile.py       ← FileReader
│   ├── httpclient.py      ← HttpReader (async, httpx)
│   ├── zipreader.py       ← ZipReader
│   └── chain.py           ← ReaderChain
│
├── resolver/
│   ├── __init__.py
│   ├── resolver.py        ← DependencyResolver
│   └── cache.py           ← MibCache
│
├── codegen/
│   ├── __init__.py
│   ├── base.py            ← AbstractCodeGen ABC
│   ├── json_codegen.py    ← MibModule → JSON dict     [PRIMARY]
│   ├── pysnmp_codegen.py  ← MibModule → PySNMP .py   [SECONDARY]
│   └── pysnmp_reader.py   ← PySNMP .py → JSON        [UTILITY]
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
    status: Literal["compiled", "cached", "borrowed", "failed"]
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
- `FileReader`: uses `with open(...)`, never leaks file handles
- `HttpReader`: `httpx.AsyncClient` with explicit `timeout`, uses `tenacity` retry,
  implements `async with` context manager to close session cleanly
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

**Grammar strategy:**
- `smiv2.lark` — covers 90%+ of modern MIBs, uses LALR(1) (fast)
- `smiv1.lark` — imports `smiv2.lark` and overrides differing rules
- Fall back to Earley algorithm if LALR fails (handles vendor dialect ambiguity)
- `MibTransformer(Transformer)` walks the Lark tree and constructs `MibModule`

**Parser pipeline:**
```
raw text
  → Lark(grammar, parser="lalr").parse(text)
  → Tree
  → MibTransformer().transform(tree)
  → MibModule
```

---

### 3.4 `resolver/`

Reads `MibModule.imports`, fetches + parses all dependencies, returns a topologically ordered list.

```python
# resolver/resolver.py
class DependencyResolver:
    def __init__(self, reader: ReaderChain, parser: SmiParser, cache: MibCache) -> None: ...

    async def resolve(self, root: MibModule) -> list[MibModule]:
        """
        BFS from root. Returns list ordered: dependencies first, root last.
        Raises CircularDependencyError on cycles.
        """
```

**Algorithm:**
```python
queue = deque([root])
seen: set[str] = set()
ordered: list[MibModule] = []

while queue:
    mib = queue.popleft()
    if mib.name in seen:
        continue
    seen.add(mib.name)
    for dep_name in mib.all_imports():
        if dep_name not in seen:
            dep_text = await reader.fetch(dep_name)
            dep_mib = parser.parse(dep_text)
            queue.append(dep_mib)
    ordered.append(mib)
```

**`MibCache`:**
- In-memory dict by default: `dict[str, MibModule]`
- Optional disk cache: pickled `MibModule` objects under `~/.cache/trishul-smi/`
- Cache key: `mib_name` — invalidated when source file mtime changes

---

### 3.5 `codegen/`

Transforms a `MibModule` into an output artifact. Multiple codegens can run on the same module.

```python
# codegen/base.py
class AbstractCodeGen(ABC):
    @abstractmethod
    def generate(self, mib: MibModule) -> str:
        """Generate output string (JSON or .py) from a MibModule."""
```

| Class | Input | Output | Method |
|---|---|---|---|
| `JsonCodeGen` | `MibModule` | JSON string | Walks dataclass, uses `orjson` |
| `PySnmpCodeGen` | `MibModule` | PySNMP `.py` string | Jinja2 template (v1.x) / manual string building (v1.0) |
| `PySnmpReader` | PySNMP `.py` file path | `MibModule` | Python `ast` module — no regex |

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
1. reader.fetch(name)          → raw ASN.1 text
2. parser.parse(text)          → MibModule
3. resolver.resolve(mib)       → [dep1, dep2, ..., mib] (ordered)
4. for each mib in ordered:
     for codegen in codegens:
       content = codegen.generate(mib)
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
    max_mib_size: int = 10 * 1024 * 1024   # 10 MB
```

---

### 3.9 `errors.py`

Flat hierarchy — no circular imports. All annotations use `TYPE_CHECKING` guard.

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from trishul_smi.models.mib_module import MibModule

class TrishulError(Exception):            """Base"""
class MibNotFoundError(TrishulError):     """Reader could not locate MIB"""
class ParseError(TrishulError):           """Grammar/syntax error in ASN.1 source"""
class CircularDependencyError(TrishulError): """Import cycle detected"""
class CodeGenError(TrishulError):         """Output generation failed"""
class WriterError(TrishulError):          """Could not write output artifact"""
class MibCacheError(TrishulError):        """Cache read/write failure"""
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
              ├─ reader.fetch("IF-MIB")  ──▶  raw ASN.1 text
              ├─ parser.parse(text)      ──▶  MibModule(name="IF-MIB", imports={...})
              ├─ resolver.resolve(mib)   ──▶  [SNMPv2-SMI, SNMPv2-TC, IF-MIB]
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
| Models | `pytest` | Simple instantiation + field validation |
| Parser | `pytest` | Feed fixture `.mib` files, assert `MibModule` shape |
| Readers | `pytest` + `pytest-httpx` | Mock HTTP, tmp dirs for file/zip |
| Resolver | `pytest` | Mock reader + parser, verify BFS order + cycle detection |
| CodeGen | `pytest` | Known `MibModule` → assert JSON/py output structure |
| Writer | `pytest` | tmp dirs, assert files written correctly |
| Compiler | `pytest-asyncio` | Integration: full pipeline with fixture MIBs |
| CLI | `typer.testing.CliRunner` | Smoke test commands end-to-end |

**Fixtures** (`tests/fixtures/`):
- `minimal.mib` — smallest valid SMIv2 module (for parser unit tests)
- `IF-MIB.mib` — real-world SMIv2 MIB (for integration tests)
- `IF_MIB.py` — PySNMP compiled version (for `pysnmp_reader` tests)
- `circular_a.mib` + `circular_b.mib` — for cycle detection tests

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

`models` and `errors` are the only true shared-leaf packages. Nothing in `reader`, `parser`, `resolver`, `codegen`, or `writer` imports from each other — all cross-stage communication goes through `models` datatypes.

---

## 7. Key Design Principles

1. **No `**kwargs` in public APIs** — all options are explicit typed parameters
2. **No circular imports** — `TYPE_CHECKING` guard for forward references
3. **No bare `open()`** — always `with open(...) as f:`
4. **No unguarded loops** — always initialise accumulator variables before loops
5. **Async I/O, sync logic** — readers and compiler are async; parser, codegen, writer are sync
6. **One responsibility per module** — reader fetches, parser parses, resolver resolves
7. **Fail fast, fail clearly** — typed exceptions with descriptive messages, no silent swallowing
