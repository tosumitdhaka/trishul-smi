#!/usr/bin/env python3
"""
compare_compilers.py — Comprehensive MIB compiler comparison.

Compiles a set of MIBs with pysmi 2.0.0 and trishul-smi (local or PyPI)
in both with-texts and no-texts modes, then produces a detailed report
covering JSON structure, field values, nodetype accuracy, and pysnmp output.

Usage:
    python scripts/compare_compilers.py \\
        --tools "pysmi==2.0.0,trishul-smi" \\
        --mib-dir ~/test/mibs \\
        --local-tsmi ~/trishul3/trishul-smi \\
        --mibs "IF-MIB,IP-MIB" \\
        --work-dir /tmp/mib-compare

    # Re-use existing venvs and compiled files:
    python scripts/compare_compilers.py ... --skip-install --skip-compile
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from typing import Any

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_DEFAULT_OUTPUT_DIR = _SCRIPT_DIR.parent / "output"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def tool_slug(tool_spec: str) -> str:
    return re.sub(r"[=.]", "-", tool_spec).replace("--", "-")


def tool_name_and_version(tool_spec: str) -> tuple[str, str]:
    if "==" in tool_spec:
        name, ver = tool_spec.split("==", 1)
        return name.strip(), ver.strip()
    return tool_spec.strip(), ""


def run_cmd(
    cmd: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict | None = None,
) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0
    return result.returncode, result.stdout, result.stderr, elapsed


def load_json_safe(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Venv setup
# ---------------------------------------------------------------------------

def venv_python(d: pathlib.Path) -> pathlib.Path:
    return d / "bin" / "python"

def venv_pip(d: pathlib.Path) -> pathlib.Path:
    return d / "bin" / "pip"

def pysmi_bin(d: pathlib.Path) -> str:
    return str(d / "bin" / "mibdump")

def tsmi_bin(d: pathlib.Path) -> str:
    return str(d / "bin" / "tsmi")


def setup_venv(
    tool_spec: str,
    work_dir: pathlib.Path,
    skip_install: bool,
    local_tsmi: pathlib.Path | None = None,
) -> dict[str, Any]:
    name, version = tool_name_and_version(tool_spec)
    is_local_tsmi = name == "trishul-smi" and local_tsmi is not None
    slug = "trishul-smi-local" if is_local_tsmi else tool_slug(tool_spec)
    venv_dir = work_dir / f"venv-{slug}"

    info: dict[str, Any] = {
        "tool_spec": tool_spec, "slug": slug, "name": name, "version": version,
        "venv_dir": str(venv_dir), "installed_version": None,
        "install_error": None, "local_source": str(local_tsmi) if is_local_tsmi else None,
    }

    if skip_install or venv_dir.exists():
        log(f"  venv exists/skipped: {venv_dir}")
    else:
        log(f"  creating venv: {venv_dir}")
        rc, _, err, _ = run_cmd([sys.executable, "-m", "venv", str(venv_dir)])
        if rc != 0:
            info["install_error"] = f"venv create failed: {err.strip()}"
            return info

        if is_local_tsmi:
            log(f"  pip install -e {local_tsmi}")
            rc, _, err, _ = run_cmd(
                [str(venv_pip(venv_dir)), "install", "--quiet", "-e", str(local_tsmi)]
            )
        elif name == "trishul-smi":
            pkgs = [f"trishul-smi=={version}" if version else "trishul-smi"]
            rc, _, err, _ = run_cmd(
                [str(venv_pip(venv_dir)), "install", "--quiet"] + pkgs
            )
        else:
            pkgs = [f"pysmi=={version}" if version else "pysmi", "trishul-smi"]
            log(f"  pip install {pkgs}")
            rc, _, err, _ = run_cmd(
                [str(venv_pip(venv_dir)), "install", "--quiet"] + pkgs
            )

        if rc != 0:
            info["install_error"] = f"pip install failed: {err.strip()[-500:]}"
            log(f"  ERROR: {info['install_error']}")
            return info

    show_pkg = "trishul-smi" if name == "trishul-smi" else "pysmi"
    rc, out, _, _ = run_cmd([str(venv_pip(venv_dir)), "show", show_pkg])
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("Version:"):
                info["installed_version"] = line.split(":", 1)[1].strip()
                break
    if is_local_tsmi:
        info["installed_version"] = (info["installed_version"] or "dev") + "+local"

    log(f"  {slug} installed_version={info['installed_version']}")
    return info


# ---------------------------------------------------------------------------
# Compile runners
# ---------------------------------------------------------------------------

def _tsmi_cache_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".cache" / "trishul-smi"


def run_pysmi_json(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    out_dir: pathlib.Path,
    mibs: list[str],
    with_texts: bool,
    cold: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        pysmi_bin(venv_dir),
        f"--mib-source=file://{mib_dir}/",
        "--destination-format=json",
        f"--destination-directory={out_dir}",
        "--no-dependencies",
    ]
    if with_texts:
        cmd += ["--generate-mib-texts", "--keep-texts-layout"]
    if cold:
        cmd += ["--rebuild"]
    cmd += mibs

    rc, stdout, stderr, elapsed = run_cmd(cmd)
    return {"exit_code": rc, "time_s": round(elapsed, 4),
            "stdout": stdout[-1000:], "stderr": stderr[-1000:]}


def run_pysmi_pysnmp(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    out_dir: pathlib.Path,
    mibs: list[str],
    with_texts: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        pysmi_bin(venv_dir),
        f"--mib-source=file://{mib_dir}/",
        "--destination-format=pysnmp",
        f"--destination-directory={out_dir}",
        "--no-dependencies",
        "--rebuild",
    ]
    if with_texts:
        cmd += ["--generate-mib-texts", "--keep-texts-layout"]
    cmd += mibs
    rc, stdout, stderr, elapsed = run_cmd(cmd)
    return {"exit_code": rc, "time_s": round(elapsed, 4), "stderr": stderr[-500:]}


def run_tsmi_json(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    out_dir: pathlib.Path,
    mibs: list[str],
    with_texts: bool,
    cold: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if cold:
        shutil.rmtree(_tsmi_cache_dir(), ignore_errors=True)
    cmd = [
        tsmi_bin(venv_dir), "compile",
        "--mib-dir", str(mib_dir),
        "--format", "json",
        "--output-dir", str(out_dir),
    ]
    if not with_texts:
        cmd += ["--no-texts"]
    if cold:
        cmd += ["--cache-dir", ""]
    cmd += mibs
    rc, stdout, stderr, elapsed = run_cmd(cmd)
    return {"exit_code": rc, "time_s": round(elapsed, 4),
            "stdout": stdout[-1000:], "stderr": stderr[-1000:]}


def run_tsmi_pysnmp(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    out_dir: pathlib.Path,
    mibs: list[str],
    with_texts: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(_tsmi_cache_dir(), ignore_errors=True)
    cmd = [
        tsmi_bin(venv_dir), "compile",
        "--mib-dir", str(mib_dir),
        "--format", "pysnmp",
        "--output-dir", str(out_dir),
        "--cache-dir", "",
    ]
    if not with_texts:
        cmd += ["--no-texts"]
    cmd += mibs
    rc, stdout, stderr, elapsed = run_cmd(cmd)
    return {"exit_code": rc, "time_s": round(elapsed, 4), "stderr": stderr[-500:]}


def warm_tsmi(
    venv_dir: pathlib.Path,
    mib_dir: pathlib.Path,
    mibs: list[str],
) -> float:
    """Prime tsmi cache, return elapsed."""
    warmup_dir = _tsmi_cache_dir().parent / "warmup-scratch"
    warmup_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        tsmi_bin(venv_dir), "compile",
        "--mib-dir", str(mib_dir),
        "--format", "json",
        "--output-dir", str(warmup_dir),
    ] + mibs
    _, _, _, elapsed = run_cmd(cmd)
    return round(elapsed, 4)


# ---------------------------------------------------------------------------
# File size helpers
# ---------------------------------------------------------------------------

def dir_sizes(out_dir: pathlib.Path, mibs: list[str], suffix: str) -> dict[str, int | None]:
    return {
        mib: ((out_dir / f"{mib}{suffix}").stat().st_size
              if (out_dir / f"{mib}{suffix}").exists() else None)
        for mib in mibs
    }


# ---------------------------------------------------------------------------
# JSON deep analysis
# ---------------------------------------------------------------------------

def _pysmi_all_objects(data: dict) -> dict[str, dict]:
    """All named dicts in pysmi JSON except meta/imports."""
    return {k: v for k, v in data.items()
            if k not in ("meta", "imports") and isinstance(v, dict)}


def _tsmi_all_objects(data: dict) -> dict[str, dict]:
    """Combined objects + notifications from tsmi JSON."""
    combined: dict[str, dict] = {}
    combined.update(data.get("objects", {}))
    combined.update(data.get("notifications", {}))
    return combined


def _norm_maxaccess(v: str | None) -> str:
    return (v or "").replace("-", "").lower()


def analyse_json_file(data: dict, tool: str, mib: str) -> dict[str, Any]:
    """Structural counts and field presence for a single compiled MIB JSON."""
    if tool == "pysmi":
        objs = _pysmi_all_objects(data)
        by_class: dict[str, list[str]] = {}
        for k, v in objs.items():
            cls = v.get("class", "unknown")
            by_class.setdefault(cls, []).append(k)
        return {
            "total": len(objs),
            "by_class": {cls: len(names) for cls, names in by_class.items()},
            "object_names": sorted(objs.keys()),
        }
    else:  # tsmi
        objects = data.get("objects", {})
        notifications = data.get("notifications", {})
        types = data.get("types", {})
        return {
            "objects": len(objects),
            "notifications": len(notifications),
            "types": len(types),
            "total": len(objects) + len(notifications),
            "object_names": sorted(list(objects.keys()) + list(notifications.keys())),
        }


def _pysmi_oid_to_list(oid: Any) -> list[int]:
    """Convert pysmi OID (dot-string '1.3.6.1.2') to int list."""
    if isinstance(oid, list):
        return [int(x) for x in oid]
    if isinstance(oid, str) and oid:
        try:
            return [int(x) for x in oid.split(".") if x]
        except ValueError:
            pass
    return []


def cross_json_compare(
    pysmi_dir: pathlib.Path,
    tsmi_dir: pathlib.Path,
    mibs: list[str],
) -> dict[str, Any]:
    """Deep field-level comparison between pysmi and tsmi JSON output."""
    results: dict[str, Any] = {}

    for mib in mibs:
        pdata = load_json_safe(pysmi_dir / f"{mib}.json")
        tdata = load_json_safe(tsmi_dir / f"{mib}.json")

        if pdata is None or tdata is None:
            results[mib] = {"error": "missing_file"}
            continue

        pobjs = _pysmi_all_objects(pdata)
        tobjs = _tsmi_all_objects(tdata)

        pnames = set(pobjs.keys())
        tnames = set(tobjs.keys())
        common = sorted(pnames & tnames)
        pysmi_only = sorted(pnames - tnames)
        tsmi_only = sorted(tnames - pnames)

        # For each common object, compare fields
        oid_matches: dict[str, bool] = {}
        nodetype_matches: dict[str, bool | None] = {}
        status_matches: dict[str, bool] = {}
        maxaccess_matches: dict[str, bool] = {}
        desc_present_texts: dict[str, dict] = {}

        for name in common:
            p = pobjs[name]
            t = tobjs[name]

            # OID — pysmi stores as dot-string, tsmi as int list
            p_oid = _pysmi_oid_to_list(p.get("oid"))
            t_oid = list(t.get("oid_path") or [])
            oid_matches[name] = (p_oid == t_oid)

            # Nodetype (only meaningful for objecttype)
            if p.get("class") == "objecttype" and "nodetype" in t:
                p_nt = p.get("nodetype", "")
                t_nt = t.get("nodetype", "")
                nodetype_matches[name] = (p_nt == t_nt)
            else:
                nodetype_matches[name] = None

            # Status
            status_matches[name] = (p.get("status") == t.get("status"))

            # Max-access
            p_ma = _norm_maxaccess(p.get("maxaccess"))
            t_ma = _norm_maxaccess(t.get("max_access"))
            maxaccess_matches[name] = (p_ma == t_ma)

            # Description
            desc_present_texts[name] = {
                "pysmi": "description" in p,
                "tsmi": "description" in t,
            }

        # Summary stats
        objecttype_common = [n for n in common if pobjs[n].get("class") == "objecttype"]
        oid_ok = sum(1 for n in objecttype_common if oid_matches.get(n))
        nt_compared = [n for n in objecttype_common if nodetype_matches.get(n) is not None]
        nt_ok = sum(1 for n in nt_compared if nodetype_matches.get(n))
        st_ok = sum(1 for n in objecttype_common if status_matches.get(n))
        ma_ok = sum(1 for n in objecttype_common if maxaccess_matches.get(n))

        # Sample objects: ifDescr, linkDown
        samples: dict[str, Any] = {}
        for sample_name in ["ifDescr", "ifTable", "ifEntry", "linkDown"]:
            if sample_name not in pobjs or sample_name not in tobjs:
                continue
            p = pobjs[sample_name]
            t = tobjs[sample_name]
            samples[sample_name] = {
                "pysmi": {
                    "class": p.get("class"),
                    "oid": p.get("oid"),
                    "nodetype": p.get("nodetype"),
                    "status": p.get("status"),
                    "maxaccess": p.get("maxaccess"),
                    "syntax": p.get("syntax"),
                    "has_description": "description" in p,
                    "has_index": "indices" in p or "index" in p,
                    "members_raw": p.get("objects"),
                },
                "tsmi": {
                    "object_type": t.get("object_type"),
                    "oid_path": t.get("oid_path"),
                    "nodetype": t.get("nodetype"),
                    "status": t.get("status"),
                    "max_access": t.get("max_access"),
                    "syntax": t.get("syntax"),
                    "constraints": t.get("constraints"),
                    "has_description": "description" in t,
                    "has_index": t.get("index") is not None,
                    "members": t.get("members"),
                },
            }

        # Module metadata comparison
        # pysmi: find moduleidentity key in flat dict
        p_mi: dict = {}
        for k, v in pobjs.items():
            if isinstance(v, dict) and v.get("class") == "moduleidentity":
                p_mi = v
                break
        t_mm = tdata.get("module_metadata") or {}

        metadata_cmp = {
            "pysmi": {
                "lastupdated": p_mi.get("lastupdated"),
                "organization": bool(p_mi.get("organization")),
                "contactinfo": bool(p_mi.get("contactinfo")),
                "description": bool(p_mi.get("description")),
                "revisions": len(p_mi.get("revisions") or []),
            },
            "tsmi": {
                "lastupdated": t_mm.get("lastupdated"),
                "organization": bool(t_mm.get("organization")),
                "contactinfo": bool(t_mm.get("contactinfo")),
                "description": bool(t_mm.get("description")),
                "revisions": len(t_mm.get("revisions") or []),
            },
        }

        # tsmi v0.3.0 new fields check
        tsmi_objs_only = tdata.get("objects", {})
        tsmi_notifs_only = tdata.get("notifications", {})
        objects_with_nodetype = sum(
            1 for o in tsmi_objs_only.values()
            if o.get("object_type") == "OBJECT-TYPE" and "nodetype" in o
        )
        objects_with_constraints = [
            name for name, o in tsmi_objs_only.items()
            if o.get("constraints") is not None
        ]
        notifs_with_member_dicts = all(
            isinstance(m, dict)
            for n in tsmi_notifs_only.values()
            for m in (n.get("members") or [])
        )
        lastupdated_is_iso = bool(
            t_mm.get("lastupdated") and
            re.match(r"^\d{4}-\d{2}-\d{2}$", str(t_mm.get("lastupdated", "")))
        )
        revisions_iso = all(
            re.match(r"^\d{4}-\d{2}-\d{2}$", str(r.get("date", "")))
            for r in (t_mm.get("revisions") or [])
        )

        results[mib] = {
            "pysmi_total": len(pnames),
            "tsmi_objects": len(tdata.get("objects", {})),
            "tsmi_notifications": len(tdata.get("notifications", {})),
            "tsmi_types": len(tdata.get("types", {})),
            "tsmi_total": len(tnames),
            "common_count": len(common),
            "pysmi_only": pysmi_only,
            "tsmi_only": tsmi_only,
            "objecttype_common": len(objecttype_common),
            "oid_agreement": f"{oid_ok}/{len(objecttype_common)}",
            "nodetype_agreement": f"{nt_ok}/{len(nt_compared)}" if nt_compared else "n/a",
            "status_agreement": f"{st_ok}/{len(objecttype_common)}",
            "maxaccess_agreement": f"{ma_ok}/{len(objecttype_common)}",
            "samples": samples,
            "metadata": metadata_cmp,
            "tsmi_v030": {
                "objects_with_nodetype": objects_with_nodetype,
                "objects_with_constraints": objects_with_constraints[:10],
                "notif_members_attributed": notifs_with_member_dicts,
                "lastupdated_iso": lastupdated_is_iso,
                "revisions_iso": revisions_iso,
                "module_description_present": bool(t_mm.get("description")),
            },
        }

    return results


def cross_no_texts(
    pysmi_dir: pathlib.Path,
    tsmi_dir: pathlib.Path,
    mibs: list[str],
) -> dict[str, Any]:
    """Check that both tools correctly strip descriptions in no-texts mode."""
    results: dict[str, Any] = {}
    for mib in mibs:
        pdata = load_json_safe(pysmi_dir / f"{mib}.json")
        tdata = load_json_safe(tsmi_dir / f"{mib}.json")
        if pdata is None or tdata is None:
            results[mib] = {"error": "missing_file"}
            continue

        pobjs = _pysmi_all_objects(pdata)
        tobjs = _tsmi_all_objects(tdata)

        # Check a sample of common objects for absent description
        sample = list(set(pobjs.keys()) & set(tobjs.keys()))[:10]
        pysmi_desc_absent = all("description" not in pobjs[n] for n in sample if n in pobjs)
        tsmi_desc_absent = all("description" not in tobjs[n] for n in sample if n in tobjs)

        # tsmi module_metadata should be absent in no-texts mode
        tsmi_metadata_absent = "module_metadata" not in tdata

        results[mib] = {
            "pysmi_descriptions_stripped": pysmi_desc_absent,
            "tsmi_descriptions_stripped": tsmi_desc_absent,
            "tsmi_metadata_stripped": tsmi_metadata_absent,
        }
    return results


# ---------------------------------------------------------------------------
# pysnmp deep analysis
# ---------------------------------------------------------------------------

def _find_pysnmp_file(out_dir: pathlib.Path, mib: str) -> pathlib.Path | None:
    # tsmi: IF-MIB.py; pysmi: IF_MIB.py
    for candidate in [
        out_dir / f"{mib}.py",
        out_dir / f"{mib.replace('-', '_')}.py",
    ]:
        if candidate.exists():
            return candidate
    for f in out_dir.glob("*.py"):
        if mib.replace("-", "") in f.stem.replace("_", ""):
            return f
    return None


def _check_mibtable_no_syntax(text: str) -> dict[str, Any]:
    """P1: MibTable and MibTableRow constructors should not receive a syntax arg."""
    lines = text.splitlines()
    violations: list[str] = []
    for i, line in enumerate(lines):
        if not ("MibTable(" in line or "MibTableRow(" in line):
            continue
        # Collect the constructor block (next 5 lines)
        block = "\n".join(lines[i: i + 6])
        # Bad: a syntax class call follows the OID tuple
        if re.search(r"OctetString\(\)|Integer32\(\)|TODO:", block):
            violations.append(line.strip()[:80])
    return {"ok": len(violations) == 0, "violations": violations}


def _check_tc_desc_guard(text: str) -> dict[str, Any]:
    # TC description guard check.
    # Valid: same-line guard or block guard on preceding line.
    # Flags bare class-body 'description = """' with no guard.
    lines = text.splitlines()
    guarded_count = 0
    unguarded: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Pattern 1: guard and assignment on same line
        if re.search(r"mibBuilder\.loadTexts.*description\s*=\s*\"\"\"", line):
            guarded_count += 1
            continue
        # Pattern 2: bare description = """ line
        if not re.match(r'^description\s*=\s*"""', stripped):
            continue
        # Check if previous non-blank line is a guard
        prev = ""
        for j in range(i - 1, max(i - 3, -1), -1):
            if lines[j].strip():
                prev = lines[j]
                break
        if "mibBuilder.loadTexts" in prev:
            guarded_count += 1
        else:
            unguarded.append(line.rstrip()[:80])

    return {
        "ok": len(unguarded) == 0,
        "guarded_count": guarded_count,
        "unguarded_samples": unguarded[:3],
    }


