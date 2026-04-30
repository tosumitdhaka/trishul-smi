# trishul-smi

> A clean, modern SNMP MIB compiler written in Python.

[![PyPI](https://img.shields.io/pypi/v/trishul-smi)](https://pypi.org/project/trishul-smi/)
[![Python](https://img.shields.io/pypi/pyversions/trishul-smi)](https://pypi.org/project/trishul-smi/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`trishul-smi` fetches, parses, and compiles SNMP MIB definitions (SMIv1 and SMIv2)
into structured JSON or pysnmp-compatible Python modules.  
It resolves transitive imports automatically, caches compiled modules on disk,
and ships a CLI that works out-of-the-box with no SNMP toolchain required.

---

## Features

- **Full import closure** — resolves every transitive dependency automatically via BFS
- **Two output formats** — structured JSON and pysnmp-compatible Python
- **Two-layer cache** — raw HTTP responses cached in `httpclient.py`; compiled objects cached by `MibCache` with mtime-based TTL
- **Concurrent fetching** — `asyncio.gather` + `to_thread` keeps the event loop unblocked during Lark parsing
- **Cycle detection** — Kahn’s algorithm with actionable error message listing cycle members
- **Pluggable readers** — `FileReader`, `HttpReader`, `ZipReader`, composable via `ReaderChain`
- **Atomic cache writes** — `rename(2)` on POSIX; no corrupted cache files on crash
- **SMIv1 + SMIv2** — separate Lark grammars (`smiv1.lark`, `smiv2.lark`) sharing a `common.lark` token set

---

## Installation

```bash
pip install trishul-smi
```

Requires Python ≥ 3.10.

---

## Quick Start

### CLI

```bash
# Compile IF-MIB and all its dependencies to JSON (default)
trishul-smi compile IF-MIB

# Write pysnmp Python modules instead
trishul-smi compile IF-MIB --format pysnmp

# Both formats at once, custom output directory
trishul-smi compile IF-MIB IP-MIB -f json -f pysnmp -o ./out

# Search a local MIB directory first, fall back to HTTP
trishul-smi compile IF-MIB -d /usr/share/snmp/mibs

# Disable the disk cache
trishul-smi compile IF-MIB --cache-dir ""

# Show per-module output paths
trishul-smi compile IF-MIB --verbose
```

Output:
```
Compiling IF-MIB → ./mibs-output (json)
Status    Module      Details
✅        SNMPv2-SMI
✅        SNMPv2-CONF
✅        IF-MIB

3 compiled
```

Exit codes: `0` all compiled — `1` any failure — `2` bad option.

### Python API

```python
import asyncio
from pathlib import Path
from trishul_smi import MibCompiler, CompilerConfig
from trishul_smi.reader import FileReader, HttpReader

async def main():
    config = CompilerConfig(output_dir=Path("./out"))
    async with HttpReader(config.sources) as http:
        compiler = (
            MibCompiler(config)
            .add_reader(FileReader("/usr/share/snmp/mibs"))  # local first
            .add_reader(http)                                  # HTTP fallback
        )
        results = await compiler.compile("IF-MIB", "IP-MIB")

    for r in results:
        print(r.name, r.status, r.output_paths)

asyncio.run(main())
```

---

## CLI Reference

```
trishul-smi compile [OPTIONS] MIB [MIB ...]

Arguments:
  MIB ...              MIB names to compile (e.g. IF-MIB IP-MIB)

Options:
  -o / --output-dir    Output directory               [default: ./mibs-output]
  -f / --format        Output format: json | pysnmp   [default: json]
                       Repeat for multiple formats.
  -d / --mib-dir       Local MIB directory (searched before HTTP)
                       Repeat for multiple directories.
  -s / --source        HTTP URL template (@mib@ replaced with MIB name)
                       Repeat for multiple sources.
                       Defaults to pysnmp.com + circitor.fr.
  --cache-dir          Compiled-module cache dir      [default: ~/.cache/trishul-smi]
                       Pass "" to disable.
  --cache-ttl-days     Cache TTL in days (0 = never expire)  [default: 7]
  --max-mib-size       Max MIB source size in bytes   [default: 10485760]
  --timeout            HTTP timeout in seconds        [default: 30.0]
  --retries            HTTP retry count               [default: 3]
  -v / --verbose       Show output file paths per module
  --help               Show this message and exit.

trishul-smi version    Print the installed version.
```

---

## Architecture

```
trishul_smi/
├── models/          MibModule, MibObject, MibType, CompileResult
├── config.py        CompilerConfig (all tunable knobs + __post_init__ validation)
├── errors.py        Exception hierarchy (TrishulError → flat subclasses)
├── reader/
│   ├── base.py        AbstractReader, FetchProtocol
│   ├── localfile.py   FileReader  (local filesystem)
│   ├── httpclient.py  HttpReader  (httpx + tenacity retries, async CM)
│   ├── zipreader.py   ZipReader   (in-memory ZIP archives)
│   └── chain.py       ReaderChain (fallback chain, MibNotFoundError-only passthrough)
├── parser/
│   ├── grammar/
│   │   ├── smiv1.lark   SMIv1 grammar
│   │   ├── smiv2.lark   SMIv2 grammar
│   │   └── common.lark  Shared token definitions
│   ├── transformer.py MibTransformer (Lark → MibModule)
│   └── smi_parser.py  SmiParser   (grammar singleton, thread-safe parse)
├── resolver/
│   ├── dependency.py  build_dependency_graph, topological_sort (Kahn’s)
│   ├── cache.py       MibCache    (orjson, atomic put, mtime TTL)
│   └── resolver.py    MibResolver (BFS import closure, asyncio.gather)
├── output/
│   ├── json_fmt.py    JsonFormatter   (orjson, FILE_SUFFIX = .json)
│   └── pysnmp_fmt.py  PysnmpFormatter (Jinja2, MibTable/Row/Scalar detection)
├── compiler.py      MibCompiler (fluent add_reader, async compile)
└── cli/
    ├── main.py        Typer app: compile + version commands
    └── __init__.py
```

**Data flow:**
```
CLI args
  ↓
MibCompiler.compile(*names)
  ↓
ReaderChain.fetch()          ←  FileReader | HttpReader | ZipReader
  ↓
MibResolver (BFS + asyncio.gather)
  ↓  checks MibCache on each wave
SmiParser.parse()            ←  runs in thread pool (asyncio.to_thread)
  ↓
topological_sort (Kahn’s)
  ↓
JsonFormatter / PysnmpFormatter
  ↓
output_dir / {ModuleName}.json | .py
```

---

## Output Formats

### JSON (`-f json`)

A structured document per MIB:

```json
{
  "module": "IF-MIB",
  "language": "SMIv2",
  "generated_by": "trishul-smi",
  "imports": { "SNMPv2-SMI": ["MODULE-IDENTITY", "OBJECT-TYPE"] },
  "objects": {
    "ifIndex": {
      "oid": "1.3.6.1.2.1.2.2.1.1",
      "oid_path": [1, 3, 6, 1, 2, 1, 2, 2, 1, 1],
      "object_type": "OBJECT-TYPE",
      "syntax": "InterfaceIndex",
      "max_access": "read-only",
      "status": "current",
      "description": "A unique value ..."
    }
  },
  "types": {},
  "notifications": {}
}
```

### pysnmp (`-f pysnmp`)

A Python module loadable by `pysnmp`’s `MibBuilder`:

```python
# IF-MIB MIB module
# Generated by trishul-smi
mibBuilder = MibBuilder()
( ModuleIdentity, ObjectType, ) = mibBuilder.importSymbols('SNMPv2-SMI', ...)
ifMIB = ModuleIdentity((1, 3, 6, 1, 2, 1, 31,))
mibBuilder.exportSymbols('IF-MIB', **{'ifMIB': ifMIB})
```

> **Note:** `MibTableColumn` detection requires full OID-tree resolution not yet
> available at format time. Table columns are emitted as `MibScalar` with a
> `# TODO` comment. This will be resolved in v0.2.0.

---

## Configuration

All options are exposed on `CompilerConfig` (Python API) and as CLI flags:

| Field | Default | Description |
|---|---|---|
| `output_dir` | `./mibs-output` | Where to write output files |
| `formats` | `["json"]` | Output formats: `json`, `pysnmp` |
| `sources` | pysnmp.com + circitor.fr | HTTP URL templates (`@mib@` replaced) |
| `cache_dir` | `~/.cache/trishul-smi` | `None` to disable |
| `cache_ttl_days` | `7` | `0` = never expire |
| `max_mib_size` | `10 MB` | Raises `MibSizeLimitError` if exceeded |
| `http_timeout` | `30.0 s` | Per-request timeout |
| `http_retries` | `3` | Retries on transient failure |

---

## Development

```bash
git clone https://github.com/tosumitdhaka/trishul-smi
cd trishul-smi
pip install -e ".[dev]"
pytest
```

Linting and type-checking:

```bash
ruff check trishul_smi
mypy trishul_smi
```

---

## License

MIT — see [LICENSE](LICENSE).
