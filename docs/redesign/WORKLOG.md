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

### Process notes

- Every writer round ends with an independent read-only verifier that reproduces the original failures, mutation-checks the new tests and reviews the diff against the rules. Round 1 of this found a regression the writer's own tests missed, so the step stays.
- Register verifier output mechanically (script from the structured result) so nothing is lost or reworded; then clean up duplicates by hand.

- Environment: general web egress is blocked (Kagi, jsdelivr, the live instance), so research agents use the built-in web search and fetch tools.
- `pkill -f` with a pattern that also appears in the invoking shell command kills the shell itself; use a bracketed pattern such as `pgrep -f "[u]vicorn backend"`.
- The bracket trick does not help when the same shell command also launched the server: the launch text contains the plain pattern, so `pgrep` matches the shell. Launch and kill in separate commands.
- `backend/config.py`, `main.py`, `streaming.py`, `frontend/index.html` and `frontend/js/ws.js` are CRLF-dominant (F11). Edit them byte-wise or restore endings afterwards (a difflib pass that gives unchanged lines their HEAD ending and new lines CRLF keeps diffs minimal; matching lines by content does not, empty lines collide), and check `git diff --stat` for whole-file diffs before handing over.
- Playwright's `page.on('websocket')` does not fire for sockets answered by `page.routeWebSocket`; count mocked connections inside the route handler.
- `page.clock` cannot stand in for a server heartbeat: fast-forwarding fires the client's stale timer before any real frame arrives. Shorten the real timings in the server config instead (`--app-set websocket.heartbeat_interval=...`).
- Node 22's `node --test` does not search a directory argument; pass a quoted glob (`'tests/js/*.test.mjs'`).
- Starlette's `TestClient` runs the app's lifespan in its own loop, so heartbeat and startup behaviour are tested with the real app in a subprocess (`run_python`) rather than by importing `backend.main` into the pytest process, which would load config at import.

