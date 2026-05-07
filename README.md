# trishul-smi

> A clean, modern SNMP MIB compiler written in Python.

[![PyPI](https://img.shields.io/pypi/v/trishul-smi)](https://pypi.org/project/trishul-smi/)
[![Python](https://img.shields.io/pypi/pyversions/trishul-smi)](https://pypi.org/project/trishul-smi/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/tosumitdhaka/trishul-smi?style=flat)](https://github.com/tosumitdhaka/trishul-smi/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/tosumitdhaka/trishul-smi?style=flat)](https://github.com/tosumitdhaka/trishul-smi/forks)
[![GitHub Issues](https://img.shields.io/github/issues/tosumitdhaka/trishul-smi)](https://github.com/tosumitdhaka/trishul-smi/issues)

`trishul-smi` fetches, parses, and compiles SNMP MIB definitions (SMIv1 and SMIv2)
into structured JSON or pysnmp-compatible Python modules.
It resolves transitive imports automatically, caches compiled modules on disk,
and ships a CLI that works out-of-the-box with no SNMP toolchain required.

---

## Features

- **Full import closure** — resolves every transitive dependency automatically
- **Full OID resolution** — all objects carry absolute numeric OID paths after compile
- **Two output formats** — structured JSON and pysnmp-compatible Python
- **Atomic JSON modules + optional sidecars** — each module JSON is usable on its own; `manifest.json` and `oid_index.json` are additive when enabled via the Python API
- **Versioned JSON IR metadata** — module JSON and optional sidecars carry `schema_version`, `producer_version`, `generated_by`, and `generated_at`
- **pysmi-parity pysnmp output** — MibTableColumn detection, full TC subtypeSpec, setIndexNames, setOrganization, setRevisions, inline constraint wrappers
- **Reverse conversion** — `tsmi convert FILE.py` converts a compiled PySNMP module back to JSON
- **Directory compile mode** — `tsmi compile -d /path/to/mibs` auto-discovers and compiles every MIB file
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
# Compile from a local MIB directory
tsmi compile IF-MIB --mib-dir /usr/share/snmp/mibs

# Fetch from the internet and compile to JSON + pysnmp
tsmi compile IF-MIB IP-MIB -f json -f pysnmp --online -o ./out
```

Python API:

```python
import asyncio
from pathlib import Path
from trishul_smi.compiler import MibCompiler
from trishul_smi.config import CompilerConfig
from trishul_smi.reader import FileReader, HttpReader

async def main():
    config = CompilerConfig(output_dir=Path("./out"))
    compiler = MibCompiler(config).add_reader(FileReader("/usr/share/snmp/mibs"))
    # optionally add HTTP: async with HttpReader(*config.sources) as http: compiler.add_reader(http)
    results = await compiler.compile("IF-MIB", "IP-MIB")
    for r in results:
        print(r.name, r.status, r.output_paths)

asyncio.run(main())
```

Each emitted `MODULE.json` is a valid standalone artifact. For downstream bundle metadata,
set `emit_manifest=True` and/or `emit_oid_index=True` on `CompilerConfig`.

---

## Documentation

- [CLI Reference](docs/cli.md) — all commands, options, and output format examples
- [Python API](docs/python-api.md) — embedding `trishul-smi` as a library
- [Configuration](docs/configuration.md) — `CompilerConfig` fields and defaults
- [JSON Bundle Contract](docs/json-bundles.md) — atomic module JSON, optional sidecars, and runtime guarantees
- [Architecture](docs/architecture.md) — package structure, data flow, design principles
- [Roadmap](docs/roadmap.md) — planned features and known limitations
- [Changelog](docs/CHANGELOG.md) — version history

## Repository

- [Contributing](.github/CONTRIBUTING.md) — dev setup, quality gates, and repo conventions
- [Contributors](.github/CONTRIBUTORS.md) — maintainer and contributor credits
- [Release Checklist](docs/release-checklist.md) — maintainer release process

---

## License

MIT — see [LICENSE](LICENSE).
