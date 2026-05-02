#!/usr/bin/env python3
"""
compare_compilers.py — Mechanical MIB compiler comparison data collector.

Collects raw timing, file-size, JSON-structure, and pysnmp-output data for
pysmi and trishul-smi across configurable MIBs.  Writes a single JSON results
file.  Prints a concise markdown summary table to stdout when done.  All
progress messages go to stderr so stdout stays clean.

Usage:
    python scripts/compare_compilers.py \
        --tools "pysmi==1.5.11,pysmi==2.0.0,trishul-smi==0.2.0" \
        --mib-dir ~/test/mibs \
        --mibs "IF-MIB,IP-MIB,SNMPv2-MIB" \
        --work-dir /tmp/mib-compare \
        --output results.json
"""

import argparse
import datetime
import difflib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from typing import Any

# Default output dir: docs/comparison/output/ relative to this script's location
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_DEFAULT_OUTPUT_DIR = _SCRIPT_DIR.parent / "output"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def tool_slug(tool_spec: str) -> str:
    """pysmi==2.0.0  ->  pysmi-2-0-0"""
    return re.sub(r"[=.]", "-", tool_spec).replace("--", "-")


def tool_name_and_version(tool_spec: str) -> tuple[str, str]:
    """'pysmi==2.0.0' -> ('pysmi', '2.0.0')"""
    if "==" in tool_spec:
        name, ver = tool_spec.split("==", 1)
        return name.strip(), ver.strip()
    return tool_spec.strip(), ""


def run_cmd(
    cmd: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict | None = None,
    capture: bool = True,
) -> tuple[int, str, str, float]:
    """Run a command, return (exit_code, stdout, stderr, elapsed_s)."""
    t0 = time.perf_counter()
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=capture,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    stdout = result.stdout if capture else ""
    stderr = result.stderr if capture else ""
    return result.returncode, stdout, stderr, elapsed


# ---------------------------------------------------------------------------
# Step 1 — Venv setup
# ---------------------------------------------------------------------------

def venv_python(venv_dir: pathlib.Path) -> pathlib.Path:
    return venv_dir / "bin" / "python"


def venv_pip(venv_dir: pathlib.Path) -> pathlib.Path:
    return venv_dir / "bin" / "pip"


def setup_venv(
    tool_spec: str,
    work_dir: pathlib.Path,
    skip_install: bool,
) -> dict[str, Any]:
    name, version = tool_name_and_version(tool_spec)
    slug = tool_slug(tool_spec)
    venv_dir = work_dir / f"venv-{slug}"

    info: dict[str, Any] = {
        "tool_spec": tool_spec,
        "slug": slug,
        "name": name,
        "version": version,
        "venv_dir": str(venv_dir),
        "installed_version": None,
        "install_skipped": False,
        "install_error": None,
    }

    if skip_install:
        log(f"  [skip-install] using existing venv: {venv_dir}")
        info["install_skipped"] = True
    elif venv_dir.exists():
        log(f"  venv already exists, skipping install: {venv_dir}")
        info["install_skipped"] = True
    else:
        log(f"  creating venv: {venv_dir}")
        rc, out, err, _ = run_cmd([sys.executable, "-m", "venv", str(venv_dir)])
        if rc != 0:
            info["install_error"] = f"venv create failed: {err.strip()}"
            log(f"  ERROR: {info['install_error']}")
            return info

        # Packages to install
        if name == "trishul-smi":
            pkgs = [f"trishul-smi=={version}" if version else "trishul-smi"]
        else:
            # pysmi venv: install both pysmi AND trishul-smi latest so tsmi is available
            pkgs = [
                f"pysmi=={version}" if version else "pysmi",
                "trishul-smi",
            ]

        log(f"  installing: {pkgs}")
        rc, out, err, _ = run_cmd(
            [str(venv_pip(venv_dir)), "install", "--quiet"] + pkgs
        )
        if rc != 0:
            info["install_error"] = f"pip install failed: {err.strip()[-500:]}"
            log(f"  ERROR: {info['install_error']}")
            return info

    # Record installed version via pip show
    show_pkg = "trishul-smi" if name == "trishul-smi" else "pysmi"
    rc, out, err, _ = run_cmd(
        [str(venv_pip(venv_dir)), "show", show_pkg]
    )
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("Version:"):
                info["installed_version"] = line.split(":", 1)[1].strip()
                break

    log(f"  installed_version={info['installed_version']}")
    return info


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------

