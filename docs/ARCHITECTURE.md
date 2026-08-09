# Architecture

ModelWatcher is a real-time LLM API monitoring dashboard. This document covers the system design, data flow, module structure, and key design principles.

## Table of contents

- [High-level architecture](#high-level-architecture) - Three-layer overview
- [The four test types](#the-four-test-types) - Benchmark, health, audit, probe
  - [Scheduling](#scheduling) - Per-model due-checking and dispatch order
- [Streaming test flow](#streaming-test-flow) - Request to metrics pipeline
- [Anthropic vs OpenAI dual streaming paths](#anthropic-vs-openai-dual-streaming-paths) - Detection, endpoints, SSE events
- [Data flow](#data-flow) - Config to WebSocket broadcast
  - [Key data structures](#key-data-structures) - model_registry, model_cache, recent_history, metrics_cache
- [Module dependency graph](#module-dependency-graph) - Backend and frontend import structure
  - [Backend](#backend-backend---27-modules) - 27 modules, strictly unidirectional imports
  - [Frontend](#frontend-frontendjs---22-modules) - 22 ES modules, no bundler, no build step
- [Single-source-of-truth principle](#single-source-of-truth-principle) - Config, labels, tiers, error format
- [Error handling - the 3-net model](#error-handling---the-3-net-model)
- [SQLite schema](#sqlite-schema) - 7 tables, WAL mode, connection model
  - [Connection model](#connection-model) - Write lock, read pool, PRAGMAs
  - [Read vs write path](#read-vs-write-path) - Hot path (model_cache) vs cold path (SQLite)
- [Config hot-reload](#config-hot-reload) - watchfiles, in-place mutation, wake event
- [PWA and service worker](#pwa-and-service-worker) - Push-only SW, auto-update, VAPID

## High-level architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Browser                              │
│  Vanilla JS (ES modules) · Chart.js · WebSocket · Web Push   │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────▼──────────────────────────────────────┐
│                  FastAPI Backend (uvicorn)                   │
│  ┌────────────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐  │
│  │  Routes    │  │ Scheduler│  │  Config   │  │ WebSocket │  │
│  │ (REST API) │  │  (tests) │  │ (hot-reload)│  │ Manager  │  │
│  └─────┬──────┘  └────┬─────┘  └─────┬─────┘  └─────┬─────┘  │
│        │              │              │              │        │
│  ┌─────▼──────────────▼──────────────▼──────────────▼─────┐  │
│  │                   model_cache (in-memory)             │  │
│  │  status · last_test · recent_history · uptime · scores│  │
│  └─────────────────────────┬─────────────────────────────┘  │
│                            │                                 │
│  ┌─────────────────────────▼─────────────────────────────┐  │
│  │              SQLite (WAL mode) - data/metrics.db       │  │
│  │  test_results · model_state · providers · model_info   │  │
│  │  push_subscriptions · audit_results · probe_results    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                       │ httpx (HTTP/1.1, fresh TCP+TLS per request)
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   LLM Provider A  LLM Provider B  LLM Provider C
```

Three layers:

1. **Backend** - Python 3.13 FastAPI app serving REST API, WebSocket, and static frontend. A scheduler dispatches test tasks that stream from LLM provider APIs, compute metrics, persist to SQLite, and update an in-memory `model_cache`.
2. **Frontend** - Vanilla JS ES modules (no bundler, no build step for JS). Loads Chart.js locally. Connects via WebSocket for live updates; falls back to REST polling when disconnected.
3. **Database** - SQLite with WAL mode. The in-memory `model_cache` is the hot read path for `/api/metrics`; SQLite is the durable store and the source for history queries.

## The four test types

| Type | What it does | When it runs | What it measures |
|------|-------------|--------------|-------------------|
| **Benchmark** | Full streaming test with a long prompt (structured essay, ~8 sections × 350 words) | `testing.benchmark.interval` (default 7200s / 2h) | TTFT, TPS, TPOT, ITL (raw + effective), stall count, hiccup count, max/median/avg/p99 ITL, tail ratio, batching ratio, burst arrivals, chunk token CV, thinking duration, network RTT |
| **Health check** | Minimal request with a short prompt (e.g. "Respond with OK.") | `testing.health_check.interval` (default 300s / 5min) | TTFT, reachability (success/failure), network RTT only. All other metrics stripped. |
| **Audit** | Automated compliance test suites (currently [SynBad](https://github.com/synthetic-lab/synbad) only; extensible to other providers in the future) | `audit.interval` (example: 21600s / 6h) | Pass/fail per eval, pass rate, suite results (reasoning, code generation, etc.) |
| **Probe** | Capability detection via lightweight completion requests | `testing.probe.interval` (default 86400s / 24h) | Vision, tools, structured output, cache support, thinking/reasoning, system fingerprint, served-by |

### Scheduling

The scheduler (`backend/scheduler.py`) uses a **per-model due-checking** model. Each cycle:

1. Build the undispatched model list (not in `running_tests` / `running_health` / `running_audit` / `running_probe`).
2. Dispatch health checks first (finish fast, free provider slots).
3. Dispatch benchmarks.
4. Dispatch probes.
5. Dispatch audits.
6. Compute next-due time as the minimum across all four test types; sleep until then or until the wake event fires.

Concurrency is controlled at two levels:
- **Global semaphore** (`testing.max_concurrent_tests`, example: 50) - limits total concurrent tests.
- **Per-provider semaphore** (`concurrent_models`, example: 1) - limits concurrent tests per provider.

Benchmarks can optionally stagger by provider (`testing.benchmark.stagger: true`), cycling providers one at a time in sorted order.

## Streaming test flow

```
run_test(model_key, test_type)
  │
  ├── Set testing flag, broadcast WS "testing"
  ├── Check for interrupted retries (db.get_resume_attempt)
  ├── Select prompt (random_prompt for benchmark, random health prompt for health)
  │
  └── Retry loop (1 + max_retries attempts):
        │
        ├── stream_test(provider, prompt, test_type)     [backend/streaming.py]
        │     ├── Build request (Anthropic /messages vs OpenAI /chat/completions)
        │     ├── httpx stream POST with trace handler (TCP + TLS RTT)
        │     │
        │     └── Iterate SSE events:
        │           ├── parse_anthropic_event()  OR  parse_openai_chunk()
        │           ├── split_thinking() (OpenAI: Qwen think/answer boundary)
        │           ├── Record token timing (first_token_time, per-chunk timestamps)
        │           ├── Collect usage info (completion_tokens, reasoning_tokens)
        │           └── Break on error / stream_end / "done"
        │
        ├── compute_stream_metrics():
        │     ├── TTFT = max(0, raw_ttft - network_rtt)
        │     ├── TPS = token_count / gen_time
        │     ├── ITLs (raw + effective, divided by per-chunk token count)
        │     ├── Stall count (jitter-adjusted threshold)
        │     ├── Hiccup count (adaptive: multiplier × median ITL)
        │     ├── Token validation chain (implausibility check, tiktoken cross-validation)
        │     └── No-answer detection (reasoning-only → success if finish_reason=length/health)
        │
        ├── _mark_insufficient_as_degraded (benchmark: <min_tokens AND <min_chunks)
        ├── strip_health_metrics (health: delete non-_HEALTH_KEEP fields)
        │
        └── db.record_result_async():
              ├── Derive status + degraded_source
              ├── Update model_cache (append to recent_history, set status/epochs)
              ├── SQLite INSERT + upsert model_state + commit
              ├── Invalidate /api/metrics ETag cache
              └── Return (uptime_pct, changed, prev_status, degraded_since)
```

After the retry loop, benchmarks run a **Critical→Degraded check**: if 2+ metrics hit the Critical tier, the result is flagged degraded with `reason="critical_tier"`.

## Anthropic vs OpenAI dual streaming paths

Detection is based on `"anthropic" in api_url.lower()`.

| Aspect | Anthropic | OpenAI-compatible |
|--------|-----------|-------------------|
| Endpoint | `{api_url}/messages` | `{api_url}/chat/completions` |
| Auth header | `x-api-key: {api_key}` | `Authorization: Bearer {api_key}` |
| Request body | `model`, `max_tokens`, `stream: true`, `messages` | `model`, `messages`, `max_completion_tokens` + `max_tokens`, `stream: true`, `stream_options`, `logprobs` |
| Thinking | `thinking: {type: enabled, budget_tokens: ...}` (if `anthropic_thinking_budget` set) | No thinking-control params sent (reasoning detected via `reasoning_content` / `reasoning` fields) |
| SSE events | `message_start`, `content_block_start/delta/stop`, `message_delta`, `message_stop` | `choices[0].delta` with `content`, `reasoning_content`, `finish_reason` |
| Think/answer boundary | Explicit `content_block_start` type transitions | `split_thinking()` detects 12-newline boundary (`THINK_END`) in a single delta field (Qwen) |
| Usage | `message_delta` usage > `message_start` usage | `chunk.usage` (inline or final) |

## Data flow

```
config/*.yaml
    │
    ▼
reload_config()  ────────────►  model_registry (flat list of {id, provider, model_id, name})
    │
    ▼
scheduler()  dispatches due tests per model per test type
    │
    ├──► stream_test()  ──►  compute_stream_metrics()  ──►  make_result()
    │                                                        │
    ├──► run_probe_test()  ──────────────────────────────────┤
    │                                                        │
    └──► run_audit_test()  ──────────────────────────────────►│
                                                             ▼
                                              db.record_result_async()
                                                    │
                              ┌───────────────────┤
                              ▼                   ▼
                        SQLite (WAL)        model_cache (in-memory)
                              │                   │
                              └─────────┬─────────┘
                                        ▼
                              /api/metrics (ETag cached, rebuilt from model_cache)
                                        │
                                        ▼
                              WebSocket broadcast (result_batch, audit_result, probe_result, notification)
                                        │
                                        ▼
                                   Frontend (live DOM update + chart refresh)
```

### Key data structures

- **`model_registry`** - Flat list of `{id, provider, model_id, name}`, built from `models.yaml` by `build_model_registry()`. Model key format: `ProviderName::model_id`.
- **`model_cache`** - In-memory dict keyed by model key. Single hot-data store for status, testing flags, uptime, `last_test`, `last_success_test`, health data, and `recent_history`. Full history lives in SQLite.
- **`recent_history`** - In-memory list (oldest-first), capped to cover both test intervals with 1.2x buffer. Used for uptime calculation and trends. Contains both benchmark and health records.
- **`metrics_cache`** - Opaque dict for ETag caching of `/api/metrics` responses. Marked dirty on any `model_cache` mutation.

## Module dependency graph

### Backend (`backend/` - 27 modules)

Strictly unidirectional, no circular imports:

```
state.py ← security.py, prompts.py, websocket.py, metrics.py, middleware.py, stats.py, model_info.py
          ← validation.py, favicons.py, models.py, db.py, migrations.py
          ← streaming.py, routes.py, push_routes.py, notifications.py, audit.py, probe.py
          ← scheduler.py, config.py ← main.py
batch.py (zero backend deps, imported by db.py and scheduler.py)
```

- `state.py` is the root - no imports from other backend modules. All domain modules import from here. Key exports: `c` (config namespace), `model_cache`, `model_registry`, `log`, `log_error`, HTTP client, paths, constants.
- `config.py` uses lazy imports (inside function bodies) for `db.py`, `scheduler.py`, `stats.py`, `favicons.py`, `model_info.py`, `notifications.py` to avoid circular dependencies.
- Modules that **mutate** shared state use `import backend.state as st` and access via `st.variable` (avoids value-copying from `from ... import` for rebound primitives).

### Frontend (`frontend/js/` - 22 modules)

```
state.js ← utils.js ─── format.js
  │    ╲         │    ╲        │
  │     api.js ──┘     ╲      ├── chart.js ← api.js, tooltips.js, theme.js
  │                    ╲ │    ├── tooltips.js
  │                     └─────┤
  │                           ├── help.js
  └────────────────────────── dom.js       modal-loader.js ← modal.js ── notifications.js ←── prefs.js
                              │                    ↑                  │   ╱
                              frame.js ← modal-loader.js         ws.js ────┘─╱
                              theme.js                              │          ╱
                              cache.js                            app.js ─────╱
```

- `utils.js` is the true leaf node (zero imports from other modules).
- Named exports only - no default exports.
- Mutable shared state via exported `const` object (`state`); primitives use setter functions (`setChartReady()`).
- `prefs.js` breaks a near-circular dependency between `notifications.js` and `ws.js`.
- `modal-loader.js` is a lazy-loading proxy - imports `modal.js` dynamically on first call.

## Single-source-of-truth principle

ModelWatcher enforces a strict single-source-of-truth discipline:

- **Config is the sole source of truth** - The `c` namespace in `state.py` starts empty (no defaults). `reload_config()` populates it from YAML. The codebase has no `getattr(c, "field", default)` patterns - a regression test (`test_config_no_defaults.py`) enforces this. If config is missing or invalid, the app fails fast.
- **Labels owned by `state.py`** - Metric labels and notification event labels live in `state.py` and are exposed via `/api/config`. The frontend reads them from the API response, never hardcoding its own. A regression test (`test_ssoT_labels.py`) enforces that the frontend does not define its own metric/event labels.
- **Tier resolution is shared** - `tier_idx()` in `stats.py` is the single tier resolution function, used by both `find_critical_metrics()` (Critical→Degraded detection) and notification TPS/TTFT degradation detection.
- **One notification filter function** - `should_notify()` in `notifications.py` is the single source of truth for all delivery channels (push, WS broadcast, history).
- **Per-file content-hash cache busting** - `?v=` params on asset URLs use per-file SHA1 hashes, so changing one file only invalidates that file's browser cache.

## Error handling - the 3-net model

No error is ever silently swallowed. Both frontend and backend use three safety nets:

| Escape path | Backend net | Frontend net |
|-------------|-------------|--------------|
| Route/event handler | `@app.exception_handler(Exception)` | `window.onerror` |
| Async task / promise | `loop.set_exception_handler()` | `unhandledrejection` |
| Your own catch blocks | `log_error(msg, exc)` in every `except` | `logError(ctx, err)` in every `catch` |

If you forget a catch block, the first two nets still catch and log the error. The third net (`log_error`/`logError`) adds contextual information. Every `except` block must either call the log function or re-raise - never bare `pass`.

**PII-safe error messages**: Provider API errors use template-based messages (structured fields → safe lookup), never passing through raw `error.message` which may contain names, org IDs, or billing URLs. Regex scrubbing is retained as defense-in-depth for stack traces.

## SQLite schema

All persistence is via SQLite with WAL mode at `data/metrics.db`.

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `test_results` | Individual test run records | `model_key`, `ts_epoch`, `available`, `degraded`, `ttft_ms`, `tps`, `error`, `retry_attempt`, `test_type`, `provider`, `success`, `degraded_reason`, `critical_metrics`, all metric fields |
| `model_state` | Per-model aggregated state | `model_key`, `status`, `degraded_source`, `uptime_pct`, `total_tests`, `total_success`, `first_ts_epoch`, `reliability_score`, `trends_json`, `archived`, `archived_by`, `updated_at` |
| `providers` | Provider metadata (TTL-cached) | `name`, `api_url`, `last_fetched_at`, `page_title`, `logo_path`, `extra` |
| `model_info` | Per-model metadata from registry/provider APIs/HuggingFace | `model_key`, `provider`, `model_id`, `display_name`, `context_window`, `output_context`, `input_price`, `output_price`, `cache_price`, `reasoning_price`, `image_price`, `supports_vision`, `supports_tools`, `supports_structured_output`, `supports_cache`, `thinking`, `modalities`, `tokenizer`, `description`, `created`, `owner`, `license`, `quantization`, `served_by`, `architecture`, `param_count`, `num_experts`, `num_experts_per_tok`, `num_shared_experts`, `moe_intermediate_size`, `fingerprint` |
| `push_subscriptions` | Web push subscription data | `endpoint`, `p256dh`, `auth`, `client_id`, `prefs`, `created_at`, `last_active` |
| `audit_results` | Audit test results (currently SynBad) | `id`, `model_key`, `ts_epoch`, `passed`, `total`, `pass_rate`, `success`, `duration_ms`, `error`, `suites_json` |
| `probe_results` | Capability probe test results | `id`, `model_key`, `ts_epoch`, `provider`, `success`, `supports_vision`, `supports_tools`, `supports_structured_output`, `supports_cache`, `thinking`, `reasoning_field`, `system_fingerprint`, `served_by`, `engine_version`, `tensor_parallel`, `served_model`, `quantization`, `fp_server`, `fp_features`, `error`, `duration_ms`, `response_meta` |
| `schema_migrations` | Database schema version tracking | `version`, `name`, `applied_at` |

### Connection model

- **`_write_conn`**: Single long-lived connection, serialized by `_write_lock = threading.RLock()`. Accessed from async callers via `asyncio.to_thread()` to avoid blocking the event loop.
- **`_read_conn()`**: Small read connection pool (size 2). WAL allows concurrent readers.
- **PRAGMAs**: `page_size=16384`, `journal_mode=WAL`, `synchronous=NORMAL`, `mmap_size=4GB`, `cache_size=128MB`, `wal_autocheckpoint=5000`, `busy_timeout=5000`, `temp_store=MEMORY`, `foreign_keys=ON`.

### Read vs write path

- **Read (hot path)**: `/api/metrics` reads from `model_cache` (in-memory), ETag-cached, rebuilt when dirty.
- **Read (cold path)**: Modal history/chart queries hit SQLite directly via `db.get_model_history()` (through `asyncio.to_thread`).
- **Write**: `record_result_async()` updates `model_cache` first (append to `recent_history`, set status/epochs), then SQLite INSERT + upsert `model_state` + commit, then invalidate ETag cache.

## Config hot-reload

`config_watcher()` uses `watchfiles.awatch()` on the `config/` directory (filtered to `.yaml`/`.yml` files). On change:

1. `reload_config(log_changes=True)` - reloads YAML files, updates `c`, rebuilds `model_registry`.
2. `apply_db_changes(result)` - syncs SQLite: delete orphaned rows, upsert registry, reconcile archive state (`archived: true` → manual archive; `archived: false` unarchives; removing the directive clears manual archives only; auto-archived rows persist and are tracked via `model_state.archived_by`).
3. Logs added/removed models and interval changes.
4. Broadcasts WS `config_updated` unconditionally.
5. Sets the wake event to reschedule the scheduler.
6. Re-fetches provider favicons and model metadata.

Config is mutated in-place (`.clear(); .update()` / `.clear(); .extend()`) so all modules holding references see the update.

## PWA and service worker

ModelWatcher is an installable progressive web app:

- **Service worker** (`frontend/sw.js`): push-only, no fetch handler. Never caches page content, so stale content is impossible. On activate, deletes old caches, claims all clients, and force-reloads all controlled windows.
- **Three-layer update detection**: (1) browser auto-checks SW on navigation, (2) `controllerchange` listener reloads page when new SW takes control, (3) deploy-version polling (every 60s) triggers reload when `/api/deploy-version` changes.
- **Push notifications**: Service worker receives push events via VAPID. Push subscriptions stored in SQLite `push_subscriptions` table. Requires HTTPS (or `http://localhost` for local dev). PWA installation may be required for background push (browser-dependent).
- **Manifest** (`frontend/manifest.json`): PWA metadata (name, icons, theme color, display mode). `__STATIC_PREFIX__` and `__APP_NAME__` placeholders replaced at serve time.
