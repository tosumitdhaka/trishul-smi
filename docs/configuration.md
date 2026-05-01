# Configuration Reference

All configuration lives in `CompilerConfig`. CLI flags map 1-to-1 to the fields below.

```python
from trishul_smi import CompilerConfig
from pathlib import Path

config = CompilerConfig(
    output_dir=Path("./out"),
    formats=["json", "pysnmp"],
    cache_ttl_days=0,
)
```

---

## Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `output_dir` | `Path` | `./mibs-output` | Directory where output files are written |
| `formats` | `list[str]` | `["json"]` | Output formats to generate: `"json"`, `"pysnmp"`, or both |
| `sources` | `list[str]` | pysnmp.com + mibbrowser.online | HTTP URL templates; `@mib@` is replaced with the MIB name |
| `cache_dir` | `Path \| None` | `~/.cache/trishul-smi` | Compiled-module cache directory; `None` disables the cache |
| `cache_ttl_days` | `int` | `7` | Cache TTL in days; `0` = never expire |
| `max_mib_size` | `int` | `10485760` (10 MB) | Maximum MIB source size in bytes; raises `MibSizeLimitError` if exceeded |
| `http_timeout` | `float` | `30.0` | Per-request HTTP timeout in seconds |
| `http_retries` | `int` | `3` | Number of retries on transient HTTP failure |
| `no_texts` | `bool` | `False` | Suppress `setDescription`/`setOrganization`/`setRevisions` and TC `description` in pysnmp output |

---

## Default HTTP sources

```
https://mibs.pysnmp.com/asn1/@mib@
https://mibbrowser.online/mibs/@mib@.mib
```

Sources are tried in order. HTTP is opt-in via `--online` on the CLI. To prepend a private mirror:

```python
config = CompilerConfig(
    sources=[
        "https://internal.corp/mibs/@mib@",
        "https://mibs.pysnmp.com/asn1/@mib@",
        "https://mibbrowser.online/mibs/@mib@.mib",
    ]
)
```

---

## Disabling the cache

```python
# Python API
config = CompilerConfig(cache_dir=None)

# CLI
trishul-smi compile IF-MIB --cache-dir ""
```

---

## Validation

`CompilerConfig.__post_init__` validates all numeric fields at construction time. Unknown format names raise `ValueError` when passed to `MibCompiler`.