def pysmi_bin(venv_dir: pathlib.Path) -> str:
    return str(venv_dir / "bin" / "mibdump")


def tsmi_bin(venv_dir: pathlib.Path) -> str:
    return str(venv_dir / "bin" / "tsmi")


def _parse_pysmi_output(stdout: str, stderr: str) -> tuple[list[str], list[str]]:
    """Extract created / up-to-date MIB names from pysmi output."""
    created: list[str] = []
    uptodate: list[str] = []
    combined = stdout + "\n" + stderr
    for line in combined.splitlines():
        line = line.strip()
        # e.g. "IF-MIB: %% created"  or  "IF-MIB: MIB module up-to-date"
        m = re.match(r"^(\S+?):\s*(.*)", line)
        if m:
            mib_name = m.group(1).rstrip(":")
            rest = m.group(2).lower()
            if "created" in rest or "written" in rest:
                created.append(mib_name)
            elif "up-to-date" in rest or "uptodate" in rest:
                uptodate.append(mib_name)
    return created, uptodate


def run_pysmi_sc1(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    out_dir: pathlib.Path,
    mibs: list[str],
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        pysmi_bin(venv_dir),
        f"--mib-source=file://{mib_dir}/",
        "--destination-format=json",
        f"--destination-directory={out_dir}",
        "--no-dependencies",
        "--generate-mib-texts",
        "--keep-texts-layout",
        "--rebuild",
    ] + mibs
    log(f"    pysmi SC1: {' '.join(cmd[-4:] + ['...'])}")
    rc, stdout, stderr, elapsed = run_cmd(cmd)
    created, uptodate = _parse_pysmi_output(stdout, stderr)
    return {
        "time_s": round(elapsed, 4),
        "exit_code": rc,
        "mibs_created": created,
        "mibs_uptodate": uptodate,
        "stdout": stdout[-2000:],
        "stderr": stderr[-2000:],
    }


