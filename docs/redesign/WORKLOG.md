# Redesign worklog

Running log of the UI redesign: goals, progress per round, how we build and test, and what we changed about the process.
Issues live in [FINDINGS.md](FINDINGS.md).

## Goals

- Make the dashboard modern, pleasing to look at and to use.
- It should feel smart and adaptive, be convenient, and be easy to understand for newcomers and operators alike.
- Show novel ideas first; implement only after a direction is chosen.

## Engineering rules for this work

- DRY, KISS, YAGNI; build shared modules, functions and tests meant for reuse.
- One source of truth for every value; no static tunables in code (config lives in `config/app.yaml`, surfaced through `st.c.*` and `/api/config`).
- Fix at the lowest choke point; no band-aids, no backward-compatibility shims.
- Expose settings, signals and actions for everything built.
- All logging goes through the central loggers (`backend/state.py` `log`/`log_error`, `frontend/js/utils.js` `logDebug`/`logInfo`/`logWarn`/`logError`).
- Every finding gets a test that would catch it next time.
- One writing agent at a time; any number of reading agents.

## How we build and test

- Local instance: virtualenv with `requirements-dev.txt` (see DEVELOPMENT.md), `npm run build:css`, seeded with `python3 -m scripts.util.scale_test_db`, run with `MW_DISABLE_TESTS=1` against the scale-test config. Since F2 and F3 no tiktoken shim or `MW_BUILT_CSS_PATH` is needed.
- Scratch server instances: start with `setsid nohup ... &`, record the PID in a separate command, stop with `kill <pid>`.
- Visual checks: Playwright with the pre-installed Chromium, screenshots at 1440, 1280, 820 and 390 px in light and dark themes.
- Tests: `npm test` runs `python3 -m pytest scripts/tests/ -v` and the JS unit tests (`node --test 'tests/js/*.test.mjs'`); `npm run test:e2e` runs the Playwright browser tests. Shared pytest fixtures live in `scripts/tests/conftest.py` (`run_python` for clean subprocesses from the project root, `git_ignored`). Project paths come from `backend.state`, never from `__file__`.
- Browser tests start their own server through `tests/e2e/harness.mjs` (seeded temp dir, free port, `MW_DISABLE_TESTS=1`, short connection timings via the seeder's `--app-set`). Frontend logic that can be pure lives in DOM-free modules (`utils.js`, `state.js`, `conn.js`) so `node --test` covers it without a browser.
- Every fix gets a regression test, and the test is mutation-checked: restore the old file from git, confirm the test fails, restore the fix.

## Rounds

### Round 1 - discovery and ideation

- Ran the app locally with seeded data and captured screenshots of the current UI.
- Workflow `redesign-understand-ideate`: four readers (UI inventory, data and API, UX critique, constraints) feed five concept generators (decision-first, calm and ambient, power-user flow, storytelling and time, visual language), two concepts each.
- Next: a judge panel scores the concepts, synthesis into three or four directions, then interactive mockups for each direction on a shared sample dataset.

### Round 2 - setup findings

- Fixed F1 (seeder root, and its drifted private schema), F2 (tiktoken at import, now a non-blocking background load with a configurable encoding, retry interval and cache in `data/tiktoken`), F3 (built CSS default path, Docker ENV next to its COPY, explicit missing-file error) and F7 (virtualenv install docs, one setup guide).
- Added 6 test modules plus shared fixtures in `conftest.py`; the suite is 163 passing, plus the 6 `test_api_errors.py` cases that need the production host (F8).
- Recorded F8 to F13 found along the way, and a lead for F5.
- Fixed F5 (same-origin WebSocket rule, one close-code table, hello and heartbeat, liveness probe separate from readiness) and F6 (one segment and separator primitive), plus F14 and F15. Added JS unit tests (`npm run test:js`) and Playwright browser tests (`npm run test:e2e`).
- An independent verifier reproduced each original failure and confirmed all fixed, then reported 26 new problems (F23 to F48). F23 was a regression from the F5 fix (a malformed Origin header raised); fixed and mutation-checked before the checkpoint commit.
- Checkpoint state: 248 Python tests (F8 file deselected offline), 17 JS unit tests and 8 browser tests pass. The shared dev server runs without the tiktoken shim or `MW_BUILT_CSS_PATH`.

### Round 3 - F5 and F6

- Re-verified both reports first: F5 (every same-origin socket closed with 4403, 3 s retry loop, false "Server unreachable") and F6 ("·OK10h 8m", 0 px gaps) reproduced on the shared instance and on a private one.
- F5: same-origin rule at the origin check; hello frame, heartbeat, distinct close codes and a liveness endpoint on the server; connection policy in `websocket.*` config delivered through `window.__MW_CONN__` and the hello; `conn.js` as the one writer of the dot and banner with a pure, unit-tested reconnect policy.
- F6: `segmentsHTML`/`sepHTML`/`SEP_TEXT` as the one separator primitive, `STATUS_GLYPH`, `TEST_TYPE_LABELS` via `/api/config`, one writer of the check line, no config fallbacks.
- Added the JS unit and browser test layers (npm `test:js`, `test:e2e`), `test_websocket.py` and `test_frontend_rules.py`. Every new test was mutation-checked against the old code.
- Registered F14 to F22; F14 (silent JS catches) and F15 (seeder output locations) fixed on the way.

### Round 4 - backend and config group

- F11 first, in a commit of its own: `.gitattributes` with `* text=auto eol=lf` and a renormalisation whose diff is empty under `--ignore-cr-at-eol`.
- Then F9, F10, F12, F13, F16, F18, F21, F36, F37, F41, F44, F46, F47, F48, the backend half of F45, F66 (found here independently) and the new F97. Each was reproduced first, and each test was mutation-checked against the old code.
- New shared pieces: `st.condition_changed()` (log a recurring problem on change), `st.is_ip_literal()`, `st.TESTS_DISABLED`, `st.LOG_LEVELS`, `config.config_path()`, `config._require_keys()`, `main.server_options()` behind `python -m backend.main` (the one launch path: Dockerfile, docs, seeder, browser tests), `HostCheckMiddleware`, `routes.module_preload_order()`, `migrations._table_columns()`, and the conftest `git` and `dockerfile_stages` fixtures.
- New config keys `server.allowed_hosts`, `websocket.ping_interval`, `websocket.ping_timeout`; new dependencies `publicsuffixlist` (runtime, bundles the list) and `pyflakes` (dev, undefined-name guard).
- The backend `except` rule now matches the frontend one and is enforced for every handler, with a keyed, documented control-flow allowlist.
- Checked live on a private instance (8093): a rebinding `Host` got 400 and a refused WebSocket, client error reports answered 200, the preload list held all 20 static modules, three stylesheet misses logged once, and a config edit hot-reloaded with `MW_DISABLE_TESTS`.

### Round 5 - tests and scripts group

- F8, F24, F26, F29, F38, F39, F40, F42 and the pytest half of F43 fixed, with F30 (the joins the stricter separator guard caught) and the new F98 and F99. Each was reproduced first and each new test mutation-checked.
- New shared pieces: `MW_DATA_DIR` in `state.py` (DB, VAPID keys, `FAVICON_DIR`, tiktoken cache), the seeder's `--server-env`, `pytest.ini`, the conftest `example_config_env` fixture and a private data dir per `run_python` child, `scripts/tests/app_child.py` (`emit`/`result`, `receive_until`), `_char_forms()` in the frontend rules, `TIER_DOT` and `_LOG_TAG_SEP` in `utils.js`.
- Blocked: F17 and the `npm test` half of F43 both need a `package.json` edit, which the session's permission check refused; they wait for the user.

### Process notes

- Every writer round ends with an independent read-only verifier that reproduces the original failures, mutation-checks the new tests and reviews the diff against the rules. Round 1 of this found a regression the writer's own tests missed, so the step stays.
- Register verifier output mechanically (script from the structured result) so nothing is lost or reworded; then clean up duplicates by hand.

- Environment: general web egress is blocked (Kagi, jsdelivr, the live instance), so research agents use the built-in web search and fetch tools.
- `pkill -f` with a pattern that also appears in the invoking shell command kills the shell itself; use a bracketed pattern such as `pgrep -f "[u]vicorn backend"`.
- The bracket trick does not help when the same shell command also launched the server: the launch text contains the plain pattern, so `pgrep` matches the shell. Launch and kill in separate commands.
- Every text file is LF since F11 (`.gitattributes`, guarded by `test_line_endings.py`); still check `git diff --stat` for whole-file diffs before handing over.
- Playwright's `page.on('websocket')` does not fire for sockets answered by `page.routeWebSocket`; count mocked connections inside the route handler.
- `page.clock` cannot stand in for a server heartbeat: fast-forwarding fires the client's stale timer before any real frame arrives. Shorten the real timings in the server config instead (`--app-set websocket.heartbeat_interval=...`).
- Node 22's `node --test` does not search a directory argument; pass a quoted glob (`'tests/js/*.test.mjs'`).
- Starlette's `TestClient` runs the app's lifespan in its own loop, so heartbeat and startup behaviour are tested with the real app in a subprocess (`run_python`) rather than by importing `backend.main` into the pytest process, which would load config at import.
- Starlette's `TestClient.websocket_connect` ignores `base_url` and always sends `Host: testserver`; pass an absolute `ws://host/...` URL when the Host matters. The real app only answers names that name it (F48), so tests against `backend.main` use `base_url="http://localhost"`.
- SQLite's trace callback never sees a statement that fails to prepare, which is exactly the swallowed kind; record statements at `execute()` (a small proxy) to prove none is issued.
- The register can grow while a round runs (F49 to F96 arrived during round 4). Edit it with targeted replacements on a fresh read, take the next free ID only after re-reading, and check `git diff` shows no lost lines.
- A Playwright-mocked WebSocket opens only after its route handler returns; a close sent inside the handler arrives first, so the page never sees `open`. For "accepted, then closed" use `ws.connectToServer()` and close on the server's first frame. An `onClose` handler on the page side is called again when the server side closes.
- Starlette's `TestClient` WebSocket `receive` has no timeout; read frames in a real-app child through `app_child.receive_until()` so a silent server fails an assertion instead of the 120 s subprocess timeout.
- A mutation that breaks `DATA_DIR` makes children write into the checkout's `data/` (a `metrics.db` appeared and was removed). Never combine such a mutation with the seeder tests: the seeder deletes its DB name first, which in `data/` is the shared dev server's database.
