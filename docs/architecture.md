# trishul-smi — Architecture

> **Last updated:** 2026-09-24

---

## 1. Overview

`trishul-smi` is a pipeline-based MIB compiler. Raw ASN.1 source text enters one end;
structured JSON (and optionally PySNMP `.py` modules) exits the other. Every stage is a
distinct, independently testable module with a clean interface.

```
┌─────────────────────────────────────────────────────────────────┐
│                        MibCompiler                              │
│                        (orchestrator)                           │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────┐  │
│  │  Reader  │──▶│  Parser  │──▶│ Resolver │──▶│ Formatter  │  │
│  └──────────┘   └──────────┘   └──────────┘   └────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Package Structure

```
README.md                  ← repository overview
LICENSE                    ← MIT license
pyproject.toml             ← packaging and tool configuration

.github/
├── CONTRIBUTING.md        ← contribution guide and repo conventions
├── CONTRIBUTORS.md        ← maintainer and contributor credits
├── FUNDING.yml            ← GitHub funding metadata
└── workflows/
    ├── ci.yml             ← CI pipeline
    └── release.yml        ← release automation

docs/
├── index.md               ← documentation index
├── python-api.md          ← library/API usage
├── configuration.md       ← CompilerConfig reference
├── cli.md                 ← CLI reference
├── json-bundles.md        ← JSON bundle runtime contract
├── architecture.md        ← this file
├── design-notes.md        ← design decisions and goals
├── roadmap.md             ← planned features and known limitations
├── release-checklist.md   ← maintainer release process
└── CHANGELOG.md           ← version history

