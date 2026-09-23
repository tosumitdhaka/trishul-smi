# Changelog

All notable changes to `trishul-smi` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.5.0] — 2026-09-23

### Removed

- **pysnmp `.py` output format** (breaking; follow-up to the v0.4.10 deprecation, #24) —
  `PysnmpFormatter` and `-f pysnmp` are removed. Selecting the format now raises an
  actionable error pointing at the JSON bundle output. `tsmi convert` (reading existing
  pysnmp `.py` files) is unaffected.

### Added

- **`tsmi lint`** — MIB validation over the existing resolve pipeline (no new parse path).
  Five checks: `missing-import` (severity is reference-role dependent: type-role references
  are errors, member/OID-role references are warnings), `undefined-type`,
  `unresolvable-oid` (errors); `unused-import`, `duplicate-oid-arc` (warnings). Text and
  JSON output (`--format`), exit codes 0 (clean) / 1 (findings or unresolved modules) /
  2 (usage/config errors).
- **Watch mode (`tsmi compile --watch`)** — debounced mtime polling with no new runtime
  dependencies. On change, recompiles only the changed module and its transitive
  dependents (invalidation set derived from the dependency graph); the watch set is
  cumulative across cycles, so no module ever silently stops being polled. Clean Ctrl-C
  exit with a run summary.
- **Plugin formatters** — third-party output formats via the `trishul_smi.formatters`
  entry-point group. Resolution is built-ins-first (a plugin cannot shadow `json`);
  unknown format names raise an error listing built-in and discovered formats; broken
  plugins are skipped with a warning. Doubles as the escape hatch for the pysnmp `.py`
  removal.

### Fixed

- **Offline-fallback staleness (v0.4.10 review M1)** — a cached misnamed alias served by
  the offline fallback no longer beats a genuinely fetchable source arriving in the same
  wave: the fresh fetch now wins, with the module-name collision warning. A fresh fetch
  that fails to parse keeps the fallback entry (with a warning) instead of emitting a
  contradictory cached-and-failed result pair for the same module.

---

## [0.4.10] — 2026-09-22

### Deprecated

- **pysnmp `.py` output format** (#24; resolves the posture question in #14) — constructing
  `PysnmpFormatter` emits a `DeprecationWarning` and `--format pysnmp` prints a CLI
  deprecation notice. The format is frozen (no behavior fixes; known escaping/`_pyid`
  gaps accepted as-is); use the JSON bundle output (`--format json`, optionally
  `--emit-manifest` / `--emit-oid-index`). Removal is targeted at **v0.5.0**. `tsmi
  convert` (reading existing pysnmp `.py` files) is unaffected.

### Added

- **Offline cache fallback** — when a source is merely not-found (`MibNotFoundError`),
  the resolver serves a warm, non-expired cache entry with a "source unavailable"
  warning, restoring offline/air-gapped compiles lost to v0.4.9's fetch-first
  fingerprinting. Transport failures (`NetworkError`) never fall back.
- **`--reproducible`** — new `CompilerConfig.reproducible` flag + CLI option pins
  `generated_at` to a fixed epoch, making module JSON, `manifest.json`, and
  `oid_index.json` byte-identical across runs.
- **Bundle compatibility policy (#16)** — documented in architecture.md: `schema_version`
  bumps only on breaking IR changes; `producer_version` always equals the package
  version; consumers accept `schema_version` ≤ their highest supported version and must
  reject higher. Contract tests pin the metadata block and the 1.1 ↔ ≥ 0.4.0 pairing.

### Fixed

- **Alias skip-path discard is now warned** — a successfully-fetched file whose declared
  name was already claimed by an earlier file in the wave emits a collision warning on
  the surviving module (was silent first-wins).
- **Nested-zip aggregate cap is recoverable** — budget exhaustion logs a warning and
  yields `MibNotFoundError` (the reader chain falls through to the next source) instead
  of aborting the whole compile; per-entry `max_mib_size` overruns remain fatal.
- **Non-UTF-8 MIB files** — local files that are not valid UTF-8 are decoded losslessly
  as latin-1 with a logged warning (was silent `errors="replace"` corruption).
- **`ReaderChain`** no longer relies on an `assert` that vanishes under `python -O`.
- **Failed `compile()` no longer poisons the compiler** — a `compile()` rejected before
  any work (no readers registered) leaves `add_reader()` usable; the documented
  `RuntimeError` guard applies only after a real compile.

### Removed

- **`MibModule.source_text`** (never populated; Python-API removal).
- **`JsonFormatter.set_artifact_metadata()`** (dead API since v0.4.9's per-run formatters).
- **`parser/grammar/common.lark`** (imported by neither grammar; terminals triplicated).

### Changed

- **Lazy `HttpReader` imports** — `import trishul_smi` no longer pulls httpx into the
  process (PEP 562 lazy attribute in both `trishul_smi` and `trishul_smi.reader`);
  `from trishul_smi import HttpReader` still works.
- **`add_reader()` now actually raises `RuntimeError`** after `compile()` has been
  invoked (was documented but unimplemented).
- `_compact_int_arrays` safety invariant documented + adversarial regression tests
  (emitted bytes unchanged).

### Known Limitations

- **Offline-fallback staleness edge case**: if a cached misnamed alias is served offline
  in the same wave in which the genuine module's source becomes fetchable, the stale
  cached entry wins and the fresh fetch is discarded (with warnings). Narrow, disclosed
  by three explicit warnings, tracked for v0.5.0.
- FileReader (latin-1 decode) and HttpReader (UTF-8 decode) produce different
  fingerprints for the same non-UTF-8 bytes — a cache entry written via one misses via
  the other.

---

## [0.4.9] — 2026-09-22

### Fixed

- **Off-loop parsing (#19)** — `MibResolver` now runs `SmiParser.parse` via
  `asyncio.to_thread`, so CPU-bound Lark parsing no longer stalls the event loop (and all
  concurrent coroutines) in embedded async services. CLAUDE.md / AGENTS.md /
  architecture.md now describe the thread-pool offload as reality (#23, item 1).
- **Concurrent-compile safety (#20)** — `MibCompiler.compile()` no longer mutates shared
  formatter state (per-run `JsonFormatter` instances carry each run's artifact metadata);
  `MibCache` writes use `tempfile.mkstemp` (a predictable `.tmp` name could interleave
  bytes from two processes sharing a cache dir and corrupt an entry through the atomic
  rename); cache reads treat any `OSError` as a miss instead of crashing the compile.
- **Cache staleness (#12)** — compiled-module cache entries record a sha256 fingerprint
  of the source text; the resolver fetches the source first and parses only on a
  fingerprint miss, so an updated MIB file can never serve a stale entry.
- **Alias edge cases (#25)** — two requested files declaring the same module name now emit
  a collision warning on the surviving module; requesting both a misnamed file and its
  declared name no longer produces a contradictory `compiled` + `missing` pair (single
  consistent result, exit 0).

### Added

- **`cached` compile status (#15)** — modules served from the compiled-module cache
  report `status="cached"` (CLI renders ♻ and an `N cached` summary segment); a success
  state — the exit-code contract is unchanged.
- **Nested-zip aggregate scan cap** — nested-archive extraction per top-level fetch is
  capped at 4 × `max_mib_size` (v0.4.8 review residue: per-entry bounds alone left
  unbounded time/churn for many-small-nested-zip archives).

### Changed

- **HttpReader: raw-body cache removed (breaking Python-API change)** — the `cache_dir`
  constructor parameter is removed (the raw cache had been write-only since the v0.4.8
  ETag removal); `cache_ttl_days` is accepted but deprecated (`DeprecationWarning`; use
  `CompilerConfig.cache_ttl_days`, which drives the compiled-module cache).
- **Cache-hit behavior (#12 trade-off)** — a warm cache no longer avoids the source fetch
  (the cache saves parsing only), and a warm cache can no longer serve a compile when the
  source is unreachable. Both are consequences of fetch-first fingerprint checking.

### Known Limitations

- Offline/air-gapped recompiles from a warm cache are no longer possible (fetch-first
  fingerprint design); a fallback that serves the cached module with a warning when the
  source fetch fails is tracked in the v0.4.10 plan.
- Aggregate nested-zip exhaustion raises `MibSizeLimitError` for the whole run; a
  legitimate bundle of many small nested zips can trip the 4× heuristic (tracked in the
  v0.4.10 plan).

---

## [0.4.8] — 2026-09-22

### Fixed

- **ZipReader nested-archive size enforcement** — nested archive reads are bounded by
  `max_mib_size` at every depth; a nested zip bomb raises `MibSizeLimitError` before
  extraction instead of exhausting memory (#17).
- **HttpReader fetch semantics** — responses are consumed as a stream and aborted as soon
  as the byte count exceeds `max_mib_size` (previously the full body was buffered before
  the check, so a chunked or lying server defeated the limit); a 404/410 on the GET is
  authoritative for `MibNotFoundError` — the HEAD pre-check that misreported
  HEAD-challenged servers as "not found" is gone (#18).
- **`--online` help text** — now names the actual default sources (`mibs.pysnmp.com` +
  `mibbrowser.online`) (#23, item 2).

### Changed

- **HttpReader ETag/304 machinery removed** — the in-memory ETag cache could never fire
  across runs (the reader lives for one compile); dead code deleted. The compiled-module
  `MibCache` remains the effective cross-run cache (#18).
- **HTTP redirects are followed** (`follow_redirects=True`): a 301/302 to an existing MIB
  resolves instead of surfacing as `NetworkError` (review follow-up to #18).

### Security

- **MIB-name validation at the CLI boundary** — explicit and discovered names are
  validated against `^[A-Za-z0-9][A-Za-z0-9._-]*$`; path traversal
  (`trishul-smi compile ../../etc/passwd -d .`) and URL-steering names are rejected with
  exit code 2 before any fetch (#22). First catch in the wild: a junk-named `$.mib` in the
  local corpus (real content: UUID-TC-MIB), since renamed.

### Known Limitations

- The raw-body disk cache under `cache_dir/raw/` is write-only (nothing reads it back);
  removal is planned for v0.4.9.
- Nested-zip scanning is bounded per entry but not in aggregate — an archive with many
  small nested zips can still cause bounded-memory time/disk churn (tracked for v0.4.9).

---

## [0.4.7] — 2026-09-22

### Fixed

- **Quote/comment-aware macro stripping** — the words `MACRO`/`END` inside `DESCRIPTION`
  strings or `--` comments can no longer trigger macro-body stripping, which previously
  swallowed module content or injected a bare `END` token. The tightened
  `MACRO ::= BEGIN` anchor also stops module names containing `MACRO` (e.g. `X-MACRO-MIB`)
  from being mistaken for macro assignments (#11).
- **SMIv1 TRAP-TYPE completeness** — `TRAP-TYPE` definitions now retain `ENTERPRISE`
  (symbolic or numeric), the full resolved OID (enterprise chain + trap number),
  `DESCRIPTION`, and the `VARIABLES` list in compiled JSON output; all fields round-trip
  through the compiled-module cache (#13).
- **Declared-name reconciliation for misnamed files** — a module whose declared name
  differs from the name it was requested under is re-keyed by its declared name, recorded
  as an alias, and surfaced with a pysmi-style warning. Fixes phantom fetches,
  falsely-blocked dependents (importing either the requested or the declared name),
  inconsistent `CompileResult` naming / `is_dependency`, and cache misses on later runs
  (#21).

### Changed

- **Imports-driven dialect detection** — dialect is decided from `FROM` import targets on
  a quote/comment-masked copy of the source, so a comment or `DESCRIPTION` mentioning
  `SNMPv2-SMI` can no longer force the SMIv2 grammar onto an SMIv1 module. Root SMIv2
  modules without any imports (e.g. `SNMPv2-SMI` itself) fall back to SMIv2-only
  construct keywords (part of #24; remaining #24 items planned for v0.4.10).

### Added

- `MibObject.enterprise` and `MibObject.trap_number` model fields, serialised through
  the compiled-module cache.

### Known Limitations

- Two requested files declaring the same module name compile once under that name; the
  earlier-fetched duplicate is silently dropped (warning/alias surfacing is planned for
  the v0.4.9 robustness release).
- Requesting both a misnamed file and its declared name in one call can yield a
  contradictory `compiled` + `missing` result pair for the same module.

---

## [0.4.6] — 2026-08-05

### Fixed

- **Vendor MIB compatibility** — two non-standard but common ASN.1 shorthand forms that
  strict validators (libsmi, `smilint`) reject are now accepted leniently instead of
  failing to parse. These appear in Ericsson and other vendor MIBs:
  - `OCTET STRING (0..30)` — a bare range written without the `SIZE` keyword (standard SMIv2
    requires `OCTET STRING (SIZE (0..30))`). Reinterpreted as a size constraint, the only
    sensible reading for an octet string's length. Same leniency applies to `Opaque`.
  - `BIT STRING { start(1), ... }` — the ASN.1 singular form, accepted as an alias for the
    SMIv2 `BITS { ... }` construct.

### Added

- **`MibModule.warnings`** — new `list[str]` field (default `[]`) carrying non-fatal parser
  warnings. Populated when non-standard vendor syntax is accepted leniently; each warning
  includes the source line number (e.g. `line 129: OCTET STRING range written without SIZE
  keyword — treated as size constraint (non-standard)`).
- **Warning pipeline** — warnings now flow transformer → `MibModule.warnings` → `MibCache`
  (serialised so cached and fresh compiles are consistent) → `CompileResult.warnings` → CLI.
  The CLI shows a per-module warning count in the result table and lists full details below
  it; the summary line reports `N with warnings`.

### Changed

- `SmiParser` now builds Lark parsers with `propagate_positions=True` so transformer methods
  can attach accurate source line numbers to warnings.

---

## [0.4.5] — 2026-05-15

### Added

- **`CompileResult.missing_dependencies`** — new `list[str]` field (default `[]`) that names
  the missing modules directly on the result, eliminating the need for callers to parse
  `error` strings. For `status="failed"` (blocked) results it lists the unresolved non-base
  imports; for `status="missing"` results it contains the module name itself.
- **`CompilerConfig.dry_run`** — new `bool` flag (default `False`). When `True`, the compiler
  resolves and parses all modules normally (so `missing_dependencies` detection is accurate)
  but skips all file writes: no module JSON, no `manifest.json`, no `oid_index.json`,
  and no output-directory creation. `output_paths` is empty on every result.
- **Public Python API exports from `trishul_smi`** — `MibCompiler`, `CompilerConfig`,
  `CompileResult`, `FileReader`, `HttpReader`, `ZipReader`, and the full error hierarchy
  (`TrishulError`, `MibNotFoundError`, `ParseError`, `CircularDependencyError`,
  `WriterError`, `MibCacheError`) are now importable directly from `trishul_smi` without
  reaching into internal submodules.

---

## [0.4.4] — 2026-05-13

### Fixed

- **`AGENT-CAPABILITIES` `ACCESS not-implemented` compatibility** — SMIv2 `VARIATION`
  clauses now accept `ACCESS not-implemented`, fixing Juniper capability modules such as
  `JNX-IP-CAPABILITY`, `JNX-SNMPv2-CAPABILITY`, `IPMCAST-MIB-CAPABILITY`, and
  `MPLS-LSR-STD-CAPABILITY`.
- **Lowercase local type-reference compatibility** — SMIv2 now accepts lowercase local
  type names in type assignments and references such as `SYNTAX SEQUENCE OF
  pgwApnSaccRatingGroupStats` and `SYNTAX pgwApnSaccRatingGroupStats`, fixing
  `GGSN-MIB` and the remaining StandardMibs local-corpus parser blocker.

## [0.4.3] — 2026-05-08

### Added

- **CLI sidecar emission flags** — `tsmi compile` now exposes `--emit-manifest` and
  `--emit-oid-index`, bringing CLI parity to the existing optional JSON bundle sidecars.
  Both flags remain additive, both still require JSON output, and both describe the final
  emitted JSON file set for the compile run.

### Fixed

- **`SNMPv2-TC` preserved-source import compatibility** — the parser now accepts
  built-in ASN.1 symbol names in `IMPORTS` clauses, including multi-token forms such
  as `OCTET STRING` and `OBJECT IDENTIFIER`, fixing bundled `SNMPv2-TC` variants that
  still carry the `TEXTUAL-CONVENTION MACRO` source text.

### Known Limitations

- **Full MACRO-body parsing remains out of scope** — `trishul-smi` still handles
  `MACRO ... END` blocks through preprocessing rather than grammar-level ASN.1 macro
  parsing. This release fixes the real `SNMPv2-TC` import failure ahead of that
  preprocessing step; it does not add general support for preserving and parsing
  arbitrary MACRO notation bodies.

## [0.4.2] — 2026-05-07

### Changed

- **PyPI maturity classifier** — package metadata now publishes
  `Development Status :: 4 - Beta` instead of `3 - Alpha`, reflecting the
  stabilized `0.4.x` runtime contract, real-corpus validation, and clean
  release path through `0.4.1`.

## [0.4.1] — 2026-05-07

### Changed

- **JSON IR schema version `1.1`** — the runtime JSON contract now reflects the `0.4.1`
  hotfix shape for canonical `oid` emission and object-valued `oid_index.json` entries.
- **Final emitted file set semantics for sidecars** — `manifest.json` and `oid_index.json`
  are now derived from the final emitted module file set for a compile run, so overlapping
  alias inputs no longer create duplicate manifest entries or self-colliding OID-index data.

### Fixed

- **Same-module forward OID references** — `resolve_oids()` now revisits unresolved objects
  within a module, so definitions such as `MODULE-IDENTITY ::= { laterDefinedNode 1 }`
  resolve before JSON emission.
- **Canonical runtime OIDs in module JSON** — `oid_path` remains the authoritative runtime
  representation, and `oid` is now emitted only when a fully resolved numeric dotted string
  can be derived from it. Symbolic-relative values such as `hrMIBAdminInfo.1` are no
  longer emitted in runtime JSON.
- **`oid_index.json` runtime contract** — sidecar entries are now object-valued rather than
  singleton arrays, keyed only by canonical numeric OIDs. Ambiguous duplicate OIDs are
  omitted from the sidecar instead of forcing consumers to pick an arbitrary winner.
- **Wrapped inline comment continuations in real IETF MIBs** — the parser now normalizes
  narrow wrapped comment-text continuations before grammar parsing without letting indented
  standalone `-- ...` comment lines swallow the next assignment or `CHOICE` member,
  fixing `HPR-MIB`, the dependent `HPR-IP-MIB`, and the remaining `SNMPv2-*` /
  `HOST-RESOURCES-MIB` parser blockers seen in the local corpus.
- **`SNMPv2-PDU` compatibility grammar** — symbolic range bounds, anonymous `CHOICE`
  members inside `SEQUENCE`, and constrained `SEQUENCE (SIZE (...)) OF ...` forms now parse
  and preserve their runtime constraints in emitted JSON.

## [0.4.0] — 2026-05-07

### Added

- **Versioned JSON IR metadata** — module JSON now carries `schema_version`,
  `producer_version`, `generated_by`, and `generated_at`, with one shared `generated_at`
  value reused across every JSON artifact emitted in a single compile run.
- **Optional JSON bundle sidecars** — `CompilerConfig` now exposes `emit_manifest` and
  `emit_oid_index` flags, both defaulting to `False` and both requiring `"json"` in
  `formats`.
- **`manifest.json` bundle inventory** — optional deterministic sidecar listing only
  successfully emitted JSON modules and referencing companion sidecars by filename.
- **`oid_index.json` reverse lookup artifact** — optional OID-to-entry accelerator derived
  from emitted module JSON, with list-valued entries from day one so duplicate OIDs remain
  representable without changing the format.

### Changed

- **Documentation layout and API docs** — package/runtime docs now live under `docs/`,
  GitHub community files live under `.github/`, and the new `docs/python-api.md` documents
  library embedding and optional JSON sidecars.

### Fixed

- **Tagged ASN.1 type assignments** — both grammars now accept application-tagged type
  definitions such as `IpAddress ::= [APPLICATION 0] IMPLICIT OCTET STRING (SIZE (4))`,
  explicit `SNMPv2-SMI` compiles now succeed, and the transformer preserves the
  underlying base-type constraints on the emitted `MibType`.
- **Resolver parse-wave deadlock on real MIB corpora** — resolver waves still fetch
  concurrently, but now parse fetched modules deterministically after the fetch phase
  instead of offloading parse through `asyncio.to_thread`, fixing hangs seen in the CLI
  `asyncio.run()` path on explicit base-MIB and local-corpus compiles.

## [0.3.1] — 2026-05-06

### Fixed

- **CLI failure semantics for `missing` results** — `trishul-smi compile` now exits with code
  `1` when any module result is `missing` or `failed`, so automation no longer treats
  incomplete compile runs as success.
- **CLI reader option wiring** — the async compile path now passes configured
  `max_mib_size`, HTTP timeout, retry count, cache directory, and cache TTL into the
  `FileReader` and `HttpReader` instances it constructs.
- **HTTP failure classification** — `HttpReader` now keeps genuine all-source misses as
  `MibNotFoundError`, while transport failures and non-404 HTTP exhaustion surface as
  `NetworkError` instead of being collapsed into `"missing"`.
- **Blocked dependent emission** — modules whose non-base imports fail to resolve are no
  longer emitted as `compiled`; they are marked `failed`, their output files are skipped,
  and the underlying missing dependency remains reported separately.
- **Warm-cache dependency discovery** — resolver cache hits now continue to expand transitive
  imports, so repeated compiles do not strand cached top-level modules with unresolved
  uncached dependencies.

### Known Limitations

- Bundle sidecars and explicit JSON IR versioning are not part of `0.3.1`; `manifest.json`,
  `oid_index.json`, and schema-version metadata remain planned for `0.4.0`.

---

## [0.3.0] — 2026-05-06

### Added

- **`class` field on all JSON objects and types** — pysmi-compatible lowercase class string
  (e.g. `"objecttype"`, `"textualconvention"`, `"notificationtype"`) on every entry in
  `objects`, `types`, and `notifications`.
- **`nodetype` field on OBJECT-TYPE** — two-pass OID-tree walk classifies each object as
  `"table"`, `"row"`, `"column"`, or `"scalar"`.
- **`members` list on conformance objects** — `OBJECT-GROUP`, `NOTIFICATION-GROUP`,
  `MODULE-COMPLIANCE`, and `NOTIFICATION-TYPE` entries carry their `OBJECTS`/`NOTIFICATIONS`
  member list, resolved to `{"module": "...", "object": "..."}` dicts.
- **TC `display_hint` and `status` in JSON** — both fields now emitted in the `types` section.
- **`module_metadata` block always emitted** — `lastupdated` (ISO 8601), `revisions`,
  `organization`, `contactinfo`, `description` in JSON output. Text fields suppressed by
  `--no-texts`; structural fields (`lastupdated`, `revisions[].date`) always present.
- **`--no-texts` for JSON** — `JsonFormatter` now honours the flag; suppresses `description`,
  `organization`, `contactinfo`, and per-revision descriptions.
- **`"missing"` compile status** — `MibNotFoundError` produces `status="missing"` instead of
  `"failed"`, distinguishing unfindable transitive dependencies from parse/format errors. CLI
  shows them as dimmed `–` rows and excludes them from the failure exit code.
- **Standard `mibBuilder` guard in pysnmp output** — compiled modules use
  `if 'mibBuilder' not in globals(): ...` instead of instantiating `MibBuilder()`.
- **`.setObjects()` on NOTIFICATION-TYPE in pysnmp output** — `OBJECTS` clause wired through
  transformer → `MibObject.members` → Jinja2 template.
- **MACRO body preprocessing** (`_strip_macro_bodies`) — `MACRO...END` blocks stripped before
  grammar parsing, eliminating the Earley parser fallback that caused ~10× slower cold parse
  on MIBs importing MACRO definitions from `SNMPv2-SMI`.
- **Grammar: SMIv1 EXPORTS clause** — `EXPORTS foo, bar ;` now parsed correctly.
- **Grammar: CHOICE type in syntax** — `SYNTAX CHOICE { ... }` handled in both grammars.
- **Grammar: BITS in SEQUENCE fields** — bare `BITS` as a SEQUENCE member type now accepted.
- **Grammar: DEFVAL variants** — negative integers (`DEFVAL { -1 }`), multi-name BITS sets
  (`DEFVAL { { bit1, bit2 } }`), and OID-style values (`DEFVAL { 0 6 }`) now parse correctly.
- **Grammar: INTEGER range in type assignments** — `INTEGER (0..65535)` in `TYPE ::= INTEGER
  (range)` now parses in both grammars.
- **`BASE_MIBS` explicit-request bypass** — `SNMPv2-SMI`, `RFC1213-MIB`, and friends compile
  normally when explicitly requested; the filter now applies only to transitive dependencies.
- **`import_reverse_map()` on `MibModule`** — shared utility (inverts the imports dict to
  `symbol → source_module`) replacing duplicate inline loops in both formatters.

### Performance

- Cold compile of a 380-MIB IETF/IANA corpus: ~9.8 s mean (pysmi 2.0.0: ~87 s, ~9× faster).
  Warm (cache-hit) compile: ~1.4 s mean (~62× faster than pysmi cold).
- Root cause of previous slow path eliminated: MACRO preprocessing keeps Lark in LALR(1) mode
  for all tested real-world MIBs.

### Fixed

- 35 → 6 corpus parse failures on 380-MIB IETF/IANA corpus. The remaining 6 also fail in
  pysmi 2.0.0.

[0.3.0]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.3.0
[0.3.1]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.3.1
[0.4.0]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.0
[0.4.1]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.1
[0.4.2]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.2
[0.4.3]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.3
[0.4.5]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.5
[0.4.4]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.4

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
[0.4.6]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.6
[0.4.7]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.7
[0.4.8]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.8
[0.4.9]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.9
[0.4.10]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/v0.4.10