def _extract_setobjects(text: str) -> list[dict[str, Any]]:
    """Extract setObjects calls with their module attributions."""
    lines = text.splitlines()
    results = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if ".setObjects(" in line:
            obj_name = line.split(".setObjects")[0].strip()
            tuples = []
            j = i + 1
            while j < len(lines) and ")" not in lines[j - 1] or j == i + 1:
                m = re.search(r"\('([^']+)',\s*'([^']+)'\)", lines[j])
                if m:
                    tuples.append({"module": m.group(1), "object": m.group(2)})
                if ")" in lines[j] and not ".setObjects(" in lines[j]:
                    break
                j += 1
                if j > i + 20:
                    break
            results.append({"name": obj_name, "tuples": tuples})
        i += 1
    return results


def deep_pysnmp_inspect(py_file: pathlib.Path, mib: str) -> dict[str, Any]:
    if not py_file.exists():
        return {"error": "file_missing"}
    try:
        text = py_file.read_text(errors="replace")
    except Exception as e:
        return {"error": str(e)}

    lines = text.splitlines()

    # Import preamble checks — accept both single and double quotes
    has_snmpv2_smi = "SNMPv2-SMI" in text
    has_snmpv2_tc = "SNMPv2-TC" in text
    has_asn1 = bool(re.search(r"""['"]ASN1['"]""", text))
    has_asn1_ref = "ASN1-REFINEMENT" in text
    standard_guard = any("if 'mibBuilder' not in globals()" in l for l in lines)
    own_instance = any(re.search(r"\bMibBuilder\(\)", l) for l in lines)

    # TODO quality check
    todo_count = text.count("# TODO")

    # MibTable/MibTableRow check
    mibtable_check = _check_mibtable_no_syntax(text)

    # TC description guard check
    tc_check = _check_tc_desc_guard(text)

    # setObjects
    setobjects = _extract_setobjects(text)

    # Counts per class — handles both pysmi 2.0 style (_X_Object = MibTable)
    # and trishul-smi style (x = MibTable(...))
    mibtable_count = len(re.findall(r"=\s*MibTable[\s(]", text))
    mibtablerow_count = len(re.findall(r"=\s*MibTableRow[\s(]", text))
    mibtablecol_count = len(re.findall(r"=\s*MibTableColumn[\s(]", text))
    mibscalar_count = len(re.findall(r"=\s*MibScalar[\s(]", text))
    tc_class_count = len(re.findall(r"class \w+\(TextualConvention,", text))

    return {
        "lines": len(lines),
        "todo_count": todo_count,
        "mibbuilder_guard_ok": standard_guard and not own_instance,
        "imports": {
            "SNMPv2-SMI": has_snmpv2_smi,
            "SNMPv2-TC": has_snmpv2_tc,
            "ASN1": has_asn1,
            "ASN1-REFINEMENT": has_asn1_ref,
        },
        "object_counts": {
            "MibTable": mibtable_count,
            "MibTableRow": mibtablerow_count,
            "MibTableColumn": mibtablecol_count,
            "MibScalar": mibscalar_count,
            "TextualConvention": tc_class_count,
        },
        "mibtable_no_syntax": mibtable_check,
        "tc_desc_guard": tc_check,
        "setobjects": setobjects,
    }


