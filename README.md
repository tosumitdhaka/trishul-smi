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

- **Full import closure** — resolves every transitive dependency automatically
- **Two output formats** — structured JSON and pysnmp-compatible Python
- **Concurrent fetching** — parallel async HTTP with retry and timeout
- **Pluggable readers** — `FileReader`, `HttpReader`, `ZipReader`, composable via `ReaderChain`
- **Disk cache** — compiled modules cached with mtime-based TTL; atomic writes
- **Cycle detection** — Kahn's algorithm with actionable error messages
- **SMIv1 + SMIv2** — separate Lark grammars, auto-detected per MIB

---

## Installation

```bash
pip install trishul-smi
```

Requires Python ≥ 3.10.

---

## Quick Start

```bash
# Compile IF-MIB and all its dependencies to JSON
trishul-smi compile IF-MIB

# Compile to both JSON and pysnmp Python modules
trishul-smi compile IF-MIB IP-MIB -f json -f pysnmp -o ./out
```

Python API:

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
            .add_reader(FileReader("/usr/share/snmp/mibs"))
            .add_reader(http)
        )
        results = await compiler.compile("IF-MIB", "IP-MIB")
    for r in results:
        print(r.name, r.status, r.output_paths)

asyncio.run(main())
```

---

## Documentation

- [CLI Reference](docs/cli.md) — all commands, options, and output format examples
- [Configuration](docs/configuration.md) — `CompilerConfig` fields and defaults
- [Architecture](docs/architecture.md) — package structure, data flow, design principles
- [Contributing](docs/contributing.md) — dev setup, test and lint commands
- [Changelog](docs/CHANGELOG.md) — version history

---

## License

MIT — see [LICENSE](LICENSE).
