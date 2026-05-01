# Changelog

All notable changes to `trishul-smi` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] — 2026-05-01

### Added

- **Full OID resolution** (`resolver/oid_resolver.py`): all `MibObject.oid` / `oid_path`
  fields are rewritten to absolute numeric paths after the dependency graph is resolved.
  Seeds well-known SNMP roots (`mib-2`, `enterprises`, `snmpTraps`, etc.).
- **`MibTableColumn` detection**: two-pass OID tree walk in `PysnmpFormatter` correctly
  classifies table columns as `MibTableColumn` instead of `MibScalar`.
- **`setIndexNames` / AUGMENTS**: `INDEX { ... }` emits `setIndexNames()`; `AUGMENTS { row }`
  emits `setIndexNames(*row.getIndexNames())`.
- **`ModuleIdentity.setRevisions()`**: revision dates extracted from the transformer and
  emitted in pysnmp output.
- **`setOrganization` / `setDescription` on MODULE-IDENTITY**: previously omitted.
- **`setDescription` on OBJECT-GROUP, NOTIFICATION-GROUP, MODULE-COMPLIANCE,
  AGENT-CAPABILITIES**: `_simple_oid_object` now extracts status and description.
- **`setDescription` on NOTIFICATION-TYPE**: emitted in pysnmp output.
- **Full TEXTUAL-CONVENTION class generation**: proper Python subclasses with
  `subtypeSpec`, `displayHint`, `status`, `description`. Constraint expressions for
  `size`, `range`, `enum`, `bits`, and `union` kinds including multi-range
  `ConstraintsUnion`.
- **Per-OBJECT-TYPE inline constraint wrappers**: objects with inline SYNTAX constraints
  (e.g. `Integer32 (0..65535)`, `DisplayString (SIZE (0..255))`) emit a
  `class _Name_Type(Base): subtypeSpec = ...` wrapper, matching pysmi output exactly.
- **Constraints on all constrainable builtin types**: `Counter32`, `Counter64`, `Gauge32`,
  `Unsigned32`, `TimeTicks`, `Opaque`, `Integer32` now carry their constraint through
  the parser alongside `INTEGER` and `OCTET STRING`.
- **`exportSymbols` single-dict format**: one `exportSymbols()` call with all objects,
  notifications, and TCs merged into a single `**{...}` dict.
- **`--no-texts` flag**: suppresses `setDescription`, `setOrganization`, `setRevisions`,
  and TC `description =` for leaner output modules.
- **`is_dependency` flag on `CompileResult`**: requested MIBs vs transitive deps are now
  distinguished; dependency rows shown dimmed in CLI output.
- **`tsmi convert FILE.py`**: reverse-converts a compiled pysmi `.py` module to JSON
  using Python `ast` — no SMI grammar required.
- **Directory compile mode**: `tsmi compile -d /path/to/mibs` without explicit MIB names
  auto-discovers and compiles every MIB file in the directory.
- **SNMPv2-CONF symbol name mapping**: `OBJECT-GROUP` → `ObjectGroup`,
  `MODULE-COMPLIANCE` → `ModuleCompliance`, etc. — correctly maps SMI macro keyword
  names to the Python class names exported by pysnmp's `SNMPv2-CONF`. Scoped to
  `SNMPv2-CONF` imports only; all other modules pass through unchanged.

### Fixed

- **Union constraint sub-items** stored as `_ConstraintInfo` objects instead of dicts
  caused `AttributeError: '_ConstraintInfo' object has no attribute 'get'` when
  rendering TCs with multi-range constraints (e.g. `DateAndTime` in `SNMPv2-TC`).
  Fixed via `_ConstraintInfo.to_dict()` which recursively serialises nested constraints.
- **Lowercase hex range bounds** (`'ffffffff'h`): grammar regex only accepted uppercase
  `H`; now accepts `[Hh]`. Fixes `UDP-MIB` parse failure.
- **`BASE_MIBS` not skipped on direct request**: `SNMPv2-SMI` and friends were already
  skipped as transitive dependencies but failed with a parse error when explicitly
  requested (e.g. auto-discovered from a directory). Now filtered at the start of
  `resolve()`.
- **`SNMPv2-SMI-v1` / `SNMPv2-TC-v1`** added to `BASE_MIBS`; these V1SMI shim names
  appeared as unresolvable dependencies in vendor MIBs.
