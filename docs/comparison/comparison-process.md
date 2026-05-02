# MIB Compiler Comparison — Process & Methodology

> **Last updated:** 2026-05-02  
> **Purpose:** Reference for anyone repeating or extending these comparisons

---

## Overview

This document records the exact process used to compare MIB compiler tools against trishul-smi. The primary method is the automation script at `scripts/compare_compilers.py`. Manual commands are provided as reference for individual steps or debugging.

Two scenarios are run for every comparison:

- **SC1** — Cold cache, texts enabled. Baseline with no prior state and all description/metadata fields on.
- **SC2** — Warm cache, texts suppressed. Repeated-run performance and minimal-output mode.

---

## Quick start — automated

The script handles venv creation, installation, both scenarios, file-size collection, JSON analysis, pysnmp checks, cross-version diff, and summary output in one run.

```bash
python docs/comparison/scripts/compare_compilers.py \
    --tools "pysmi==1.5.11,pysmi==2.0.0,trishul-smi==0.2.0" \
    --mib-dir ~/test/mibs \
    --mibs "IF-MIB,IP-MIB,SNMPv2-MIB,ENTITY-MIB,HOST-RESOURCES-MIB,TCP-MIB,UDP-MIB,IANAifType-MIB,INET-ADDRESS-MIB,UUID-TC-MIB,IANA-ENTITY-MIB,SNMP-FRAMEWORK-MIB" \
    --work-dir /tmp/mib-compare
```

**Output** — a timestamped subdirectory is created under `docs/comparison/output/` (e.g. `output/2026-05-02T10-30-00/`) containing:

| File | Contents |
|---|---|
| `results.json` | Full structured data: timings, file sizes, JSON analysis, pysnmp flags |
| `summary.md` | Markdown summary tables (also printed to stdout) |

### Key flags

| Flag | Default | Purpose |
|---|---|---|
| `--tools` | `pysmi==1.5.11,pysmi==2.0.0,trishul-smi==0.2.0` | Comma-separated `name==version` specs |
| `--mib-dir` | `~/test/mibs` | Local MIB source directory |
| `--mibs` | 12-MIB standard set | MIBs to compile |
| `--work-dir` | `/tmp/mib-compare` | Venvs and intermediate output dirs |
| `--output-dir` | `docs/comparison/output/` | Root for timestamped run subdirs |
| `--output` | `<run-dir>/results.json` | Override JSON output path |
| `--skip-install` | off | Reuse existing venvs (skip pip install) |
| `--skip-pysnmp` | off | Skip pysnmp output generation and checks |

### Rerunning without reinstalling

Once venvs are populated in `--work-dir`, use `--skip-install` to skip the pip step:

```bash
python docs/comparison/scripts/compare_compilers.py \
    --tools "pysmi==2.0.0,trishul-smi==0.2.0" \
    --mib-dir ~/test/mibs \
    --work-dir /tmp/mib-compare \
    --skip-install
```

---

## Test MIBs

All MIBs live at `~/test/mibs/`. Use only local MIBs — no `--online` / no HTTP fetching — to ensure reproducibility and eliminate network variance.

```
~/test/mibs/
  IF-MIB              IANAifType-MIB       SNMP-FRAMEWORK-MIB
  IP-MIB              INET-ADDRESS-MIB     SNMPv2-MIB
  ENTITY-MIB          UUID-TC-MIB          SNMPv2-SMI
  HOST-RESOURCES-MIB  IANA-ENTITY-MIB      SNMPv2-TC
  TCP-MIB             CISCO-EPM-*          SNMPv2-CONF
  UDP-MIB
```

Standard 12-MIB compile set used in all benchmarks:

```
IF-MIB IP-MIB SNMPv2-MIB ENTITY-MIB HOST-RESOURCES-MIB TCP-MIB UDP-MIB
IANAifType-MIB INET-ADDRESS-MIB UUID-TC-MIB IANA-ENTITY-MIB SNMP-FRAMEWORK-MIB
```

---

## Manual scenario commands

These are the exact commands the script runs internally. Use them to reproduce individual steps or debug a specific tool.

### Environment setup

