# scripts/

Developer utilities for the ModelWatcher backend.

## Structure

| Directory | Purpose |
|-----------|---------|
| `tests/` | Pytest unit tests for `backend/model_info.py` extraction rules. Run via `npm test` or `python3 -m pytest scripts/tests/ -v`. |
| `util/` | Reusable infrastructure scripts (circular-import checker, synthetic DB generator). |

## Running the tests

```bash
npm test                          # or: python3 -m pytest scripts/tests/ -v
```

The tests are pure unit tests - they call `extract_model_info()` with synthetic
flat-input dicts and assert the extracted fields. No API keys, no network, no
database. They cover pricing normalization (per-token, per-million, cents-per-million),
capability detection (vision/tools/structured-output/cache/thinking), context window
resolution, suffix rules (Ollama), and two combined real-world model fixtures.

## Utilities

- `util/_check_imports.py` - scans `backend/` for lazy imports and reports the
  no-circular-imports invariant. Run: `python3 scripts/util/_check_imports.py`
- `util/scale_test_db.py` - generates a synthetic SQLite database with configurable
  provider/model counts and history depth for scale testing. See its docstring for
  usage.