# ---------------------------------------------------------------------------
# Markdown report builder
# ---------------------------------------------------------------------------

TICK = "✓"
CROSS = "✗"
NA = "—"


def _reduction(a: Any, b: Any) -> str:
    try:
        if a and b:
            return f"{round((1 - int(b) / int(a)) * 100)}% smaller"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return NA


def _yn(v: Any) -> str:
    if v is True:
        return TICK
    if v is False:
        return CROSS
    return str(v) if v is not None else NA


def build_report(results: dict[str, Any]) -> str:
    meta = results.get("meta", {})
    mibs = meta.get("mibs", [])
    tools_data = results.get("tools", {})
    cross = results.get("cross_json", {})
    cross_nt = results.get("cross_no_texts", {})
    cross_pysnmp = results.get("cross_pysnmp", {})

    # identify tool slugs by role
    pysmi_slug = next((s for s in tools_data if "pysmi" in s), None)
    tsmi_slug = next((s for s in tools_data if "trishul" in s), None)

    pysmi_ver = tools_data.get(pysmi_slug, {}).get("installed_version", "?") if pysmi_slug else "?"
    tsmi_ver = tools_data.get(tsmi_slug, {}).get("installed_version", "?") if tsmi_slug else "?"

    lines: list[str] = []

    def h(level: int, text: str) -> None:
        lines.append("")
        lines.append("#" * level + " " + text)
        lines.append("")

    def table(headers: list[str], rows: list[list[str]]) -> None:
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        lines.append("")

    lines.append(f"# MIB Compiler Comparison: pysmi 2.0 vs trishul-smi")
    lines.append("")
    lines.append(f"Run at: {meta.get('run_at', '?')}")
    lines.append(f"pysmi: `{pysmi_ver}`  |  trishul-smi: `{tsmi_ver}`")
    lines.append(f"MIBs: {', '.join(mibs)}")
    lines.append("")

    # -----------------------------------------------------------------------
    h(2, "1. Timing")

    timing_rows = []
    for slug, td in tools_data.items():
        wt = td.get("with_texts", {})
        nt = td.get("no_texts", {})
        timing_rows.append([
            slug,
            wt.get("cold_s", NA),
            wt.get("warm_s", NA),
            nt.get("cold_s", NA),
        ])
    table(
        ["Tool", "with-texts cold (s)", "with-texts warm (s)", "no-texts cold (s)"],
        timing_rows,
    )

    # -----------------------------------------------------------------------
    h(2, "2. JSON Output — Object Coverage")

    for mib in mibs:
        cmp = cross.get(mib, {})
        if "error" in cmp:
            lines.append(f"**{mib}**: missing output file")
            continue

        h(3, mib)

        p_total = cmp.get("pysmi_total", NA)
        t_objs = cmp.get("tsmi_objects", NA)
        t_notifs = cmp.get("tsmi_notifications", NA)
        t_types = cmp.get("tsmi_types", NA)
        t_total = cmp.get("tsmi_total", NA)
        common = cmp.get("common_count", NA)

        table(
            ["", f"pysmi {pysmi_ver}", f"trishul-smi {tsmi_ver}"],
            [
                ["Named objects (flat)", p_total, "—"],
                ["objects (OBJECT-TYPE etc)", "—", t_objs],
                ["notifications", "—", t_notifs],
                ["types (TCs)", "—", t_types],
                ["Total named items", p_total, t_total],
                ["Common names", common, common],
            ],
        )

        pysmi_only = cmp.get("pysmi_only", [])
        tsmi_only = cmp.get("tsmi_only", [])
        if pysmi_only:
            lines.append(f"**pysmi-only** ({len(pysmi_only)}): "
                         f"`{'`, `'.join(pysmi_only[:20])}`"
                         + (" …" if len(pysmi_only) > 20 else ""))
            lines.append("")
        if tsmi_only:
            lines.append(f"**tsmi-only** ({len(tsmi_only)}): "
                         f"`{'`, `'.join(tsmi_only[:20])}`"
                         + (" …" if len(tsmi_only) > 20 else ""))
            lines.append("")

    # -----------------------------------------------------------------------
    h(2, "3. JSON Field Agreement — IF-MIB OBJECT-TYPE Objects")

    cmp_if = cross.get("IF-MIB", {})
    if "error" not in cmp_if:
        n = cmp_if.get("objecttype_common", 0)
        table(
            ["Metric", "Result"],
            [
                ["Common OBJECT-TYPE objects compared", n],
                ["OID path agreement", cmp_if.get("oid_agreement", NA)],
                ["nodetype agreement", cmp_if.get("nodetype_agreement", NA)],
                ["status agreement", cmp_if.get("status_agreement", NA)],
                ["max-access agreement (normalised)", cmp_if.get("maxaccess_agreement", NA)],
            ],
        )

    # -----------------------------------------------------------------------
    h(2, "4. Sample Object Deep-Dive — IF-MIB")

    samples = cmp_if.get("samples", {})
    for sample_name, s in samples.items():
        h(3, sample_name)
        p = s.get("pysmi", {})
        t = s.get("tsmi", {})

        p_syntax = p.get("syntax")
        if isinstance(p_syntax, dict):
            p_syntax_str = p_syntax.get("type", str(p_syntax))
            if isinstance(p_syntax_str, dict):
                p_syntax_str = p_syntax_str.get("name", str(p_syntax_str))
        else:
            p_syntax_str = str(p_syntax) if p_syntax is not None else NA

        rows = [
            ["class / object_type", p.get("class", NA), t.get("object_type", NA)],
            ["oid / oid_path", str(p.get("oid", NA))[:50], str(t.get("oid_path", NA))[:50]],
            ["nodetype", p.get("nodetype", NA), t.get("nodetype", NA)],
            ["status", p.get("status", NA), t.get("status", NA)],
            ["max_access", p.get("maxaccess", NA), t.get("max_access", NA)],
            ["syntax (raw)", p_syntax_str, t.get("syntax", NA)],
            ["constraints", NA, str(t.get("constraints", NA))[:60]],
            ["description present", _yn(p.get("has_description")), _yn(t.get("has_description"))],
            ["index", _yn(p.get("has_index")), _yn(t.get("has_index"))],
        ]
        if sample_name == "linkDown":
            rows.append(["members", str(p.get("members_raw", NA))[:60],
                         str(t.get("members", NA))[:80]])
        table([f"Field", f"pysmi {pysmi_ver}", f"trishul-smi {tsmi_ver}"], rows)

    # -----------------------------------------------------------------------
    h(2, "5. Module Metadata — IF-MIB")

    meta_cmp = cmp_if.get("metadata", {})
    if meta_cmp:
        pm = meta_cmp.get("pysmi", {})
        tm = meta_cmp.get("tsmi", {})
        table(
            ["Field", f"pysmi {pysmi_ver}", f"trishul-smi {tsmi_ver}"],
            [
                ["lastupdated", pm.get("lastupdated", NA), tm.get("lastupdated", NA)],
                ["organization", _yn(pm.get("organization")), _yn(tm.get("organization"))],
                ["contactinfo", _yn(pm.get("contactinfo")), _yn(tm.get("contactinfo"))],
                ["description", _yn(pm.get("description")), _yn(tm.get("description"))],
                ["revisions count", pm.get("revisions", NA), tm.get("revisions", NA)],
            ],
        )

    # -----------------------------------------------------------------------
    h(2, "6. trishul-smi v0.3.0 New Fields — IF-MIB")

    v030 = cmp_if.get("tsmi_v030", {})
    if v030:
        table(
            ["Check", "Result", "Notes"],
            [
                ["OBJECT-TYPE objects with nodetype",
                 v030.get("objects_with_nodetype", NA),
                 "table/row/column/scalar"],
                ["Objects with constraints dict",
                 len(v030.get("objects_with_constraints", [])),
                 ", ".join(v030.get("objects_with_constraints", [])[:5])],
                ["notification members attributed",
                 _yn(v030.get("notif_members_attributed")),
                 "{module, object} dicts"],
                ["lastupdated ISO 8601",
                 _yn(v030.get("lastupdated_iso")),
                 "YYYY-MM-DD format"],
                ["revision dates ISO 8601",
                 _yn(v030.get("revisions_iso")),
                 "all revisions"],
                ["module description present",
                 _yn(v030.get("module_description_present")),
                 "from MODULE-IDENTITY"],
            ],
        )

    # -----------------------------------------------------------------------
    h(2, "7. No-texts Mode")

    for mib in mibs:
        nt_cmp = cross_nt.get(mib, {})
        if "error" in nt_cmp:
            continue

        # File size comparison
        pysmi_td = tools_data.get(pysmi_slug, {}) if pysmi_slug else {}
        tsmi_td = tools_data.get(tsmi_slug, {}) if tsmi_slug else {}

        p_wt_sz = (pysmi_td.get("with_texts", {}).get("json_sizes", {}).get(mib))
        p_nt_sz = (pysmi_td.get("no_texts", {}).get("json_sizes", {}).get(mib))
        t_wt_sz = (tsmi_td.get("with_texts", {}).get("json_sizes", {}).get(mib))
        t_nt_sz = (tsmi_td.get("no_texts", {}).get("json_sizes", {}).get(mib))

        def _sz(sz: int | None) -> str:
            return f"{sz // 1024} KB" if sz else NA

        h(3, mib)
        table(
            ["Metric", f"pysmi {pysmi_ver}", f"trishul-smi {tsmi_ver}"],
            [
                ["with-texts size", _sz(p_wt_sz), _sz(t_wt_sz)],
                ["no-texts size", _sz(p_nt_sz), _sz(t_nt_sz)],
                ["size reduction", _reduction(p_wt_sz, p_nt_sz), _reduction(t_wt_sz, t_nt_sz)],
                ["descriptions stripped", _yn(nt_cmp.get("pysmi_descriptions_stripped")),
                 _yn(nt_cmp.get("tsmi_descriptions_stripped"))],
                ["module_metadata stripped", NA, _yn(nt_cmp.get("tsmi_metadata_stripped"))],
            ],
        )

    # -----------------------------------------------------------------------
    h(2, "8. pysnmp Output — IF-MIB")

    cp = cross_pysnmp.get("IF-MIB", {})
    p_py_wt = cp.get("pysmi_with_texts", {})
    p_py_nt = cp.get("pysmi_no_texts", {})
    t_py_wt = cp.get("tsmi_with_texts", {})
    t_py_nt = cp.get("tsmi_no_texts", {})

    h(3, "8a. Line counts")
    table(
        ["Mode", f"pysmi {pysmi_ver}", f"trishul-smi {tsmi_ver}"],
        [
            ["with texts", p_py_wt.get("lines", NA), t_py_wt.get("lines", NA)],
            ["no texts", p_py_nt.get("lines", NA), t_py_nt.get("lines", NA)],
            ["reduction", _reduction(p_py_wt.get("lines"), p_py_nt.get("lines")),
             _reduction(t_py_wt.get("lines"), t_py_nt.get("lines"))],
        ],
    )

    h(3, "8b. Object class counts (with texts)")
    p_cnt = p_py_wt.get("object_counts", {})
    t_cnt = t_py_wt.get("object_counts", {})
    table(
        ["Class", f"pysmi {pysmi_ver}", f"trishul-smi {tsmi_ver}"],
        [
            ["MibTable", p_cnt.get("MibTable", NA), t_cnt.get("MibTable", NA)],
            ["MibTableRow", p_cnt.get("MibTableRow", NA), t_cnt.get("MibTableRow", NA)],
            ["MibTableColumn", p_cnt.get("MibTableColumn", NA), t_cnt.get("MibTableColumn", NA)],
            ["MibScalar", p_cnt.get("MibScalar", NA), t_cnt.get("MibScalar", NA)],
            ["TextualConvention", p_cnt.get("TextualConvention", NA), t_cnt.get("TextualConvention", NA)],
        ],
    )

    h(3, "8c. Quality checks")

    def _tc_guard(d: dict) -> str:
        tc = d.get("tc_desc_guard", {})
        if tc.get("ok"):
            return f"{TICK} ({tc.get('guarded_count', 0)} guarded)"
        return f"{CROSS} {tc.get('unguarded_samples', [])}"

    def _mibtable_check(d: dict) -> str:
        mt = d.get("mibtable_no_syntax", {})
        return TICK if mt.get("ok") else f"{CROSS} {mt.get('violations', [])}"

    table(
        ["Check", f"pysmi {pysmi_ver}", f"trishul-smi {tsmi_ver}"],
        [
            ["mibBuilder guard", _yn(p_py_wt.get("mibbuilder_guard_ok")), _yn(t_py_wt.get("mibbuilder_guard_ok"))],
            ["SNMPv2-SMI import", _yn(p_py_wt.get("imports", {}).get("SNMPv2-SMI")), _yn(t_py_wt.get("imports", {}).get("SNMPv2-SMI"))],
            ["SNMPv2-TC import", _yn(p_py_wt.get("imports", {}).get("SNMPv2-TC")), _yn(t_py_wt.get("imports", {}).get("SNMPv2-TC"))],
            ["ASN1 import", _yn(p_py_wt.get("imports", {}).get("ASN1")), _yn(t_py_wt.get("imports", {}).get("ASN1"))],
            ["MibTable: no syntax arg", _mibtable_check(p_py_wt), _mibtable_check(t_py_wt)],
            ["TC description loadTexts guard", _tc_guard(p_py_wt), _tc_guard(t_py_wt)],
            ["TODO comments", p_py_wt.get("todo_count", NA), t_py_wt.get("todo_count", NA)],
        ],
    )

    h(3, "8d. setObjects (IF-MIB notifications)")
    for tool_label, d in [
        (f"pysmi {pysmi_ver}", p_py_wt),
        (f"trishul-smi {tsmi_ver}", t_py_wt),
    ]:
        so_list = d.get("setobjects", [])
        if not so_list:
            lines.append(f"**{tool_label}**: no setObjects calls found")
            lines.append("")
        else:
            lines.append(f"**{tool_label}**:")
            for so in so_list[:3]:
                tuples_str = ", ".join(f"({t['module']}, {t['object']})" for t in so["tuples"])
                lines.append(f"  `{so['name']}.setObjects({tuples_str})`")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comprehensive MIB compiler comparison.")
    parser.add_argument("--tools", default="pysmi==2.0.0,trishul-smi",
                        help="Comma-separated tool==version specs")
    parser.add_argument("--mib-dir", default=str(pathlib.Path.home() / "test" / "mibs"))
    parser.add_argument("--mibs", default=(
        "IF-MIB,IP-MIB,SNMPv2-MIB,ENTITY-MIB,HOST-RESOURCES-MIB,"
        "TCP-MIB,UDP-MIB,IANAifType-MIB,INET-ADDRESS-MIB,UUID-TC-MIB,"
        "IANA-ENTITY-MIB,SNMP-FRAMEWORK-MIB"
    ))
    parser.add_argument("--work-dir", default="/tmp/mib-compare")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--skip-compile", action="store_true",
                        help="Skip all compile steps (use existing output dirs)")
    parser.add_argument("--local-tsmi", default=None, metavar="PATH")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_specs = [t.strip() for t in args.tools.split(",") if t.strip()]
    mibs = [m.strip() for m in args.mibs.split(",") if m.strip()]
    mib_dir = pathlib.Path(args.mib_dir).expanduser().resolve()
    work_dir = pathlib.Path(args.work_dir).expanduser().resolve()
    local_tsmi = pathlib.Path(args.local_tsmi).expanduser().resolve() if args.local_tsmi else None

    run_at = datetime.datetime.now(datetime.timezone.utc)
    run_ts = run_at.strftime("%Y-%m-%dT%H-%M-%S")
    output_root = pathlib.Path(
        args.output_dir if args.output_dir else _DEFAULT_OUTPUT_DIR
    ).expanduser().resolve()
    run_dir = output_root / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    log(f"=== MIB Compiler Comparison ===")
    log(f"Tools:  {tool_specs}")
    log(f"MIBs:   {mibs[:4]}{'...' if len(mibs) > 4 else ''}")
    log(f"Run:    {run_dir}")

    results: dict[str, Any] = {
        "meta": {
            "run_at": str(run_at),
            "mib_dir": str(mib_dir),
            "mibs": mibs,
            "tools": tool_specs,
            "local_tsmi": str(local_tsmi) if local_tsmi else None,
        },
        "tools": {},
    }

    pysmi_slug: str | None = None
    tsmi_slug: str | None = None

    for tool_spec in tool_specs:
        name, version = tool_name_and_version(tool_spec)
        log(f"\n--- {tool_spec} ---")

        venv_info = setup_venv(tool_spec, work_dir, args.skip_install, local_tsmi)
        slug = venv_info["slug"]
        venv_dir = pathlib.Path(venv_info["venv_dir"])
        is_tsmi = (name == "trishul-smi")

        if is_tsmi:
            tsmi_slug = slug
        else:
            pysmi_slug = slug

        td: dict[str, Any] = {
            "installed_version": venv_info.get("installed_version"),
            "install_error": venv_info.get("install_error"),
        }

        # ---------- JSON: with-texts cold ----------
        wt_json_dir = work_dir / f"json-wt-{slug}"
        if not args.skip_compile:
            log(f"  [json with-texts cold]")
            if is_tsmi:
                r = run_tsmi_json(venv_dir, mib_dir, wt_json_dir, mibs, with_texts=True, cold=True)
            else:
                r = run_pysmi_json(venv_dir, mib_dir, wt_json_dir, mibs, with_texts=True, cold=True)
            cold_s = r["time_s"]
        else:
            cold_s = NA

        # ---------- JSON: with-texts warm (tsmi only — pysmi doesn't have a meaningful warm) ----------
        warm_s: Any = NA
        if is_tsmi and not args.skip_compile:
            log(f"  [json with-texts warm]")
            warm_s = warm_tsmi(venv_dir, mib_dir, mibs)
            r2 = run_tsmi_json(venv_dir, mib_dir, wt_json_dir, mibs, with_texts=True, cold=False)
            warm_s = r2["time_s"]

        # ---------- JSON: no-texts cold ----------
        nt_json_dir = work_dir / f"json-nt-{slug}"
        if not args.skip_compile:
            log(f"  [json no-texts cold]")
            if is_tsmi:
                r_nt = run_tsmi_json(venv_dir, mib_dir, nt_json_dir, mibs, with_texts=False, cold=True)
            else:
                r_nt = run_pysmi_json(venv_dir, mib_dir, nt_json_dir, mibs, with_texts=False, cold=True)
            nt_cold_s = r_nt["time_s"]
        else:
            nt_cold_s = NA

        td["with_texts"] = {
            "cold_s": cold_s,
            "warm_s": warm_s,
            "json_dir": str(wt_json_dir),
            "json_sizes": dir_sizes(wt_json_dir, mibs, ".json"),
        }
        td["no_texts"] = {
            "cold_s": nt_cold_s,
            "json_dir": str(nt_json_dir),
            "json_sizes": dir_sizes(nt_json_dir, mibs, ".json"),
        }

        # ---------- pysnmp: with-texts and no-texts ----------
        py_wt_dir = work_dir / f"pysnmp-wt-{slug}"
        py_nt_dir = work_dir / f"pysnmp-nt-{slug}"
        if not args.skip_compile:
            log(f"  [pysnmp with-texts]")
            if is_tsmi:
                run_tsmi_pysnmp(venv_dir, mib_dir, py_wt_dir, ["IF-MIB"], with_texts=True)
                log(f"  [pysnmp no-texts]")
                run_tsmi_pysnmp(venv_dir, mib_dir, py_nt_dir, ["IF-MIB"], with_texts=False)
            else:
                run_pysmi_pysnmp(venv_dir, mib_dir, py_wt_dir, ["IF-MIB"], with_texts=True)
                log(f"  [pysnmp no-texts]")
                run_pysmi_pysnmp(venv_dir, mib_dir, py_nt_dir, ["IF-MIB"], with_texts=False)

        td["pysnmp_wt_dir"] = str(py_wt_dir)
        td["pysnmp_nt_dir"] = str(py_nt_dir)
        results["tools"][slug] = td
        log(f"  done.")

    # -----------------------------------------------------------------------
    # Cross-tool JSON comparison (with-texts outputs)
    # -----------------------------------------------------------------------
    if pysmi_slug and tsmi_slug:
        p_wt_dir = pathlib.Path(results["tools"][pysmi_slug]["with_texts"]["json_dir"])
        t_wt_dir = pathlib.Path(results["tools"][tsmi_slug]["with_texts"]["json_dir"])
        p_nt_dir = pathlib.Path(results["tools"][pysmi_slug]["no_texts"]["json_dir"])
        t_nt_dir = pathlib.Path(results["tools"][tsmi_slug]["no_texts"]["json_dir"])

        log(f"\n[cross-tool JSON comparison]")
        results["cross_json"] = cross_json_compare(p_wt_dir, t_wt_dir, mibs)
        results["cross_no_texts"] = cross_no_texts(p_nt_dir, t_nt_dir, mibs)

        # Cross-tool pysnmp comparison
        log(f"[cross-tool pysnmp comparison]")
        pysnmp_cross: dict[str, Any] = {}
        p_py_wt_dir = pathlib.Path(results["tools"][pysmi_slug]["pysnmp_wt_dir"])
        p_py_nt_dir = pathlib.Path(results["tools"][pysmi_slug]["pysnmp_nt_dir"])
        t_py_wt_dir = pathlib.Path(results["tools"][tsmi_slug]["pysnmp_wt_dir"])
        t_py_nt_dir = pathlib.Path(results["tools"][tsmi_slug]["pysnmp_nt_dir"])

        for mib in ["IF-MIB"]:
            p_f_wt = _find_pysnmp_file(p_py_wt_dir, mib)
            p_f_nt = _find_pysnmp_file(p_py_nt_dir, mib)
            t_f_wt = _find_pysnmp_file(t_py_wt_dir, mib)
            t_f_nt = _find_pysnmp_file(t_py_nt_dir, mib)

            pysnmp_cross[mib] = {
                "pysmi_with_texts": deep_pysnmp_inspect(p_f_wt, mib) if p_f_wt else {"error": "missing"},
                "pysmi_no_texts": deep_pysnmp_inspect(p_f_nt, mib) if p_f_nt else {"error": "missing"},
                "tsmi_with_texts": deep_pysnmp_inspect(t_f_wt, mib) if t_f_wt else {"error": "missing"},
                "tsmi_no_texts": deep_pysnmp_inspect(t_f_nt, mib) if t_f_nt else {"error": "missing"},
            }
        results["cross_pysnmp"] = pysnmp_cross

    # -----------------------------------------------------------------------
    # Write outputs
    # -----------------------------------------------------------------------
    results_path = run_dir / "results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"\nResults JSON → {results_path}")

    report_md = build_report(results)
    summary_path = run_dir / "summary.md"
    summary_path.write_text(report_md + "\n")
    log(f"Summary MD  → {summary_path}")

    print(report_md)


if __name__ == "__main__":
    main()
