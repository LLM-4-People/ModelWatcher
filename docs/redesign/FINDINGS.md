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
| F8 | tests | `test_api_errors.py` calls the production host `https://stats.ai4fun.dev`, so 6 tests fail offline and the suite checks a remote deployment instead of the checked-out code | medium | fixed |
| F9 | docker | `COPY config/ config/` in the Dockerfile bakes the build host's local `config/*.yaml` (gitignored, may hold inline keys) into image layers; the later `COPY config/*.example config/` is redundant | medium | fixed |
| F10 | scripts | `_check_imports.py` ranks modules with a hand-kept `CHAIN` that misses `audit`, `db_probe`, `db_push`, `migrations` and others and still lists the removed `ping`, so it reports false violations | low | fixed |
| F11 | repo | Mixed CRLF/LF line endings in 8 tracked files; any edit that normalises them turns into a whole-file diff | low | fixed |
| F12 | backend | `favicons.root_url()` keeps the last two host labels, so IP hosts become invalid URLs (`https://127.0.0.1:9/v1` becomes `https://0.1:9`) and multi-part TLDs collapse (`api.example.co.uk` becomes `co.uk`) | low | fixed |
| F13 | backend | 43 `except` blocks neither log nor re-raise, against the CONTRIBUTING.md rule; `test_error_logging.py` only catches `except Exception: pass` | low | fixed |
| F14 | frontend | 11 JS `catch` handlers neither log nor re-throw, and the client-error reporter writes to `console` directly; no test enforces the CONTRIBUTING.md rule for JS | low | fixed |
| F15 | scripts/docs | The seeder says the server only reads `data/` and `config/`, yet `MW_DB_NAME`/`MW_*_YAML` accept absolute paths (undocumented), so isolated runs looked impossible | low | fixed |
| F16 | backend/docker | WebSocket protocol ping interval/timeout (30s/90s) are hardcoded twice, in `main.py`'s `__main__` and the Dockerfile `CMD`, and quoted in DEPLOYMENT.md | low | fixed |
| F17 | repo | The `perf`/`stress` npm scripts point at `tests/*.mjs` files that are not in the repository | low | open |
| F18 | backend | `/health` logs a warning on every failing request, so a Docker HEALTHCHECK against a degraded or `MW_DISABLE_TESTS` instance logs one every 30s | low | fixed |
| F19 | frontend | 63 localStorage key literals in 9 modules (and 5 in the inline FOUC script in `routes.py`) bypass the `LS` registry in `state.js` | low | open |
| F20 | backend/frontend | Static tunables remain in code, including two fallbacks for config values in `notifications.js` (toast duration, history cap) | medium | open |
| F21 | backend | `config.py` hand-writes 35 "is required" checks, 24 of them the same `for key in ...: if key not in ...` loop | low | fixed |
| F22 | frontend | `state.js` seeds `statusValues`, `testTypes` and `chartViews` with copies of backend constants that `/api/config` later overwrites | low | open |
| F23 | backend | Regression from the F5 fix: a malformed `Origin` header (`http://[`) raised inside `is_allowed_origin()`, so the WebSocket died with 1006 and two error tracebacks instead of a clean 1008 | medium | fixed |
| F24 | tests | closingSocket() closes the routed WebSocket inside the route handler, so the page's socket never fires onopen | medium | fixed |
| F25 | frontend | No timeout for a socket that is accepted but never sends hello | medium | open |
| F26 | tests | Stale detection is untested | medium | fixed |
| F27 | frontend | Phone-width regression in the modal error box | medium | open |
| F28 | frontend | Modal title vertical misalignment introduced by .seg-list {align-items: baseline} | low | open |
| F29 | tests | Static guard holes, confirmed by mutation | low | fixed |
| F30 | frontend | Separators on the same code path are still built by hand with em dashes: _statusMessage joins retry and error with ' - ' (dom.js:148), and format.js:394 does the same | low | fixed |
| F31 | frontend | The claim 'spaces keep copied text readable' does not hold in a browser | low | open |
| F32 | frontend | The frontend depends on window.__MW_CONN__ and has no guard | low | open |
| F33 | frontend | _probeWhileDown() runs probeBackend().then(...) without .catch, and it re-arms itself inside the .then callback | low | open |
| F34 | frontend | _measureClientRTTFresh logs a liveness failure with logError, even though CONTRIBUTING (as updated by the same writer) says expected failures such as a liveness probe while the server is down should use logDebug or logWarn | low | open |
| F35 | frontend | The F14 rule is not enforced outside frontend/js: sw.js has 4 catches that swallow or only console.error (lines 25, 54, 75, 83), and the inline FOUC script built in backend/routes.py:403 has `catch(e){}` | low | open |
| F36 | backend | conn.js is missing from _MODULE_PRELOAD_ORDER although api.js, ws.js and help.js import it on the startup path, so the browser finds it one round trip later | low | fixed |
| F37 | docs | The endpoint count is stale after adding /health/live: API.md:3 and README.md:13 and README.md:80 say '15 REST endpoints', but there are now 16 (16 '### GET/POST/...' sections, 16 routes) | low | fixed |
| F38 | tests | --app-template and --app-set are applied only after the DB is seeded (minutes for the default 5000 models) and after models YAML is written | low | fixed |
| F39 | tests | F15 is incomplete | low | fixed |
| F40 | docs | The claim that browser tests 'never touch data/, config/ or a running instance' is false on a fresh clone | low | fixed |
| F41 | backend | Results computed without an encoder are indistinguishable | low | fixed |
| F42 | tests | When heartbeats are missing, test_real_app_with_tests_disabled blocks in ws.receive_json() until conftest's 120s subprocess timeout | low | fixed |
| F43 | package.json | `npm test` is `pytest && npm run test:js` | low | open |
| F44 | backend | built_css() calls log_error with a full FileNotFoundError traceback on every stylesheet request while the file is missing, which is every page load | low | fixed |
| F45 | backend | MODEL_KEY_SEP is a single source only for the backend | low | open |
| F46 | backend | The _TEST_TASKS comment says 'Background work that exists to run tests', but the table includes the config watcher (hot reload), favicons and model info | low | fixed |
| F47 | backend | close_all's per-socket except now logs with log.debug | low | fixed |
| F48 | backend | No Host header validation: with the same-origin WebSocket rule, a DNS-rebinding page now counts as same-origin (the HTTP API was already exposed this way) | low | fixed |
| F49 | frontend | Pressing Escape inside the date-range picker closes the whole model modal instead of only the picker | medium | open |
| F50 | frontend | Help > Legend > Performance prints the literal text `<span class="underline">Critical</span>` | medium | open |
| F51 | frontend | The Status legend's tip promises live counts, but the legend renders none and calls the error status "Errors" | low | open |
| F52 | frontend | A tooltip stays up after its trigger opens content and covers it: the calendar tip hides the picker's month header, a Help section's tip covers the section it just expanded | low | open |
| F53 | frontend | On touch, a long-pressed tooltip is never dismissed by a tap and stays above sheets and the modal; a long-press inside a card also opens the modal | medium | open |
| F54 | frontend | History day headers show the wrong chevron in both states (▸ when expanded, a rotated ▾ pointing left when collapsed) and can only be toggled with a pointer | low | open |
| F55 | frontend | The history window is persisted as an absolute `mw_hist_since` that infinite scroll widens (3d became 15 days) and the next modal restores, while `mw_hist_range` still says `3d` | medium | open |
| F56 | frontend | The theme toggle cannot return to following the system theme once it has been clicked | low | open |
| F57 | frontend | `#config-warning` in the header is never filled or shown; `refreshModelList()` only ever hides it | low | open |
| F58 | frontend | Card and modal show different TTFT values under the same label and tier colours (BetaLLM/gpt-5.4: 1.86 s on the card, 160 ms in the modal) | high | open |
| F59 | backend/frontend | A failed last benchmark is invisible on the dashboard: the next health success sets status back to `online`, and the card keeps its green glow while silently dropping the TPS and P99 tiles | high | open |
| F60 | backend | Modal trends are inverted for ranges beyond `recent_history`: the SQL path feeds newest-first rows to `compute_trends()` (14d TPS: API says degrading 31.3, the data says improving 16.4) | high | open |
| F61 | backend | The card "Health + TTFT" view plots benchmark TTFT: its buckets are built from benchmark records and equal the speed view's | medium | open |
| F62 | backend | Every card trend rests on 3 benchmarks (all 184 trend entries have `data_points: 3`), e.g. "availability degrading 66.7 pp"; the split, minimum and deadbands are code constants | medium | open |
| F63 | backend | Provider summaries contradict themselves: trend direction is a majority vote but the change averages magnitudes across directions ("stable, change 33", "degrading, change 4" under a 5-point deadband), and scores are unrounded (62.349999999999994) | medium | open |
| F64 | backend | Chart bands change meaning at the 2-day boundary: the RAM path returns median and true P10/P90, the SQL path the mean with MIN/MAX labelled `p10`/`p90`, and the tooltip calls both "P10-P90" | medium | open |
| F65 | backend | `tier_continuous_score()` scores lower-is-better metrics one band harsher than higher-is-better ones (TTFT 999 ms = 1.0, 1000 ms = 0.75; TPS 100 = 1.0) and returns negative, non-monotonic scores when a threshold list lacks the 0 sentinel, which validation allows | medium | open |
| F66 | backend | `POST /api/client-error` always returns 500 (`NameError` on a bare `c` in `routes.py:719`), so every browser error report is lost | medium | fixed |
| F67 | frontend | A filter with no matches leaves a blank page with only "0 of 20 models"; there is no empty state | low | open |
| F68 | frontend | The main area has no loading or error state: a first visit without cache is blank until `/api/metrics` answers, and with the API down it stays blank under the banner | medium | open |
| F69 | frontend | Provider header status counts are unlabeled coloured numbers and ignore the active filter (AlphaAI shows "4" while 1 card matches) | low | open |
| F70 | frontend | `prefers-reduced-motion` does not stop the trend-arrow animation: the override loses on specificity, and 23 infinite animations run with reduce set | medium | open |
| F71 | frontend | Light theme fails WCAG AA for 14 of 23 text elements on a card (values 3.30:1, labels 1.81 to 2.72:1, units 2.56:1); in dark mode Bad and Critical are near-identical reds | medium | open |
| F72 | frontend | The model modal does not manage focus: focus stays on the card, Tab leaves the dialog for the page behind, and the page scrolls under it | medium | open |
| F73 | frontend | The closed Help drawer is only translated off-screen: its 6 controls stay in the tab order and accessibility tree, and its shadow shows at the right edge | low | open |
| F74 | frontend | Keyboard and screen-reader structure: cards are `role=button` with nested focusables, the first card is the 17th tab stop with no skip link, and nothing below the h1 is a heading | medium | open |
| F75 | frontend | Interactive targets below 24x24 px (WCAG 2.5.8): the focusable connection dot is 10x10, chart-view pills are 18 px tall, score groups 23 px | low | open |
| F76 | frontend | Capability labels are defined in four places with three spellings (Thinking/Reasoning, JSON/Structured Output/Structured output), and the Specs options exist in both `index.html` and `filter.js` | low | open |
| F77 | frontend | Status labels have no single source: `error` is "Offline" on badges and in the filter, "Errors" in the legend, and the Offline filter also matches `unknown` | low | open |
| F78 | frontend | HELP texts contradict the scoring and degradation rules and hardcode config values (scores described as trends, S as "TPS trend", C with chunk CV; "one or more" Critical metrics where the rule is two; stall "500ms", hiccup "3x") | medium | open |
| F79 | frontend | Rendered em and en dashes remain on 19 lines beyond F30 (footer, HELP, capability tip, chart tooltips, filter labels), and no guard covers them | low | open |
| F80 | frontend | Chart view and series labels are hardcoded in `chart.js`/`chart-helpers.js` and used as lookup keys; the SSoT label tests only pin two variable names, so such copies pass | low | open |
| F81 | frontend | Score tier boundaries and labels (80/60/40/20, Excellent to Critical) are hardcoded twice in the frontend and are not in config | low | open |
| F82 | frontend/backend | The base palette is copied into seven files (input.css, index.html, routes.py FOUC, manifest.json, theme.js, state.js, format.js), and the two series-colour copies already disagree | low | open |
| F83 | frontend | Chart colour code accepts only 6-digit hex: any other token silently turns tier zones grey, and `_hexToRgba` returns `rgba(NaN,NaN,2,0.5)` | low | open |
| F84 | frontend | Card and modal charts render with `devicePixelRatio: 1`, so they are blurry on high-DPI screens (346 px backing store for 346 CSS px at DPR 2) | low | open |
| F85 | frontend | Mobile breakpoint and default ranges are duplicated: `640` literals and `<= BP_SM` next to `< BP_SM`, default chart and history ranges typed in two modules, and English-only picker month names | low | open |
| F86 | frontend | Modal tier bands and threshold lines stay drawn when their series is hidden through the legend | low | open |
| F87 | backend | `/api/deploy-version` and the `sw.js` fingerprint are cached until some client loads `/`, so the documented 60 s deploy poll never reloads an open tab on its own | low | open |
| F88 | frontend | Card-bucket refresh re-downloads every provider's buckets after any result and every 5 min (88 KB for 20 models, 1.13 MB for 500) | medium | open |
| F89 | frontend | First load fetches and builds every expanded card, bypassing the lazy provider observer: 500 models give 500 cards, 500 canvases and 23,495 nodes for 9 visible cards | medium | open |
| F90 | backend/frontend | The header schedule line shows check intervals that read as data ages, and keeps advertising them while the scheduler is disabled; the API exposes no scheduler state | low | open |
| F91 | docs | API.md documents trends as `{"tps": "up"}`, and app.yaml.example says reliability quality comes from the C and S scores; both are wrong | low | open |
| F92 | frontend | The model name is printed twice on every card and in the modal title when the display name equals the model id (20 of 20 in the demo) | low | open |
| F93 | frontend | Below 640 px every C/S/R score is hidden (cards and provider headers) and the modal never shows them, so scores are unreachable on phones | medium | deferred-to-redesign |
| F94 | frontend | Card sparklines scale each series to its own range with no axis or legend, and their tier bands belong to one series only (the TTFT line is drawn over TPS bands) | medium | deferred-to-redesign |
| F95 | frontend | Score chips render "C 74 %" with a gap before the unit, and a stable trend as "±", which reads as uncertainty | low | deferred-to-redesign |
| F96 | frontend | At 390 px the Scores filter's last bucket ("≤19%") sits past the viewport edge in a scroller whose scrollbar is hidden | low | open |
| F97 | backend | Config paths were resolved in three places: `MW_*_YAML` overrides were honoured when loading, but the watcher watched `config/` and the `reset_epoch` rewrite edited `config/models.yaml`, so overrides outside `config/` never hot-reloaded and their `reset_epoch` was re-applied on every reload | low | fixed |
| F98 | frontend | The browser notification title and the toast's aria-label join provider, model and event with a hand-built `' - '`, outside `SEP_TEXT`, and build the same string twice | low | fixed |
| F99 | docs | `.env.example` says `MW_DISABLE_TESTS` skips the config watcher, which runs in every mode since F46, and leaves out the token encoder it does skip | low | fixed |

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
- Fix: `test_api_errors.py` runs the checked-out `backend.main` in a child (`run_python`, the `example_config_env` fixture, `MW_DISABLE_TESTS`, a private data dir) and sends one request per layer that answers with an error: handler 400s, query and body validation 422s, router 404 and 405, and the Host (400) and body-size (413) middleware. Each answer must be `application/json` with `error` as its only key. The case table is passed to the child, which reports through `app_child.emit()`.
- Tests: `test_api_errors.py` (10 cases). Reproduced first: 6 failures with `URLError: Tunnel connection failed: 403`. Mutation-checked: without the `RequestValidationError` and `StarletteHTTPException` handlers the five validation, 404 and 405 cases fail; a `detail` key in the middleware answers fails the two middleware cases.

