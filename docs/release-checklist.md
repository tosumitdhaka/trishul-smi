# Release Checklist

Follow this checklist for every release. Steps must be completed in order.

---

## 1. Pre-release verification

- [ ] All CI checks green on `main` (lint, typecheck, tests — Python 3.10–3.13)
- [ ] Run the release gate script — it covers lint, format, mypy, tests, coverage, wheel
  build, duplicate check, and CLI smoke test in one step:
  ```bash
  .venv/bin/python scripts/run_release_gate.py
  ```
  All steps must show `[PASS]` and `Overall: PASSED`. Options:
  ```bash
  # keep the temporary wheel-test venv for inspection
  .venv/bin/python scripts/run_release_gate.py --keep-wheel-test-venv

  # emit a JSON report (useful for CI artifact upload)
  .venv/bin/python scripts/run_release_gate.py --json

  # smoke-test different MIBs
  .venv/bin/python scripts/run_release_gate.py --smoke-mibs IF-MIB IP-MIB SNMPv2-MIB
  ```
- [ ] No open issues tagged for this milestone that are not resolved
- [ ] If `~/test/mibs/` (or any local MIB corpus) is available, compile all of them:
  ```bash
  .venv/bin/trishul-smi compile --mib-dir ~/test/mibs -f json -f pysnmp --cache-dir "" --verbose
  ```
  Every MIB in the corpus must show ✅ before tagging.
  Judge success by the CLI result rows, not by output file count: alias source files such as
  `*-V1SMI.my` can declare the same canonical module name and overwrite the same artifact path.

---

## 2. Version bump

- [ ] Update `version` in `pyproject.toml`
- [ ] Update `VERSION` in `trishul_smi/version.py`
- [ ] Add a new section to `docs/CHANGELOG.md` following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format:
  - Date in `YYYY-MM-DD`
  - `### Added` / `### Changed` / `### Fixed` / `### Removed` as applicable
  - `### Known Limitations` if any deferred issues remain
  - Add a reference link at the bottom: `[x.y.z]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/vx.y.z`
- [ ] Update `docs/architecture.md` `Last updated` date if the architecture changed
- [ ] Update `docs/roadmap.md` to mark shipped items as `done`

---

## 3. Final checks

- [ ] Re-run the release gate after the version bump to confirm the new version flows through
  correctly (wheel name, `trishul-smi version` output):
  ```bash
  .venv/bin/pip install -e ".[dev]"
  .venv/bin/python scripts/run_release_gate.py
  ```

---

## 4. Tag and publish

- [ ] Commit the version bump and changelog: `git commit -m "chore: release vx.y.z"`
- [ ] Tag the commit: `git tag vx.y.z`
- [ ] Push tag: `git push origin vx.y.z`
- [ ] Confirm the `release` GitHub Actions workflow completes successfully:
  - Test → Build → PyPI publish → GitHub Release created
- [ ] Verify the package is live: `pip install trishul-smi==x.y.z`

---

## 5. Post-release

- [ ] Close the milestone on GitHub (if used)
- [ ] Update any open issues that were resolved in this release
- [ ] Note any known limitations in the GitHub Release description if not already in the changelog
