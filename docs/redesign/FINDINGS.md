# Redesign findings register

Single source of truth for every issue found while working on the UI redesign, including low-impact ones.
Each entry records how it was reproduced, its root cause, and what was done about it.

Status values: `open`, `investigating`, `fixed`, `deferred-to-redesign` (resolved by the redesign itself, tracked in its plan).

| ID | Area | Summary | Severity | Status |
|----|------|---------|----------|--------|
| F1 | scripts | `scale_test_db.py` resolves the project root to `scripts/`, so it cannot open `data/metrics-scale-test.db` | medium | fixed |
| F2 | backend | `streaming.py` downloads the tiktoken encoding at import time, so the server cannot start without outbound access to `openaipublic.blob.core.windows.net` | high | fixed |
| F3 | backend/docs | Built CSS path defaults to `/opt/frontend/tailwind.min.css`, so following DEVELOPMENT.md locally serves a 404 for the stylesheet and an unstyled page; the documented build output is not gitignored | medium | fixed |
| F4 | frontend | Inter font is preloaded from `cdn.jsdelivr.net`, an external dependency for an app whose PWA shell is meant to work offline | low | deferred-to-redesign |
| F5 | backend/frontend | Header shows a red connection dot and a "Server unreachable - retrying automatically" banner against a healthy local server | medium | fixed |
| F6 | frontend | Model modal header renders `Bench 5h 2m ·OK10h 1m` with a missing space and inconsistent separator | low | fixed |
| F7 | docs | DEVELOPMENT.md installs Python dependencies into the system interpreter; on Debian-patched Python `http-ece` fails to build (`install_layout`), a virtualenv avoids it | low | fixed |
| F8 | tests | `test_api_errors.py` calls the production host `https://stats.ai4fun.dev`, so 6 tests fail offline and the suite checks a remote deployment instead of the checked-out code | medium | open |
| F9 | docker | `COPY config/ config/` in the Dockerfile bakes the build host's local `config/*.yaml` (gitignored, may hold inline keys) into image layers; the later `COPY config/*.example config/` is redundant | medium | open |
| F10 | scripts | `_check_imports.py` ranks modules with a hand-kept `CHAIN` that misses `audit`, `db_probe`, `db_push`, `migrations` and others and still lists the removed `ping`, so it reports false violations | low | open |
| F11 | repo | Mixed CRLF/LF line endings in 8 tracked files; any edit that normalises them turns into a whole-file diff | low | fixed |
| F12 | backend | `favicons.root_url()` keeps the last two host labels, so IP hosts become invalid URLs (`https://127.0.0.1:9/v1` becomes `https://0.1:9`) and multi-part TLDs collapse (`api.example.co.uk` becomes `co.uk`) | low | open |
| F13 | backend | 43 `except` blocks neither log nor re-raise, against the CONTRIBUTING.md rule; `test_error_logging.py` only catches `except Exception: pass` | low | open |
| F14 | frontend | 11 JS `catch` handlers neither log nor re-throw, and the client-error reporter writes to `console` directly; no test enforces the CONTRIBUTING.md rule for JS | low | fixed |
| F15 | scripts/docs | The seeder says the server only reads `data/` and `config/`, yet `MW_DB_NAME`/`MW_*_YAML` accept absolute paths (undocumented), so isolated runs looked impossible | low | fixed |
| F16 | backend/docker | WebSocket protocol ping interval/timeout (30s/90s) are hardcoded twice, in `main.py`'s `__main__` and the Dockerfile `CMD`, and quoted in DEPLOYMENT.md | low | open |
| F17 | repo | The `perf`/`stress` npm scripts point at `tests/*.mjs` files that are not in the repository | low | open |
| F18 | backend | `/health` logs a warning on every failing request, so a Docker HEALTHCHECK against a degraded or `MW_DISABLE_TESTS` instance logs one every 30s | low | open |
| F19 | frontend | 63 localStorage key literals in 9 modules (and 5 in the inline FOUC script in `routes.py`) bypass the `LS` registry in `state.js` | low | open |
| F20 | backend/frontend | Static tunables remain in code, including two fallbacks for config values in `notifications.js` (toast duration, history cap) | medium | open |
| F21 | backend | `config.py` hand-writes 35 "is required" checks, 24 of them the same `for key in ...: if key not in ...` loop | low | open |
| F22 | frontend | `state.js` seeds `statusValues`, `testTypes` and `chartViews` with copies of backend constants that `/api/config` later overwrites | low | open |
| F23 | backend | Regression from the F5 fix: a malformed `Origin` header (`http://[`) raised inside `is_allowed_origin()`, so the WebSocket died with 1006 and two error tracebacks instead of a clean 1008 | medium | fixed |
| F24 | tests | closingSocket() closes the routed WebSocket inside the route handler, so the page's socket never fires onopen | medium | open |
| F25 | frontend | No timeout for a socket that is accepted but never sends hello | medium | open |
| F26 | tests | Stale detection is untested | medium | open |
| F27 | frontend | Phone-width regression in the modal error box | medium | open |
| F28 | frontend | Modal title vertical misalignment introduced by .seg-list {align-items: baseline} | low | open |
| F29 | tests | Static guard holes, confirmed by mutation | low | open |
| F30 | frontend | Separators on the same code path are still built by hand with em dashes: _statusMessage joins retry and error with ' - ' (dom.js:148), and format.js:394 does the same | low | open |
| F31 | frontend | The claim 'spaces keep copied text readable' does not hold in a browser | low | open |
| F32 | frontend | The frontend depends on window.__MW_CONN__ and has no guard | low | open |
| F33 | frontend | _probeWhileDown() runs probeBackend().then(...) without .catch, and it re-arms itself inside the .then callback | low | open |
| F34 | frontend | _measureClientRTTFresh logs a liveness failure with logError, even though CONTRIBUTING (as updated by the same writer) says expected failures such as a liveness probe while the server is down should use logDebug or logWarn | low | open |
| F35 | frontend | The F14 rule is not enforced outside frontend/js: sw.js has 4 catches that swallow or only console.error (lines 25, 54, 75, 83), and the inline FOUC script built in backend/routes.py:403 has `catch(e){}` | low | open |
| F36 | backend | conn.js is missing from _MODULE_PRELOAD_ORDER although api.js, ws.js and help.js import it on the startup path, so the browser finds it one round trip later | low | open |
| F37 | docs | The endpoint count is stale after adding /health/live: API.md:3 and README.md:13 and README.md:80 say '15 REST endpoints', but there are now 16 (16 '### GET/POST/...' sections, 16 routes) | low | open |
| F38 | tests | --app-template and --app-set are applied only after the DB is seeded (minutes for the default 5000 models) and after models YAML is written | low | open |
| F39 | tests | F15 is incomplete | low | open |
| F40 | docs | The claim that browser tests 'never touch data/, config/ or a running instance' is false on a fresh clone | low | open |
| F41 | backend | Results computed without an encoder are indistinguishable | low | open |
| F42 | tests | When heartbeats are missing, test_real_app_with_tests_disabled blocks in ws.receive_json() until conftest's 120s subprocess timeout | low | open |
| F43 | package.json | `npm test` is `pytest && npm run test:js` | low | open |
| F44 | backend | built_css() calls log_error with a full FileNotFoundError traceback on every stylesheet request while the file is missing, which is every page load | low | open |
| F45 | backend | MODEL_KEY_SEP is a single source only for the backend | low | open |
| F46 | backend | The _TEST_TASKS comment says 'Background work that exists to run tests', but the table includes the config watcher (hot reload), favicons and model info | low | open |
| F47 | backend | close_all's per-socket except now logs with log.debug | low | open |
| F48 | backend | No Host header validation: with the same-origin WebSocket rule, a DNS-rebinding page now counts as same-origin (the HTTP API was already exposed this way) | low | open |