```bash
# Dedicated venv per tool to avoid dependency conflicts
python3 -m venv /tmp/mib-compare/venv-pysmi-2-0-0
python3 -m venv /tmp/mib-compare/venv-trishul-smi-0-2-0

pip install pysmi==2.0.0 trishul-smi        # pysmi venv includes tsmi for paired runs
pip install trishul-smi==0.2.0              # tsmi-only venv

# Verify
python -c "import pysmi; print(pysmi.__version__)"
tsmi version
```

### SC1 — Cold cache, texts enabled

**pysmi:**
```bash
mibdump \
  --mib-source=file:///home/dhaka/test/mibs/ \
  --destination-format=json \
  --destination-directory=/tmp/sc1-pysmi \
  --no-dependencies \
  --generate-mib-texts --keep-texts-layout \
  --rebuild \
  IF-MIB IP-MIB ...
```

- `--rebuild` forces recompile even if output files already exist.
- `--generate-mib-texts --keep-texts-layout` enables description/organization/revision fields.
- `--no-dependencies` prevents fetching transitive deps from the network.

**trishul-smi:**
```bash
rm -rf ~/.cache/trishul-smi   # clear intermediate parse cache
tsmi compile \
  --mib-dir /home/dhaka/test/mibs \
  --format json \
  --output-dir /tmp/sc1-tsmi \
  --cache-dir "" \
  IF-MIB IP-MIB ...
```

- `--cache-dir ""` disables the intermediate cache (cold start).
- Descriptions are always written to JSON regardless of flags (`--no-texts` only affects pysnmp output).

### SC2 — Warm cache, texts suppressed

**pysmi — warm up first, then measure:**
```bash
# Warm up: populate destination dir
mibdump --mib-source=file:///... --destination-directory=/tmp/sc2-pysmi \
  --no-dependencies IF-MIB IP-MIB ... > /dev/null 2>&1

# Measure (no --rebuild; mtime check sees files as up-to-date)
time mibdump --mib-source=file:///... --destination-directory=/tmp/sc2-pysmi \
  --no-dependencies IF-MIB IP-MIB ...
```

pysmi's "cache" is its destination directory. Files up to date = no recompile, but grammar startup overhead always applies.

**trishul-smi — warm up first, then measure:**
```bash
# Warm up: populate ~/.cache/trishul-smi
tsmi compile --mib-dir /home/dhaka/test/mibs --format json \
  --output-dir /tmp/sc2-tsmi-warmup IF-MIB IP-MIB ... > /dev/null 2>&1

# Measure warm run
time tsmi compile --mib-dir /home/dhaka/test/mibs --format json \
  --output-dir /tmp/sc2-tsmi IF-MIB IP-MIB ...
```

Run each warm scenario at least **twice** to confirm the floor is stable.

---

## What to measure

### 1. Timing

Use `time <command>` and record `real` elapsed. Take the **median of 3 runs** per scenario, or use the script which runs twice and records both. Notes:

- Grammar initialisation cost dominates cold runs — tools using Lark have a higher floor than PLY-based tools.
- trishul-smi warm run skips the parse stage entirely; expect 3–5× speedup over cold.

### 2. MIBs compiled

Count files written to the output directory. Note any MIBs silently skipped (e.g. SNMPv2-MIB in tsmi — in `BASE_MIBS` frozenset).

### 3. Output file sizes

```bash
for f in IF-MIB IP-MIB ENTITY-MIB SNMP-FRAMEWORK-MIB UUID-TC-MIB; do
  sc1=$(wc -c < /tmp/sc1-tsmi/${f}.json)
  sc2=$(wc -c < /tmp/sc2-tsmi/${f}.json)
  printf "%-24s sc1=%-8s sc2=%s\n" "$f" "$sc1" "$sc2"
done
```

Compare SC1 vs SC2 sizes to verify text stripping actually works. trishul-smi JSON is identical across scenarios — `--no-texts` has no effect on JSON output.

### 4. JSON schema and field coverage

Run these checks on IF-MIB as a representative:

