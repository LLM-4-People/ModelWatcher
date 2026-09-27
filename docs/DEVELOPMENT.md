# Development guide

This guide covers local setup, project structure, testing, and conventions for contributing to ModelWatcher.

## Table of contents

- [Local setup](#local-setup) - Prerequisites, dependencies, build, run
  - [Prerequisites](#prerequisites) - Python 3.13+, Node.js 22+
  - [Install dependencies](#install-dependencies) - Virtualenv + pip, npm
  - [Build Tailwind CSS](#build-tailwind-css) - One-time and watch modes
  - [Run the server](#run-the-server) - Config templates, env vars, uvicorn
- [Project structure](#project-structure) - Backend, frontend, config, scripts
- [Testing](#testing) - Pytest suite, JS unit tests, browser tests
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

Python dependencies go into a project virtualenv, never the system interpreter: on Debian-patched Pythons `http-ece` (pulled in by `pywebpush`) fails to build outside one. `.venv/` is gitignored and excluded from the Docker build context.

```bash
# Python dependencies (runtime + dev) in a project virtualenv
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python3 -m pip install -r requirements.txt -r requirements-dev.txt

# Node.js dependencies (Tailwind CSS, SynBad, perf test tools)
npm install
```

Activate the virtualenv (`source .venv/bin/activate`) in every new shell before running the server, the tests or the utility scripts.

### Build Tailwind CSS

```bash
npm run build:css    # one-time build → frontend/tailwind.min.css (gitignored)
npm run watch:css    # auto-rebuild on file change (development)
```

The server serves the stylesheet from `frontend/tailwind.min.css` by default (`MW_BUILT_CSS_PATH` overrides it; the Docker image builds its own copy into `/opt/frontend/tailwind.min.css` because the repo is mounted read-only over `/app`). If the file is missing, `/frontend/tailwind.min.css` returns 404 and the server logs the expected path and the build command.

Tailwind v4 uses CSS-native configuration (`@theme`, `@source`, `@custom-variant` in `frontend/input.css`). No `tailwind.config.js` is needed. Source paths include `./index.html` and `./js/*.js` so Tailwind scans both HTML and JS modules for class names.

> Tailwind only generates CSS for class names it finds at build time. New classes will not take effect until rebuilt. Dynamic class names (constructed via template literals at runtime) are NOT detected by the scanner - use explicit class maps (see `TIER_TEXT`, `TIER_BG` in `frontend/js/format.js`).

### Run the server

The server fails fast without its config files, so copy the templates first. Provider API keys are read from the environment (`${VAR_NAME}` references in `models.yaml`); `.env.example` lists them.

```bash
cp config/app.yaml.example config/app.yaml   # edit for your environment
cp config/models.yaml.example config/models.yaml
python3 -m backend.main
```

`python -m backend.main` is the one way to start the server, here, in the Dockerfile and in the browser tests: `HOST` and `PORT` (default `0.0.0.0:8080`) come from the environment, and code reload (`app.debug`) and the WebSocket ping settings from `app.yaml`. The dashboard is available at `http://localhost:8080`; by IP address or as `localhost` it needs no configuration, other names go in `server.allowed_hosts`. Set `MW_DISABLE_TESTS=1` to browse without testing or contacting providers (config hot reload keeps working); the scale-test seeder under [Utility scripts](#utility-scripts) provides sample data.

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
│   ├── middleware.py           # Connection/size limit, Host check, security headers
│   ├── scheduler.py            # Test scheduling (benchmark, health, audit, probe)
│   ├── routes.py               # REST API route handlers, static serving
│   ├── audit.py                 # SynBad-based audit runner
│   ├── probe.py                 # Capability detection probes
│   ├── migrations.py           # SQLite schema migrations
│   └── main.py                  # FastAPI app factory, lifespan, wiring
├── frontend/
│   ├── index.html              # HTML structure + inline CSS (no inline JS)
│   ├── input.css               # Tailwind v4 input
│   ├── js/                      # 23 ES modules (no bundler, no build step)
│   ├── sw.js                    # Service worker (push-only)
│   ├── manifest.json           # PWA manifest
│   └── js/vendor/               # Chart.js + date-fns adapter (local, not CDN)
├── config/                      # 3 YAML config files + .example templates
│   ├── app.yaml.example
│   ├── models.yaml.example
│   └── audits.yaml.example
├── data/                        # SQLite DB, VAPID keys, favicons (gitignored)
├── scripts/
│   ├── tests/                   # Pytest suite (shared fixtures in conftest.py)
│   └── util/                    # Infrastructure scripts
├── tests/
│   ├── js/                      # node --test unit tests of DOM-free frontend modules
│   └── e2e/                     # Playwright browser tests against a private seeded server
├── requirements.txt             # Python runtime deps
├── requirements-dev.txt         # Python dev deps (adds pytest)
├── pytest.ini                   # pytest settings: project root importable, test paths
├── package.json                 # Node deps (Tailwind, SynBad, perf tools)
├── Dockerfile                   # Multi-stage: node CSS builder + python runtime
└── compose.example.yaml  # Example compose config
```

## Testing

```bash
npm test            # pytest suite, then the JS unit tests
npm run test:js     # JS unit tests only: node --test tests/js/*.test.mjs
npm run test:e2e    # browser tests (builds the CSS first)
```

`npm test` runs `python3 -m pytest scripts/tests/ -v` and then `npm run test:js`, so run it with the virtualenv active. `pytest.ini` puts the project root on the import path and names the test directory, so a bare `pytest` (or an IDE runner) works from any directory as well.

The JS unit tests import frontend modules straight into Node (`utils.js`, `state.js` and `conn.js` touch no DOM at import time) and cover the pure logic: segment and separator markup, status glyphs, WebSocket close classification and reconnect pacing.

The browser tests need Chromium for Playwright (`npx playwright install chromium`; set `MW_E2E_CHROMIUM` to a Chromium binary to use another one). Each spec file starts its own server: `tests/e2e/harness.mjs` seeds a small scale-test dataset into a temp dir with `scripts.util.scale_test_db` (`--app-set` shortens the connection timings), starts `python -m backend.main` on a free port with `MW_DISABLE_TESTS=1` and no code reload, and removes everything afterwards. The seeder and the server share a temp `MW_DATA_DIR` (DB, favicons, VAPID keys), and the server starts on the environment the seeder writes with `--server-env`, so they never touch `data/`, `config/` or a running instance.

| Browser test | What it covers |
|--------------|----------------|
| `tests/e2e/connection.test.mjs` | Header dot and banner: one socket that stays up on a quiet server, banner hidden without CSS, backoff on a busy server and slow retries after an origin rejection (each socket really opens before the close, as in production), a socket gone silent after its hello closed as stale and replaced, recovery through the liveness check while `/health` is 503 |
| `tests/e2e/segments.test.mjs` | Every segment row keeps its gap between visible items, every separator has equal and uniform spacing, and phones hide the last-OK group without leaving a stray separator |

Most tests are pure unit tests (extract_model_info, config validation, schema checks, dead-code detection) - no API keys, no network, no shared database. Tests that need a process of their own (seeder, import-time behaviour, env defaults, the real app) use the `run_python` fixture from `conftest.py`, which runs from the project root with dev-only overrides (`PYTHONPATH`, `MW_BUILT_CSS_PATH`, `TIKTOKEN_CACHE_DIR`) removed and a private `MW_DATA_DIR`, so no child writes the checkout's `data/`. Children that load the real app (`backend.main` loads its config at import) run on the example configs (the `example_config_env` fixture), report through `emit()` and read WebSocket frames with a deadline through `receive_until()`, both in `scripts/tests/app_child.py`.

| Test file | What it covers |
|-----------|---------------|
| `test_api_docs.py` | `docs/API.md` has a section for exactly the app's REST routes, and the endpoint and tag counts in README.md and API.md match the app |
| `test_api_errors.py` | API error responses are `{"error": "..."}` and nothing else, from every layer that answers with one (handlers, query and body validation, routing, the Host and body-size middleware), checked on the checked-out app with the example configs |
| `test_built_css.py` | npm scripts, `backend/state.py`, the Dockerfile, `index.html` and the ignore files agree on the built stylesheet path; a missing file logs its path and the build command once per outage, without a traceback |
| `test_check_imports.py` | `scripts/util/_check_imports.py` derives the load order from the import graph (every backend module, `from backend import x` included), fails on a cycle, and tells needed lazy imports from avoidable ones |
| `test_config_examples.py` | Every `config/*.example` passes the backend validators, every `app.yaml.example` key is documented in CONFIGURATION.md, leaving out any required key fails with `<path> is required`, and values (log level, host patterns, WebSocket settings) are validated |
| `test_config_no_defaults.py` | Config has no defaults in code - config is the sole source of truth |
| `test_config_reload.py` | With `MW_DISABLE_TESTS`, an edit to a config file outside `config/` (an `MW_*_YAML` override) hot-reloads and reaches an open socket as `config_updated` within a deadline, without provider fetches; `reset_epoch` is stripped from the models file in use |
| `test_db_split.py` | `db_push` and `db_probe` modules use live binding for `db._write_conn` (no stale `None` capture) |
| `test_deployment.py` | The image copies only `config/*.example`, the build context leaves out local configs and env files, the container starts `python -m backend.main`, no second launch command or ping setting exists, and `server_options()` takes pings and reload from config |
| `test_docs_commands.py` | Documented commands work as written: Python installs happen inside a virtualenv, scripts that import `backend` run in module form, a bare `pytest` imports the project |
| `test_error_logging.py` | Every backend `except` re-raises or logs through `backend.state` (a broad catch at warning or above); control flow is a keyed, documented allowlist with no stale entries; the scanner is self-tested |
| `test_favicons.py` | `root_url()` derives a provider's homepage with the Public Suffix List: IP and single-label hosts keep host and port, multi-part and hosted suffixes stay whole |
| `test_frontend_rules.py` | Frontend source rules: separators (dash joiners included), status glyphs (every codepoint ever used, literal, escaped or as an entity) and test type labels have one home, config values have no fallbacks (aliases included), only `conn.js` writes the connection dot and banner, paths and close codes come from the server, every JS `catch` logs or re-throws |
| `test_host_check.py` | Only a `Host` that names the server is served: IP addresses, `localhost`, the `app.site_url` host and `server.allowed_hosts` patterns; others get 400 or a refused WebSocket, with one warning |
| `test_line_endings.py` | Every tracked text file is stored and checked out with LF (`.gitattributes` `* text=auto eol=lf`) |
| `test_migrations.py` | Migrations read columns instead of probing with failing statements: a fresh schema runs no ALTER, missing columns are added once, a failing ALTER is not swallowed |
| `test_model_key.py` | Model keys are built and split only through `make_model_key`/`parse_model_key` in backend and scripts |
| `test_pricing.py` | `extract_model_info()` pricing normalization (per-token, per-million, cents-per-million) |
| `test_project_paths.py` | Only `backend/state.py` derives project paths from `__file__`; everything else imports them; the DB, VAPID keys, favicons and tiktoken cache all follow `MW_DATA_DIR`, and the real app writes only there |
| `test_rate_limits.py` | All rate limits come from config, none hardcoded |
| `test_routes.py` | Readiness logs when it changes, not per request; the client error reporter answers and rate-limits from config; the module preload list is the static import closure of `app.js`, dependencies first |
| `test_rules.py` | `extract_model_info()` rules: context window, capabilities, thinking, modalities, Ollama suffix rules, two real-world fixtures |
| `test_scale_test_db.py` | The scale-test seeder creates missing dirs, writes the expected rows with the backend schema, and emits configs the validators accept; a bad `--app-set` is a usage error before anything is written; a server started on its `--server-env` serves every seeded logo |
| `test_schemas.py` | Pydantic body models match handler field expectations (no drift) |
| `test_ssoT_labels.py` | Single source of truth for labels - `state.py` owns, `/api/config` exposes, frontend does not redefine |
| `test_token_encoder.py` | tiktoken is never loaded at import or on the event loop; a failed load logs once, retries after `token_encoding_retry`, token counts fall back to chunk counts, and effective (per-token) ITL stays unmeasured without the encoder |
| `test_undefined_names.py` | No backend or scripts module uses a name it never defines or imports (pyflakes) |
| `test_websocket.py` | Same-origin pages are always accepted, other origins follow the allowlist (close 1008), the connection limit closes with 1013, every socket starts with a `hello`, heartbeats run with `MW_DISABLE_TESTS` (read with a deadline, so missing ones fail in seconds), `close_all` tells a gone client from a failure, and the real app serves liveness 200 while readiness is 503, rejects a foreign `Host` and preloads the derived module list |

| JS unit test | What it covers |
|--------------|----------------|
| `tests/js/conn.test.mjs` | Close classification (only a socket that never reached the server is a failure), backoff growth, rejection and restart pacing, the unreachable floor, a help tip per connection state |
| `tests/js/segments.test.mjs` | `segmentsHTML`/`sepHTML`: nested groups are lists, separators carry no spacing, copied text stays readable; distinct status glyphs; `kvGrids` splitting |

Each test file includes a docstring describing the bug family it catches.

## Frontend conventions

- **Named exports only** - no default exports. This makes dependencies explicit.
- **ES modules** - no bundler, no build step for JS. Served directly with `?v=` content-hash params for cache busting.
- **Mutable shared state** via exported `const` object (`state`). Primitive `let` exports use setter functions (`setChartReady()`) because live bindings are read-only for primitives.
- **No function duplication** - if two modules need the same logic, extract it to a shared module (`prefs.js` for notification prefs, `utils.js` for collapsibles/esc).
- **Tailwind v4** - CSS-native config in `input.css`. Use explicit class maps (complete string literals in source) so the JIT scanner can detect them. Never construct class names with template literals.
- **Custom color palette** - `accent` (blue), `success` (green), `warn` (amber), `danger` (red, with `danger-400` for Bad and `danger-700` for Critical), `teal`, `surface` (grays). Use these, not Tailwind's default `blue-400`, `green-500`, etc.
- **Error handling** - every `catch` block must log through `logError`/`logWarn`/`logInfo`/`logDebug` (`logError` unless the failure is expected) or re-throw. Never empty `catch {}`; `test_frontend_rules.py` scans for it.

## Backend conventions

- **Package architecture** - `backend/` is a Python package with strict unidirectional dependencies. No circular imports at load time. `scripts/util/_check_imports.py` derives the load order from the import graph, fails on a cycle, and tells needed lazy imports from ones that could move to the top.
- **Naming**: Public functions (called from other modules) have no `_` prefix (e.g., `stream_test`, `make_result`). Internal-only helpers keep the `_` prefix (e.g., `_check_metric_degradation`).
- **Shared state**: Modules that mutate shared state use `import backend.state as st` and access via `st.variable` (avoids value-copying from `from ... import` for rebound primitives like `scheduler_running`). Dicts/lists/objects are fine with direct imports since mutations propagate.
- **`app_cfg` / `models_cfg` / `model_registry`** are mutated in-place (`.clear(); .update()` / `.clear(); .extend()`) instead of rebinding, so all modules holding references see the update.
- **Error handling**: Every `except` block re-raises or logs through `backend.state`: `log_error(msg, exc)` for unexpected failures, `log.warning`/`log.info`/`log.debug` for expected ones; a broad `except Exception` needs `log_error` or at least `log.warning`. Expected control flow (a parse helper returning `None`, a 400 answer) is listed with its reason in `test_error_logging.py`'s allowlist. Code that runs on every request logs a recurring problem when it starts and when it clears (`st.condition_changed()`), not on every hit. Two global safety nets (`@app.exception_handler`, `loop.set_exception_handler`) catch anything missed.
- **PII-safe errors**: Provider API errors use template-based messages, never passing through raw `error.message`. See `backend/security.py`.

## Adding a new provider

1. Add the API key to your environment (`.env.modelwatcher`):

```bash
NEW_PROVIDER_API_KEY=sk-your-key
```

2. If running in Docker, recreate the container so the new env var is available:

```bash
docker compose -f compose.yaml up -d --build
```

> Environment variables are read at container creation time, not at runtime. Code reload (`app.debug`) and the config watcher only see source files and config YAMLs, not environment variables.

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

Scripts import `backend` for its paths and helpers, so run them from the project root in module form (`python3 -m scripts.util.<name>`).

| Script | Purpose | Run command |
|--------|---------|-------------|
| `scripts/util/_check_imports.py` | Derives the backend load order from the import graph, exits 1 on a load-time cycle, and says which lazy imports are needed to avoid one | `python3 -m scripts.util._check_imports` |
| `scripts/util/scale_test_db.py` | Generates a synthetic SQLite database (schema from `backend/db.py`), matching `models`/`app` YAML files and placeholder favicons for scale testing. The DB and favicons go to the server's data dir (`MW_DATA_DIR`, default `data/`); the config dir, sizes, rates and file names are options. A bad `--app-set` or template fails before anything is written. | `python3 -m scripts.util.scale_test_db --help` |

A typical scale-test run seeds a small dataset, then starts the server on it with tests disabled (the seeder ends by logging this command for its output; `--server-env` also writes the variables as JSON):

```bash
python3 -m scripts.util.scale_test_db --providers 10 --models-per 5
MW_DB_NAME=metrics-scale-test.db MW_MODELS_YAML=models-scale-test.yaml MW_APP_YAML=app-scale-test.yaml \
MW_SCALE_TEST_KEY=dummy MW_DISABLE_TESTS=1 PORT=8080 python3 -m backend.main
```

## Performance/stress test scripts

The `package.json` defines npm scripts for performance and stress testing, but the test scripts themselves (`tests/perf-test.mjs`, `tests/stress-ui.mjs`, etc.) are **not included in the repository** - they are maintained separately (see finding F17 in `docs/redesign/FINDINGS.md`). If you have the test scripts, place them in `tests/` and run:

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