## Details

### F1 - seeder project root

- Reproduce: `python3 scripts/util/scale_test_db.py --providers 5 --models-per 4 --months 0.5` fails with `sqlite3.OperationalError: unable to open database file`.
- Root cause: `Path(__file__).resolve().parent.parent` was correct before the script moved into `scripts/util/`; it now points at `scripts/`. The docstring usage line still references the old location.
- Also found: the seeder kept its own copy of the schema and insert columns, which had drifted from `backend/db.py` (no `model_state.archived`, no fingerprint columns, no audit/probe tables); every test file and `_check_imports.py` recomputed the root with `parents[N]`, and `audit.py` did too.
- Fix: `backend/state.py` is the single source of project paths (`BASE_DIR`, `BACKEND_DIR`, `CONFIG_DIR`, `DATA_DIR`, `FRONTEND_DIR`); scripts, tests and `audit.py` import them. Scripts run in module form (`python3 -m scripts.util.scale_test_db`). The seeder now builds its DB through `db.init()` and the new `db.insert_results()`, derives favicon names from `favicons.provider_slug()` and model keys from the new `state.make_model_key()`, writes YAML with `yaml.safe_dump` instead of regex edits, creates missing output dirs, and exposes output dirs, file names, template, rates, seed and batch size as CLI options. Status, uptime, trends and reliability are left for the server to derive at startup.
- Tests: `test_scale_test_db.py` (seeds into a scratch dir, checks row counts per test type, schema equality with `db.init()`, and that both YAML files pass the backend validators), `test_project_paths.py` (only `state.py` may use `__file__`), `test_docs_commands.py` (no file-form script invocations; referenced modules exist). Verified by restoring the old seeder: the path test fails and the seeder fixture errors.

### F2 - import-time network fetch

- Reproduce: start the server in an environment without access to `openaipublic.blob.core.windows.net`; startup fails with `requests.exceptions.ProxyError` from `tiktoken.get_encoding("o200k_base")`, even with `MW_DISABLE_TESTS=1`.
- Root cause: `backend/streaming.py` builds the encoder at module import. The Docker image does not pre-cache the encoding, so every fresh container depends on that host at startup.
- Also found: tiktoken downloads with `requests.get()` and no timeout, so a merely lazy load inside `_validate_token_counts` could block the event loop indefinitely.
- Fix: `streaming.get_encoder()` is the one accessor. It never blocks: a missing encoder starts one daemon-thread load (`load_encoder()`); failures are logged once per attempt through `log_error` and retried after `testing.benchmark.token_encoding_retry`; until then `_validate_token_counts` uses its chunk-count fallbacks. The encoding name is `testing.benchmark.token_encoding` (validated in `config.py`, documented in CONFIGURATION.md, added to `app.yaml.example` and the local `app.yaml` / `app-scale-test.yaml`). `state.py` sets `TIKTOKEN_CACHE_DIR` to `data/tiktoken` (env override kept) so a deployment downloads once. Startup warms the encoder when tests are enabled.
- Tests: `test_token_encoder.py` (import with `get_encoding` raising, non-blocking single load, log-once and retry-after-interval, chunk-count fallback, counts with a loaded encoder), `test_config_examples.py` (examples validate and load through `reload_config`, every `app.yaml.example` key is documented, new keys are required and validated). Verified by restoring the old module: collection fails with the same `ProxyError`.
- Verified live: an instance on port 8091 started without the tiktoken shim and served `/` with 200; an instance on 8092 with tests enabled logged `Token encoding 'o200k_base' unavailable ... retrying in 3600s` after `Startup complete` and kept serving.

### F3 - built CSS path