### F9 - local configs copied into the Docker image

- Observed by reading: `.dockerignore` does not exclude `config/*.yaml`, and the runtime stage runs `COPY config/ config/` before `COPY config/*.example config/`. A build on a host with a local `config/models.yaml` puts it into an image layer even though compose mounts `config/` at runtime.
- Next: confirm with `docker build` plus `docker history`/`docker run ls /app/config`, then copy only the examples.
- Reproduced statically (no Docker daemon in this environment): `.dockerignore` listed no `config/` pattern, so `COPY config/ config/` put every local `config/*.yaml` into the runtime layer; the later `COPY config/*.example` only re-copied the templates.
- Fix: the redundant `COPY config/ config/` is gone, so only `config/*.example` ships; `.dockerignore` also drops `config/*.yaml`, `config/*.yml` and every `.env*` from the build context, so no later `COPY` can pick them up.
- Tests: `test_deployment.py` (every config source the final stage copies is an `.example` and nothing copies the whole context; a Docker-style `.dockerignore` matcher leaves out `config/app.yaml`, the scale-test copies, `.env.modelwatcher` and `data/` but keeps the examples and the code). Mutation-checked: the old Dockerfile and `.dockerignore` fail 9 cases.

### F10 - stale import-order list in `_check_imports.py`

