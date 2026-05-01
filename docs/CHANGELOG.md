# Changelog

All notable changes to `trishul-smi` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] — 2026-05-01

First public release.

### Added

#### Core models (`trishul_smi/models/`)
- `MibModule` — parsed MIB representation with `objects`, `types`, `notifications`, `imports`
- `MibObject` — per-object metadata (OID, syntax, access, status, description, index)
- `MibType` — TEXTUAL-CONVENTION and derived type definitions
- `CompileResult` — per-module result with `status`, `output_paths`, `warnings`, `error`

#### Configuration (`trishul_smi/config.py`)
- `CompilerConfig` dataclass with `__post_init__` validation for all numeric fields
- Defaults: HTTP sources (pysnmp.com + circitor.fr), `~/.cache/trishul-smi`, 7-day TTL, 10 MB size limit

#### Error hierarchy (`trishul_smi/errors.py`)
- `TrishulError` base; flat subclasses: `MibNotFoundError`, `MibSizeLimitError`, `ParseError`,
  `CircularDependencyError`, `NetworkError`, `CodeGenError`, `WriterError`, `MibCacheError`

#### Readers (`trishul_smi/reader/`)
- `AbstractReader` / `FetchProtocol` — structural protocol for type-safe reader composition
- `FileReader` (`localfile.py`) — resolves MIBs from local filesystem directories
- `HttpReader` (`httpclient.py`) — async context manager; httpx + tenacity retries; `time.monotonic()` TTL for in-memory cache
- `ZipReader` (`zipreader.py`) — reads MIBs from in-memory ZIP archives
- `ReaderChain` — fallback chain; only `MibNotFoundError` triggers fallback; all other exceptions propagate
- `trishul_smi.reader` re-exports all four classes for clean top-level imports

#### Parser (`trishul_smi/parser/`)
- Lark grammars: `smiv1.lark` (SMIv1), `smiv2.lark` (SMIv2), `common.lark` (shared tokens)
- `MibTransformer` — Lark tree → `MibModule`; external imports silently skipped
- `SmiParser` — grammar singleton (compiled once), thread-safe `parse()` for `asyncio.to_thread`

#### Resolver (`trishul_smi/resolver/`)
- `MibCache` — orjson serialisation; atomic `put()` via `rename(2)`; mtime-based TTL; corrupted file self-heals on next miss
- `build_dependency_graph` + `topological_sort` — Kahn’s algorithm with `sorted()` for deterministic output; `CircularDependencyError` includes cycle members
- `MibResolver` — BFS import closure; `asyncio.gather(return_exceptions=True)` + `asyncio.to_thread` for concurrent fetch+parse; `MibSizeLimitError` propagates immediately; per-module errors collected in `ResolveResult.errors`

#### Output formatters (`trishul_smi/output/`)
- `JsonFormatter` — structured orjson output; `FILE_SUFFIX = ".json"`
- `PysnmpFormatter` — Jinja2 template; `_pysnmp_obj_class` detects MibTable / MibTableRow / MibScalar; hyphens replaced in Python identifiers; known limitations annotated with `# TODO`

#### Compiler (`trishul_smi/compiler.py`)
- `MibCompiler` — fluent `add_reader()` chain; unknown formats raise `ValueError` at `__init__`; formatter errors are non-fatal (captured in `warnings`, logged at WARNING)

#### CLI (`trishul_smi/cli/`)
- `trishul-smi compile MIB [MIB ...]` — full option set; Rich table output; exit codes 0/1/2
- `trishul-smi version`
- `python -m trishul_smi` entry point
- `--cache-dir ""` to disable cache; `--format` / `--source` / `--mib-dir` all repeatable

#### CI (`/.github/workflows/`)
- `ci.yml` — lint (ruff) + typecheck (mypy) + test matrix (Python 3.10–3.13) with coverage upload
- `release.yml` — test → build → PyPI OIDC trusted publish → GitHub Release on `v*.*.*` tags

### Fixed

- Removed dead `with patch(...) / pytest.raises(AttributeError): pass` block in
  `tests/test_compiler.py` that would have caused CI failure on the formatter-error test.
- Corrected CLI reader import paths (`reader.localfile`, `reader.httpclient`) which were
  written as `reader.file` / `reader.http` — non-existent modules that would have raised
  `ImportError` on first `trishul-smi compile` invocation.

### Known Limitations

- `MibTableColumn` detection in `PysnmpFormatter` requires full OID-tree resolution
  (not yet available at format time). Columns are emitted as `MibScalar`. Planned for v0.2.0.
- TEXTUAL-CONVENTION constraints (DisplayHint, range, enumerations) are partially emitted
  in pysnmp output. Full TC support planned for v0.2.0.
- `ZipReader` does not yet support nested ZIP archives.

[0.1.0]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.1.0