trishul_smi/
├── __init__.py            ← package version export
├── compiler.py            ← MibCompiler: pipeline orchestrator
├── config.py              ← CompilerConfig dataclass
├── errors.py              ← exception hierarchy
├── version.py             ← producer version helpers for emitted JSON artifacts
│
├── models/
│   ├── mib_module.py      ← MibModule dataclass
│   ├── mib_object.py      ← MibObject dataclass
│   ├── mib_type.py        ← MibType dataclass
│   └── result.py          ← CompileResult dataclass
│
├── parser/
│   ├── grammar/
│   │   ├── smiv2.lark     ← complete SMIv2 grammar (RFC 2578)
│   │   └── smiv1.lark     ← complete SMIv1 grammar (RFC 1155)
│   ├── _constants.py      ← parser constants and helpers
│   ├── transformer.py     ← Lark Transformer → MibModule
│   └── smi_parser.py      ← public API: SmiParser.parse(text) → MibModule
│
├── reader/
│   ├── base.py            ← AbstractReader ABC + FetchProtocol (structural)
│   ├── localfile.py       ← FileReader
│   ├── httpclient.py      ← HttpReader (httpx + tenacity, async CM)
│   ├── zipreader.py       ← ZipReader
│   └── chain.py           ← ReaderChain (fallback chain)
│
├── resolver/
│   ├── resolver.py        ← MibResolver (BFS + asyncio.gather) + ResolveResult
│   ├── dependency.py      ← build_dependency_graph, topological_sort (Kahn's)
│   ├── oid_resolver.py    ← resolve_oids: rewrites MibObject.oid/oid_path to absolute paths
│   └── cache.py           ← MibCache (orjson disk cache, mtime TTL)
│
├── output/
│   ├── base.py            ← FormatterProtocol
│   ├── json_ir.py         ← shared JSON artifact metadata
│   ├── json_contract.py   ← shared JSON class/nodetype semantics
│   ├── json_bundle.py     ← optional manifest.json / oid_index.json builders
│   ├── registry.py        ← formatter registry: built-ins + entry-point plugins
│   └── json_fmt.py        ← JsonFormatter  (FILE_SUFFIX = ".json")
│
├── convert/
│   └── pysnmp_reader.py   ← PySNMPReader: compiled .py → MibModule (ast-based)
│
└── cli/
    └── main.py            ← Typer app: compile + convert + version commands

tests/
├── conftest.py            ← shared pytest fixtures
├── helpers.py             ← model builder helpers
├── test_cli.py
├── test_compiler.py
├── test_compat_policy.py
├── test_config.py
├── test_convert.py
├── test_errors.py
├── test_httpreader.py
├── test_init.py
├── test_json_bundle.py
├── test_json_ir.py
├── test_json_oid_index.py
├── test_models.py
├── test_oid_resolver.py
├── test_parser.py
├── test_plugins.py
├── test_readers.py
├── test_reproducible.py
├── test_resolver.py
└── test_transformer.py
```

---

## 3. Module Contracts

### 3.1 `models/`

All pipeline stages communicate via these dataclasses. Pure data, no business logic.

```python
@dataclass
class MibModule:
    name: str
    language: Literal["SMIv1", "SMIv2"]
    imports: dict[str, list[str]]      # {"SNMPv2-SMI": ["OBJECT-TYPE", ...]}
    objects: dict[str, MibObject]
    types: dict[str, MibType]
    notifications: dict[str, MibObject]
    lastupdated: str | None = None     # SMIv2 date string from MODULE-IDENTITY
    organization: str | None = None
    contactinfo: str | None = None
    description: str | None = None

@dataclass
class MibObject:
    name: str
    oid: str                           # absolute dotted: "1.3.6.1.2.1.2.2.1.2"
    oid_path: list[int]                # absolute numeric arcs (resolved by oid_resolver)
    object_type: str                   # "OBJECT-TYPE", "MODULE-IDENTITY", etc.
    syntax: str | None = None
    max_access: str | None = None
    status: str | None = None
    description: str | None = None
    index: list[str] | None = None
    augments: str | None = None
    oid_parent: str | None = None      # pre-resolution parent name arc
    constraints: dict[str, Any] | None = None  # inline SYNTAX constraint
    members: list[str] | None = None   # OBJECTS/NOTIFICATIONS clause members

@dataclass
class MibType:
    name: str
    base_type: str
    constraints: dict[str, Any] | None = None
    description: str | None = None
    display_hint: str | None = None
    status: str | None = None

@dataclass
class CompileResult:
    name: str
    status: Literal["compiled", "cached", "failed", "missing"]
    output_paths: list[Path]
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    is_dependency: bool = False        # True for transitive deps, False for explicitly requested
```

---

### 3.2 `reader/`

Fetches raw ASN.1 MIB text from a source. Stateless per-call.

```python
class AbstractReader(ABC):
    @abstractmethod
    async def fetch(self, mib_name: str) -> str:
        """Raises MibNotFoundError if not found."""

# FetchProtocol is a structural protocol — duck typing, not ABC inheritance
class FetchProtocol(Protocol):
    async def fetch(self, mib_name: str) -> str: ...
```

**Key contracts:**
- `FileReader` — reads from local filesystem directories, enforces `max_mib_size`
- `HttpReader` — `httpx.AsyncClient` (follows redirects), `tenacity` retry with exponential backoff, `async with` context manager; responses are consumed as a stream with an early abort once the byte count exceeds `max_mib_size`; 404/410 on the GET is authoritative for `MibNotFoundError`, other non-2xx and transport failures map to `NetworkError`
- `ZipReader` — reads MIBs from in-memory ZIP archives
- `ReaderChain` — tries each reader in order; **only `MibNotFoundError` triggers fallback**, all other errors propagate immediately

---

### 3.3 `parser/`

Converts raw ASN.1 text into a `MibModule` via a Lark grammar + Transformer.

```python
class SmiParser:
    def parse(self, text: str) -> MibModule:
        """Raises ParseError on invalid input."""
```

**Grammar strategy:** two independent complete files (Lark does not support grammar rule overriding via imports):
- `smiv2.lark` — complete SMIv2 grammar (RFC 2578/2579/2580), LALR(1)
- `smiv1.lark` — complete SMIv1 grammar (RFC 1155/1212/1215), LALR(1)

Dialect is auto-detected from the MIB source: SMIv2 iff an IMPORTS clause references an
SMIv2 module (recognised as `FROM` targets on a quote/comment-masked copy, so mentions in
comments or descriptions cannot flip it); import-less root modules (e.g. `SNMPv2-SMI`
itself) fall back to SMIv2-only construct keywords. Grammar text is cached process-wide and
compiled `Lark` parsers are cached per thread. Tagged ASN.1 type assignments such as
`[APPLICATION 0] IMPLICIT OCTET STRING` are preserved as the underlying base type plus
constraint metadata. The parser also performs narrow pre-parse normalization for wrapped
inline comment continuations seen in real IETF MIBs and accepts `SNMPv2-PDU`-style
symbolic range bounds, anonymous `CHOICE` members inside `SEQUENCE`, and constrained
`SEQUENCE OF` forms.

**Lenient vendor-syntax handling:** real-world vendor MIBs (Ericsson, Cisco, and others)
frequently use non-standard ASN.1 shorthand that strict validators like libsmi reject. The
parser accepts two such forms and records a non-fatal warning on `MibModule.warnings` (with
the source line number) rather than failing:
- `OCTET STRING (0..30)` — bare range without the `SIZE` keyword; reinterpreted as a size
  constraint (the only sensible reading for an octet string's length).
- `BIT STRING { start(1), ... }` — the ASN.1 singular form; accepted as an alias for the
  SMIv2 `BITS { ... }` construct.

Warnings flow through the full pipeline: transformer → `MibModule.warnings` → cache
(serialised so cached and fresh compiles are consistent) → `CompileResult.warnings` → CLI
(per-module warning count in the result table, full details below it).

**Async boundary:** `SmiParser.parse()` is CPU-bound sync code. The resolver offloads it to
the default thread pool via `asyncio.to_thread`, so parsing never runs on the event-loop
thread; `SmiParser` caches compiled Lark parsers per thread so concurrent worker threads do
not share mutable parser state. Fetches stay async and parse happens deterministically after
each fetch wave.

---

### 3.4 `resolver/`

Reads `MibModule.imports`, fetches dependencies in parallel, parses each wave
deterministically, and returns a topologically ordered list.

```python
@dataclass
class ResolveResult:
    modules: list[MibModule]   # topologically ordered; deps before dependents
    errors: dict[str, str]     # mib_name → error message for failed modules

class MibResolver:
    async def resolve(self, mib_names: list[str]) -> ResolveResult:
        """BFS import closure. Returns ResolveResult."""
```

**Algorithm:**
1. BFS over the import graph — each wave fetched concurrently via `asyncio.gather(return_exceptions=True)`
2. Check the cache against the fetched text's sha256 fingerprint; parse cache-missing text in the thread pool (`asyncio.to_thread`) after the fetch wave completes
3. Topological sort via Kahn's algorithm (`resolver/dependency.py`) — `sorted()` for deterministic output
4. `CircularDependencyError` includes the cycle members and propagates immediately
5. `MibSizeLimitError` propagates immediately; per-module fetch/parse failures are collected in `ResolveResult.errors`

**`MibCache`:**
- Disk cache at `~/.cache/trishul-smi/<mib>.json`; `orjson` serialization (no pickle)
- Atomic writes via a uniquely-named `tempfile.mkstemp` temp file + rename (`rename(2)` on POSIX)
- Invalidation by file mtime + configurable TTL; corrupted/unreadable files self-heal on next miss
- Content fingerprinting (issue #12): each entry records the sha256 of the source text it was parsed from, and `get()` treats a fingerprint mismatch as a miss — the resolver always fetches first, so the cache saves parsing only (never fetch-avoidance), guaranteeing updated files can never serve stale entries

---

### 3.5 `output/`

Transforms a `MibModule` into an output string. Conforming to `FormatterProtocol` (structural — not ABC).

```python
class FormatterProtocol(Protocol):
    FILE_SUFFIX: str             # e.g. ".json"
    def format(self, module: MibModule) -> str | bytes: ...
```

| Class | Output | Method |
|---|---|---|
| `JsonFormatter` | `.json` | `orjson` serialization; shared artifact metadata; descriptions normalized; `oid_path` compact |

`json_ir.py` creates one shared metadata block per compile run, `json_contract.py`
centralizes runtime-visible JSON `class` and `nodetype` semantics, and `json_bundle.py`
builds optional `manifest.json` and `oid_index.json` sidecars from the final emitted JSON
file set for the compile run. `registry.py` resolves format names to formatter classes —
built-ins first, then entry-point-discovered plugins (see [§3.11 Plugins](#311-plugins)).

---

### 3.6 `compiler.py` — Orchestrator

The only module that knows about all other modules. Everything else is decoupled.

```python
class MibCompiler:
    def __init__(self, config: CompilerConfig | None = None) -> None: ...
    def add_reader(self, reader: FetchProtocol) -> MibCompiler: ...  # fluent
    async def compile(self, *mib_names: str) -> list[CompileResult]: ...
```

**Compile flow:**
```
1. MibResolver.resolve(mib_names)
   → BFS + asyncio.gather(fetch) + synchronous parse wave
   → topological_sort → ResolveResult
2. if JSON output is enabled:
     create one shared JSON artifact metadata block for the compile run
3. for each module in ResolveResult.modules:
     for each formatter in formatters:
       content = formatter.format(module)
       write to output_dir/<name><FILE_SUFFIX>
4. if emit_oid_index and JSON modules were emitted:
     build from the final emitted JSON file set and write output_dir/oid_index.json
5. if emit_manifest and JSON modules were emitted:
     build from the same final emitted JSON file set and write output_dir/manifest.json
6. return list[CompileResult]
```

Formatter errors are non-fatal — captured in `CompileResult.warnings`, logged at WARNING level.

---

### 3.7 `config.py`

```python
@dataclass
class CompilerConfig:
    sources: list[str]           # HTTP URL templates; @mib@ replaced with MIB name
    output_dir: Path             # default: ./mibs-output
    formats: list[str]           # ["json"]
    cache_dir: Path | None       # None disables cache; default: ~/.cache/trishul-smi
    cache_ttl_days: int          # 0 = never expire; default: 7
    max_mib_size: int            # bytes; default: 10 MB
    http_timeout: float          # seconds; default: 30.0
    http_retries: int            # default: 3
    no_texts: bool               # suppress descriptions/org/revisions; default: False
    emit_manifest: bool          # optional manifest.json sidecar; default: False
    emit_oid_index: bool         # optional oid_index.json sidecar; default: False
```

Unknown format names raise `ValueError` at `MibCompiler.__init__` time. Sidecar flags
require `"json"` to be present in `formats`.

---

### 3.8 `errors.py`

Flat hierarchy — no circular imports. All `TYPE_CHECKING`-only annotations use `from __future__ import annotations`.

```
TrishulError
  ├── MibNotFoundError       reader could not locate MIB (triggers ReaderChain fallback)
  ├── MibSizeLimitError      MIB exceeds max_mib_size (propagates immediately)
  ├── ParseError             grammar/syntax error in ASN.1 source
  ├── CircularDependencyError import cycle detected (propagates immediately)
  ├── NetworkError           HTTP/transport failure (not 404)
  ├── CodeGenError           output generation failed
  ├── WriterError            could not write output file
  └── MibCacheError          cache read/write failure
```

---

### 3.9 `cli/`

```
trishul-smi compile [MIB ...] [OPTIONS]
trishul-smi convert FILE.py   [OPTIONS]
trishul-smi version
```

**compile:** constructs a `CompilerConfig` from flags → builds `MibCompiler` with `FileReader` (if `--mib-dir` given) and `HttpReader` (if `--online` or `--source` given) → optionally enables JSON sidecars via `--emit-manifest` / `--emit-oid-index` → calls `compile()` → displays results via Rich table. HTTP is opt-in; running without any source exits with code 2. MIB names may be omitted to auto-discover every MIB file in `--mib-dir` directories. Explicit and discovered names are validated against a safe-character allowlist (`^[A-Za-z0-9][A-Za-z0-9._-]*$`); invalid names exit with code 2 before any fetch.

**convert:** reads a compiled PySNMP `.py` file via `PySNMPReader` → emits JSON via `JsonFormatter`. No network or grammar required.

Exit codes: `0` all compiled — `1` any failure — `2` bad option.

---

### 3.10 Bundle compatibility policy

The JSON IR emitted by trishul-smi — module `*.json` files plus the optional
`manifest.json` and `oid_index.json` sidecars — is consumed by downstream tools
such as `trishul-snmp`. This section pins down the version-compatibility
contract between producer (this package) and consumers.

- **(a) Required metadata block.** Every emitted module JSON and every sidecar
  carries the same four-field metadata block: `schema_version`,
  `producer_version`, `generated_by`, and `generated_at`. These fields have been
  present on every artifact since v0.4.0.
- **(b) `schema_version` semantics.** `schema_version` is bumped ONLY when the
  JSON IR changes in a way that breaks existing consumers: field removals,
  renames, or changed types/semantics of existing fields. Purely additive
  changes — new optional keys that older consumers can safely ignore — do NOT
  bump `schema_version`.
- **(c) `producer_version`.** `producer_version` always equals the trishul-smi
  package version that produced the artifact (see `trishul_smi/version.py`).
  Consumers may use it for diagnostics and bug triage, but compatibility is
  decided by `schema_version` alone.
- **(d) Consumer acceptance rules.** A consumer SHOULD accept any bundle whose
  `schema_version` is less than or equal to its highest supported version, and
  MUST reject bundles whose `schema_version` is higher. Within an accepted
  version, consumers SHOULD ignore unknown (additive) fields rather than
  failing.
- **(e) Version pairing.** The single source of truth for the current schema
  version is the `JSON_IR_SCHEMA_VERSION` constant in
  `trishul_smi/output/json_ir.py`; the contract tests
  (`tests/test_compat_policy.py`) pin emitted artifacts to that constant.

  | `schema_version` | Producer |
  |---|---|
  | `1.1` | trishul-smi ≥ 0.4.0 |

---

### 3.11 Plugins

Custom output formats can be shipped as third-party packages. A plugin is a
class conforming to the structural `FormatterProtocol` (`FILE_SUFFIX` class
attribute + `format(module) -> str | bytes`), registered under the
`trishul_smi.formatters` entry-point group:

```toml
# pyproject.toml of the plugin package
[project.entry-points."trishul_smi.formatters"]
yaml = "my_package.formatters:YamlFormatter"
```

```python
# my_package/formatters.py
from trishul_smi.models.mib_module import MibModule

class YamlFormatter:
    FILE_SUFFIX = ".yaml"

    def format(self, module: MibModule) -> str:
        ...  # render the module however you like
```

**Resolution order** (implemented in `output/registry.py`): built-in formats
(`json`) are checked first; only names that are not built-in trigger
entry-point discovery. A plugin can therefore never shadow a built-in name.

**Broken-plugin policy:** a plugin that fails to import, or whose loaded object
is not a formatter class, is skipped with a logged warning — it never aborts a
compile run. Formatter errors at render time are already non-fatal (captured in
`CompileResult.warnings`), and per-run formatter instances (v0.4.10) keep
concurrent compiles race-free.

**Escape hatch for the v0.5.0 pysnmp removal:** this plugin mechanism is the
supported way to keep producing the old `.py` output after the built-in
`pysnmp` format was removed — anyone who still needs it can ship a
`PysnmpFormatter` as a plugin under this entry-point group. For a working
reference implementation, see the `trishul-smi-plugins` monorepo
(<https://github.com/tosumitdhaka/trishul-smi-plugins>): its `trishul-smi-pysnmp`
package revives the removed `.py` format as a full end-to-end example.

---

## 4. Data Flow — End to End

```
$ tsmi compile IF-MIB -f json --emit-manifest --emit-oid-index --online

cli/main.py
  ├─ CompilerConfig(formats=["json"], ...)
  ├─ MibCompiler(config).add_reader(FileReader(...)).add_reader(http)  # http only if --online
  └─ await compiler.compile("IF-MIB")
        │
        ├─ MibResolver.resolve(["IF-MIB"])
        │     ├─ wave 1: fetch+parse IF-MIB          → discovers [SNMPv2-SMI, SNMPv2-CONF, ...]
        │     ├─ wave 2: asyncio.gather(fetch deps)  → parallel
        │     ├─ wave N: closure complete
        │     └─ Kahn's sort → [SNMPv2-SMI, SNMPv2-CONF, ..., IF-MIB]
        │
        ├─ resolve_oids(modules)  → rewrite all oid/oid_path to absolute numeric paths
        │
        ├─ make shared JSON metadata once per compile() call
        │
        └─ for each module in ordered list:
             JsonFormatter.format(module)     → IF-MIB.json
        │
        ├─ build_oid_index_bytes(...)         → oid_index.json   [optional; final file set]
        └─ build_manifest_bytes(...)          → manifest.json    [optional; final file set]
```

---

## 5. Testing Strategy

| Layer | Tool | Approach |
|---|---|---|
| Models | `pytest` | Instantiation + field validation |
| Parser | `pytest` | Feed MIB text strings, assert `MibModule` shape |
| Readers | `pytest` + `pytest-httpx` | Mock HTTP responses; tmp dirs for file/zip; size limit tests |
| Resolver | `pytest-asyncio` | Mock reader+parser; verify BFS ordering + cycle detection |
| Output | `pytest` | Known `MibModule` → assert JSON/py output structure |
| Compiler | `pytest-asyncio` | Integration: full pipeline with in-memory fixture MIBs |
| CLI | `typer.testing.CliRunner` | Smoke test commands end-to-end |

---

## 6. Internal Dependency Graph

```
cli
 ├── compiler
 │    ├── reader (chain, localfile, httpclient, zipreader)
 │    ├── parser (grammar, transformer, smi_parser)
 │    ├── resolver
 │    │    ├── reader
 │    │    ├── parser
 │    │    ├── oid_resolver
 │    │    └── cache
 │    └── output (json_fmt)
 └── convert (pysnmp_reader)
      └── output (json_fmt)

All modules → models
All modules → errors
No module   → cli
No module   → compiler  (except cli)
```

`models` and `errors` are the only true shared-leaf packages. `reader`, `parser`, `resolver`, and `output` do not import from each other.

---

## 7. Key Design Principles

1. **No `**kwargs` in public APIs** — all options are explicit typed parameters
2. **No circular imports** — `TYPE_CHECKING` guard for forward references in `errors.py`
3. **Async I/O, sync parse/format logic** — readers fetch asynchronously; parser and
   formatters remain synchronous; resolver keeps network I/O concurrent while parsing each
   fetched wave deterministically
4. **No pickle** — disk cache uses `orjson` JSON serialization only
5. **One responsibility per module** — reader fetches, parser parses, resolver resolves, formatter formats
6. **Fail fast, fail clearly** — typed exceptions with descriptive messages; only `MibNotFoundError` is recoverable at the reader level
7. **Size limits enforced at source** — `FileReader` and `HttpReader` both enforce `max_mib_size`
8. **Atomic cache writes** — `rename(2)` on POSIX; no corrupted cache on crash
9. **Protocol-based composition** — `FetchProtocol` and `FormatterProtocol` are structural protocols, enabling duck typing without ABC inheritance