- Reproduce: follow DEVELOPMENT.md (`npm run build:css`, then run uvicorn). `GET /frontend/tailwind.min.css` returns 404 and the dashboard renders unstyled. `git status` then shows `frontend/tailwind.min.css` as untracked.
- Root cause: `backend/state.py` defaults `MW_BUILT_CSS_PATH` to the Docker location. The Docker location exists because compose mounts the repo read-only over `/app`, which would hide a CSS file built into `/app/frontend`.
- Fix: `state.BUILT_CSS_PATH` defaults to `FRONTEND_DIR / BUILT_CSS_NAME` (the `npm run build:css` output); the Dockerfile sets `ENV MW_BUILT_CSS_PATH=/opt/frontend/tailwind.min.css` right before the `COPY` that writes `${MW_BUILT_CSS_PATH}`, with the read-only-mount reason. The route name and the `?v=` hash both use `BUILT_CSS_NAME`/`BUILT_CSS_PATH`, so the hash always describes the served file. A missing file returns `404 {"error": "Stylesheet not built"}` and `log_error` names the path and `npm run build:css`. `frontend/tailwind.min.css` is ignored by git and by `.dockerignore` (the image builds its own copy).
- Tests: `test_built_css.py` (npm `build:css`/`watch:css` output, state default in a clean subprocess, env override, Dockerfile builder `WORKDIR` + output = `COPY` source and `ENV` = `COPY` destination, `index.html` link, ignore rules, missing-file log and response, served file and version hash share one path). Verified by restoring the old code: 4 of 9 fail.
- Verified live: on port 8091 without `MW_BUILT_CSS_PATH`, the stylesheet returned 404 with the new log line before the build and 200 `text/css` after `npm run build:css`, byte-identical to the build, with a matching `?v=`; Chromium loaded 186 rules and applied the theme.

### F4 - external font dependency

- Observed: the sandbox proxy rejected `cdn.jsdelivr.net`; the page fell back to system fonts.
- Plan: the redesign self-hosts its fonts alongside the vendored Chart.js.

### F5 - false "server unreachable" state