- Reproduce: `python3 -m scripts.util._check_imports` reports `VIOLATION` for `audit`, `db_probe` and `db_push` (rank -1) and ranks `favicons` before `db` although it imports `db` at load time.
- Proposed fix: derive the order from the import graph (topological sort) instead of a hand-kept `CHAIN`.
- Fix: `_check_imports.py` is rewritten around `graphlib.TopologicalSorter`: `backend_imports()` builds the load-time graph (now including `from backend import x`, which the old parser missed) and the lazy imports, `load_order()` derives the order, a cycle exits 1, and a lazy import is "needed" only when its target reaches the importer at load time. Current result: 26 modules, no cycles, 62 lazy imports of which 4 are needed.
- Tests: `test_check_imports.py` (the real graph covers every backend module and each module follows its imports, main.py's `from backend import` targets count, the script prints no `VIOLATION`, a synthetic backend classifies lazy imports and a synthetic cycle exits 1). Mutation-checked: the old script cannot satisfy the API, and dropping the `from backend import x` form fails 2 cases.

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
- Fix: `root_url()` uses the Public Suffix List (`publicsuffixlist`, a new dependency that bundles the list, so no download) for the registrable domain; IP literals (`st.is_ip_literal`) and single-label names keep their host and port, credentials are dropped, the port is dropped only when the host changes, and the scheme defaulting goes through `st.ensure_scheme` instead of a copy.
- Tests: `test_favicons.py` (15 URL cases: IPv4/IPv6 with ports, localhost, a Docker service name, `.co.uk`/`.com.au`, a `vercel.app` hosted suffix, the old docstring cases, a bare host, userinfo, ports on the same and on a parent host; plus `is_ip_literal`). Mutation-checked: the old function fails 6 cases.

### F13 - except blocks that neither log nor re-raise

- Reproduce: an AST scan of `backend/*.py` for `except` handlers without a `raise` or a `log_error`/`log.*` call finds 43, for example `routes.py:110` (`except OSError: pass` while fingerprinting assets) and `routes.py:136` (silent fallback to the global version); `state.py:562` is an intended optional-import fallback.
- Note: some are intended control flow (`CancelledError` during shutdown, optional-import fallbacks), so each needs triage. `test_error_logging.py` should grow into the full rule with an explicit allowlist.
- Triage of the 43 (plus `db.py:466`, which the first scan counted as logging because only its nested handler logged): real failures now log (`audit.py` synbad timeout at warning, an unstartable synbad binary through `log_error`, `db.py` broken pooled read connection at warning, `batch.py` flush failures through `log_error` instead of a private `logging.getLogger`, `websocket.py` closed sockets at debug); swallowing or error-probing code was restructured so no handler is needed (`migrations.py` reads columns through one `_table_columns()` helper instead of `except Exception: pass` around every ALTER and SELECT probes; `routes.py` file helpers share `_mtime()`/`_read_bytes()`, `max(default=)` and `Path.is_relative_to()`, and reject NUL in client paths explicitly; `config.py`'s dead `TypeError` guard and `model_info.py`'s duplicate float parse are gone); handler types were narrowed to what can actually be raised. 24 control-flow handlers remain, each keyed and explained in `CONTROL_FLOW`. Also removed: the `2.0`/`200` batcher constructor defaults that copied `metrics.write_batch_*`. The rule in CONTRIBUTING.md, DEVELOPMENT.md and ARCHITECTURE.md now matches the frontend's: `log_error` for unexpected failures, lower levels for expected ones, broad catches at warning or above.
- Tests: `test_error_logging.py` (AST scan of every handler: re-raise or a central log call on the handler's own path, not in nested handlers or defs; broad catches need warning or `log_error`; a keyed allowlist with reasons, which must be narrow and fails when stale; 14 scanner self-tests), `test_migrations.py` (fresh schema runs no ALTER, columns are added once, a failing ALTER is not swallowed, no probing SELECT). Mutation-checked: the old backend yields 33 violations; the old migrations fail 3 of 5.

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
- Fix: `websocket.ping_interval`/`ping_timeout` in app.yaml (validated, documented, in the example and both local configs). `main.server_options()` builds every uvicorn setting (bind and trusted proxies from `HOST`/`PORT`/`FORWARDED_ALLOW_IPS`, reload and `reload_dirs` from `app.debug`, pings from config) and `python -m backend.main` is the one launch path: the Dockerfile `CMD`, DEVELOPMENT.md, the seeder's printed command and the browser-test harness all use it. The manual `uvloop.install()` went too (uvicorn's default loop picks uvloop). DEPLOYMENT.md's nginx comment points at the config key instead of quoting 90s.
- Tests: `test_deployment.py` (`CMD` is `python -m backend.main`; no `ws-ping`/`ws_ping` and no direct uvicorn launch in the Dockerfile, compose example, docs, scripts, e2e harness, configs or other backend modules; `server_options()` in a clean process takes pings, reload and `reload_dirs` from config and bind from env). Mutation-checked: hardcoded 30/90 fail once the config value differs; the old Dockerfile fails the entry-point and launch-path cases.

### F17 - npm scripts for files outside the repo

- Reproduce: `npm run perf` fails with `Cannot find module tests/perf-test.mjs`; the same for `perf:*`, `stress*`. DEVELOPMENT.md says the scripts are maintained separately.
- Proposed fix: remove the scripts and their tool dependencies (`autocannon`, `lighthouse`) or add the scripts; either way add a test that every npm script's file exists (`test_frontend_rules.py` already checks the test scripts).
- Status (tests-scripts round): reproduced (`npm run perf` fails with `Cannot find module`), not fixed. The fix is an edit to `package.json` (drop the eight `perf*`/`stress*` scripts and `autocannon`/`lighthouse`, then the DEVELOPMENT.md section and the CONTRIBUTING line that mention them), and the session's permission check refused that edit, so it waits for the user's approval.

### F18 - readiness log on every request

- Observed by reading: `routes.health_check()` logs "Health check failing: ..." at warning level for every 503. With `MW_DISABLE_TESTS`, or when every model is down, the Docker HEALTHCHECK adds one warning every 30 s. (The dashboard no longer polls `/health`, see F5.)
- Proposed fix: log on state changes only (healthy to degraded and back), and test it with two consecutive failing calls.
- Fix: `st.condition_changed(key, failing)` is the shared "log on transition" primitive; `health_check()` warns when readiness starts failing and logs info when it recovers, nothing in between.
- Tests: `test_routes.py` (three failing polls give one warning, recovery one info, a new failure one warning; a healthy start logs nothing; the primitive's transitions). Mutation-checked: the old handler logs a warning per failing call.

### F19 - localStorage keys outside the registry

- Reproduce: `grep -o "'mw_[a-z_]*'" frontend/js/*.js` outside `state.js` finds 63 literals in 9 modules (26 in `notifications.js`, 22 in `modal-ranges.js`); the inline FOUC script built in `routes.py` repeats `mw_theme`, `mw_notif_settings` and `mw_notif_local`. `LS` in `state.js` lists 14 keys but few call sites use it.
- Proposed fix: every key in `LS` (the FOUC script gets its keys injected from one backend list shared with the page, like `__MW_CONN__`), plus a guard test for `'mw_` literals.

### F20 - static tunables left in code

- Observed by reading: frontend timings (`app.js` 30 s check-line refresh and metrics poll, 5 min card-bucket refresh, 60 s deploy poll; `ws.js` card-bucket debounce 5 s/30 s; `dom.js` 5 min provider staleness; `chart.js` TTL and cleanup timings), cache TTLs (300/3600/86400 s), freshness ratios (1.5/3.0 in `format.js`), `notifications.js` fallbacks for config values (`toast_duration_ms || 5000`, `history_size || 50`), and backend limits (`favicons.py` sizes, timeout and concurrency, `audit.py` text caps, config watcher restarts, notification cooldowns, `routes.py` bucket and history caps, the 10 s `/api/config` cache).
- Proposed fix: move operator-relevant values into app.yaml (client ones delivered like `__MW_CONN__`), delete the config-value fallbacks first (same class as the F6 interval fallbacks), and extend `test_frontend_rules.py`/`test_config_no_defaults.py` to catch literals.

### F21 - repeated required-key validation

- Observed by reading: the validators in `config.py` raise `... is required` from 35 hand-written checks; 24 are the same `for key in (...): if key not in x: raise ValueError(...)` loop (the F5 keys added two, following the existing pattern rather than mixing styles).
- Proposed fix: one `_require_keys(path, mapping, keys)` helper; `test_config_examples.py` already pins the messages.
- Fix: `_require_keys(path, mapping, keys, prefix=)` checks the mapping type and every key and names all missing paths; all app.yaml, audits.yaml and models.yaml validators use it, the event list and threshold metrics are single tuples shared with `reload_config`, audits/models paths lost their odd `audits.audit.`/`models.providers` prefixes, and `_validate_choice` covers enumerations. While there: `app.log_level` is now validated against one `st.LOG_LEVELS` tuple, replacing two separate level maps that silently fell back to WARNING (state.py) and to 2 (routes.py) on a typo.
- Tests: `test_config_examples.py` (deleting any of the 144 key paths of app.yaml.example fails with exactly `app.yaml: <path> is required`, the one optional key may be left out, helper messages for one, several and a non-mapping, invalid log level and host patterns). Mutation-checked: a helper that checks only the first key fails 113 cases.

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
- Root cause: Playwright's mocked socket opens only after the route handler returns (or on the first frame sent to the page); a close issued inside the handler reaches the page first. The real-server alternatives do not work: `server.max_connections` must be at least 1 and also caps HTTP requests, and the page always connects to its own origin, which the server always accepts.
- Fix: `closingSocket()` connects every routed socket to the real server with `connectToServer()`, so the page's socket really opens, drops the server's first frame (its hello) and closes the page side with the busy or policy code, which is what a busy or rejecting server does after its accept.
- Tests: `tests/e2e/connection.test.mjs` (busy backoff and rejected pacing). Mutation-checked: `_resetBackoff()` in `ws.onopen` now fails the busy case (`backoff did not grow: 428,408,408,...`).

### F25 - verifier round 1 (frontend/js/ws.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: No timeout for a socket that is accepted but never sends hello. _armStale runs only on a received frame, so a proxy that upgrades but does not forward, or a server stalled before hello, leaves the page 'connecting' forever on one socket. Reproduced with the e2e harness (stale_after=2): after 6s there was still 1 socket and data-state=connecting.
- Proposed fix: Arm the stale timer (or a configurable hello timeout delivered in __MW_CONN__) in onopen, so a socket without a hello is closed and retried, and count it as a failure. Add an e2e case with a silent routed socket that asserts a reconnect.

### F26 - verifier round 1 (tests/e2e/connection.test.mjs)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Stale detection is untested. Removing ws.close(...) from _armStale passes pytest, the JS tests and every e2e test (mutation missed). Live verification shows it currently works: hello then silence gives 3 sockets in 5s with stale_after=2.
- Proposed fix: Add an e2e case in which a routed socket sends one hello and then goes silent, and assert a new socket within stale_after plus the reconnect delay.
- Fix: `silentAfterHello()` routes each socket to the real server and passes on only its hello. The new case asserts the page closes that socket with `close_codes.stale`, no sooner than `stale_after`, opens a replacement within `stale_after + reconnect.min_delay` (plus slack), and shows no banner.
- Tests: `tests/e2e/connection.test.mjs` ("a socket that goes silent after its hello is closed as stale and replaced"). Mutation-checked: without `ws.close(...)` in `_armStale` it times out waiting for the replacement.

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
- Fix: `test_frontend_rules.py` builds character patterns with one helper, `_char_forms()` (literal, `\uXXXX`, `\u{X}`, `&#x..;`, `&#..;`). (1) Server paths match `/health` and `/ws` after any character, `/health/live` included (it comes from `state.conn.liveness_path` too). (2) Status glyphs cover all seven codepoints; the tier-legend bullet, the only other `\u25cf`, got a named home (`TIER_DOT` in `utils.js`, drawn by one `_tierDotHTML()` in `format.js`). (3) The separator guard flags any string literal that is only an em or en dash (every spelling) or a spaced hyphen; the log-tag joiner is the named `_LOG_TAG_SEP`, and the offending joins were fixed (F30, F98). (4) The fallback guard follows local aliases and destructuring of the config-derived state fields, read from `applyConfig()`. Dashes inside longer text, such as the capability tip's `${label} \u2014 ${desc}`, are prose rather than joiners and stay with F79's guard.
- Tests: `test_frontend_rules.py`, plus `test_fallback_scanner_follows_aliases` for the scanner itself. Mutation-checked with the verifier's four cases: ``fetch(`${location.origin}/health`)``, `'\u25b2'` and `'●'` in a status map, `.join(' \u2014 ')` and `.join(' - ')`, and `const bi = state.benchmarkInterval; bi || 3600` each fail their guard.

### F30 - verifier round 1 (frontend/js/dom.js)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Separators on the same code path are still built by hand with em dashes: _statusMessage joins retry and error with ' - ' (dom.js:148), and format.js:394 does the same. Both render inside the modal error box next to the new SEP dot. This contradicts the claim of one separator primitive, and CONTRIBUTING bans em dashes.
- Proposed fix: Route these joins through SEP_TEXT or a named separator in utils.js and extend the separator guard to em and en dash joiners.
- Fix (with F29): `_statusMessage()` and `recordErrorText()` join with `SEP_TEXT`; the separator guard now flags dash joiners in every spelling.
- Tests: `test_frontend_rules.py::test_separators_come_from_the_shared_primitive`. Mutation-checked: restoring either `' \u2014 '` join fails it.

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
- Fix: `routes.module_preload_order()` walks the static `from '...'` specifiers of `js/app.js` (the same `_JS_FROM_RE` that versions them) depth-first and returns the closure dependencies-first; `index()` preloads exactly that. It found four missing modules (`conn.js`, `filter.js`, `chart-plugins.js`, `chart-helpers.js`) and that the old list was not dependency-ordered (`state.js` before `utils.js`); `modal.js`, a dynamic `import()`, stays on demand.
- Tests: `test_routes.py` (each preloaded module follows its imports, only modules reachable from `app.js` are listed, conn/filter included, modal excluded), `test_websocket.py` (the real page's modulepreload tags equal the derived list). Mutation-checked: the old list fails.

### F37 - verifier round 1 (docs/API.md)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The endpoint count is stale after adding /health/live: API.md:3 and README.md:13 and README.md:80 say '15 REST endpoints', but there are now 16 (16 '### GET/POST/...' sections, 16 routes).
- Proposed fix: Update the counts or remove them; a doc test could compare against app.routes.
- Fix: README.md (two places) and API.md say 16.
- Tests: `test_api_docs.py` (API.md's `### METHOD /path` sections equal the app's schema routes, and every "N REST endpoints [across T tags]" in README.md and API.md matches the app, loaded in a clean process). Mutation-checked: reverting one README count fails.

### F38 - verifier round 1 (scripts/util/scale_test_db.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: --app-template and --app-set are applied only after the DB is seeded (minutes for the default 5000 models) and after models YAML is written. A bad value leaves partial output (verified: DB plus models YAML, no app YAML). A nested unknown key (--app-set nosuch.key=1) raises a bare KeyError: 'nosuch', because only the leaf is checked. Errors surface as tracebacks, not argparse errors.
- Proposed fix: Load the template and apply the overrides first (checking every path segment), fail through ap.error(), then seed and write.
- Fix: `_app_config()` builds the generated app config before anything is seeded: the scaled benchmark interval and stagger go through the same path check as `--app-set` (every segment must exist and lead to a mapping), and the result passes `config._validate_config()`. Any failure, an unreadable template or a value that is not YAML included, is an `ap.error()` (exit 2, one line, no traceback) and nothing is written.
- Tests: `test_scale_test_db.py::test_bad_app_override_fails_before_any_output` (a misspelt leaf, an unknown top-level key, a path through a scalar, an invalid value: exit 2, message, no config dir, no DB). Mutation-checked: validating after seeding fails all four; the old leaf-only check fails the two nested cases.

### F39 - verifier round 1 (scripts/util/scale_test_db.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: F15 is incomplete. Favicons go to <--data-dir>/favicons, but the server always reads st.DATA_DIR/favicons (FAVICON_DIR is not overridable). An out-of-tree run, including the e2e harness, therefore has no provider logos, while the printed start command implies a fully isolated setup.
- Proposed fix: Make the data dir overridable at one choke point (e.g. MW_DATA_DIR in state.py, from which FAVICON_DIR, VAPID and TIKTOKEN derive), or state in the seeder output that favicons are only served from data/.
- Fix: `state.DATA_DIR` honours `MW_DATA_DIR` (a name inside the project or an absolute path, created if missing); the DB, VAPID key files, `FAVICON_DIR` (moved into `state.py`) and the tiktoken cache derive from it. The seeder has no `--data-dir` of its own any more: it writes the DB and favicons to `st.DATA_DIR` and `st.FAVICON_DIR`, exactly the paths the server reads, and `--server-env PATH` writes the environment that starts a server on its output (the logged start command prints the same variables). The browser-test harness seeds with a temp `MW_DATA_DIR` and starts the server on that JSON instead of repeating the seeder's file names.
- Tests: `test_project_paths.py::test_everything_the_server_writes_follows_the_data_dir` (default and absolute), `test_scale_test_db.py::test_server_env_points_the_server_at_the_output` and `::test_server_on_the_seeded_env_serves_every_logo` (a real app on the seeded env returns a logo for every provider). Mutation-checked: a fixed `FAVICON_DIR` fails the path test; seeding favicons anywhere else fails the logo test.

### F40 - verifier round 1 (docs/DEVELOPMENT.md)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The claim that browser tests 'never touch data/, config/ or a running instance' is false on a fresh clone. Startup loads or generates VAPID keys in the fixed st.DATA_DIR: the HEAD scratch server logged 'Generated new VAPID key pair at .../data/vapid_private.pem', and the new server loaded the repo data/ keys while its DB pointed elsewhere. test_real_app_with_tests_disabled does the same.
- Proposed fix: Same fix as the favicon item (an overridable data dir) and point it at the temp dir in the harness and fixture, or reword the doc.
- Fix: the F39 data dir. `run_python` scrubs an inherited `MW_DATA_DIR` and gives every child a fresh one (a test can pass its own; empty means `data/`), and the browser-test harness runs the seeder and the server on one temp data dir, so DEVELOPMENT.md's claim now holds.
- Tests: `test_project_paths.py::test_real_app_writes_only_into_its_data_dir` (startup writes the DB, the VAPID pair and the favicon dir into `MW_DATA_DIR`). Mutation-checked: a fixed `DATA_DIR` fails it. Checked live: after the browser tests the checkout's `data/` had no new or changed file.

### F41 - verifier round 1 (backend/streaming.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Results computed without an encoder are indistinguishable. With per_chunk_tokens empty, the ITL normalization block is skipped: a direct _compute_itl_statistics check gives an effective median and p99 of 80 ms without an encoder vs 20 ms with one for 4 tokens per chunk. Batching providers can therefore show inflated ITL and stall metrics, which can feed critical-tier degradation, for up to token_encoding_retry (1h) or indefinitely offline. Only one log line is written per retry.
- Proposed fix: Record on each result whether token counts came from the encoder (or leave effective ITL fields null without one), and skip ITL-based degradation when normalization was not possible. Add a test.
- Fix: `_compute_itl_statistics` computes effective (per-token) ITL only from per-chunk token counts; without the encoder the effective median/avg/p99 and tail ratio are `None` (the frontend already shows `--` or hides them), so they cannot feed the consistency score or critical-tier degradation, and such results are distinguishable (like `chunk_token_ratio`, already `None` without the encoder). Raw ITL and stall counts do not depend on the encoder and are unchanged.
- Tests: `test_token_encoder.py` (a batching provider at 4 tokens per 80 ms chunk: 20 ms effective with the encoder, raw 80 ms and no effective values without it, and no `effective_itl_tail_ratio` among its critical metrics). Mutation-checked: the old code reports the per-chunk 80 ms as effective.

### F42 - verifier round 1 (scripts/tests/test_websocket.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: When heartbeats are missing, test_real_app_with_tests_disabled blocks in ws.receive_json() until conftest's 120s subprocess timeout. The mutation is caught, but only after 2 minutes and with a timeout message instead of an assertion.
- Proposed fix: Bound the child's receives (a thread with join(timeout), or a short socket timeout) and assert on the frames received.
- Also found: `test_config_reload.py`'s child had the same unbounded wait; its deadline loop relied on heartbeats to return from each receive.
- Fix: `scripts/tests/app_child.py` holds the helpers for real-app children: `receive_until(ws, done, seconds)` reads frames on a daemon thread and returns what arrived by the deadline (a server close or leaving the socket's context ends the reader), `emit()`/`result()` replace the hand-written `RESULT` printing and parsing in five test files. Both children now assert on the frames they got.
- Tests: `test_websocket.py::test_real_app_with_tests_disabled`, `test_config_reload.py`. Mutation-checked: without `ws_mgr.start_heartbeat()` the websocket case fails in 6 s with "expected a hello and two heartbeats within 5s" (was a 120 s subprocess timeout); without the heartbeat and the config watcher the reload case fails at its 20 s deadline with "no reload within 20s".

### F43 - verifier round 1 (package.json)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: `npm test` is `pytest && npm run test:js`. While F8 fails offline, the JS unit tests never run through the documented entry point, and `npm test` exits non-zero. Separately, bare `pytest scripts/tests` now fails at conftest import (ModuleNotFoundError: backend); HEAD failed for 4 modules, now the whole session does. Only `python -m pytest` works.
- Proposed fix: Fix F8 (in-process TestClient). Add a pytest config with pythonpath = . so that bare pytest works too.
- Fix (pytest part): `pytest.ini` sets `pythonpath = .` and `testpaths = scripts/tests` (whitelisted in `.gitignore`), so `pytest` works from any directory; F8 no longer fails offline, so `npm test` reaches the JS tests again.
- Tests: `test_docs_commands.py::test_bare_pytest_imports_the_project` (collects a test module with `python -P`, which, like the `pytest` entry point, leaves the working directory off `sys.path`). Mutation-checked: without `pytest.ini` it fails with `No module named 'backend'`.
- Still open: `npm test` still stops at the first failing suite. The fix is a `package.json` edit (a small runner that runs every suite and reports each result, with `test:py` next to `test:js`); the session's permission check refused that edit, so it waits for the user's approval.

### F44 - verifier round 1 (backend/routes.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: built_css() calls log_error with a full FileNotFoundError traceback on every stylesheet request while the file is missing, which is every page load. That is a lot of noise for one misconfiguration.
- Proposed fix: Log once per path, or at error level without exc_info for FileNotFoundError, and keep the 404 body.
- Fix: `built_css()` logs an unreadable stylesheet once per outage through `st.condition_changed()`, at error level with the path, the OS reason and the build command but no traceback, and logs info when it is readable again.
- Tests: `test_built_css.py` (three missing requests give one error without `exc_info`, recovery one info, a new outage a new error). Mutation-checked: the old handler logs three tracebacks.

### F45 - verifier round 1 (backend/state.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: MODEL_KEY_SEP is a single source only for the backend. The frontend still hardcodes '::' (state.js:198 and 213, utils.js:159, notifications.js:302 and 371), and backend/notifications.py:737/762 builds f"{provider}::" directly instead of make_model_key.
- Proposed fix: Use make_model_key(provider, '') in notifications.py, and deliver the separator to the frontend via /api/config or __MW_CONN__, using one parse and build helper in utils.js.
- Fix (backend part): `notifications.py` builds provider-level keys with `make_model_key(provider, "")`. The frontend half (the `'::'` literals in `state.js`, `utils.js`, `notifications.js`) is not done yet, so the finding stays open.
- Tests: `test_model_key.py` (AST guard over backend and `scripts/util`: no f-string, concatenation or str method with the separator outside `state.py`; scanner self-test; provider-level round trip). Mutation-checked: the old `notifications.py` fails.

### F46 - verifier round 1 (backend/main.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: The _TEST_TASKS comment says 'Background work that exists to run tests', but the table includes the config watcher (hot reload), favicons and model info. Behaviour matches HEAD, but with MW_DISABLE_TESTS config hot reload is off, so the documented path 'a config reload reaches open pages through hello' cannot be exercised in the mode DEVELOPMENT.md recommends.
- Proposed fix: Rename the table and log line to what it is (external or background fetches), or move the config watcher out of it if hot reload should work with tests disabled.
- Fix: the table is `_OUTBOUND_TASKS` and holds only work that tests or contacts providers (token encoder download, scheduler, favicons, model info). The config watcher and the broadcast batcher always start; a reload skips the favicon and model-info fetches (and `apply_db_changes` the fetch for added models) when `st.TESTS_DISABLED`, the one reading of `MW_DISABLE_TESTS`. A missing watchfiles now logs a warning instead of silently disabling hot reload. CONFIGURATION.md, DEPLOYMENT.md and DEVELOPMENT.md describe the mode as "without testing or contacting providers".
- Tests: `test_config_reload.py` (a real app with `MW_DISABLE_TESTS=1` and an app.yaml in a temp dir: an edit reaches an open socket as `config_updated` and changes `app_name`, no provider fetch starts, the startup log does not list the watcher), `test_websocket.py` (the skipped-task log line names `_OUTBOUND_TASKS`). Mutation-checked: not starting the watcher, or fetching on reload with tests disabled, fails.
- Verified live: a private instance on 8093 (`MW_DISABLE_TESTS=1`, configs in a temp dir) logged `Config reloaded: app_name: ModelWatcher -> LiveReloaded` two seconds after the edit, and `/api/config` served the new name.

### F47 - verifier round 1 (backend/websocket.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: close_all's per-socket except now logs with log.debug. Better than the previous silent handler, but it does not follow CONTRIBUTING's Python rule (log_error or re-raise). This belongs to F13's triage.
- Proposed fix: Triage it with F13's allowlist, or log through log_error once with the aggregate count.
- Fix: `close_all` treats only what Starlette raises for a client that already left (`WebSocketDisconnected`, `WebSocketDisconnect`) as expected (debug per socket, one info summary); anything else goes through `log_error` with its traceback and the loop continues. Broadcast sends use the same `_CLIENT_GONE` tuple. The F13 rule now requires broad catches to log at warning or above, so a debug-only broad catch cannot come back.
- Tests: `test_websocket.py` (four sockets: fine, closed, dead transport, a bug; all get the restart close, exactly one error with the `ValueError`, "2/4 clients had already disconnected"), `test_error_logging.py` (broad catch below warning is a violation). Mutation-checked: the old code logs no error.

### F48 - verifier round 1 (backend/websocket.py)

- Found by: independent verifier after the F1-F7 fixes.
- Issue: Informational. The same-origin rule trusts the Host header and there is no Host validation (no TrustedHost or allowed_hosts anywhere in backend). A DNS-rebinding page is therefore same-origin by construction and now gets the WebSocket, which the strict allowlist used to refuse. The HTTP API is already exposed the same way, so no new data leaks.
- Proposed fix: If rebinding matters for deployments, add TrustedHost validation (hosts from config) at the middleware choke point, which covers both HTTP and WS.
- Fix: `HostCheckMiddleware` (the choke point for HTTP and WebSocket) serves a request only when its `Host` names the server: an IP literal or `localhost` (a rebinding page always arrives under the attacker's name), the host of `app.site_url`, or a `server.allowed_hosts` entry (`"*.example.com"`, or `"*"` to turn the check off). Others get `400 {"error": "Invalid host header"}` with security headers, or a WebSocket closed before accept (handshake 403); the first rejection warns with the setting to change, later ones log at debug. The example ships `allowed_hosts: []`, so same-origin dev access by `localhost`, loopback or LAN IP needs no configuration. Documented in CONFIGURATION.md, SECURITY.md, DEPLOYMENT.md and API.md.
- Tests: `test_host_check.py` (the rule for defaults and patterns, 400 JSON on HTTP, 1008 before accept on WS, own hosts served, one warning then debug, the example config loads into the names the middleware reads), `test_websocket.py` (the real app: a rebinding Host gets 400 while `http://localhost` pages keep their socket). Mutation-checked: an always-true rule fails 12 cases, unwiring the middleware fails the real-app test.
- Verified live on 8093: `Host: rebind.attacker.example` got 400 on HTTP and 403 on the WebSocket handshake; loopback, `localhost` and the `site_url` host got 200.

### F49 - Escape in the date picker closes the modal

- Found by: analyst round (UI inventory); reproduced on :8080 with Playwright (`/tmp/claude-0/findings-extract/p1.mjs`).
- Reproduce: open any model, click the calendar button next to the range pills, press Escape. Expected: only the picker closes. Actual: `pickerOpen: false, modalOpen: false`.
- Root cause: three independent `document` keydown listeners handle Escape. `app.js:40-46` closes the modal whenever the Help and notification panels are closed, and it is registered first, so it runs before `modal-ranges.js:256` (`_dateRangeEscHandler`, added at `:418`), whose `stopPropagation()` cannot stop a listener on the same node anyway. `filter.js:662-667` is a third one. No layer knows what is on top.
- Proposed fix: one dismiss stack in `utils.js` (`pushLayer(close)`, returns a pop function); the single Escape listener closes only the top layer. The modal, picker, filter panels, Help and notifications register with it, and the three ad-hoc listeners go.
- Test: e2e: open modal, open picker, Escape, assert picker gone and modal open; second Escape closes the modal. Unit test for the stack order.
- Classification: fix-now (interaction logic that every direction keeps: layered overlays).

### F50 - Legend prints escaped HTML for Critical

- Found by: analyst round (UI inventory and UX critique); reproduced on :8080 (`p1.mjs`): the Legend text reads `Bad | <span class="underline">Critical</span>`.
- Root cause: `help.js:67-75` builds HTML for the last tier label, and `_legendDotItem()` (`help.js:58-61`) passes every label through `esc()`.
- Proposed fix: `_legendDotItem` takes plain text plus an optional class for the label; the Critical marker comes from the same helper that marks critical values elsewhere (`fmtCritical`), not a hand-built span.
- Test: e2e or DOM unit test: no `<` or `&lt;` in the Legend's text content; the Critical item carries the critical marker class.
- Classification: fix-now (Help drawer survives the redesign).

### F51 - Status legend promises counts it never shows

- Found by: analyst round; reproduced on :8080 (`p1.mjs`, `p3.mjs`): the Status section lists "Online | Degraded | Errors | Testing" with no numbers, while its tip (`HELP.statusLegend`, `utils.js:83`) says "Counts update live as tests complete".
- Root cause: `_legendStatusHTML()` (`help.js:63-65`) never passes the `count` argument that `_legendDotItem()` supports; `updateStatusLegend()` (`help.js:164-171`, called from `frame.js:43` and `dom.js:803`) re-renders the same count-less list on every summary change, so the live-update path runs for nothing.
- Proposed fix: pass `state._counts` (the numbers `recalcCounts` already keeps) into the legend, or drop the claim and the update hook. The label mismatch is F77.
- Test: e2e: legend numbers equal the per-status card counts, and change after a simulated WS status change.
- Classification: fix-now.

### F52 - Tooltip covers the content its trigger opened

- Found by: analyst round; reproduced on :8080 (`p1.mjs`, `p4.mjs`). Hover then click the calendar pill: `#help-tip` ("Select a custom date range.", z-index 100) overlaps the picker (z-index 80) over its month header. Click a Help > Legend section header: its tip stays and overlaps the expanded section body.
- Root cause: the desktop click handler in `tooltips.js:190-204` shows or keeps the tip on a click of its own trigger instead of hiding it; the shared tip layer (`index.html:109-110`, z 100) sits above every popover and sheet (picker 80 at `index.html:317`, Help 60, modal 50), with z-index literals spread over the stylesheet and no layer scale.
- Proposed fix: activating a trigger hides its tip (tips describe controls, the opened content replaces them); define the overlay order once (one z-index scale of CSS custom properties) so a tip can never outrank a newer layer.
- Test: e2e: after clicking the calendar pill and a Help section, `#help-tip` is hidden.
- Classification: fix-now (tooltip system survives).

### F53 - Touch tooltips stick and long-press also activates the card

- Found by: analyst round; reproduced on :8080 at 390 px with CDP touch events (`p4.mjs`). Long-press the TTFT tile of a card: the tip shows and the modal opens under it (`modalOpen: true`, tip z 100 over the modal). Long-press the connection dot, then tap the Help button: the tip is still visible after the sheet opens.
- Root cause: `tooltips.js:190-191` returns early from the click handler while `_shownByTap` is set, so no tap ever dismisses a long-pressed tip; only `touchmove` and `scroll` (`:207-208`) hide it. The long-press path (`:134-164`) never suppresses the click that follows `touchend`, so a tip target inside a clickable card also opens the modal.
- Proposed fix: after a long-press fires, cancel the following click (preventDefault on that touchend or a one-shot capture click guard); any tap outside the tip, and opening any layer (F49's stack), hides it.
- Test: e2e with touch emulation: long-press a tile, assert tip visible and modal closed; tap elsewhere, assert tip hidden.
- Classification: fix-now.

### F54 - History day headers: chevrons and keyboard

- Found by: analyst round; reproduced on :8080 (`p1.mjs`). Expanded day: text `▸`, no transform. Collapsed day: text `▾` with `rotate(90deg)`, which points left. The header row has no `role`, `tabindex` or `aria-expanded`.
- Root cause: two mechanisms for one state. `_toggleDayCollapse()` and `_restoreCollapsedDays()` (`modal-history.js:266-277`, `:300-305`) swap the glyph, and `index.html:263-264` and `:267-268` also rotate it. The collapse is bound with `click` only (`modal-history.js:260-263`) on a `<tr>` or `<div>`.
- Proposed fix: one chevron that CSS rotates from `aria-expanded`, on a real `<button>` inside the day header (the same pattern as the provider toggle), so keyboard and screen readers get it for free.
- Test: e2e: the chevron's rendered direction differs between states and matches the provider toggle; Enter on a focused day header collapses it.
- Classification: fix-now (the history table is on the keep list, and the keyboard part is accessibility).

### F55 - History range persisted as a growing absolute date

- Found by: analyst round; reproduced on :8080 (`p1.mjs`). With `mw_hist_range=3d`, open a model: label "Sep 24, 2026 → now". Scroll the history until more pages load: label "Sep 12, 2026 → now", `mw_hist_since` is 15.1 days old and `mw_hist_range` still says `3d`. Open another model: it starts at Sep 12 with no range pill active.
- Root cause: `_updateHistRangeLabel()` (`modal-ranges.js:72-85`, called from `modal-history.js:359`) moves `_histSince` back to the oldest loaded row and persists it, so a pagination cursor overwrites the user's window; `_applyHistRange()` (`:137-151`) persists the derived absolute `since` next to the key; `initRangeStateForOpen()` (`:443-465`) prefers that stored epoch over the key, so even an untouched `3d` grows by one day per day.
- Proposed fix: persist only the range key (and explicit custom bounds); derive `since` from the key at each open; keep the pagination cursor separate from the window and never write it to storage.
- Test: JS unit test on the range state (apply `3d`, feed older rows, assert since unchanged and nothing stored but the key); e2e reopen shows the 3d label.
- Classification: fix-now (state logic).

### F56 - No way back to the system theme

- Found by: analyst round; reproduced on :8080 (`p2.mjs`): three clicks give `mw_theme` null, `dark`, `light`, `dark`; nothing removes the key.
- Root cause: `toggleTheme()` (`theme.js:133-136`) only writes `light` or `dark`; the system listener (`theme.js:143-145`) acts only while the key is absent. The FOUC script in `routes.py` reads the same key.
- Proposed fix: a three-state preference (system, light, dark) exposed as a setting, stored under the `LS.THEME` key (F19), with the button cycling or a menu offering the three.
- Test: JS unit test for the preference cycle; e2e: selecting "system" removes the key and follows `prefers-color-scheme`.
- Classification: fix-now (a user setting, independent of the visual design).

### F57 - `#config-warning` is dead

- Found by: analyst round; reproduced on :8080 (`p2.mjs`): the element is empty and hidden; `grep` finds no writer.
- Root cause: `index.html:873` declares the span; the only code touching it is `refreshModelList()` (`dom.js:833-834`), which adds `hidden`.
- Proposed fix: remove the element and the dead code (YAGNI). If a config warning is wanted, it belongs to a real signal (for example a config reload error in the hello frame) with its own finding.
- Test: `test_frontend_rules.py`: every static id in `index.html` is referenced by some writer, or the specific id is gone.
- Classification: fix-now (dead code).

### F58 - Card TTFT and modal TTFT disagree

- Found by: analyst round (UI inventory and UX critique); reproduced on :8080 (`p3.mjs`, `/api/metrics`). BetaLLM/gpt-5.4: card TTFT `1.86s` (`health_ttft_ms` 1856.4), modal tile `160ms` (`last_test.ttft_ms` 159.8). The same happens for 5 of 20 models by more than 3x (EchoNet/llama-4-8b 183 ms vs 5.68 s).
- Root cause: `_latestTTFT()` (`dom.js:356-362`) returns the health-check TTFT whenever the health check is newer than the last benchmark; `modal.js:374` always shows the benchmark TTFT. Both are labelled "TTFT" and coloured with the same benchmark tiers, although a health check is a short request with different latency.
- Proposed fix: one definition per label. Either show benchmark TTFT everywhere under "TTFT", or show both with distinct labels ("TTFT" and "Health TTFT", from `METRIC_LABELS`); pick it in one shared helper used by card and modal.
- Test: e2e: for a model whose health check is newer, the card and modal values under the same label are equal.
- Classification: fix-now (data semantics, survive any layout).

### F59 - Failed benchmark invisible on the dashboard

- Found by: analyst round; reproduced on :8080 and in the DB. AlphaAI/gpt-5.4: last benchmark 7 h ago failed ("Stream interrupted"), followed by 19 successful health checks; `model_state` is `('online', None)`. The card has `online-glow`, no badge, and shows only TTFT (from the health check, F58) and Uptime; the TPS and P99 tiles are hidden (`p8.mjs`). CloudMind/gpt-5.4-mini is the same.
- Root cause: `_derive_status_from_result()` (`db.py:1535-1570`) returns `("error", None)` for a failed benchmark (`:1570`), so the benchmark does not own the state and the next clean health check returns `("online", None)` (`:1556-1559`). A degraded benchmark, by contrast, is benchmark-owned and survives health successes. In the card, `buildCardDOM()` (`dom.js:408-410`) hides any tile whose value is null, with no reason shown.
- Proposed fix: make benchmark failure a benchmark-owned state until the next benchmark (for example `degraded` with `degraded_reason: benchmark_failed`, or a separate `last_benchmark_ok` flag in the summary), and render a missing value with its reason ("Last benchmark failed 7h ago") instead of hiding the tile.
- Test: pytest on `_derive_status_from_result` (failed benchmark then health success keeps the benchmark-owned state); e2e: the failed-benchmark card shows a marker and keeps its tiles.
- Classification: fix-now (status semantics and API).

### F60 - Inverted modal trends on the SQL path

- Found by: analyst round (data and API); reproduced on :8080. `GET /api/metrics?model=AlphaAI::gpt-5.4-mini&type=modal&since=<now-14d>` returns TPS `degrading, 31.27`; a chronological split of the same 66 benchmark rows in the DB gives baseline median 79.83 and recent median 96.19 (improving by 16.36), while a newest-first split reproduces exactly -31.27. `since_ts` is 6.9 h old, the newest row, not the range start.
- Root cause: `build_chart_response()` passes `db.get_model_history(...)` (`stats.py:1409-1411`) to `compute_trends()`, which assumes oldest-first (`stats.py:433-451`); `get_model_history()` sorts `ts_epoch DESC` (`db.py:85`). The RAM path (`stats.py:1388`) is oldest-first and correct.
- Proposed fix: `compute_trends()` sorts its input by timestamp itself (the lowest choke point, so no caller can get it wrong), or the DB call asks for ascending order.
- Test: pytest: `compute_trends()` gives the same result for a list and its reverse; a route test over a seeded range checks the direction.
- Classification: fix-now (data correctness).

### F61 - Card Health view shows benchmark TTFT

- Found by: analyst round; reproduced on :8080. For BetaLLM/gpt-5.4 the `card_buckets.health[].ttft_ms` series equals `card_buckets.speed[].ttft_ms` (9 of 9 buckets identical), while `type=card&test_type=health` returns different health-check values (3339, 3449, 2818 ms ...).
- Root cause: `cached_card_buckets()` (`stats.py:983-1004`) builds all four card views from `bench_only(history)`. `HELP.chartHealth` (`utils.js:90`) promises "TTFT from lightweight reachability checks".
- Proposed fix: build the `health` card view from health records (the same aggregation `query_bucketed_health` uses, over `recent_history`), keeping the versioned cache.
- Test: pytest: with seeded health and benchmark records of different TTFT, the card health buckets follow the health records.
- Classification: fix-now (data correctness).

### F62 - Card trends from three samples

- Found by: analyst round; reproduced on :8080: every one of the 184 card trend entries in `/api/metrics` has `data_points: 3`; AlphaAI/gpt-5.4 shows `available: degrading 66.7 pp` and `reliability_score: degrading 79.1 pts` from 3 recent tests. These drive the animated arrows on every card.
- Root cause: `compute_trends()` splits the benchmarks in `recent_history` (2 days, 10 to 15 tests at the seeded cadence) 75/25 (`stats.py:451`), so "recent" is 3 tests, the minimum `_TREND_MIN_RECENT = 3` (`stats.py:407`). The split, the minimum and the deadbands (`_TREND_THRESHOLDS`, `stats.py:398`) are code constants, against the no-static-tunables rule.
- Proposed fix: time-based windows with a configurable minimum sample count and deadbands in `app.yaml` (validated, documented, exposed through `/api/config`), and "not enough data" instead of a direction below the minimum.
- Test: pytest: with fewer than the configured minimum per window, no direction is returned; config keys are required and validated.
- Classification: fix-now (data correctness).

### F63 - Provider trend direction and change disagree

- Found by: analyst round; reproduced on :8080 (`/api/metrics` `providers`): AlphaAI `reliability_score: stable, change 33`; CloudMind `consistency_score: stable, change 16` and `reliability_score: degrading, change 4` (below the 5-point deadband that defines "stable" for models); AlphaAI `speed: 62.349999999999994`.
- Root cause: `_median_trend()` (`stats.py:1067-1100`), despite its name, takes the direction by majority vote and averages the absolute changes of all models, including those moving the other way; its `data_points` counts models, not records. Provider scores are `_median()` results with no rounding (`stats.py:1170-1174`), unlike model scores.
- Proposed fix: aggregate signed changes (the median of signed deltas) and derive the direction from that value with the same deadband as the models; name the field for what it counts; round provider scores like model scores.
- Test: pytest: a provider with one model +15 and one -15 reports stable with change 0; scores have one decimal.
- Classification: fix-now (data correctness).

### F64 - Chart band semantics change at 2 days

- Found by: analyst round; reproduced on :8080. A 14d modal bucket for AlphaAI/gpt-5.4-mini returns `tps: {avg: 87.89, p10: 52.5, p90: 123.15}`; the six DB rows in that bucket have min 52.5, max 123.15, mean 87.89 and median 91.94. Ranges up to 2 days use median and true P10/P90 (`compute_bucketed_history`, `stats.py:846-918`).
- Root cause: `query_bucketed_history()` and the modal SQL (`db.py:907`, `:950-960`) select `AVG` as the centre and `MIN`/`MAX` aliased `_p10`/`_p90`; the chart tooltips label both paths "P10-P90" (`chart-helpers.js:240-254`, `:429-433`).
- Proposed fix: compute real percentiles for SQL ranges (fetch the values per bucket and aggregate in Python with the RAM-path function, or a window-function percentile), one aggregation function for both paths.
- Test: pytest: the same seeded records give the same bucket statistics through the RAM and SQL paths.
- Classification: fix-now (data correctness).

### F65 - Score interpolation asymmetry and negative scores

- Found by: analyst round (TTFT cliff), extended while reproducing. `tier_continuous_score()` on the live thresholds: TTFT 999 ms scores 1.0 and 1000 ms 0.75; TPS 100 scores 1.0 and 99.9 scores 0.9995. So every lower-is-better boundary scores 0.25 lower than the matching higher-is-better boundary, and the whole Excellent band is flat at 1.0. With a 4-value lower-is-better list without the trailing 0 (`[1,2,3,15]`), 14 stalls score -0.3056 and 15 stalls 0.0.
- Root cause: `stats.py:86-151` maps lower-is-better threshold `i` to the lower edge of tier `i+1` (`1 - (i+1)/(n-1)`), while higher-is-better maps it to the upper edge of tier `i`; without a sentinel the last band interpolates below 0. `config.py:369-373` checks only the length of each threshold list, not the sentinel or monotonic order.
- Proposed fix: one mapping for both directions (tier boundaries at the same scores, interpolation inside every band including Excellent and Critical), clamp to [0, 1], and validate order and sentinel in `config.py`.
- Test: pytest: property test that scores are in [0, 1], monotonic in the value, and symmetric for mirrored thresholds; config rejects a list without the sentinel.
- Classification: fix-now (scoring logic).

### F66 - `/api/client-error` always 500

- Found by: analyst round; reproduced on :8080: `curl -X POST -H 'Content-Type: application/json' -d '{"message":"probe","source":"x"}' /api/client-error` returns `500 {"error":"Internal Server Error"}`.
- Root cause: `handle_client_error()` (`routes.py:713-719`) uses a bare `c` (`c.notif_rate_limit_client_error`) instead of `st.c`; the frontend reporter in `utils.js` swallows the failure, so no client error has ever been recorded.
- Proposed fix: `st.c`, plus a lint that flags bare `c.` in backend modules that do not import it.
- Test: in-process route test (the F8 approach): a valid report returns 2xx and is logged; the rate limit returns 429.
- Classification: fix-now (API).
- Also found independently by the backend-config round (pyflakes: `undefined name 'c'`); the bare `c` dates from v1.0.0.
- Fix: `st.c.notif_rate_limit_client_error`, and the docstring now names the config key instead of "10/min".
- Tests: `test_routes.py` (a report is logged as `[CLIENT] error ...`; the third report within the limit gets 429), `test_undefined_names.py` (pyflakes, now a dev dependency, finds no undefined name in any backend or scripts module: the lint the proposed fix asked for, for every name rather than `c` alone). Mutation-checked: the old `routes.py` fails both.
- Verified live on a private instance (8093): the reproduction request answered `200 {"ok": true}` and the server logged `[CLIENT] error live check`.

### F67 - No empty state for a filter

- Found by: analyst round; reproduced on :8080 (`p2.mjs`): searching `zzzz-no-match` hides all 5 sections; the main area is empty and only the filter bar reads "0 of 20 models".
- Root cause: `applyFilter()` (`filter.js:186`) hides sections and cards but has no zero-result branch.
- Proposed fix: a single empty-state element owned by the filter, shown when the match count is 0, naming the active filters with a "Clear all" action.
- Test: e2e: a no-match search shows the empty state; clearing hides it.
- Classification: fix-now (filter state).

### F68 - No loading or error state in the main area

- Found by: analyst round; reproduced on :8080 (`p2.mjs`, `p6.mjs`). First visit without IndexedDB cache and a 4 s `/api/metrics`: after 2.5 s the page has the header, no filter bar and an empty body, with the footer floating mid-viewport. With every `/api/*` answering 502: the banner shows (F5 works), but the main area stays empty indefinitely.
- Root cause: `#skeleton` (`index.html:957`) is an empty div; `init()` builds sections only after metrics arrive (`app.js:202-224`) and `buildProviderSections()` removes the skeleton (`dom.js:744-745`); nothing renders a placeholder or an error for the main area.
- Proposed fix: render placeholders from the provider list (or a fixed number) until metrics arrive, and an error state with retry when the first load fails, both driven by the existing connection state (`conn.js`).
- Test: e2e: with a delayed metrics route, placeholders are visible; with 502s, the error state is visible.
- Classification: fix-now (app state).

### F69 - Provider header counts unlabeled and unfiltered

- Found by: analyst round; reproduced on :8080 (`p3.mjs`): searching "mini" leaves 1 AlphaAI card visible, while the AlphaAI header still reads "... 4" (`aria-label="4 online"`).
- Root cause: `providerCountBadges()` (`dom.js:105-125`) renders bare coloured numbers from `state.providerSummaries[p].counts` (`dom.js:752-761`), the server's unfiltered counts; the `aria-label` sits on a generic `span`, which assistive technology does not announce.
- Proposed fix: label each count ("4 online") or show one count with its meaning, and compute it from the filtered set when a filter is active, in the same pass as the "N of M" count.
- Test: e2e: with a filter active, the header count equals the visible cards; each number has an accessible name.
- Classification: fix-now (state and accessibility).

### F70 - Reduced motion ignored for trend arrows

- Found by: analyst round; reproduced on :8080 with `reducedMotion: 'reduce'` (`p2.mjs`): 23 running infinite `trend-breathe` animations.
- Root cause: `.score-trend:not(.text-text-muted)` (`index.html:78`, specificity 0,2,0) starts the animation, and the override `.score-trend { animation: none }` (`index.html:80`, 0,1,0) loses.
- Proposed fix: one global reduced-motion rule for all decorative animations (or the override with matching specificity), and no infinite decorative motion by default.
- Test: e2e with `reducedMotion: 'reduce'`: `document.getAnimations()` has no infinite running animation.
- Classification: fix-now (accessibility, WCAG 2.3.3).

### F71 - Light-theme contrast and Bad/Critical distinction

- Found by: analyst round; reproduced on :8080 (`p5.mjs`): in light mode 14 of 23 text elements on the first card are below AA: tier-success values 3.30:1 at 16 px, the TPS label 1.81:1, TTFT 2.72:1, P99 ITL 2.65:1, Uptime 2.26:1, units 2.56:1. In dark mode Bad is `#f87171` and Critical `#ef4444`, told apart mainly by an underline.
- Root cause: light tier tokens (`input.css:144-148`), `--color-text-faint`/muted (`input.css:61-62`) and the series-coloured labels (`--chart-label-cc-*`) were not chosen against the card surface; the dark tier pair at `input.css:285-286`.
- Proposed fix: choose tokens that meet 4.5:1 (3:1 for large text) against `--color-raised` in both themes, and make Critical distinct by more than hue.
- Test: a token-level contrast test (pytest parsing `input.css`, or e2e computing ratios for every text element on a card in both themes), which also guards the redesign's palette.
- Classification: fix-now (accessibility; the test survives any palette).

### F72 - Modal focus management

- Found by: analyst round; reproduced on :8080 (`p2.mjs`). After opening a card with a click, `document.activeElement` is still the card; the first Tab lands on a score group on the page behind; `window.scrollBy(0, 600)` scrolls the page under the open modal (body overflow `visible`, the rest of the page not `inert`).
- Root cause: `openModal()` (`modal.js:388`) and `closeModal()` (`modal.js:603`) neither move nor restore focus, nor make the page outside `#modal` inert; the dialog is a styled `div` with `role="dialog"` (`index.html:968`).
- Proposed fix: move focus into the dialog on open, make the rest of the page `inert` (or use `<dialog>` with `showModal()`), lock page scroll, restore focus to the trigger on close. One helper shared with the Help and notification sheets.
- Test: e2e: focus is inside the modal after open, Tab cycles within it, focus returns to the card after close.
- Classification: fix-now (accessibility).

### F73 - Closed Help drawer stays reachable

- Found by: verifying the analyst's tab-order notes; reproduced on :8080 (`p3.mjs`). With the drawer closed, Tab from the last card goes through 4 metric tiles into "Close help panel", the two Help tabs and three section buttons, all off-screen. `#help-panel` has no `inert`, `hidden` or `aria-hidden`, and its `box-shadow: -8px 0 32px` shows as a grey band at the right edge (visible in `p2-firstload.png`).
- Root cause: `#help-panel` is closed with `transform: translateX(100%)` and `pointer-events: none` only (`index.html:370-380`); `openHelpPanel()`/`closeHelpPanel()` (`help.js:104`, `:119`) never toggle `inert`.
- Proposed fix: set `inert` (and drop the shadow) while closed, in the same helper as F72.
- Test: e2e: with the drawer closed, no focusable inside it is reachable by Tab.
- Classification: fix-now (accessibility; the drawer is on the keep list).

### F74 - Card and page structure for keyboard and screen readers

- Found by: analyst round; reproduced on :8080 (`p2.mjs`): the first card is the 17th tab stop; each card is `role="button" tabindex="0"` (`dom.js:392`) and contains further focusables (score group `dom.js:183`, metric tiles `format.js:408`, badges `dom.js:238-253`); there is no skip link; the only dashboard heading is the h1 (provider and model names are not headings).
- Root cause: tooltips are reached by making every labelled element focusable, inside an element that is itself a button (nested interactive content), and sections are plain `div`s.
- Proposed fix: a card is an article with a heading whose name is the link or button that opens the modal; tooltip content becomes reachable without extra tab stops (for example a single details control); provider sections get headings; add a skip link to the model list.
- Test: e2e: no focusable descendants inside an element with `role=button`; the first model is reachable within a small bounded number of Tabs; provider names are headings.
- Classification: fix-now (accessibility).

### F75 - Targets below the minimum size

- Found by: analyst round; reproduced on :8080 (`p2.mjs`): `#ws-status` is a focusable 10x10 dot (`index.html:868`), chart-view pills are 18 px tall (`index.html:232-246`), score groups 23 px.
- Root cause: sizes set by typography, with no minimum target rule.
- Proposed fix: a minimum 24x24 hit area for every interactive element (padding or a pseudo-element), set once in the base styles.
- Test: e2e: every visible focusable element's box is at least 24x24 (or has 24 px spacing), at 1440 and 390 px.
- Classification: fix-now (accessibility; the test guards the redesign too).

### F76 - Capability labels in four places

- Found by: analyst round; reproduced by reading and on :8080. The card badge (`dom.js:213-226`) says "Thinking" and "JSON"; the filter (`filter.js:65-71`, repeated as static buttons in `index.html:940-946`) says "Reasoning" and "Structured Output"; the modal (`modal.js:291-292`) says "Structured output" and "Thinking"; `HELP.capabilities` (`utils.js:96`) repeats the card list. The Context and Params options are also defined twice: static buttons in `index.html:921-936` and `CONTEXT_DEFS`/`PARAM_DEFS` in `filter.js:50-62`, with their bounds a third time in `filter.js:139-150`.
- Root cause: no shared definition of capabilities and spec buckets.
- Proposed fix: one capability table (key, label, description) delivered by the backend next to the model-info fields, used by card, filter, modal and Help; the filter renders its Specs buttons from its defs like it already does for Status.
- Test: `test_frontend_rules.py` or `test_ssoT_labels.py`: no capability label literal outside its home; index.html has no Specs option buttons.
- Classification: fix-now (single source of truth).

### F77 - Status labels without a single source

- Found by: analyst round; reproduced by reading and on :8080. `error` is "Offline" on the card badge (`dom.js:238`) and in the filter (`filter.js:31`), "Errors" in the legend (`help.js:52`); the Offline filter matches `error` or `unknown` (`filter.js:106`), while an unknown card has no Offline badge.
- Root cause: `STATUS_VALUES` in `backend/state.py:405` has no labels, so each module names statuses itself (the gap F6 closed for test types).
- Proposed fix: `STATUS_LABELS` next to `STATUS_VALUES`, delivered through `/api/config` like `TEST_TYPE_LABELS`; decide explicitly how `unknown` is filtered and shown.
- Test: `test_ssoT_labels.py`: every status has a label and `/api/config` exposes it; `test_frontend_rules.py`: no status label literals in the frontend.
- Classification: fix-now (single source of truth).

### F78 - HELP texts contradict the rules

- Found by: checking the analysts' score explanations against the code (the UI inventory repeated the wrong "Speed is a TPS trend" claim from the tooltip).
- Reproduce: `HELP.scores` (`utils.js:86`) says the scores are based on "trends", S = "TPS trend", R = "uptime trend"; the code (`stats.py:254-331`) computes per-test tier scores, S from TTFT and TPS, R from uptime and the degraded share. `HELP.consistency` (`utils.js:53`) names chunk CV; the weights use burst arrival % (`app.yaml.example:305`). `HELP.degraded` and `degraded_critical_tier` (`utils.js:35-36`) say "one or more" Critical metrics; the rule is two or more (`scheduler.py:374`, itself a literal). `HELP.stall` (`utils.js:50`) hardcodes 500 ms, the configured `stalls.visible_threshold_ms` plus jitter or half the RTT (`streaming.py:689-693`); `HELP.hiccups` (`utils.js:62`) hardcodes 3x (`stalls.hiccup_multiplier`).
- Root cause: explanatory text written by hand next to rules that live in config and backend code.
- Proposed fix: correct the texts; put the degraded threshold count in config; expose the scoring weights and stall parameters in `/api/config` and build the numeric parts of these texts from them.
- Test: pytest: the HELP strings that mention thresholds are built from config (no numeric literals in those entries), and the score description lists exactly the configured components.
- Classification: fix-now (labels and explanations survive any design).

### F79 - Em and en dashes still rendered

- Found by: analyst round (constraints), counted on the current tree: 19 lines outside the two F30 joiners. `index.html:924-925`, `:934-935` (`&ndash;` in Specs labels) and `:963` (footer `&mdash;`); `utils.js:35` and `:96` (`\u2014` in HELP); `dom.js:224` (capability tip); `chart-helpers.js:240`, `:246`, `:254`, `:429`, `:431`, `:433` (`\u2013` in "P10-P90" tooltips); `filter.js:40`, `:53-54`, `:60-61` (range labels).
- Root cause: the CONTRIBUTING rule bans U+2014/U+2013, but the characters are written as escapes or entities, which no check sees.
- Proposed fix: replace them (hyphen, "to", or the shared separator from F6), and extend the F30 guard to `\u2014`, `\u2013`, `&mdash;`, `&ndash;` and the literal characters in `frontend/` and inline HTML built in `backend/`.
- Test: `test_frontend_rules.py`: no dash character in any form in frontend sources and index.html.
- Classification: fix-now (rule enforcement).

### F80 - Chart labels hardcoded and used as keys

- Found by: analyst round (constraints); confirmed by reading. `CHART_VIEWS` in `chart.js:47-52` holds the view labels while `/api/config` sends only the keys; `chart-helpers.js:159-161`, `:179`, `:187`, `:405-413`, `:563`, `:572` type series labels ("TPS", "TTFT", "Tails", "Batching", "Consistency", "Speed", "Reliability"), and `chart-helpers.js:225` maps those display strings back to data keys. "Tails" plots raw P99 ITL, which `METRIC_LABELS` calls "P99 ITL (raw)". `test_ssoT_labels.py:74-89` only asserts that `const _TYPE_LABELS = {` and `const METRIC_LABELS = {` are absent from two named files.
- Root cause: F6 moved test-type labels to the backend but not the chart labels; the SSoT tests check variable names, not values.
- Proposed fix: chart view labels and tips next to `CHART_VIEWS` in `state.py`, series labels from `METRIC_LABELS`, lookups by key never by label; make the SSoT test scan all frontend modules for any backend label value.
- Test: the extended SSoT test (fails on the current `chart-helpers.js`).
- Classification: fix-now (single source of truth).

### F81 - Score tiers duplicated in the frontend

- Found by: analyst round (constraints); confirmed by reading: `SCORE_TIERS` (`format.js:142-148`, boundaries 80/60/40/20 with tier labels) and `_SCORE_THRESHOLDS` (`chart-plugins.js:52`, the same boundaries); the filter derives its buckets from `SCORE_TIERS`. The metric tiers, by contrast, come from `color_thresholds` in config.
- Root cause: score tiers were never moved to config.
- Proposed fix: a `scores.tiers` (or `color_thresholds.scores`) entry in `app.yaml`, validated and documented, delivered through `/api/config`, used by the formatter, the chart zones and the filter.
- Test: config test (key required and validated); `test_frontend_rules.py`: no score boundary literals.
- Classification: fix-now (single source of truth).

### F82 - Palette copied into seven files

- Found by: analyst round (constraints); confirmed by reading. Base colours `#0c1220`/`#f8fafc` appear in `input.css:6`, `:15`, `:51`, `:198`, `index.html:12` (meta) and `:29` (critical CSS), `routes.py:384-396` (FOUC script and bell colours `#2563eb`/`#60a5fa`), `manifest.json:8-9` (always dark) and `theme.js:122` (fallback). Series colours exist as `CC` defaults in `state.js:92` and `_LABEL_COLORS` in `format.js:397`, and they already disagree (TPS `#06b6d4` vs `#22d3ee`, TTFT `#8b5cf6` vs `#a78bfa`).
- Root cause: every early-paint consumer (FOUC, meta, manifest, fallbacks) got a literal instead of reading the token source.
- Proposed fix: `input.css` tokens as the only source; the backend reads the base and accent values from it (or from one generated file) for the FOUC script, meta and manifest; JS reads CSS variables and has no hex fallbacks.
- Test: pytest: no hex colour literal outside `input.css` (and the generated file), and the FOUC, meta and manifest values equal the tokens.
- Classification: fix-now (single source of truth; the redesign changes the values, not the mechanism).

### F83 - Chart colours must be 6-digit hex

- Found by: analyst round (constraints); reproduced on :8080 (`p7.mjs`): setting `--chart-zone-base-success` to `rgb(22 163 74)` and redrawing the modal chart changes the zone fill to `rgba(100, 100, 100, 0.07)` with no log line; `_hexToRgba('rgb(22 163 74)', 0.5)` returns `rgba(NaN,NaN,2,0.5)`.
- Root cause: `_hexToRgba` (`chart-plugins.js:9-13`) parses only `#rrggbb`, and the callers (`chart-plugins.js:154`, `:305`, `chart-helpers.js:48`) fall back to grey silently. Tailwind v4 defaults and modern palettes are `oklch()`.
- Proposed fix: resolve any CSS colour once through the browser (for example a canvas `fillStyle` round trip, or `color-mix()` in the token itself) into an alpha-capable form; log through `logWarn` when a token cannot be parsed.
- Test: JS unit test for the colour helper with hex, `rgb()`, `oklch()` input.
- Classification: fix-now (chart pipeline is kept).

### F84 - Charts at device pixel ratio 1

- Found by: analyst round (constraints); reproduced on :8080 at deviceScaleFactor 2 (`p2.mjs`): card canvas 346x144 backing store for 346 CSS px, modal canvas 358 for 358.
- Root cause: `devicePixelRatio: 1` hardcoded in both option branches (`chart-helpers.js:483`, `:531`).
- Proposed fix: `Math.min(window.devicePixelRatio, cap)` with the cap in config (the lazy destroy pipeline keeps the memory cost bounded).
- Test: e2e at deviceScaleFactor 2: canvas width equals twice its CSS width (up to the cap).
- Classification: fix-now (chart pipeline is kept; a static tunable).

### F85 - Breakpoint and default ranges duplicated

- Found by: analyst round (constraints) and reading. `BP_SM = 640` (`utils.js:194`) is bypassed by `640` literals in `modal-ranges.js:379` and `:390`; `modal.js:42` uses `<= BP_SM` where every other module uses `< BP_SM`, so at exactly 640 px the modal title takes the phone layout while the CSS (`min-width: 640px`) is in desktop layout. The default ranges `24h`/`7d` and `4h`/`3d` are typed in `modal.js:401-403` and again in the picker's Clear handler (`modal-ranges.js:379-381`). The picker's month and day names are English literals (`modal-ranges.js:278-279`) next to `toLocaleDateString` labels.
- Root cause: constants re-typed where needed.
- Proposed fix: one breakpoint helper (`isPhone()`) used everywhere; default ranges in config (`time_ranges` defaults per form factor) delivered with the ranges; month and weekday names from `Intl.DateTimeFormat`.
- Test: `test_frontend_rules.py`: no `640` literal outside `utils.js`, no range-key literals outside config; JS unit test for the helper at 639/640 px.
- Classification: fix-now (single source of truth).

### F86 - Bands stay when their series is hidden

- Found by: analyst round; reproduced on :8080 (`p6.mjs`): on the modal speed chart, hiding the TPS dataset keeps the three zone fills and the threshold lines (fillRect count 3 before and after; only the TPS line stroke disappears), so the TTFT line is drawn over TPS tier bands.
- Root cause: `thresholdPlugin` and `cardZonesPlugin` (`chart-plugins.js:134-160`, `:237-305`) never check `chart.isDatasetVisible()` for the series the zones describe.
- Proposed fix: draw zones and threshold labels only while their dataset is visible (and label which series they belong to).
- Test: e2e or JS unit test with a stub chart: hiding the mapped dataset removes the zones.
- Classification: fix-now (modal chart logic is kept).

### F87 - Deploy detection depends on another page load

- Found by: checking the analysts' "every frontend change reloads open tabs" claim; reproduced in a subprocess with `st.FRONTEND_DIR` pointed at a copy of `frontend/`: after editing `js/utils.js`, `deploy_version()` and `_asset_fingerprint()` return the old values; only after the reset that `index()` performs does the version change.
- Root cause: `_static_version()` and `_asset_fingerprint()` (`routes.py:81-110`) cache in module globals that only `index()` clears (`routes.py:346-347`). A tab that polls `/api/deploy-version` every 60 s (ARCHITECTURE.md "Three-layer update detection") therefore sees the new version only after some client loads `/`; a single open tab never does. The built CSS (`BUILT_CSS_PATH`, outside `frontend/` in Docker) is not part of the version at all.
- Proposed fix: key the caches on a cheap directory signature (max mtime scan with a short TTL, or a file watcher) instead of page loads, and include the built CSS.
- Test: pytest with a temp frontend dir: an edit changes `/api/deploy-version` without calling `index()`.
- Classification: fix-now (backend).

### F88 - Card-bucket refresh downloads everything

- Found by: analyst round (constraints); measured on :8080 and on a private 500-model instance (port 8093, `p9.mjs`): `/api/metrics?card_buckets=1` for all providers is 88,040 B (11,294 B gzip) for 20 models and 1,132,942 B for 500; one provider is 17,009 B.
- Root cause: `refreshCardBuckets()` (`ws.js:27-42`) refetches `state.providerOrder` (every provider, collapsed or off-screen) after any benchmark result (debounced) and every 5 minutes (`app.js:249`), to update the models that changed; any result changes the collection ETag, so the 304 path does not help.
- Proposed fix: send the changed model's buckets with its WS result (the server already has them in `cached_card_buckets`), or fetch only the providers of changed and visible models; drop the blanket 5-minute refresh.
- Test: e2e: after a simulated result for one model, the page requests at most that model's provider (or nothing).
- Classification: fix-now (data flow; the scale target is 5000 models).

### F89 - First load builds every expanded card

- Found by: analyst round (constraints); measured on a private 500-model instance (`p9.mjs`): one `/api/metrics` response of 1.13 MB, then 500 cards, 500 canvases and 23,495 DOM nodes, with 9 cards in the viewport and no deferred grid; the load took 4.3 s.
- Root cause: `init()` fetches metrics for all providers and marks every provider fetched (`app.js:203-211`), so `buildProviderSections()` renders all expanded cards and the lazy provider observer (`dom.js:543-560`, which fetches unfetched providers near the viewport) never has work.
- Proposed fix: fetch summaries first and card data only for providers near the viewport (the observer path), with a configurable eager count; the rendered DOM then scales with the viewport.
- Test: e2e on a seeded instance with more providers than fit on screen: after load, cards exist only for the first providers.
- Classification: fix-now (data flow and performance).

### F90 - Schedule line reads as ages and ignores a stopped scheduler

- Found by: analyst round (UX critique); reproduced on :8080: the header reads "Health: 5m · Bench: 51m · Audit: 6h" while the scheduler is off (`MW_DISABLE_TESTS=1`, `last_run_ago_seconds` and `next_run_in_seconds` are null in `/api/config`), and the check-line ages in the modal turn red.
- Root cause: `renderSchedule()` (`dom.js:469-475`) prints the configured intervals with a timer icon and no wording or tip; `/api/config` has no field for whether the scheduler runs, so the page cannot say "paused".
- Proposed fix: expose scheduler state (running, paused by `MW_DISABLE_TESTS`, next due) in `/api/config` and the hello frame; label the intervals ("every 5m") with a tip, and show "Testing paused" when not running.
- Test: route test: `/api/config` reports the scheduler state; e2e: with tests disabled the header says paused.
- Classification: fix-now (API signal; the header wording follows it in any design).

### F91 - Stale trend and reliability docs

- Found by: analyst round (data and API); confirmed by reading. `docs/API.md:90` shows `"trends": {"tps": "up", "ttft": "flat"}`; the API returns `{direction, change, unit, data_points}` per metric plus `since_ts`. `config/app.yaml.example:309` says reliability "combines uptime (availability) with quality (consistency + speed scores)"; `compute_reliability_score()` uses quality = 1 - share of degraded benchmarks (`stats.py:293-331`).
- Proposed fix: correct both (the other agent owns docs/ and config/ in this round).
- Test: a doc test that parses the API.md example responses and validates them against the real response shape for the documented endpoints.
- Classification: fix-now (stale docs).

### F92 - Model name printed twice

- Found by: analyst round (UX critique); reproduced on :8080 (`p3.mjs`, `p2.mjs`): every card's first two lines are "gpt-5.4" / "gpt-5.4"; the phone modal title reads "· gpt-5.4 gpt-5.4".
- Root cause: `buildCardDOM()` (`dom.js:396` and `:399`) and `_modalTitleHTML()` (`modal.js:56-57`) always render both `entry.name` and `entry.model_id`, and `name` falls back to the id when no display name is configured.
- Proposed fix: one helper that returns the secondary identifier only when it differs from the name.
- Test: JS unit test for the helper; e2e: a card whose name equals its id shows it once.
- Classification: fix-now (display rule shared by card and modal).

### F93 - Scores unreachable on phones

- Found by: analyst round; reproduced on :8080 at 390 px: `.score-group` is `display: none` below 640 px (`index.html:68-69`), which removes C/S/R from cards and provider headers; the modal has no score display at any width.
- Root cause: phone layout hides the chips instead of re-laying them out.
- Acceptance criterion for the redesign: every value the desktop dashboard shows (scores, trend and freshness included) is reachable at 390 px without hover, and the model detail view shows the model's scores and their trend.
- Classification: deferred-to-redesign (the card score chip and the phone layout are replaced by every direction).

### F94 - Card sparklines cannot be compared

- Found by: analyst round (UI inventory and UX critique); confirmed on :8080: each card series is min-max normalised on its own (`_normalizeValues`, `chart-helpers.js:68-77`), the card chart has no axis or legend, and the zone bands follow one metric per view (`_ZONE_METRIC_MAP`, `chart-plugins.js:45-50`), so in the speed view the TTFT line runs over TPS bands.
- Acceptance criterion for the redesign: small charts that sit side by side share a stated scale (or show the value range), say which series they show, and draw tier bands only behind the series they describe; the existing lazy chart lifecycle is kept.
- Classification: deferred-to-redesign (every direction replaces the card sparkline).

### F95 - Score chip typography

- Found by: analyst round (UX critique); reproduced on :8080: the provider chip reads "C 74 % ↑ S 62 % ↓ R 83 % ±"; the gap comes from the chip's flex gap between the value and its `%` span (`dom.js:165`, `index.html:68`), and `trendArrow()` renders stable as "±" (`format.js:161-165`).
- Acceptance criterion for the redesign: numbers and units read as one token ("74%"), and a stable trend uses a glyph or word that reads as "no change" (explained in Help).
- Classification: deferred-to-redesign (the score chip markup is replaced by every direction).

### F96 - Scores filter clipped on phones

- Found by: analyst round; reproduced on :8080 at 390 px (`p2.mjs`): the last bucket "≤19%" ends at x=413 in a 390 px viewport.
- Root cause: `.filter-seg` (`index.html:715-724`) scrolls horizontally with its scrollbar hidden, so the overflow has no affordance.
- Proposed fix: let the segment wrap (or use a two-row layout) below the phone breakpoint; never hide the scrollbar of a scroller that clips options.
- Test: e2e at 360 and 390 px: every filter option is inside the viewport.
- Classification: fix-now (the filter model and panel survive in several directions).

### F97 - config file paths resolved three ways

- Found by: the backend-config round, while moving the config watcher out of the test tasks (F46).
- Reproduce: start with `MW_APP_YAML=/tmp/x/app.yaml` and edit that file: nothing reloads, because `config_watcher()` watches `config/` with a `.yaml` filter. With `MW_MODELS_YAML` set and `reset_epoch: true` in that file, `_strip_reset_epoch_from_yaml()` rewrites `config/models.yaml` instead (or fails when it does not exist), so the directive stays and forces a retest on every later reload. Edits to unrelated YAML files in `config/` triggered reloads.
- Root cause: `_load_yaml` knew the `MW_*_YAML` overrides; the watcher and the rewrite hardcoded `CONFIG_DIR`.
- Fix: `config.config_path(name)` is the one resolver; loading, the watcher (the directories of the files in use, filtered to exactly those files) and the `reset_epoch` rewrite all use it. `st.Change`, which only the old filter used, is gone.
- Tests: `test_config_reload.py` (an override in a temp dir hot-reloads, its `reset_epoch` is stripped at startup; `config_path` follows names and absolute paths). Mutation-checked: watching `config/` or rewriting `config/models.yaml` each fail.

### F98 - notification titles joined by hand

- Found by: the tests-scripts round, when the F29 separator guard learnt spaced-hyphen joiners.
- Reproduce: `grep -n "join(' - ')" frontend/js/notifications.js` finds the browser notification title (`:277`) and the toast `aria-label` (`:296`), the same string built twice.
- Root cause: both predate `SEP_TEXT`, and the separator guard only looked for the middle dot.
- Fix: one `_notifTitle(provider, model, notif)` joins with `SEP_TEXT` for both.
- Tests: `test_frontend_rules.py::test_separators_come_from_the_shared_primitive`. Mutation-checked: a `' - '` join fails it.

### F99 - stale `MW_DISABLE_TESTS` comment in `.env.example`

- Found by: the tests-scripts round, while documenting `MW_DATA_DIR` there.
- Issue: the comment lists the config watcher among the skipped tasks, which F46 moved out of them, and leaves out the token encoder download, which is skipped.
- Fix: the comment names what `_OUTBOUND_TASKS` holds.