def run_pysmi_sc2(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    out_dir: pathlib.Path,
    mibs: list[str],
) -> dict[str, Any]:
    """SC2: warm cache (reuse sc1 out_dir), no rebuild, no texts. Run twice."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        pysmi_bin(venv_dir),
        f"--mib-source=file://{mib_dir}/",
        "--destination-format=json",
        f"--destination-directory={out_dir}",
        "--no-dependencies",
    ] + mibs

    results = {}
    for run_n in (1, 2):
        log(f"    pysmi SC2 run{run_n}")
        rc, stdout, stderr, elapsed = run_cmd(cmd)
        created, uptodate = _parse_pysmi_output(stdout, stderr)
        results[f"run{run_n}"] = {
            "time_s": round(elapsed, 4),
            "exit_code": rc,
            "mibs_created": created,
            "mibs_uptodate": uptodate,
            "stderr": stderr[-1000:],
        }

    return {
        "warm_run1_s": results["run1"]["time_s"],
        "warm_run2_s": results["run2"]["time_s"],
        "exit_code": results["run2"]["exit_code"],
        "run1": results["run1"],
        "run2": results["run2"],
    }


def _tsmi_cache_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".cache" / "trishul-smi"


def run_tsmi_sc1(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    out_dir: pathlib.Path,
    mibs: list[str],
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Cold cache: delete ~/.cache/trishul-smi
    cache_dir = _tsmi_cache_dir()
    if cache_dir.exists():
        log(f"    tsmi SC1: removing cache {cache_dir}")
        shutil.rmtree(cache_dir, ignore_errors=True)

    cmd = [
        tsmi_bin(venv_dir),
        "compile",
        "--mib-dir", str(mib_dir),
        "--format", "json",
        "--output-dir", str(out_dir),
        "--cache-dir", "",
    ] + mibs
    log(f"    tsmi SC1: {' '.join(cmd)}")
    rc, stdout, stderr, elapsed = run_cmd(cmd)
    return {
        "time_s": round(elapsed, 4),
        "exit_code": rc,
        "stdout": stdout[-2000:],
        "stderr": stderr[-2000:],
        "mibs_created": [],   # tsmi doesn't report this the same way
        "mibs_uptodate": [],
    }


def run_tsmi_sc2(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    work_dir: pathlib.Path,
    mibs: list[str],
    slug: str,
) -> dict[str, Any]:
    """SC2: warm cache. Warmup run, then two timed runs."""
    warmup_dir = work_dir / f"sc2-warmup-{slug}"
    out_dir = work_dir / f"sc2-{slug}"
    warmup_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Warmup: prime the default cache
    warmup_cmd = [
        tsmi_bin(venv_dir),
        "compile",
        "--mib-dir", str(mib_dir),
        "--format", "json",
        "--output-dir", str(warmup_dir),
    ] + mibs
    log(f"    tsmi SC2 warmup")
    run_cmd(warmup_cmd)

    results = {}
    for run_n in (1, 2):
        cmd = [
            tsmi_bin(venv_dir),
            "compile",
            "--mib-dir", str(mib_dir),
            "--format", "json",
            "--output-dir", str(out_dir),
        ] + mibs
        log(f"    tsmi SC2 run{run_n}")
        rc, stdout, stderr, elapsed = run_cmd(cmd)
        results[f"run{run_n}"] = {
            "time_s": round(elapsed, 4),
            "exit_code": rc,
            "stderr": stderr[-1000:],
        }

    return {
        "warm_run1_s": results["run1"]["time_s"],
        "warm_run2_s": results["run2"]["time_s"],
        "exit_code": results["run2"]["exit_code"],
        "run1": results["run1"],
        "run2": results["run2"],
        "sc2_out_dir": str(out_dir),
    }


# ---------------------------------------------------------------------------
# Step 4 — File sizes
# ---------------------------------------------------------------------------

def collect_file_sizes(
    out_dir: pathlib.Path,
    mibs: list[str],
) -> dict[str, int | None]:
    sizes: dict[str, int | None] = {}
    for mib in mibs:
        p = out_dir / f"{mib}.json"
        sizes[mib] = p.stat().st_size if p.exists() else None
    return sizes


# ---------------------------------------------------------------------------
# Step 5 — JSON object analysis
# ---------------------------------------------------------------------------

REFERENCE_OBJECTS = ["linkDown", "linkUp", "linkUpDownNotificationsGroup"]
PYSMI_OBJ_FIELDS = ["class", "description", "maxaccess", "nodetype", "oid", "status", "syntax", "name"]
PYSMI_MI_FIELDS = ["organization", "contactinfo", "lastupdated", "revisions", "description"]
TSMI_OBJ_FIELDS = ["oid", "oid_path", "object_type", "syntax", "max_access", "status", "description", "index", "augments"]
TSMI_MI_FIELDS = ["organization", "contactinfo", "lastupdated", "revisions", "description"]


def _load_json_safe(path: pathlib.Path) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"_load_error": str(e)}


def analyse_pysmi_json(sc1_dir: pathlib.Path, mibs: list[str]) -> dict[str, Any]:
    analysis: dict[str, Any] = {}
    for mib in mibs:
        p = sc1_dir / f"{mib}.json"
        if not p.exists():
            analysis[mib] = {"error": "file_missing"}
            continue
        data = _load_json_safe(p)
        if "_load_error" in data:
            analysis[mib] = {"error": data["_load_error"]}
            continue

        # Count keys excluding meta, imports, class
        excluded = {"meta", "imports", "class"}
        keys = [k for k in data.keys() if k not in excluded]
        object_count = len(keys)

        # Reference objects present/missing
        present_refs = [r for r in REFERENCE_OBJECTS if r in data]
        missing_refs = [r for r in REFERENCE_OBJECTS if r not in data]

        # Sample object: ifDescr
        sample_obj_fields: dict | None = None
        if "ifDescr" in data:
            obj = data["ifDescr"]
            sample_obj_fields = {
                f: (f in obj) for f in PYSMI_OBJ_FIELDS
            }

        # Module-identity fields
        mi_fields: dict | None = None
        mi_key: str | None = None
        # look for ifMIB or any moduleidentity class
        for k, v in data.items():
            if isinstance(v, dict) and v.get("class") == "moduleidentity":
                mi_key = k
                mi_fields = {f: (f in v) for f in PYSMI_MI_FIELDS}
                break

        # Imports wart check
        imports_wart = False
        if "imports" in data and isinstance(data["imports"], dict):
            for imp_val in data["imports"].values():
                if isinstance(imp_val, dict) and imp_val.get("class") == "imports":
                    imports_wart = True
                    break
            if isinstance(data["imports"], dict) and data["imports"].get("class") == "imports":
                imports_wart = True

        analysis[mib] = {
            "object_count": object_count,
            "present_refs": present_refs,
            "missing_refs": missing_refs,
            "sample_object_fields": sample_obj_fields,
            "module_identity_key": mi_key,
            "module_identity_fields": mi_fields,
            "imports_class_wart": imports_wart,
        }
    return analysis


def analyse_tsmi_json(sc1_dir: pathlib.Path, mibs: list[str]) -> dict[str, Any]:
    analysis: dict[str, Any] = {}
    for mib in mibs:
        p = sc1_dir / f"{mib}.json"
        if not p.exists():
            analysis[mib] = {"error": "file_missing"}
            continue
        data = _load_json_safe(p)
        if "_load_error" in data:
            analysis[mib] = {"error": data["_load_error"]}
            continue

        objects = data.get("objects", {})
        types = data.get("types", {})
        object_count = len(objects)
        type_count = len(types)

        # Reference objects present/missing
        present_refs = [r for r in REFERENCE_OBJECTS if r in objects]
        missing_refs = [r for r in REFERENCE_OBJECTS if r not in objects]

        # Sample object: ifDescr
        sample_obj_fields: dict | None = None
        if "ifDescr" in objects:
            obj = objects["ifDescr"]
            sample_obj_fields = {f: (f in obj) for f in TSMI_OBJ_FIELDS}

        # Module-identity: look for object with lastupdated / organization
        mi_key: str | None = None
        mi_fields: dict | None = None
        for k, v in objects.items():
            if isinstance(v, dict) and ("organization" in v or "lastupdated" in v):
                mi_key = k
                mi_fields = {f: (f in v) for f in TSMI_MI_FIELDS}
                break

        # Check "class" wart absent from imports section
        imports = data.get("imports", {})
        has_class_wart = "class" in imports if isinstance(imports, dict) else False

        analysis[mib] = {
            "object_count": object_count,
            "type_count": type_count,
            "present_refs": present_refs,
            "missing_refs": missing_refs,
            "sample_object_fields": sample_obj_fields,
            "module_identity_key": mi_key,
            "module_identity_fields": mi_fields,
            "imports_class_wart_absent": not has_class_wart,
        }
    return analysis


# ---------------------------------------------------------------------------
# Step 6 — pysnmp output checks
# ---------------------------------------------------------------------------

def _inspect_pysnmp_file(py_file: pathlib.Path) -> dict[str, Any]:
    if not py_file.exists():
        return {"error": "file_missing"}
    try:
        text = py_file.read_text(errors="replace")
    except Exception as e:
        return {"error": str(e)}

    lines_list = text.splitlines()
    line_count = len(lines_list)

    # Check mibBuilder idiom
    # Standard: "if 'mibBuilder' not in globals():"
    # Non-standard: "mibBuilder = MibBuilder()"
    standard_idiom = any("if 'mibBuilder' not in globals()" in l for l in lines_list)
    own_instance = any(re.search(r"\bMibBuilder\(\)", l) for l in lines_list)
    mibbuilder_standard = standard_idiom and not own_instance

    # Check linkDown .setObjects
    linkdown_lines = [l for l in lines_list if "linkDown" in l]
    linkdown_setobjects = any(".setObjects" in l for l in linkdown_lines)

    return {
        "lines": line_count,
        "mibbuilder_standard": mibbuilder_standard,
        "linkdown_setobjects": linkdown_setobjects,
        "linkdown_lines_sample": linkdown_lines[:5],
    }


def run_pysmi_pysnmp(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    py_out: pathlib.Path,
) -> dict[str, Any]:
    py_out.mkdir(parents=True, exist_ok=True)
    cmd = [
        pysmi_bin(venv_dir),
        f"--mib-source=file://{mib_dir}/",
        "--destination-format=pysnmp",
        f"--destination-directory={py_out}",
        "--no-dependencies",
        "--generate-mib-texts",
        "--keep-texts-layout",
        "--rebuild",
        "IF-MIB",
    ]
    log(f"    pysmi pysnmp: {' '.join(cmd)}")
    rc, stdout, stderr, elapsed = run_cmd(cmd)
    result: dict[str, Any] = {
        "exit_code": rc,
        "time_s": round(elapsed, 4),
        "stderr": stderr[-1000:],
    }
    py_file = py_out / "IF_MIB.py"
    if not py_file.exists():
        # pysmi may use IF-MIB.py or IF_MIB.py
        for candidate in py_out.glob("IF*MIB*.py"):
            py_file = candidate
            break
    result["IF-MIB"] = _inspect_pysnmp_file(py_file)
    return result


def run_tsmi_pysnmp(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    py_out: pathlib.Path,
) -> dict[str, Any]:
    py_out.mkdir(parents=True, exist_ok=True)
    cmd = [
        tsmi_bin(venv_dir),
        "compile",
        "--mib-dir", str(mib_dir),
        "--format", "pysnmp",
        "--output-dir", str(py_out),
        "IF-MIB",
    ]
    log(f"    tsmi pysnmp: {' '.join(cmd)}")
    rc, stdout, stderr, elapsed = run_cmd(cmd)
    result: dict[str, Any] = {
        "exit_code": rc,
        "time_s": round(elapsed, 4),
        "stderr": stderr[-1000:],
    }
    py_file = py_out / "IF-MIB.py"
    if not py_file.exists():
        for candidate in py_out.glob("IF*MIB*.py"):
            py_file = candidate
            break
    result["IF-MIB"] = _inspect_pysnmp_file(py_file)
    return result


# ---------------------------------------------------------------------------
# Step 7 — Cross-version JSON diff
# ---------------------------------------------------------------------------

def cross_version_diff(
    results: dict[str, Any],
    pysmi_slugs: list[str],
    work_dir: pathlib.Path,
    mibs: list[str],
) -> dict[str, Any]:
    """Diff pysmi JSON outputs between versions. Only for pysmi tools."""
    if len(pysmi_slugs) < 2:
        return {}

    slug_a, slug_b = pysmi_slugs[0], pysmi_slugs[1]
    dir_a = work_dir / f"sc1-{slug_a}"
    dir_b = work_dir / f"sc1-{slug_b}"

    diffs: dict[str, Any] = {}
    _pysmi_header = re.compile(r"Produced by pysmi", re.IGNORECASE)

    for mib in mibs:
        fa = dir_a / f"{mib}.json"
        fb = dir_b / f"{mib}.json"
        if not fa.exists() or not fb.exists():
            diffs[mib] = {"error": "one_or_both_files_missing"}
            continue
        try:
            lines_a = [l for l in fa.read_text().splitlines() if not _pysmi_header.search(l)]
            lines_b = [l for l in fb.read_text().splitlines() if not _pysmi_header.search(l)]
        except Exception as e:
            diffs[mib] = {"error": str(e)}
            continue

        diff = list(difflib.unified_diff(lines_a, lines_b, lineterm=""))
        # Count changed lines (lines starting with + or - but not +++ / ---)
        changed = [l for l in diff if (l.startswith("+") or l.startswith("-")) and not l.startswith(("+++", "---"))]
        excerpt = changed[:3]
        diffs[mib] = {
            "diff_lines": len(changed),
            "excerpt": excerpt,
        }

    return {f"{slug_a}_vs_{slug_b}": diffs}


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------

def build_summary(results: dict[str, Any]) -> str:
    tools = results.get("tools", {})
    mibs = results.get("meta", {}).get("mibs", [])

    lines = [
        "## MIB Compiler Comparison Summary",
        "",
        f"Run at: {results.get('meta', {}).get('run_at', 'unknown')}",
        f"MIBs:   {', '.join(mibs)}",
        "",
    ]

    # Timing table
    lines.append("### Timing")
    lines.append("| Tool | SC1 cold (s) | SC2 warm-1 (s) | SC2 warm-2 (s) |")
    lines.append("|------|-------------|----------------|----------------|")
    for slug, tdata in tools.items():
        sc1 = tdata.get("sc1", {})
        sc2 = tdata.get("sc2", {})
        t1 = sc1.get("time_s", "n/a")
        t2a = sc2.get("warm_run1_s", "n/a")
        t2b = sc2.get("warm_run2_s", "n/a")
        lines.append(f"| {slug} | {t1} | {t2a} | {t2b} |")

    lines.append("")

    # Object count table (IF-MIB)
    lines.append("### Object counts (IF-MIB)")
    lines.append("| Tool | Objects | Types | missing refs |")
    lines.append("|------|---------|-------|--------------|")
    for slug, tdata in tools.items():
        ja = tdata.get("json_analysis", {})
        if_mib = ja.get("IF-MIB", {})
        obj_count = if_mib.get("object_count", "n/a")
        type_count = if_mib.get("type_count", "n/a")  # tsmi only
        missing = if_mib.get("missing_refs", [])
        missing_str = ", ".join(missing) if missing else "none"
        lines.append(f"| {slug} | {obj_count} | {type_count} | {missing_str} |")

    lines.append("")

    # pysnmp flags (IF-MIB)
    lines.append("### pysnmp output (IF-MIB)")
    lines.append("| Tool | exit | lines | mibbuilder_std | linkdown_setobjects |")
    lines.append("|------|------|-------|----------------|---------------------|")
    for slug, tdata in tools.items():
        py = tdata.get("pysnmp", {})
        if_mib_py = py.get("IF-MIB", {})
        exit_code = py.get("exit_code", "n/a")
        lc = if_mib_py.get("lines", "n/a")
        mb_std = if_mib_py.get("mibbuilder_standard", "n/a")
        ld_so = if_mib_py.get("linkdown_setobjects", "n/a")
        lines.append(f"| {slug} | {exit_code} | {lc} | {mb_std} | {ld_so} |")

    lines.append("")

    # Cross-version diffs summary
    vd = {}
    for slug, tdata in tools.items():
        if tdata.get("version_diffs"):
            vd.update(tdata["version_diffs"])
    if vd:
        lines.append("### Cross-version diffs")
        for pair_key, mib_diffs in vd.items():
            lines.append(f"**{pair_key}**")
            lines.append("| MIB | diff lines |")
            lines.append("|-----|-----------|")
            for mib, dinfo in mib_diffs.items():
                dl = dinfo.get("diff_lines", "err")
                lines.append(f"| {mib} | {dl} |")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect MIB compiler comparison data."
    )
    parser.add_argument(
        "--tools",
        default="pysmi==1.5.11,pysmi==2.0.0,trishul-smi==0.2.0",
        help="Comma-separated tool==version specs",
    )
    parser.add_argument(
        "--mib-dir",
        default=str(pathlib.Path.home() / "test" / "mibs"),
        help="Directory containing MIB source files",
    )
    parser.add_argument(
        "--mibs",
        default=(
            "IF-MIB,IP-MIB,SNMPv2-MIB,ENTITY-MIB,HOST-RESOURCES-MIB,"
            "TCP-MIB,UDP-MIB,IANAifType-MIB,INET-ADDRESS-MIB,UUID-TC-MIB,"
            "IANA-ENTITY-MIB,SNMP-FRAMEWORK-MIB"
        ),
        help="Comma-separated MIB names to compile",
    )
    parser.add_argument(
        "--work-dir",
        default="/tmp/mib-compare",
        help="Working directory for venvs and outputs",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Root directory for run outputs. Each run creates a timestamped "
            "subdirectory here (e.g. 2026-05-02T10-30-00/). "
            f"Defaults to {_DEFAULT_OUTPUT_DIR}"
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Path for the JSON results file. Defaults to results.json inside "
            "the timestamped run subdirectory under --output-dir."
        ),
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip venv creation/install (use existing venvs)",
    )
    parser.add_argument(
        "--skip-pysnmp",
        action="store_true",
        help="Skip pysnmp output checks",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    tool_specs = [t.strip() for t in args.tools.split(",") if t.strip()]
    mibs = [m.strip() for m in args.mibs.split(",") if m.strip()]
    mib_dir = pathlib.Path(args.mib_dir).expanduser().resolve()
    work_dir = pathlib.Path(args.work_dir).expanduser().resolve()

    # Resolve output run directory (timestamped subdir under --output-dir)
    run_at = datetime.datetime.now(datetime.timezone.utc)
    run_ts = run_at.strftime("%Y-%m-%dT%H-%M-%S")
    output_root = pathlib.Path(
        args.output_dir if args.output_dir else _DEFAULT_OUTPUT_DIR
    ).expanduser().resolve()
    run_dir = output_root / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    output_path = (
        pathlib.Path(args.output).expanduser()
        if args.output
        else run_dir / "results.json"
    )
    summary_path = run_dir / "summary.md"

    work_dir.mkdir(parents=True, exist_ok=True)

    log(f"=== MIB Compiler Comparison ===")
    log(f"Tools:   {tool_specs}")
    log(f"MIBs:    {mibs}")
    log(f"MIB dir: {mib_dir}")
    log(f"Work:    {work_dir}")
    log(f"Run dir: {run_dir}")
    log(f"Output:  {output_path}")
    log("")

    results: dict[str, Any] = {
        "meta": {
            "run_at": run_at,
            "mib_dir": str(mib_dir),
            "mibs": mibs,
            "tools": tool_specs,
        },
        "tools": {},
    }

    # Track pysmi slugs for cross-version diff
    pysmi_slugs: list[str] = []

    # -----------------------------------------------------------------------
    # Per-tool processing
    # -----------------------------------------------------------------------
    for tool_spec in tool_specs:
        name, version = tool_name_and_version(tool_spec)
        slug = tool_slug(tool_spec)
        log(f"--- Tool: {tool_spec} (slug={slug}) ---")

        # Step 1: venv setup
        log(f"[1] Setting up venv for {tool_spec}")
        venv_info = setup_venv(tool_spec, work_dir, args.skip_install)
        venv_dir = pathlib.Path(venv_info["venv_dir"])

        tool_result: dict[str, Any] = {
            "installed_version": venv_info.get("installed_version"),
            "install_error": venv_info.get("install_error"),
        }

        is_tsmi = (name == "trishul-smi")
        sc1_dir = work_dir / f"sc1-{slug}"
        sc2_dir = work_dir / f"sc2-{slug}"

        # Step 2: SC1 — cold cache, texts ON
        log(f"[2] SC1 — cold cache, texts ON")
        if is_tsmi:
            sc1 = run_tsmi_sc1(venv_dir, mib_dir, sc1_dir, mibs)
        else:
            sc1 = run_pysmi_sc1(venv_dir, mib_dir, sc1_dir, mibs)
            pysmi_slugs.append(slug)
        tool_result["sc1"] = sc1

        # Step 3: SC2 — warm cache, texts OFF
        log(f"[3] SC2 — warm cache, texts OFF")
        if is_tsmi:
            sc2 = run_tsmi_sc2(venv_dir, mib_dir, work_dir, mibs, slug)
        else:
            sc2 = run_pysmi_sc2(venv_dir, mib_dir, sc2_dir, mibs)
        tool_result["sc2"] = sc2

        # Step 4: File sizes
        log(f"[4] Collecting file sizes")
        sc1_sizes = collect_file_sizes(sc1_dir, mibs)
        if is_tsmi:
            sc2_out_dir = pathlib.Path(sc2.get("sc2_out_dir", str(sc2_dir)))
        else:
            sc2_out_dir = sc2_dir
        sc2_sizes = collect_file_sizes(sc2_out_dir, mibs)
        tool_result["file_sizes"] = {"sc1": sc1_sizes, "sc2": sc2_sizes}

        # Step 5: JSON analysis (SC1 output, texts ON)
        log(f"[5] JSON object analysis")
        if is_tsmi:
            tool_result["json_analysis"] = analyse_tsmi_json(sc1_dir, mibs)
        else:
            tool_result["json_analysis"] = analyse_pysmi_json(sc1_dir, mibs)

        # Step 6: pysnmp output checks
        tool_result["pysnmp"] = {}
        if not args.skip_pysnmp:
            log(f"[6] pysnmp output checks")
            py_out = work_dir / f"pysnmp-{slug}"
            if is_tsmi:
                tool_result["pysnmp"] = run_tsmi_pysnmp(venv_dir, mib_dir, py_out)
            else:
                tool_result["pysnmp"] = run_pysmi_pysnmp(venv_dir, mib_dir, py_out)
        else:
            log(f"[6] skipping pysnmp (--skip-pysnmp)")

        results["tools"][slug] = tool_result
        log("")

    # Step 7: Cross-version JSON diff
    log(f"[7] Cross-version JSON diff")
    if len(pysmi_slugs) >= 2:
        version_diffs = cross_version_diff(results, pysmi_slugs, work_dir, mibs)
        # Attach to the first pysmi tool entry
        first_pysmi_slug = pysmi_slugs[0]
        if first_pysmi_slug in results["tools"]:
            results["tools"][first_pysmi_slug]["version_diffs"] = version_diffs
    else:
        log("  fewer than 2 pysmi versions, skipping cross-version diff")
        for slug in pysmi_slugs:
            if slug in results["tools"]:
                results["tools"][slug]["version_diffs"] = {}

    # Step 8: Write output
    log(f"[8] Writing results to {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"  wrote {output_path.stat().st_size} bytes")

    # Build markdown summary, print to stdout, and save to run dir
    summary_md = build_summary(results)
    print(summary_md)
    summary_path.write_text(summary_md + "\n")
    log(f"  wrote summary to {summary_path}")


if __name__ == "__main__":
    main()
