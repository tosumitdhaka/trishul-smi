# JSON Bundle Contract

`trishul-smi` JSON output is designed so downstream runtimes can consume compiled module
JSON directly. Runtime correctness does not depend on raw MIB files, raw MIB directories,
or importing `trishul-smi`.

---

## Core Contract

- A single compiled module JSON file such as `IF-MIB.json` is a valid usable artifact.
- A bundle is a logical set of compiled module JSON files, not a requirement for
  `manifest.json` plus `oid_index.json`.
- Valid bundle shapes include a single JSON file, a directory of JSON files, a directory of
  JSON files plus `manifest.json`, and a directory of JSON files plus both sidecars.
- Module JSON is the source of truth. Sidecars are derived metadata.
- When sidecars are emitted, they describe the final emitted JSON file set for that
  `compile()` run.
- `oid_path` is the canonical runtime OID representation.
- `oid`, when emitted, is the matching numeric dotted string derived from `oid_path`.
- A single module JSON file is therefore a valid degenerate bundle.

---

## Shared Metadata

Every JSON artifact emitted in one `compile()` call shares the same metadata values:

- `schema_version`
- `producer_version`
- `generated_by`
- `generated_at`

`producer_version` tracks the installed `trishul-smi` package version that produced the
artifacts.

Module JSON remains authoritative for object, type, notification, and module metadata.

---

## `manifest.json`

- Optional bundle metadata emitted only when `CompilerConfig.emit_manifest=True`.
- Lists only successfully emitted module JSON files.
- Lists each final emitted module file at most once, even when multiple requested or
  discovered source names compile to the same `MODULE.json`.
- Uses filenames rather than absolute paths so a bundle stays relocatable.
- References `oid_index.json` in `sidecars` when both sidecars are emitted.
- Emitted only when at least one JSON module artifact was successfully written.

`manifest.json` improves deterministic discovery, but it is not required for correctness.

---

## `oid_index.json`

- Optional reverse-lookup accelerator emitted only when
  `CompilerConfig.emit_oid_index=True`.
- Derived from the emitted module JSON files, not an independent source of truth.
- Derived from the same final emitted module file set described by `manifest.json`.
- Uses the canonical numeric dotted runtime OID as each lookup key.
- Maps each unique OID to one object entry containing runtime-visible lookup data such as
  `module`, `object`, `class`, `object_type`, and `nodetype` for `OBJECT-TYPE` entries.
- Alias or duplicate source inputs must not create self-collisions that drop otherwise valid
  OID entries from the sidecar.
- Omits ambiguous duplicate OIDs rather than forcing consumers to pick an arbitrary winner.
- Emitted only when at least one JSON module artifact was successfully written.

`oid_index.json` improves lookup speed, but it is not required for correctness.

---

## Python API Example

```python
import asyncio
from pathlib import Path

from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.reader import FileReader

async def main() -> None:
    config = CompilerConfig(
        output_dir=Path("./out"),
        formats=["json"],
        emit_manifest=True,
        emit_oid_index=True,
    )
    compiler = MibCompiler(config).add_reader(FileReader("/usr/share/snmp/mibs"))
    results = await compiler.compile("IF-MIB")

asyncio.run(main())
```

If both flags are left at their defaults, `trishul-smi` emits only per-module JSON files.
These sidecars may be enabled either through `CompilerConfig` or through the CLI flags
`--emit-manifest` and `--emit-oid-index`.

## CLI Example

```bash
tsmi compile IF-MIB --mib-dir /usr/share/snmp/mibs --emit-manifest --emit-oid-index
```
