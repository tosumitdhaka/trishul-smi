# Contributing

---

## Setup

```bash
git clone https://github.com/tosumitdhaka/trishul-smi
cd trishul-smi
pip install -e ".[dev]"
```

---

## Common commands

```bash
# Run the full test suite
pytest

# Run a single test file
pytest tests/test_compiler.py

# Run a single test by name (with output)
pytest tests/test_compiler.py::test_compile_success -xvs

# Run with coverage
pytest --cov=trishul_smi --cov-report=term-missing:skip-covered

# Type checking
mypy trishul_smi

# Lint (auto-fix)
ruff check trishul_smi tests --fix

# Format
ruff format trishul_smi tests
```

---

## Project conventions

- **Type annotations** — all public APIs must be fully annotated; `mypy --strict` must pass with zero errors.
- **No `**kwargs`** in public APIs — all options must be explicit typed parameters.
- **No pickle** — the disk cache uses `orjson` JSON serialization only.
- **Async I/O, sync logic** — readers and resolver are async; formatters are sync.
- **No circular imports** — use `TYPE_CHECKING` guards for forward references.
- **Atomic file writes** — use temp-file + rename for any file output (the cache already does this).
- **One responsibility per module** — reader fetches, parser parses, resolver resolves, formatter formats.

---

## Running the CLI locally

```bash
# After pip install -e ".[dev]"
tsmi compile IF-MIB --mib-dir /usr/share/snmp/mibs
tsmi compile IF-MIB --online --verbose
```

---

## CI

Pull requests must pass all three CI gates before merge:

1. **Lint** — `ruff check` + `ruff format --check`
2. **Typecheck** — `mypy trishul_smi`
3. **Tests** — `pytest` across Python 3.10–3.13

See [`workflows/ci.yml`](workflows/ci.yml).
