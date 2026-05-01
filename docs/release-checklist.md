# Release Checklist

Follow this checklist for every release. Steps must be completed in order.

---

## 1. Pre-release verification

- [ ] All CI checks green on `main` (lint, typecheck, tests — Python 3.10–3.13)
- [ ] `mypy trishul_smi` passes with zero errors
- [ ] `ruff check trishul_smi tests` passes with zero warnings
- [ ] No open issues tagged for this milestone that are not resolved

---

## 2. Version bump

- [ ] Update `version` in `pyproject.toml`
- [ ] Add a new section to `docs/CHANGELOG.md` following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format:
  - Date in `YYYY-MM-DD`
  - `### Added` / `### Changed` / `### Fixed` / `### Removed` as applicable
  - `### Known Limitations` if any deferred issues remain
  - Add a reference link at the bottom: `[x.y.z]: https://github.com/tosumitdhaka/trishul-smi/releases/tag/vx.y.z`
- [ ] Update `docs/architecture.md` `Last updated` date if the architecture changed
- [ ] Update `docs/plan.md` status header if applicable

---

## 3. Final checks

- [ ] Run the full test suite one last time on a clean install:
  ```bash
  pip install -e ".[dev]"
  pytest
  ```
- [ ] Smoke-test the CLI end-to-end:
  ```bash
  trishul-smi compile IF-MIB -f json -f pysnmp --verbose
  ```
- [ ] Verify the built package installs and runs correctly:
  ```bash
  hatch build
  pip install dist/trishul_smi-x.y.z-py3-none-any.whl
  trishul-smi version
  trishul-smi compile IF-MIB
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
