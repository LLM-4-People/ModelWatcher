# Development guide

This guide covers local setup, project structure, testing, and conventions for contributing to ModelWatcher.

## Table of contents

- [Local setup](#local-setup) - Prerequisites, dependencies, build, run
  - [Prerequisites](#prerequisites) - Python 3.13+, Node.js 22+
  - [Install dependencies](#install-dependencies) - pip + npm
  - [Build Tailwind CSS](#build-tailwind-css) - One-time and watch modes
  - [Run the server](#run-the-server) - uvicorn with --reload
- [Project structure](#project-structure) - Backend, frontend, config, scripts
- [Testing](#testing) - 89 tests across 9 files
- [Frontend conventions](#frontend-conventions) - Named exports, ES modules, Tailwind
- [Backend conventions](#backend-conventions) - Package architecture, naming, error handling
- [Adding a new provider](#adding-a-new-provider) - Env var, recreate, edit YAML
  - [Anthropic providers](#anthropic-providers) - Triggering the Anthropic streaming path
- [Adding a new test type](#adding-a-new-test-type) - 9-step guide
- [Config hot-reload](#config-hot-reload) - watchfiles, in-place mutation
- [Utility scripts](#utility-scripts) - Import checker, DB scale tester
- [Performance/stress test scripts](#performancestress-test-scripts) - Not included in repo

## Local setup

### Prerequisites

- **Python 3.13+**
- **Node.js 22+** (for Tailwind CSS build and SynBad audit tests)

### Install dependencies

```bash
# Python dependencies (runtime + dev)
python3 -m pip install -r requirements.txt -r requirements-dev.txt

# Node.js dependencies (Tailwind CSS, SynBad, perf test tools)
npm install
```

### Build Tailwind CSS

```bash
npm run build:css    # one-time build → frontend/tailwind.min.css
npm run watch:css    # auto-rebuild on file change (development)
```

Tailwind v4 uses CSS-native configuration (`@theme`, `@source`, `@custom-variant` in `frontend/input.css`). No `tailwind.config.js` is needed. Source paths include `./index.html` and `./js/*.js` so Tailwind scans both HTML and JS modules for class names.

> Tailwind only generates CSS for class names it finds at build time. New classes will not take effect until rebuilt. Dynamic class names (constructed via template literals at runtime) are NOT detected by the scanner - use explicit class maps (see `TIER_TEXT`, `TIER_BG` in `frontend/js/format.js`).

### Run the server

**Option 1: Run locally** (recommended for active development):

```bash
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload --reload-dir backend --loop uvloop
```

Or, set up config files and run directly:

```bash
cp config/app.yaml.example config/app.yaml   # edit for your environment
cp config/models.yaml.example config/models.yaml
python3 -m uvicorn backend.main:app --reload --reload-dir backend
```

**Option 2: Build and run in Docker** (for testing Docker builds or matching production):

```bash
cp compose.modelwatcher.build.yaml compose.modelwatcher.yaml
# Edit .env with your API keys
docker compose -f compose.modelwatcher.yaml up -d --build
docker logs -f modelwatcher
```

The build-based compose mounts the full source tree read-only and enables `--reload` for backend code changes. Config changes in `./config/` are hot-reloaded by `watchfiles` without container restart.

The dashboard is available at `http://localhost:8080`.

## Project structure

```
ModelWatcher/
├── backend/                     # Python backend package (27 modules)
│   ├── __init__.py
│   ├── state.py                 # Shared mutable state, paths, constants, logging, canonical labels
│   ├── security.py              # PII-safe error messages, stream error extraction
│   ├── prompts.py              # Word banks, random prompt generation
│   ├── websocket.py             # WSManager, WebSocket endpoint
│   ├── batch.py                 # PeriodicBatcher base class
│   ├── db.py                    # SQLite persistence (WAL mode, thread-safe writes)
│   ├── db_push.py              # Push subscription persistence (split from db.py)
│   ├── db_probe.py             # Audit/probe result persistence (split from db.py)
│   ├── schemas.py              # Pydantic request body models for OpenAPI
│   ├── validation.py            # Push key validation (P-256 curve check)
│   ├── metrics.py               # model_cache initialization
│   ├── model_info.py            # Provider/HuggingFace model metadata fetch
│   ├── models.py                # Model registry, provider lookup, grouping
│   ├── favicons.py             # Provider favicon extraction and caching
│   ├── config.py                # YAML loading, hot-reload, config watcher
│   ├── streaming.py            # SSE parsing, streaming tests, metrics computation
│   ├── notifications.py       # Degradation detection, notification dispatch
│   ├── push_routes.py         # Push subscription API routes, VAPID management
│   ├── stats.py                 # Composite scores, tiers, trends, chart data
│   ├── middleware.py           # Connection/size limit, security headers
│   ├── scheduler.py            # Test scheduling (benchmark, health, audit, probe)
│   ├── routes.py               # REST API route handlers, static serving
│   ├── audit.py                 # SynBad-based audit runner
│   ├── probe.py                 # Capability detection probes
│   ├── migrations.py           # SQLite schema migrations
│   └── main.py                  # FastAPI app factory, lifespan, wiring
├── frontend/
│   ├── index.html              # HTML structure + inline CSS (no inline JS)
│   ├── input.css               # Tailwind v4 input
│   ├── js/                      # 22 ES modules (no bundler, no build step)
│   ├── sw.js                    # Service worker (push-only)
│   ├── manifest.json           # PWA manifest
│   └── js/vendor/               # Chart.js + date-fns adapter (local, not CDN)
├── config/                      # 3 YAML config files + .example templates
│   ├── app.yaml.example
│   ├── models.yaml.example
│   └── audits.yaml.example
├── data/                        # SQLite DB, VAPID keys, favicons (gitignored)
├── scripts/
│   ├── tests/                   # Pytest unit tests (9 files)
│   └── util/                    # Infrastructure scripts
├── tests/                       # Performance/stress test scripts (Node.js)
├── requirements.txt             # Python runtime deps
├── requirements-dev.txt         # Python dev deps (adds pytest)
├── package.json                 # Node deps (Tailwind, SynBad, perf tools)
├── Dockerfile                   # Multi-stage: node CSS builder + python runtime
└── compose.modelwatcher.yaml         # Pre-built image compose config (pulls from ghcr.io)
└── compose.modelwatcher.build.yaml   # Build-based compose config (local development)
```

## Testing

```bash
npm test    # or: python3 -m pytest scripts/tests/ -v
```

89 tests across 9 files. Most tests are pure unit tests (extract_model_info, config validation, schema checks, dead-code detection) - no API keys, no network, no database. The `test_api_errors.py` file tests the live `/api/*` endpoints for error format uniformity and requires network access to the running server.

| Test file | What it covers |
|-----------|---------------|
| `test_api_errors.py` | API error responses are uniform (`{"error": "..."}`) across all routes |
| `test_config_no_defaults.py` | Config has no defaults in code - config is the sole source of truth |
| `test_db_split.py` | `db_push` and `db_probe` modules use live binding for `db._write_conn` (no stale `None` capture) |
| `test_error_logging.py` | No silent exception swallows in backend (no `except: pass`) |
| `test_pricing.py` | `extract_model_info()` pricing normalization (per-token, per-million, cents-per-million) |
| `test_rate_limits.py` | All rate limits come from config, none hardcoded |
| `test_rules.py` | `extract_model_info()` rules: context window, capabilities, thinking, modalities, Ollama suffix rules, two real-world fixtures |
| `test_schemas.py` | Pydantic body models match handler field expectations (no drift) |
| `test_ssoT_labels.py` | Single source of truth for labels - `state.py` owns, `/api/config` exposes, frontend does not redefine |

Each test file includes a docstring describing the bug family it catches.

## Frontend conventions

- **Named exports only** - no default exports. This makes dependencies explicit.
- **ES modules** - no bundler, no build step for JS. Served directly with `?v=` content-hash params for cache busting.
- **Mutable shared state** via exported `const` object (`state`). Primitive `let` exports use setter functions (`setChartReady()`) because live bindings are read-only for primitives.
- **No function duplication** - if two modules need the same logic, extract it to a shared module (`prefs.js` for notification prefs, `utils.js` for collapsibles/esc).
- **Tailwind v4** - CSS-native config in `input.css`. Use explicit class maps (complete string literals in source) so the JIT scanner can detect them. Never construct class names with template literals.
- **Custom color palette** - `accent` (blue), `success` (green), `warn` (amber), `danger` (red, with `danger-400` for Bad and `danger-700` for Critical), `teal`, `surface` (grays). Use these, not Tailwind's default `blue-400`, `green-500`, etc.
- **Error handling** - every `catch` block must call `logError(ctx, err)` or re-throw. Never empty `catch {}`.

## Backend conventions

- **Package architecture** - `backend/` is a Python package with strict unidirectional dependencies. No circular imports. A utility script (`scripts/util/_check_imports.py`) scans for lazy imports and reports the no-circular-imports invariant.
- **Naming**: Public functions (called from other modules) have no `_` prefix (e.g., `stream_test`, `make_result`). Internal-only helpers keep the `_` prefix (e.g., `_check_metric_degradation`).
- **Shared state**: Modules that mutate shared state use `import backend.state as st` and access via `st.variable` (avoids value-copying from `from ... import` for rebound primitives like `scheduler_running`). Dicts/lists/objects are fine with direct imports since mutations propagate.
- **`app_cfg` / `models_cfg` / `model_registry`** are mutated in-place (`.clear(); .update()` / `.clear(); .extend()`) instead of rebinding, so all modules holding references see the update.
- **Error handling**: Every `except` block must call `log_error(msg, exc)` or re-raise. Never bare `pass`. Two global safety nets (`@app.exception_handler`, `loop.set_exception_handler`) catch anything missed.
- **PII-safe errors**: Provider API errors use template-based messages, never passing through raw `error.message`. See `backend/security.py`.

## Adding a new provider

1. Add the API key to your environment (`.env`):

```bash
NEW_PROVIDER_API_KEY=sk-your-key
```

2. If running in Docker (build-based), recreate the container so the new env var is available:

```bash
docker compose -f compose.modelwatcher.yaml up -d --build
```

> Environment variables are read at container creation time, not at runtime. Config changes in `./config/` are hot-reloaded by `watchfiles` without container restart. Backend code changes are hot-reloaded by uvicorn `--reload` (no restart needed).

3. Edit `config/models.yaml` - add a provider entry:

```yaml
providers:
  - name: "NewProvider"
    api_url: "https://api.newprovider.com/v1"
    api_key: "${NEW_PROVIDER_API_KEY}"
    models:
      - id: "model-name"
        name: "Display Name"
```

4. Save the file - the config watcher hot-reloads automatically. The new provider's models appear immediately and are tested on the next scheduler cycle.

> Order matters: the env var must be available before the config watcher resolves `${VAR_NAME}` references. If you edit `models.yaml` before recreating the container, the reference stays as a literal `${...}` string.

### Anthropic providers

If the provider uses the Anthropic API format, include `"anthropic"` in the `api_url` (e.g. `https://api.anthropic.com/v1`). This triggers the Anthropic streaming path (`x-api-key` header, `/messages` endpoint, `content_block_delta` events). Set `anthropic_thinking_budget` in `app.yaml` to enable extended thinking.

## Adding a new test type

To add a fifth test type alongside benchmark/health/audit/probe:

1. Add a `TEST_NEWTYPE` constant in `backend/state.py` and a corresponding `running_newtype` set.
2. Extend `test_type_schedule()` in `state.py` to return the interval and epoch key for the new type.
3. Add the new type to `_due_tests()` and `_next_due_in()` in `backend/scheduler.py`.
4. Add dispatch logic in `_dispatch_due()` (health first, then benchmarks, then probes, then audits).
5. Implement the test runner function (e.g., `run_newtype_test()`).
6. Add a `testing.newtype` section in `config/app.yaml` with `enabled`, `interval`, and any type-specific settings.
7. Add the new type to `_validate_config()` in `config.py`.
8. Add WS message handling for the new type in `frontend/js/ws.js`.
9. Update the scheduler's `_iter_undispatched_models()` to check the new `running_newtype` set.

## Config hot-reload

Config changes (providers, intervals, thresholds) take effect without a server restart via `config_watcher()` in `backend/config.py`:

1. `watchfiles.awatch()` watches `config/` for `.yaml`/`.yml` changes.
2. `reload_config()` reloads all YAML files, updates the `c` namespace, and rebuilds `model_registry`.
3. `apply_db_changes()` syncs SQLite: deletes orphaned rows for removed models, upserts the new registry, applies archive directives.
4. WS `config_updated` is broadcast to all connected clients.
5. The scheduler's wake event fires, rescheduling tests with the new intervals.
6. Provider favicons and model metadata are re-fetched.

Config is mutated in-place so all modules holding references see the update immediately.

## Utility scripts

| Script | Purpose | Run command |
|--------|---------|-------------|
| `scripts/util/_check_imports.py` | Scans `backend/` for lazy imports and reports the no-circular-imports invariant | `python3 scripts/util/_check_imports.py` |
| `scripts/util/scale_test_db.py` | Generates a synthetic SQLite database with configurable provider/model counts and history depth for scale testing | See its docstring |

## Performance/stress test scripts

The `package.json` defines npm scripts for performance and stress testing, but the test scripts themselves (`tests/perf-test.mjs`, `tests/stress-ui.mjs`, etc.) are **not included in the repository** - they are maintained separately. The `tests/` directory is a placeholder. If you have the test scripts, place them in `tests/` and run:

```bash
npm run perf          # Full performance test
npm run perf:quick    # Quick performance test
npm run perf:har      # With HAR capture
npm run perf:install  # Install perf test dependencies (npm + playwright chromium)
npm run stress        # Full stress test
npm run stress:quick  # Quick stress test
npm run stress:profile       # Stress test with profiling
npm run stress:profile:quick # Quick stress test with profiling
```