- **OID resolution idempotency**: `oid_parent` is cleared after successful resolution so
  warm-cache re-runs do not double-prepend the parent path.
- **`snmpTraps` OID** added to `WELL_KNOWN_OIDS` so `linkDown`/`linkUp`
  NOTIFICATION-TYPEs resolve correctly without `SNMPv2-MIB` in the compile set.

### Changed

- `tests/tmp/` excluded from ruff linting (generated output files).
- `trishul_smi/output/pysnmp_fmt.py` excluded from ruff E501 (Jinja2 template strings
  cannot be wrapped).

[0.2.0]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.2.0

---

## [0.1.2] — 2026-05-01

### Added

- `tsmi` command alias — shorter alternative to `trishul-smi` installed alongside it.

[0.1.2]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.1.2

---

## [0.1.1] — 2026-05-01

### Changed

- Default HTTP fallback source replaced: `circitor.fr` → `mibbrowser.online`
  (`https://mibbrowser.online/mibs/@mib@.mib`). circitor.fr issued 301 redirects
  that the HTTP client did not follow reliably.
- HTTP fetching is now **opt-in**. `trishul-smi compile` no longer contacts the
  network by default. Pass `--online` to enable HTTP sources, or `--source URL`
  to use a custom source. Running without either and without `--mib-dir` now
  exits with code 2 and a clear error message.

### Fixed

- Grammar now parses `named_type` with SIZE constraints (e.g., `DisplayString (SIZE (0..255))`).
  This was blocking `IF-MIB` from compiling on the first `pip install` run.
- Grammar now parses range constraints on all numeric builtin types (`Unsigned32`, `Gauge32`,
  `Counter32`, `Counter64`, `TimeTicks`), not just `INTEGER` and `Integer32`.
  This was blocking `IP-MIB` (`Unsigned32 (0..65535)`).
- Grammar now accepts negative values in `INTEGER` enumeration items and range bounds
  (e.g., `INTEGER { reserved(-2), low(-1), medium(0) }`).
  This was blocking `IP-MIB` on the pysnmp.com source.
- Dialect detection no longer false-positives on `SNMPv2-TC-v1` (was matching
  `SNMPv2-TC` as a substring, causing V1SMI files to be parsed as SMIv2).
- `TRAP-TYPE` now accepts lowercase identifiers as the trap name (e.g.,
  `ciscoEpmNotificationAlarm TRAP-TYPE ...`), matching real Cisco MIBs.

[0.1.1]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.1.1

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
- Lark 1.3.1 grammar compatibility: flattened multi-line rule bodies to single lines;
  replaced `rule?` with `[rule]`; added `!` prefix to `status_value` and `access_value`
  so anonymous string tokens are kept as transformer children.
- `object_identity_assignment` grammar rule now uses `description_clause` (was inline
  `"DESCRIPTION" QUOTED_STRING`), fixing a bug where `description` was always `None` for
  OBJECT-IDENTITY objects.
- Resolver now skips fetching well-known SNMP base MIBs (`SNMPv2-SMI`, `SNMPv2-TC`,
  `RFC1213-MIB`, etc.) which are built into pysnmp and not available as standalone files.
- `MibCache.put()` now wraps `OSError` in `MibCacheError` instead of leaking the raw exception.
- `VALID_FORMATS` is now a single source of truth in `config.py`; `compiler.py` imports it
  rather than defining its own copy. Error message aligned to `"Unknown output format(s):"`.
- `HttpReader` 304-without-cache fallback now routes through `_fetch_url_with_retry()`
  instead of a bare `client.get()` call, ensuring retry policy applies on the fallback.
- CLI mib-dir non-existence warning is now emitted before `_compile_async()` so it is
  visible when `_compile_async` is patched in tests.
- `KeyboardInterrupt`/`SystemExit` returned by `asyncio.gather(return_exceptions=True)` are
  now re-raised immediately instead of being silently collected in `ResolveResult.errors`.
- `PysnmpFormatter` now includes `NOTIFICATION-TYPE` objects in `exportSymbols`.
- CI test job now enforces `--cov-fail-under=95`.

### Known Limitations

See [roadmap.md](roadmap.md) for the full list of planned v0.2.0 improvements.

[0.1.0]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.1.0