```python
import json

with open('/tmp/sc1-tsmi/IF-MIB.json') as f:
    t = json.load(f)

# Top-level structure
print(list(t.keys()))              # module/language/generated_by/imports/objects/types

# Object count and coverage gaps
tsmi_objs = set(t.get('objects', {}).keys())
print('objects:', len(tsmi_objs))

# NOTIFICATION-TYPE coverage
print(t['objects'].get('linkDown'))     # None if missing
print(t['objects'].get('linkUp'))

# Module-identity metadata
for k, v in t['objects'].items():
    if 'organization' in v or 'lastupdated' in v:
        print(k, list(v.keys()))
        break

# TC coverage
print(list(t.get('types', {}).keys())[:5])  # displayhint/status present?
```

### 5. Cross-version diff (when comparing two versions of the same tool)

```bash
# Strip header/timestamp line before diffing
diff /tmp/sc1-v1/IF-MIB.json /tmp/sc1-v2/IF-MIB.json \
  | grep -v "generated_by\|Generated by" | grep "^[<>]"
```

A zero count means outputs are functionally identical. Non-zero lines indicate schema changes between versions.

### 6. pysnmp output checks

```bash
# Line count
wc -l /tmp/sc1-tsmi/IF-MIB.py

# MibBuilder idiom — standard injection vs own instance
grep "if 'mibBuilder' not in globals()" /tmp/sc1-tsmi/IF-MIB.py  # want: present
grep "MibBuilder()" /tmp/sc1-tsmi/IF-MIB.py                       # want: absent

# Notification bound-objects
grep -A 6 "^linkDown" /tmp/sc1-tsmi/IF-MIB.py  # check for .setObjects()
```

---

## Evaluation criteria

When writing up results, assess each tool on these axes:

| Axis | What to check |
|---|---|
| **Cold speed** | SC1 `real` time |
| **Warm speed** | SC2 `real` time, multiple runs |
| **JSON completeness** | NOTIFICATION-TYPE present? Module-identity metadata? Conformance group members? TC displayhint/status? |
| **JSON schema** | Structured vs flat; `oid_path` array; clean imports section; `nodetype` vs `object_type` |
| **Text stripping** | Does `--no-texts` actually reduce JSON size? |
| **pysnmp correctness** | Standard `mibBuilder` injection? `.setObjects()` on notifications? Correct type subclassing? |
| **SNMPv2 base MIBs** | Are SNMPv2-SMI/TC/CONF/MIB compiled or skipped? |
| **SMIv1 handling** | Try CISCO-EPM-NOTIFICATION-MIB-V1SMI.my or INET-ADDRESS-MIB-V1SMI.my |
| **Error handling** | Does a MIB with missing deps fail cleanly or silently? |
| **Dep health** | `pip show <tool>` — any unmaintained or abandoned dependencies? |

---

## Output directory layout

Script runs go into `docs/comparison/output/` (tracked in git via `.gitignore` — run outputs are excluded):

```
docs/comparison/output/
  2026-05-02T10-30-00/
    results.json        full structured data
    summary.md          markdown summary tables

/tmp/mib-compare/       intermediate working directory (not committed)
  venv-pysmi-2-0-0/     tool venvs
  venv-trishul-smi-0-2-0/
  sc1-pysmi-2-0-0/      SC1 JSON output per tool
  sc1-trishul-smi-0-2-0/
  sc2-pysmi-2-0-0/      SC2 JSON output per tool
  sc2-trishul-smi-0-2-0/
  sc2-warmup-trishul-smi-0-2-0/   tsmi warmup run output
  pysnmp-pysmi-2-0-0/   pysnmp .py output per tool
  pysnmp-trishul-smi-0-2-0/
```

---

## Adding a new tool to the comparison

1. Add the tool spec to `--tools` (e.g. `net-snmp-mibs==1.0.0`).
2. If the tool is neither `pysmi` nor `trishul-smi`, add a new `run_<tool>_sc1/sc2` function in `compare_compilers.py` following the existing pattern, and a matching `analyse_<tool>_json` function if its JSON schema differs.
3. Run with `--skip-install` omitted first to let the script create the venv and install.
4. Collect output, then write up results following the evaluation criteria above.
5. Add a row to the Completed Comparisons table below.

---

## Completed comparisons

| Document | Tools | Date |
|---|---|---|
| [compiler-comparison.md](compiler-comparison.md) | pysmi 1.5.11 · pysmi 2.0.0 · trishul-smi 0.2.0 | 2026-05-02 |
