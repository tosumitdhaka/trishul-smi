# Configuration Reference

All runtime configuration lives in `CompilerConfig`. Most CLI flags map directly to these
fields; JSON sidecar emission is currently enabled through the Python API via
`emit_manifest` and `emit_oid_index`.

```python
from pathlib import Path
from trishul_smi.config import CompilerConfig

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
| `no_texts` | `bool` | `False` | Omit description, organization, and contact text from output for leaner files. Structural metadata (OIDs, dates, types) is always preserved. |
| `emit_manifest` | `bool` | `False` | Emit optional `manifest.json` bundle metadata alongside JSON output. Requires `"json"` in `formats`. |
| `emit_oid_index` | `bool` | `False` | Emit optional `oid_index.json` reverse-lookup metadata alongside JSON output. Requires `"json"` in `formats`. |

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

## JSON Bundle Sidecars

For library/API consumers, each emitted `MODULE.json` file is the atomic usable artifact.
`manifest.json` and `oid_index.json` are optional additive sidecars and are never required
for correctness.

```python
from pathlib import Path
from trishul_smi.config import CompilerConfig

config = CompilerConfig(
    output_dir=Path("./out"),
    formats=["json"],
    emit_manifest=True,
    emit_oid_index=True,
)
```

Both sidecar flags default to `False`. They currently do not have dedicated CLI flags.
When enabled, all JSON artifacts emitted by one `compile()` call share the same
`generated_at` value.

---

## Validation

`CompilerConfig.__post_init__` validates all numeric fields at construction time. Unknown
format names raise `ValueError` when passed to `MibCompiler`. `emit_manifest` and
`emit_oid_index` also require `"json"` to be present in `formats`.