- Reproduce: open the dashboard on a local instance (for example the scale-test server on `http://127.0.0.1:8080`). Every WebSocket attempt opens, the client sends `sync_prefs`, and the server closes it with 4403 "forbidden origin"; the dot stays red (`ws_disconnected`). A raw client shows only `Origin: https://your-domain.example.com` (the placeholder `site_url`) is accepted; `http://127.0.0.1:8080`, `http://localhost:8080` and no Origin are rejected. The same code with `allowed_origins` set to the page's origin shows a green dot, so neither `MW_DISABLE_TESTS` nor a heartbeat caused it.
- Root cause: `websocket.is_allowed_origin()` only consulted `websocket.allowed_origins`, which every shipped config sets to `[*site_url]`, so the page served by the very same host was rejected. A same-origin page cannot be cross-site WebSocket hijacking, the one thing the check exists to stop.
- Follow-on bugs, all reproduced:
  - The server accepted before closing, the client counted `onopen` as connected and reset its backoff there, so it retried every 3 s forever and refetched `/api/metrics` on each attempt.
  - Every non-restart close called `trackFail()`: three server-sent closes (4403, or the client's own 4000 stale close) proved the server was up yet tripped "Server unreachable".
  - Recovery probed `/health`, the readiness check, which answers 503 whenever the scheduler is off or no model is healthy; once down, `api()` short-circuits, so a page stayed "unreachable" until reload (reproduced with three 502s). The RTT measurement used it too and logged a console error.
  - Staleness was "no app message in 5 min" with no server heartbeat, so a quiet server's healthy socket was torn down every 5 min; the 5 min window, the 60 s check and the 3/15/60 s backoff were literals in `ws.js`.
  - 4403 meant both "never" (origin) and "later" (connection limit).
  - The banner was hidden by the Tailwind `hidden` class, so without the stylesheet (F3) it always showed; the dot's class map existed twice (`ws.js` and `api.js`).
  - The startup line "MW_DISABLE_TESTS set - scheduler, favicons, ping disabled" named a ping that is never disabled and omitted three skipped tasks, which pointed the investigation at a heartbeat.
  - Found while fixing: the recovery probe called `connectWS()` while a socket could still be open and orphaned it; `onclose` cleared `_wsConnected` before checking that the closing socket was the current one.
- Fix:
  - Backend: `is_allowed_origin(origin, host)` accepts same-origin pages (Origin host equals the `Host` header, the Gorilla `CheckOrigin` rule) and then the allowlist. `websocket.CLOSE_CODES` is the one list of close codes (1008 policy, 1013 try again, 1012 restart, 1009, 1011, 4000 stale). Every accepted socket first gets `{"type": "hello", "config": ...}`, and a heartbeat task broadcasts every `websocket.heartbeat_interval`, started regardless of `MW_DISABLE_TESTS`. `GET /health/live` (`st.LIVENESS_PATH`) is the liveness check; `/health` stays the readiness check. `_startup` starts the test tasks from one `_TEST_TASKS` table and logs exactly the skipped names. The WebSocket message size limit and the sync_prefs rate now come from config too, through the shared `st.rate_limited()` sliding window that `routes.check_rate_limit` also uses.
  - Config: new `websocket.heartbeat_interval`, `stale_after`, `reconnect.{min_delay,max_delay}`, `unreachable.{after_failures,retry_interval}`, `max_message_bytes`, `sync_prefs_per_minute` (validated, documented, in the example and local configs). `websocket.connection_config()` is the one builder of the client's policy (plus the WS and liveness paths and the close codes); it goes into the page bootstrap as `window.__MW_CONN__` and into every hello, so a hot reload reaches open pages.
  - Frontend: new `conn.js` is the one writer of the dot (`data-state`, styled in `index.html`) and the banner (native `hidden` attribute, works without CSS), derived from the socket state and the backend-down flag. A socket counts as connected on its hello; `wsCloseKind()` counts only a socket that never reached the server as a failure; `wsReconnectPlan()` grows the backoff until a hello, retries a rejection at `max_delay` (`ws_rejected` state and tip) and a busy server on the backoff (`ws_busy`). Staleness is a timer re-armed by every frame. `fetchLive()` in `api.js` is the only liveness request (probe, RTT, foreground check); the probe loop reads its cadence from config each round; `connectWS()` never opens a second socket.
  - Docs: CONFIGURATION.md (keys, same-origin rule, `MW_DISABLE_TESTS`), API.md (`/health/live`, WebSocket frames and close codes), SECURITY.md, DEPLOYMENT.md (forward `Host`), ARCHITECTURE.md.
- Tests: `test_websocket.py` (origin rule table; the F5 case with the placeholder allowlist; 1008/1013/1009 closes; hello first; config-driven sync_prefs limit; heartbeats and heartbeat resilience; the real app booted with the example configs and `MW_DISABLE_TESTS=1` serving liveness 200 while readiness is 503, bootstrap config equal to the hello config, heartbeats, and the exact skipped-task log line), `test_config_examples.py` (new keys required and validated), `test_frontend_rules.py` (one writer of the dot and banner, `hidden` attribute, no hardcoded paths, close codes or timings, every connection state styled and explained), `tests/js/conn.test.mjs` (close classification and pacing), `tests/e2e/connection.test.mjs` (one socket that stays up across several stale windows on a quiet server, banner hidden without CSS, growing backoff on 1013, slow retries on 1008, three 502s show the banner which clears through liveness while `/health` is 503, and no second socket). Mutation-checked: the old origin rule fails 9 pytest cases and the first two browser tests; without the `connectWS()` guard the recovery test sees two sockets.
- Verified live: on a private instance (scale-test config with the placeholder allowlist, `MW_DISABLE_TESTS=1`) the page gets its hello within 20 ms and keeps one green socket; the banner stays hidden.

### F6 - modal header separator

- Reproduce: open a model whose last benchmark failed after an earlier success (for example `AlphaAI::gpt-5.4` in the scale-test data) at 1400 px. `#modal-chk` renders "✗ Bench 5h 9m ·OK10h 8m": 0 px between "·", "OK" and the age. At 400 px the group is hidden, so phones never showed it.
- Root cause: `_checkSlot` relied on the chip's flex gap for spacing, but wrapped "· OK <age>" in a plain `.last-ok` span. That span is a single flex item, so its children laid out inline with no gap and no whitespace. The same separator was built by hand in 13 places with 3 spellings, 4 colours and 5 spacing mechanisms (measured 6/6, 4/4, 3.8/3.8, 4/0 and 2/2 px). Same-path drift: test type labels typed in three places, status glyphs that disagreed (degraded ▲ in the check line, ⚠ everywhere else and in the help text; failed ✕ in notifications), three writers of `#modal-chk`, interval fallbacks (`|| 60`, `|| 3600`, `|| 21600`) that disagreed with `app.yaml`, and `text-xs` on every check-line child, which overrode the chip's intended 9 px mobile size.
- Fix: one primitive in `utils.js`: `segmentsHTML(parts, {sep, cls, attrs})` renders a `.seg-list` row whose only spacing is its gap (`--seg-gap`), `sepHTML('dot'|'slash'|'rule')` renders aria-hidden separators without margins, `SEP_TEXT` joins plain text, and nested groups are themselves lists. Check line, card info line, modal title, schedule line, latency line, error box, score groups and the provider header all use it; `.score-sep` and `.provider-score-sep` are gone, and `kvSep()` is the only `kv-sep`. `STATUS_GLYPH` (✓ ⚠ ✗ ○) is the one glyph map, used by the check line, badges, history, charts, notifications and the help text. `TEST_TYPE_LABELS` in `backend/state.py` (full and short) reaches the page through `/api/config` and `testTypeLabel()`. `renderModalCheckLine()` is the one writer of `#modal-chk`, which gets a `checkLine` tip explaining "OK <age>". Config-derived state starts as `null`, so a missing config shows an unknown state instead of a guessed interval.
- Tests: `tests/e2e/segments.test.mjs` (every row keeps its gap between visible leaves, every separator has equal and uniform spacing, no glued tokens, no stray separator on phones), `tests/js/segments.test.mjs` (primitive contract), `test_frontend_rules.py` (no hand-built separators, glyphs, test type labels or config fallbacks outside their homes; config-derived state starts null), `test_ssoT_labels.py` (full and distinct short labels for every test type, exposed by `/api/config`). Mutation-checked: restoring the plain `.last-ok` wrapper fails two browser tests ("· -> OK (0.0px < 4px)"); every guard in `test_frontend_rules.py` fails on the old frontend.
- Verified live: the check line reads "✓ Health 1h 17m | ✗ Bench 6h 2m · OK 11h 2m" and every dot on the page measures 4/4 px.

### F7 - dependency install instructions

- Reproduce: `pip install -r requirements-dev.txt` on Debian's Python 3.11 fails building `http-ece`. Installing into a virtualenv succeeds.
- Fix: DEVELOPMENT.md creates and activates `.venv` before `pip install` and explains why; README.md, CONTRIBUTING.md and scripts/README.md now point to DEVELOPMENT.md instead of repeating (already drifted) install steps; the `requirements-dev.txt` comment points there too. `.venv` is ignored by git (the `*` whitelist) and by `.dockerignore`. Stale test counts were removed from README.md, DEVELOPMENT.md and the PR template.
- Tests: `test_docs_commands.py` (every `pip install` in the docs and requirement files sits in a fenced block that first runs `-m venv` and `activate`; `.venv` is ignored). Verified by restoring the old README.md, CONTRIBUTING.md and DEVELOPMENT.md: 4 cases fail.

### F8 - tests depend on the production deployment

- Reproduce: `python -m pytest scripts/tests/test_api_errors.py` without access to `stats.ai4fun.dev` fails 6 tests with `URLError: Tunnel connection failed: 403 Forbidden`.
- Root cause: `BASE = "https://stats.ai4fun.dev"` is hard-coded, so these tests check whatever that deployment runs, not this checkout.
- Proposed fix: exercise the routes in-process (FastAPI `TestClient`) with the example configs, as `test_config_examples.py` already loads them.

### F9 - local configs copied into the Docker image

- Observed by reading: `.dockerignore` does not exclude `config/*.yaml`, and the runtime stage runs `COPY config/ config/` before `COPY config/*.example config/`. A build on a host with a local `config/models.yaml` puts it into an image layer even though compose mounts `config/` at runtime.
- Next: confirm with `docker build` plus `docker history`/`docker run ls /app/config`, then copy only the examples.

### F10 - stale import-order list in `_check_imports.py`

- Reproduce: `python3 -m scripts.util._check_imports` reports `VIOLATION` for `audit`, `db_probe` and `db_push` (rank -1) and ranks `favicons` before `db` although it imports `db` at load time.
- Proposed fix: derive the order from the import graph (topological sort) instead of a hand-kept `CHAIN`.

### F11 - mixed line endings

- Reproduce: `git ls-files --eol` shows `i/mixed` for `backend/config.py`, `main.py`, `streaming.py`, `model_info.py`, `notifications.py`, `scheduler.py`, `frontend/index.html` and `frontend/js/ws.js` (the first three are almost entirely CRLF).
- Impact: tools that write LF turn a small change into a whole-file diff; the F2 work had to restore CRLF line by line to keep its diff reviewable.
- Proposed fix: a `.gitattributes` with `* text=auto eol=lf` and a dedicated renormalisation commit.
- Fix: `.gitattributes` sets `* text=auto eol=lf` (whitelisted in `.gitignore`, which ignores everything by default); the 8 files were converted and `git add --renormalize .` run in a commit of its own, whose diff is empty under `--ignore-cr-at-eol`. The conftest `git` fixture is now the one git runner (`git_ignored` builds on it).
- Tests: `test_line_endings.py` (`.gitattributes` exists and is not ignored, every tracked file carries `text=auto eol=lf`, no CRLF or mixed endings in the index or the working tree). Mutation-checked: all 4 fail on the old tree; a `.gitattributes` without the rule fails the attribute case; a CRLF rewrite of `main.py` fails the working-tree case.

### F12 - favicon root URL for IP hosts

- Reproduce: `backend.favicons.root_url("https://127.0.0.1:9/v1")` returns `https://0.1:9`; `root_url("https://api.example.co.uk/v1")` returns `https://co.uk`. Seen in the 8092 log as `Favicon: homepage fetch failed for https://0.1:9`.
- Root cause: the function keeps the last two dot-separated labels of the netloc, including the port, without checking for IP addresses.
- Proposed fix: return the host unchanged for IP literals and `localhost`, and use a public-suffix-aware split for domains.

### F13 - except blocks that neither log nor re-raise

- Reproduce: an AST scan of `backend/*.py` for `except` handlers without a `raise` or a `log_error`/`log.*` call finds 43, for example `routes.py:110` (`except OSError: pass` while fingerprinting assets) and `routes.py:136` (silent fallback to the global version); `state.py:562` is an intended optional-import fallback.
- Note: some are intended control flow (`CancelledError` during shutdown, optional-import fallbacks), so each needs triage. `test_error_logging.py` should grow into the full rule with an explicit allowlist.

### F14 - silent JS catches

- Reproduce: scan `frontend/js/*.js` for `catch` blocks and `.catch()` handlers without a logger call or `throw`: 11 (`api.js` probe, `app.js` RTT and collapsed state, `chart.js` register, `modal.js` audit evals, model info and accordion state (2), `tooltips.js` copy, `utils.js` client-error reporter (2)). The reporter also wrote to `console.warn` directly.
- Fix: each logs through `logError`, or `logDebug`/`logWarn` where the failure is expected; the reporter uses `logWarn`, which only writes to the console and so cannot recurse into the reporter. CONTRIBUTING.md and DEVELOPMENT.md state the rule with the levels.
- Tests: `test_frontend_rules.py::test_js_catches_log_or_rethrow` (per module) and a self-test of the scanner.

### F15 - seeder output locations

- Reproduce: seed into another dir (`--data-dir /tmp/x --config-dir /tmp/x`); the seeder warns that the server only reads `data/` and `config/`. It does not: `MW_DB_NAME` and `MW_*_YAML` join onto those dirs with `Path`, so an absolute path works, but nothing documented it.
- Fix: the seeder prints a start command with absolute paths when it wrote elsewhere, CONFIGURATION.md documents names or absolute paths, and `--app-set PATH=VALUE` (repeatable, existing template keys only) overrides generated app.yaml values, which the browser-test harness uses for short connection timings.
- Tests: `test_scale_test_db.py` (overrides land in the generated app.yaml; unknown keys fail), `test_websocket.py` and the browser tests run servers on temp-dir files.

### F16 - duplicated WebSocket ping settings

- Observed by reading: `main.py` passes `ws_ping_interval=30, ws_ping_timeout=90` to `uvicorn.run`, the Dockerfile `CMD` repeats `--ws-ping-interval 30 --ws-ping-timeout 90`, and DEPLOYMENT.md's nginx example quotes the 90 s. The app-level heartbeat from F5 does not replace them: protocol pings let the server drop half-open clients.
- Proposed fix: `websocket.ping_interval`/`ping_timeout` in app.yaml, applied where uvicorn is started (the container would start through `python -m backend.main` so it reads the config), plus a test that no other copy exists.

### F17 - npm scripts for files outside the repo

- Reproduce: `npm run perf` fails with `Cannot find module tests/perf-test.mjs`; the same for `perf:*`, `stress*`. DEVELOPMENT.md says the scripts are maintained separately.
- Proposed fix: remove the scripts and their tool dependencies (`autocannon`, `lighthouse`) or add the scripts; either way add a test that every npm script's file exists (`test_frontend_rules.py` already checks the test scripts).

### F18 - readiness log on every request

- Observed by reading: `routes.health_check()` logs "Health check failing: ..." at warning level for every 503. With `MW_DISABLE_TESTS`, or when every model is down, the Docker HEALTHCHECK adds one warning every 30 s. (The dashboard no longer polls `/health`, see F5.)
- Proposed fix: log on state changes only (healthy to degraded and back), and test it with two consecutive failing calls.

### F19 - localStorage keys outside the registry

- Reproduce: `grep -o "'mw_[a-z_]*'" frontend/js/*.js` outside `state.js` finds 63 literals in 9 modules (26 in `notifications.js`, 22 in `modal-ranges.js`); the inline FOUC script built in `routes.py` repeats `mw_theme`, `mw_notif_settings` and `mw_notif_local`. `LS` in `state.js` lists 14 keys but few call sites use it.
- Proposed fix: every key in `LS` (the FOUC script gets its keys injected from one backend list shared with the page, like `__MW_CONN__`), plus a guard test for `'mw_` literals.

### F20 - static tunables left in code

- Observed by reading: frontend timings (`app.js` 30 s check-line refresh and metrics poll, 5 min card-bucket refresh, 60 s deploy poll; `ws.js` card-bucket debounce 5 s/30 s; `dom.js` 5 min provider staleness; `chart.js` TTL and cleanup timings), cache TTLs (300/3600/86400 s), freshness ratios (1.5/3.0 in `format.js`), `notifications.js` fallbacks for config values (`toast_duration_ms || 5000`, `history_size || 50`), and backend limits (`favicons.py` sizes, timeout and concurrency, `audit.py` text caps, config watcher restarts, notification cooldowns, `routes.py` bucket and history caps, the 10 s `/api/config` cache).
- Proposed fix: move operator-relevant values into app.yaml (client ones delivered like `__MW_CONN__`), delete the config-value fallbacks first (same class as the F6 interval fallbacks), and extend `test_frontend_rules.py`/`test_config_no_defaults.py` to catch literals.

### F21 - repeated required-key validation

- Observed by reading: the validators in `config.py` raise `... is required` from 35 hand-written checks; 24 are the same `for key in (...): if key not in x: raise ValueError(...)` loop (the F5 keys added two, following the existing pattern rather than mixing styles).
- Proposed fix: one `_require_keys(path, mapping, keys)` helper; `test_config_examples.py` already pins the messages.

### F22 - backend constants copied into state.js

- Observed by reading: `state.js` starts `statusValues`, `testTypes` and `chartViews` with literal copies of `STATUS_VALUES`, `TEST_TYPES` and `CHART_VIEWS`, which `/api/config` then overwrites. Code that runs before config arrives uses the copies, which can drift unnoticed.
- Proposed fix: start them empty (or deliver them in the page bootstrap if something needs them before `/api/config`), and extend `test_config_derived_state_starts_unknown` to them.

### F23 - verifier round 1 (backend/websocket.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Regression from the F5 fix. is_allowed_origin() calls urlsplit(origin) on an untrusted header, and a malformed Origin such as 'http://[' raises ValueError('Invalid IPv6 URL') outside the handler's try. Reproduced on :8092: the client gets an abrupt 1006 with no close frame, and the server logs two ERROR tracebacks ('Unhandled exception in SecurityHeadersMiddleware', 'Exception in ASGI application'). HEAD closed cleanly with 4403. Any unauthenticated client can flood the error log this way.
- Proposed fix: Treat a parse failure as not same-origin: catch ValueError inside is_allowed_origin and fall through to the allowlist, which then closes with 1008. Add malformed origins ('http://[', 'null', 'http://a]b') to test_origin_rule and to a TestClient close-code case.
- Fix: `is_allowed_origin()` no longer parses the header. Same-origin is an exact match of the lower-cased Origin against `http://` or `https://` plus the Host header, so malformed input is simply not same-origin and falls through to the allowlist (close 1008).
- Tests: malformed, `null`, path-bearing and `ws://` origins added to `test_origin_rule`, and `http://[` to the close-code table. Mutation-checked: the old `urlsplit` version fails 5 cases.

### F24 - verifier round 1 (tests/e2e/connection.test.mjs)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: closingSocket() closes the routed WebSocket inside the route handler, so the page's socket never fires onopen. Verified: the mock gives [close 1013], while a real accept-then-close gives [open, close]. The original F5 failure mode (accept, onopen resets the backoff, server closes) is therefore not reproduced. Re-adding _resetBackoff() in ws.onopen passes the whole suite (mutation missed).
- Proposed fix: Let the mocked socket open before closing it (close on a later tick, or after the client's first message), or run the busy and rejected cases against the real server (--app-set server.max_connections=0, or a page origin outside the allowlist) so onopen fires. Keep the growing-backoff assertion.

### F25 - verifier round 1 (frontend/js/ws.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: No timeout for a socket that is accepted but never sends hello. _armStale runs only on a received frame, so a proxy that upgrades but does not forward, or a server stalled before hello, leaves the page 'connecting' forever on one socket. Reproduced with the e2e harness (stale_after=2): after 6s there was still 1 socket and data-state=connecting.
- Proposed fix: Arm the stale timer (or a configurable hello timeout delivered in __MW_CONN__) in onopen, so a socket without a hello is closed and retried, and count it as a failure. Add an e2e case with a silent routed socket that asserts a reconnect.

### F26 - verifier round 1 (tests/e2e/connection.test.mjs)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Stale detection is untested. Removing ws.close(...) from _armStale passes pytest, the JS tests and every e2e test (mutation missed). Live verification shows it currently works: hello then silence gives 3 sockets in 5s with stale_after=2.
- Proposed fix: Add an e2e case in which a routed socket sends one hello and then goes silent, and assert a new socket within stale_after plus the reconnect delay.

### F27 - verifier round 1 (frontend/js/modal.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Phone-width regression in the modal error box. The message is now a flex item next to the nowrap timestamp, so its min-content width equals its longest unbreakable token. With a long URL in the error, ordinary words are clipped at the box edge: at 390px scrollWidth is 554 vs clientWidth 332. HEAD overflows only at the URL (scrollWidth 456) and wraps the words. Screenshots: /tmp/claude-0/verify/errbox-8092.png vs errbox-8093.png.
- Proposed fix: Do not use a flex row for prose. Keep the timestamp and separator inline before the text, or give the message item min-width:0 and overflow-wrap:anywhere. Add an e2e assertion that the error box does not overflow horizontally at 390px with a long message.

### F28 - verifier round 1 (frontend/index.html)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Modal title vertical misalignment introduced by .seg-list {align-items: baseline}. The provider link contains an img, so its baseline is not its text. At 1400px 'AlphaAI' sits 4px above '· gpt-5.4-mini' (text-node mids 111 vs 115). On HEAD all three text nodes share top=65. The segment e2e tests measure only horizontal gaps.
- Proposed fix: Center-align that row, or make the provider link an inline-flex with a text baseline. Extend tests/e2e/segments.test.mjs to assert equal text-node vertical centers per .seg-list row (a probe script is in /tmp/claude-0/verify/pw_align.mjs).

### F29 - verifier round 1 (scripts/tests/test_frontend_rules.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Static guard holes, confirmed by mutation. (1) The readiness-path guard needs a quote right before /health, so the exact original RTT form fetch(`${location.origin}/health`) passes; only e2e test 5 catches it. (2) The glyph guard misses the escaped ▲ (the old degraded triangle) and has no ● (U+25CF, the old ok/unknown check-line glyph) in literal or escaped form. (3) The separator guard ignores em-dash separators, and an added ' - ' separator passes. (4) The fallback guard misses aliased fallbacks (const bi = state.benchmarkInterval; bi || 3600).
- Proposed fix: Match /health\b(?!/live) regardless of the preceding character. Build the glyph pattern from every codepoint that has been used (2713, 2717, 2715, 26a0, 25cb, 25b2, 25cf, in literal and \u forms). Include -/- joiners in the separator guard. Accept that regex guards cannot catch aliasing and rely on e2e for it.

### F30 - verifier round 1 (frontend/js/dom.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Separators on the same code path are still built by hand with em dashes: _statusMessage joins retry and error with ' - ' (dom.js:148), and format.js:394 does the same. Both render inside the modal error box next to the new SEP dot. This contradicts the claim of one separator primitive, and CONTRIBUTING bans em dashes.
- Proposed fix: Route these joins through SEP_TEXT or a named separator in utils.js and extend the separator guard to em and en dash joiners.

### F31 - verifier round 1 (frontend/js/utils.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The claim 'spaces keep copied text readable' does not hold in a browser. Flex items are blockified, so a selection copy of #schedule-info is now '⏱\nHealth: 5m\n·\nBench: 16m\n·\nAudit: 6h' (HEAD: '⏱ Health: 5m · Bench: 2h · Audit: 6h'). The same applies to the modal title and latency line. tests/js/segments.test.mjs only strips tags from the HTML string.
- Proposed fix: Test with window.getSelection().toString() in e2e and either accept newline-separated copies (and drop the claim and the unit test) or render these text rows inline with margin-based spacing.

### F32 - verifier round 1 (frontend/js/app.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The frontend depends on window.__MW_CONN__ and has no guard. Right now the shared :8080 runs the pre-change backend while serving the new frontend files from disk: /health/live is 404 and no bootstrap is injected. Loading it (read-only) shows 0 sockets, the dot stuck on 'connecting', and init failing with TypeError: Cannot read properties of null (reading 'liveness_path'). _probeWhileDown and _onApiResult also dereference state.conn. This will also happen briefly during any deploy where frontend files update before the backend reloads.
- Proposed fix: Operational: restart :8080 on the new code; the shim and MW_BUILT_CSS_PATH are no longer needed once the CSS is built, which /tmp/claude-0/tailwind.min.css predates. Code: check state.conn once at init, show the rejected or down state with one logError instead of scattered TypeErrors, and add a unit or e2e case for a missing bootstrap.

### F33 - verifier round 1 (frontend/js/app.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: _probeWhileDown() runs probeBackend().then(...) without .catch, and it re-arms itself inside the .then callback. Any throw in the recovery branch stops the probe loop for good. ws.js:281 has the same uncaught .then. CONTRIBUTING requires every .then chain to have a .catch, and no test enforces that.
- Proposed fix: Add .catch(logError) and re-arm in .finally(). Extend test_frontend_rules with a .then-without-.catch scan.

### F34 - verifier round 1 (frontend/js/app.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: _measureClientRTTFresh logs a liveness failure with logError, even though CONTRIBUTING (as updated by the same writer) says expected failures such as a liveness probe while the server is down should use logDebug or logWarn.
- Proposed fix: Use logDebug or logWarn for the RTT probe failure, as probeBackend already does.

### F35 - verifier round 1 (frontend/sw.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The F14 rule is not enforced outside frontend/js: sw.js has 4 catches that swallow or only console.error (lines 25, 54, 75, 83), and the inline FOUC script built in backend/routes.py:403 has `catch(e){}`. test_js_catches_log_or_rethrow scans only frontend/js/*.js.
- Proposed fix: Extend the scanner to sw.js and the inline scripts, with an explicit, commented allowlist where the central logger cannot be imported (service worker context).

### F36 - verifier round 1 (backend/routes.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: conn.js is missing from _MODULE_PRELOAD_ORDER although api.js, ws.js and help.js import it on the startup path, so the browser finds it one round trip later. The preload list is a hand-kept copy of the import graph.
- Proposed fix: Add conn to the list, or better, derive the preload list from the import graph of app.js.

### F37 - verifier round 1 (docs/API.md)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The endpoint count is stale after adding /health/live: API.md:3 and README.md:13 and README.md:80 say '15 REST endpoints', but there are now 16 (16 '### GET/POST/...' sections, 16 routes).
- Proposed fix: Update the counts or remove them; a doc test could compare against app.routes.

### F38 - verifier round 1 (scripts/util/scale_test_db.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: --app-template and --app-set are applied only after the DB is seeded (minutes for the default 5000 models) and after models YAML is written. A bad value leaves partial output (verified: DB plus models YAML, no app YAML). A nested unknown key (--app-set nosuch.key=1) raises a bare KeyError: 'nosuch', because only the leaf is checked. Errors surface as tracebacks, not argparse errors.
- Proposed fix: Load the template and apply the overrides first (checking every path segment), fail through ap.error(), then seed and write.

### F39 - verifier round 1 (scripts/util/scale_test_db.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: F15 is incomplete. Favicons go to <--data-dir>/favicons, but the server always reads st.DATA_DIR/favicons (FAVICON_DIR is not overridable). An out-of-tree run, including the e2e harness, therefore has no provider logos, while the printed start command implies a fully isolated setup.
- Proposed fix: Make the data dir overridable at one choke point (e.g. MW_DATA_DIR in state.py, from which FAVICON_DIR, VAPID and TIKTOKEN derive), or state in the seeder output that favicons are only served from data/.

### F40 - verifier round 1 (docs/DEVELOPMENT.md)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The claim that browser tests 'never touch data/, config/ or a running instance' is false on a fresh clone. Startup loads or generates VAPID keys in the fixed st.DATA_DIR: the HEAD scratch server logged 'Generated new VAPID key pair at .../data/vapid_private.pem', and the new server loaded the repo data/ keys while its DB pointed elsewhere. test_real_app_with_tests_disabled does the same.
- Proposed fix: Same fix as the favicon item (an overridable data dir) and point it at the temp dir in the harness and fixture, or reword the doc.

### F41 - verifier round 1 (backend/streaming.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Results computed without an encoder are indistinguishable. With per_chunk_tokens empty, the ITL normalization block is skipped: a direct _compute_itl_statistics check gives an effective median and p99 of 80 ms without an encoder vs 20 ms with one for 4 tokens per chunk. Batching providers can therefore show inflated ITL and stall metrics, which can feed critical-tier degradation, for up to token_encoding_retry (1h) or indefinitely offline. Only one log line is written per retry.
- Proposed fix: Record on each result whether token counts came from the encoder (or leave effective ITL fields null without one), and skip ITL-based degradation when normalization was not possible. Add a test.

### F42 - verifier round 1 (scripts/tests/test_websocket.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: When heartbeats are missing, test_real_app_with_tests_disabled blocks in ws.receive_json() until conftest's 120s subprocess timeout. The mutation is caught, but only after 2 minutes and with a timeout message instead of an assertion.
- Proposed fix: Bound the child's receives (a thread with join(timeout), or a short socket timeout) and assert on the frames received.

### F43 - verifier round 1 (package.json)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: `npm test` is `pytest && npm run test:js`. While F8 fails offline, the JS unit tests never run through the documented entry point, and `npm test` exits non-zero. Separately, bare `pytest scripts/tests` now fails at conftest import (ModuleNotFoundError: backend); HEAD failed for 4 modules, now the whole session does. Only `python -m pytest` works.
- Proposed fix: Fix F8 (in-process TestClient). Add a pytest config with pythonpath = . so that bare pytest works too.

### F44 - verifier round 1 (backend/routes.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: built_css() calls log_error with a full FileNotFoundError traceback on every stylesheet request while the file is missing, which is every page load. That is a lot of noise for one misconfiguration.
- Proposed fix: Log once per path, or at error level without exc_info for FileNotFoundError, and keep the 404 body.

### F45 - verifier round 1 (backend/state.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: MODEL_KEY_SEP is a single source only for the backend. The frontend still hardcodes '::' (state.js:198 and 213, utils.js:159, notifications.js:302 and 371), and backend/notifications.py:737/762 builds f"{provider}::" directly instead of make_model_key.
- Proposed fix: Use make_model_key(provider, '') in notifications.py, and deliver the separator to the frontend via /api/config or __MW_CONN__, using one parse and build helper in utils.js.

### F46 - verifier round 1 (backend/main.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The _TEST_TASKS comment says 'Background work that exists to run tests', but the table includes the config watcher (hot reload), favicons and model info. Behaviour matches HEAD, but with MW_DISABLE_TESTS config hot reload is off, so the documented path 'a config reload reaches open pages through hello' cannot be exercised in the mode DEVELOPMENT.md recommends.
- Proposed fix: Rename the table and log line to what it is (external or background fetches), or move the config watcher out of it if hot reload should work with tests disabled.

### F47 - verifier round 1 (backend/websocket.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: close_all's per-socket except now logs with log.debug. Better than the previous silent handler, but it does not follow CONTRIBUTING's Python rule (log_error or re-raise). This belongs to F13's triage.
- Proposed fix: Triage it with F13's allowlist, or log through log_error once with the aggregate count.

### F48 - verifier round 1 (backend/websocket.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Informational. The same-origin rule trusts the Host header and there is no Host validation (no TrustedHost or allowed_hosts anywhere in backend). A DNS-rebinding page is therefore same-origin by construction and now gets the WebSocket, which the strict allowlist used to refuse. The HTTP API is already exposed the same way, so no new data leaks.
- Proposed fix: If rebinding matters for deployments, add TrustedHost validation (hosts from config) at the middleware choke point, which covers both HTTP and WS.
