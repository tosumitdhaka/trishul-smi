# Release Checklist

Follow this checklist for every release. Steps must be completed in order.

---

## 1. Pre-release verification

- [ ] All CI checks green on `main` (lint, typecheck, tests — Python 3.10–3.13)
- [ ] Lint passes with zero warnings:
  ```bash
  ruff check trishul_smi tests
  ```
- [ ] Formatting is clean:
  ```bash
  ruff format trishul_smi tests --check
  ```
- [ ] Type checking passes with zero errors:
  ```bash
  mypy trishul_smi
  ```
- [ ] Full test suite passes:
  ```bash
  pytest
  ```
- [ ] Coverage meets the project threshold (≥ 95%):
  ```bash
  pytest --cov=trishul_smi --cov-report=term-missing
  ```
- [ ] No open issues tagged for this milestone that are not resolved

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

- [ ] Run the full quality gate on a clean install:
  ```bash
  pip install -e ".[dev]"
  ruff check trishul_smi tests
  ruff format trishul_smi tests --check
  mypy trishul_smi
  pytest --cov=trishul_smi
  ```
- [ ] Smoke-test the CLI end-to-end **from a clean venv** (not the dev install):
  ```bash
  python -m venv /tmp/trishul-release-test
  /tmp/trishul-release-test/bin/pip install dist/trishul_smi-x.y.z-py3-none-any.whl
  /tmp/trishul-release-test/bin/trishul-smi version
  /tmp/trishul-release-test/bin/trishul-smi compile IF-MIB IP-MIB -f json -f pysnmp --online --verbose
  ```
  Both MIBs must show ✅. This catches grammar gaps that unit tests can miss because
  test fixtures are hand-written and may not exercise real-world MIB syntax.

- [ ] Verify wheel has no duplicate entries (caused `0.1.0` PyPI rejection):
  ```bash
  python -c "
  import zipfile, collections
  z = zipfile.ZipFile('dist/trishul_smi-x.y.z-py3-none-any.whl')
  dupes = [n for n, c in collections.Counter(z.namelist()).items() if c > 1]
  print('DUPLICATES:', dupes or 'none')
  "
  ```

- [ ] If `~/test/mibs/` (or any local MIB corpus) is available, compile all of them:
  ```bash
  trishul-smi compile $(ls ~/test/mibs/*.mib ~/test/mibs/*.my 2>/dev/null | xargs -n1 basename | sed 's/\..*//' | sort -u | tr '\n' ' ') \
    --mib-dir ~/test/mibs -f json -f pysnmp --verbose
  ```
  Every MIB in the corpus must show ✅ before tagging.

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
