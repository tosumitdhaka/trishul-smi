# Python API

Use the Python API when `trishul-smi` is embedded as a library instead of driven
through the CLI.

---

## Core Types

- `CompilerConfig` configures output formats, caching, HTTP behavior, text suppression,
  and optional JSON sidecars.
- `MibCompiler` orchestrates reading, parsing, resolving, and formatting.
- `FileReader`, `HttpReader`, and `ZipReader` provide MIB sources and can be combined on
  one compiler instance.

---

## Basic Usage

```python
import asyncio
from pathlib import Path

from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.reader import FileReader, HttpReader


async def main() -> None:
    config = CompilerConfig(
        output_dir=Path("./out"),
        formats=["json", "pysnmp"],
    )

    compiler = MibCompiler(config).add_reader(FileReader("/usr/share/snmp/mibs"))

    async with HttpReader(*config.sources) as http:
        compiler.add_reader(http)
        results = await compiler.compile("IF-MIB", "IP-MIB")

    for result in results:
        print(result.name, result.status, result.output_paths)


asyncio.run(main())
```

---

## Compile Results

`compile()` returns `list[CompileResult]`, including requested modules and resolved
dependencies.

- `compiled`: the module was parsed and any requested outputs were written.
- `missing`: the module could not be found in any configured reader.
- `failed`: parsing, resolution, network, or output emission failed.

Each result also carries `output_paths`, `warnings`, `error`, and `is_dependency`.

---

## JSON Sidecars

For library use, module JSON is the atomic artifact. Optional sidecars are enabled via
`CompilerConfig` flags:

```python
config = CompilerConfig(
    output_dir=Path("./out"),
    formats=["json"],
    emit_manifest=True,
    emit_oid_index=True,
)
```

- `emit_manifest=True` writes `manifest.json`.
- `emit_oid_index=True` writes `oid_index.json`.
- Both flags default to `False`.
- Both require `"json"` in `formats`.
- Sidecars are emitted only after at least one JSON module is successfully written.
- Sidecars describe the final emitted JSON file set for the compile run, so overlapping
  source aliases do not create duplicate manifest rows or false duplicate-OID collisions.
- Module JSON and any emitted sidecars share one `generated_at` value per `compile()` run.

See [JSON Bundle Contract](json-bundles.md) for the runtime guarantees around these files.
