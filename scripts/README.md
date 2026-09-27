# scripts/

Developer utilities for the ModelWatcher backend.

## Structure

| Directory | Purpose |
|-----------|---------|
| `tests/` | Pytest suite, shared fixtures in `tests/conftest.py`. Run via `npm test` or `python3 -m pytest scripts/tests/ -v`. |
| `util/` | Reusable infrastructure scripts (circular-import checker, synthetic DB generator). |

## Running the tests

```bash
npm test                          # or: python3 -m pytest scripts/tests/ -v
```

What each test file covers is listed in
[docs/DEVELOPMENT.md#testing](../docs/DEVELOPMENT.md#testing).

## Utilities

Scripts import `backend` for its paths and helpers, so run them from the project
root in module form:

- `util/_check_imports.py` - scans `backend/` for lazy imports and reports the
  no-circular-imports invariant. Run: `python3 -m scripts.util._check_imports`
- `util/scale_test_db.py` - generates a synthetic SQLite database (backend schema),
  matching YAML configs and favicons for scale testing. Run:
  `python3 -m scripts.util.scale_test_db --help` for every option.
