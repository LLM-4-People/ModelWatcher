# Configuration reference

ModelWatcher is configured via three YAML files in `config/`. The YAML files are the **sole source of truth** - the codebase has no hardcoded defaults; `reload_config()` only validates types and ranges. If a required value is missing or invalid, the app fails fast with a clear error.

Example files are provided: `config/app.yaml.example`, `config/models.yaml.example`, `config/audits.yaml.example`. Copy them (dropping the `.example` suffix) and edit.

## Table of contents

- [app.yaml](#appyaml) - Runtime tuning
  - [app](#app---application-identity) - Application identity
  - [server](#server---connection-limits) - Connection limits
  - [testing](#testing---test-scheduling-and-execution) - Test scheduling and execution
    - [testing.benchmark](#testingbenchmark---full-streaming-benchmarks) - Full streaming benchmarks
    - [testing.health_check](#testinghealth_check---lightweight-health-checks) - Lightweight health checks
    - [testing.probe](#testingprobe---capability-detection-probes) - Capability detection probes
  - [metrics](#metrics---data-retention-and-aggregation) - Data retention and aggregation
  - [auto_archive](#auto_archive---automatic-archiving-of-offline-models) - Automatic archiving
  - [stalls](#stalls---stall-detection-thresholds-inter-token-latency) - Stall detection thresholds
  - [websocket](#websocket---websocket-settings) - WebSocket settings
  - [notifications](#notifications---notification-system) - Notification system
    - [notifications.events](#notificationsevents) - Event toggles
    - [notifications.in_app](#notificationsin_app) - In-app toast settings
    - [notifications.rate_limits](#notificationsrate_limits) - Rate limits
    - [notifications.webhooks](#notificationswebhooks) - Webhook channels
  - [color_thresholds](#color_thresholds---tier-system) - Tier system
    - [color_thresholds.tiers](#color_thresholdstiers) - Tier definitions
    - [color_thresholds.<metric>](#color_thresholdsmetric) - Per-metric thresholds
  - [scores](#scores---composite-scores) - Composite scores
  - [time_ranges](#time_ranges---modal-chart-time-ranges) - Modal chart time ranges
- [models.yaml](#modelsyaml) - Provider definitions
  - [Provider fields](#provider-fields) - Required and optional provider-level fields
  - [Model fields](#model-fields) - Required and optional per-model fields
  - [request_options](#request_options) - Control which request parameters are sent to OpenAI-compatible APIs
- [audits.yaml](#auditsyaml) - Audit suite definitions
  - [Top-level fields](#top-level-fields) - Master toggle and suite dict
  - [Suite fields](#suite-fields) - Per-suite configuration (url, stream, count, skip_reasoning, etc.)
- [Environment variables](#environment-variables) - API keys, overrides, server bind
  - [Provider API keys](#provider-api-keys) - Env var references via ${VAR_NAME} syntax
  - [Config file path overrides](#config-file-path-overrides) - MW_MODELS_YAML, MW_APP_YAML, etc.
  - [Server bind](#server-bind) - HOST, PORT, FORWARDED_ALLOW_IPS
  - [Diagnostics](#diagnostics) - MW_DISABLE_TESTS
- [Cross-file relationships](#cross-file-relationships) - How config values reference each other

---

## app.yaml

### `app` - application identity

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | (required) | Display name - used in HTML title, manifest, footer, push notification titles |
| `description` | string | (required) | Short description - used in meta tags, manifest, og:description |
| `debug` | bool | (required) | Enable uvicorn `--reload` (hot-reload backend on file change). Only used when running locally without Docker. The published Docker image does not use `--reload`. |
| `static_url_prefix` | string | (required) | URL prefix for frontend assets (e.g. `/frontend`). Requires server restart - not hot-reloadable. |
| `log_level` | string | (required) | Logging verbosity: `"debug"`, `"info"`, `"warning"`, `"error"`. Controls both backend Python logs and browser console output. |
| `site_url` | string | (required) | Public URL of the dashboard - used in CSP `base-uri`, webhook payloads, WS allowed origins. |
| `vapid_email` | string | (required) | VAPID subject claim for web push (`mailto:` or `https:` URL). |

```yaml
app:
  name: "ModelWatcher"
  description: "Real-time LLM API monitoring dashboard"
  debug: true
  static_url_prefix: "/frontend"
  log_level: "info"
  site_url: "https://your-domain.example.com"
  vapid_email: "mailto:you@your-domain.example.com"
```

### `server` - connection limits

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_connections` | int | (required) | Max concurrent HTTP connections (excess returns 503). Safety net - nginx is the primary limiter. |
| `http_connect_timeout` | int | (required) | HTTP connect timeout in seconds (for outbound API requests to providers). |
| `http_pool_max` | int | (required) | Max connections in the httpx connection pool. |

### `testing` - test scheduling and execution

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_retries` | int | (required) | Retries on test failure (benchmark/health only; HTTP 4xx never retried). |
| `initial_delay` | int | (required) | Seconds to wait before first test run. |
| `retry_delay` | int | (required) | Seconds between retry attempts. |
| `stream_activity_timeout` | int | (required) | Seconds before declaring a stream stalled. |
| `timeout` | int | (required) | Request timeout in seconds (shared by benchmark, health, audit, and probe tests). |
| `max_concurrent_tests` | int | (required) | Max models tested concurrently (global limit). |

#### `testing.benchmark` - full streaming benchmarks

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `interval` | int | (required) | Run benchmarks every N seconds. |
| `stagger` | bool | (required) | Run only one provider's benchmarks at a time - providers cycle in sorted order. `false` runs all providers concurrently (up to `max_concurrent_tests`). |
| `target_total_tokens` | int | (required) | Total output token ceiling for all models (reasoning + answer combined). Models that exceed this stop at the ceiling (`finish_reason="length"`). |
| `min_tokens` | int | (required) | Minimum tokens for a valid benchmark result (checked against `completion_tokens`). |
| `min_chunks` | int | (required) | Minimum streaming chunks for a valid benchmark result (independent of `min_tokens`). |
| `anthropic_thinking_budget` | int\|null | (required) | Anthropic-specific `budget_tokens` for extended thinking. `null` disables Anthropic extended thinking (param omitted). |
| `prompts.suffix` | string | (required) | Suffix appended to every random prompt - drives output length for TPS/TTFT measurement. |

#### `testing.health_check` - lightweight health checks

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | (required) | Enables lightweight health checks (reachability + TTFT only). |
| `interval` | int | (required) | Health check interval in seconds (more frequent than benchmarks). |
| `max_tokens` | int | (required) | Maximum output tokens for health checks. |
| `prompts` | list[string] | (required) | Short prompts designed to elicit a small number of tokens naturally. |

#### `testing.probe` - capability detection probes

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | (required) | Enables capability detection probes. |
| `interval` | int | (required) | How often to probe in seconds. |
| `max_tokens` | int | (required) | Token budget for each probe request (reasoning models need more). |

Probes detect: vision, tools, structured output, cache support, and thinking/reasoning capability. Probe timeout uses `testing.timeout`.

### `metrics` - data retention and aggregation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `retention_days` | int | (required) | Days of test results to retain in SQLite (older deleted by `cleanup_interval`). |
| `uptime_window` | int | (required) | Uptime calculation window in seconds. |
| `recent_history` | duration | (required) | Duration of recent history kept in memory (e.g. `2d`, `2h`, `1w`). Cap is dynamically computed. |
| `min_data_points_score` | int | (required) | Minimum data points needed to compute composite scores. |
| `min_data_points_trend` | int | (required) | Minimum data points per half for trend computation. |
| `history_query_limit` | int | (required) | Maximum rows returned by history queries (modal charts, history tables). |
| `provider_fetch_ttl` | int | (required) | How often (seconds) to re-fetch provider page titles/logos. |
| `cleanup_interval` | int | (required) | How often (seconds) to run DB cleanup: delete old results and orphaned rows. |
| `write_batch_interval` | float | (required) | How often (seconds) to flush buffered SQLite writes and WS broadcasts. |
| `write_batch_max_buffer` | int | (required) | Maximum buffered results before triggering an immediate flush. |

Duration strings: `s` (seconds), `m` (minutes), `h` (hours), `d` (days), `w` (weeks), `mo` (30-day months). Bare integers are treated as seconds.

### `auto_archive` - automatic archiving of offline models

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | (required) | Master toggle. Stops new auto-archiving but does NOT unarchive previously archived models. |
| `offline_duration` | duration | (required) | Auto-archive after this much continuous offline (error status). Uses duration strings (e.g. `1d`, `2d`, `1w`). |

Archived models stop being tested but remain visible in the UI. Per-model or per-provider opt-out: set `auto_archive: false` in `models.yaml`.

### `stalls` - stall detection thresholds (inter-token latency)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `visible_threshold_ms` | int | (required) | User-visible stall: ITL above this is a pause a human can perceive. |
| `hiccup_threshold_ms` | int | (required) | Fallback hiccup threshold when median ITL is unavailable. |
| `hiccup_multiplier` | float | (required) | Adaptive hiccup multiplier: ITL > this x median is a hiccup. |
| `batching_log_threshold` | float | (required) | Tokens-per-chunk ratio above which delivery is considered batched (log-only, does not affect metrics). |

### `websocket` - webSocket settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allowed_origins` | list[string] | (required) | Empty list = all origins allowed (not recommended for production). Protocol-level ping/pong is configured via uvicorn CLI flags. |

### `notifications` - notification system

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | (required) | Master toggle for all notification delivery (push, webhook, in-app). |
| `webhook_timeout` | int | (required) | HTTP timeout for webhook delivery (seconds). |
| `push_ttl` | int | (required) | Push notification TTL (seconds). |
| `events` | object | (required) | Which events trigger notifications (see below). |
| `degraded_tps_tier` | int | (required) | Tier index (0-4) that triggers TPS degradation alerts. |
| `degraded_ttft_tier` | int | (required) | Tier index (0-4) that triggers TTFT degradation alerts. |
| `in_app` | object | (required) | In-app toast notification settings (see below). |
| `rate_limits` | object | (required) | Rate limits for API endpoints (requests per minute, see below). |
| `webhooks` | list | (required) | Webhook channels (set to `[]` when not using webhooks). |

#### `notifications.events`

| Field | Type | Description |
|-------|------|-------------|
| `offline` | bool | Model goes down |
| `recovered` | bool | Model recovers (from offline or degraded) |
| `degraded` | bool | Model degraded (stream error, insufficient output, or critical metrics) |
| `degraded_tps` | bool | TPS drops below chosen tier |
| `recovered_tps` | bool | TPS recovers above chosen tier |
| `degraded_ttft` | bool | TTFT rises above chosen tier |
| `recovered_ttft` | bool | TTFT recovers below chosen tier |
| `provider_changed` | bool | Provider added to or removed from config |
| `model_changed` | bool | Model added to or removed from existing provider |

#### `notifications.in_app`

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | Enables in-app toast notifications |
| `toast_duration_ms` | int | How long toasts stay visible (milliseconds) |
| `history_size` | int | Max notifications kept in in-memory history |
| `retention_days` | int | Days before in-app history entries are deleted |
| `api_response_cap` | int | Max notifications returned by `/api/notifications` |

#### `notifications.rate_limits`

| Field | Type | Description |
|-------|------|-------------|
| `prefs_per_minute` | int | Max `PUT /api/push/preferences` per minute per IP |
| `push_test_per_minute` | int | Max `POST /api/push/test` per minute (global, not per-IP) |
| `subscribe_per_minute` | int | Max `POST /api/push/subscribe` per minute per IP |
| `validate_per_minute` | int | Max `GET /api/push/validate` per minute per IP |
| `client_error_per_minute` | int | Max `POST /api/client-error` per minute per IP |

#### `notifications.webhooks`

Each webhook entry:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Webhook name |
| `url` | string | yes | Webhook URL (receives HTTP POST with JSON payload) |
| `secret` | string | no | HMAC-SHA256 signing secret (`X-ModelWatcher-Signature` header) |
| `filters.providers` | list | no | Provider allowlist (empty = all) |
| `filters.models` | list | no | Model allowlist (empty = all) |
| `events` | object | no | Per-event toggles (e.g. `offline: true`) |

```yaml
webhooks:
  - name: "Slack"
    url: "https://hooks.slack.com/services/..."
    secret: ""
    filters:
      providers: []
      models: []
    events:
      offline: true
      recovered: true
```

### `color_thresholds` - tier system

Defines the five quality tiers and per-metric thresholds. All metric thresholds reference the shared tier palette by position.

#### `color_thresholds.tiers`

Five tiers, best to worst:

| Index | Label | Color class |
|-------|-------|-------------|
| 0 | Excellent | `accent-400` |
| 1 | Good | `success-400` |
| 2 | OK | `warn-400` |
| 3 | Bad | `danger-400` |
| 4 | Critical | `danger-700` |

#### `color_thresholds.<metric>`

Each metric entry:

| Field | Type | Description |
|-------|------|-------------|
| `higher_is_better` | bool | `true` = higher is better (e.g. TPS); `false` = lower is better (e.g. TTFT) |
| `thresholds` | list[5] | Five threshold values matching the five tiers. For `higher_is_better: true`, `>=` each threshold. For `false`, `<` each threshold. |

Metrics with thresholds:

| Metric | Direction | Thresholds | Meaning |
|--------|-----------|------------|---------|
| `uptime` | higher | [99, 95, 85, 50, 0] | Percentage |
| `tps` | higher | [100, 50, 30, 15, 0] | Tokens/sec |
| `ttft` | lower | [1000, 3000, 5000, 10000, 0] | Milliseconds |
| `stall_count` | lower | [1, 2, 3, 15, 0] | Count |
| `raw_p99_itl_ms` | lower | [30, 80, 200, 500, 0] | Milliseconds |
| `raw_median_itl_ms` | lower | [15, 25, 40, 80, 0] | Milliseconds |
| `raw_max_itl_ms` | lower | [200, 500, 1000, 5000, 0] | Milliseconds |
| `effective_itl_tail_ratio` | lower | [2.0, 4.0, 10, 25, 0] | P99/P50 ratio |
| `chunk_token_ratio` | lower | [1.5, 3.0, 5.0, 8.0, 0] | Tokens per SSE delivery |
| `burst_arrival_pct` | lower | [5, 15, 30, 50, 0] | Percentage |
| `chunk_token_cv` | lower | [0.1, 0.3, 0.5, 1.0, 0] | Coefficient of variation |

### `scores` - composite scores

Each score is a weighted average of metric tiers (0-1 normalized).

| Section | Field | Description |
|---------|-------|-------------|
| `consistency.weights` | object | Weighted metrics: `stall_count`, `effective_itl_tail_ratio`, `chunk_token_ratio`, `burst_arrival_pct` |
| `speed.weights` | object | Weighted metrics: `ttft_ms`, `tps` |
| `reliability.availability_weight` | float | Weight for uptime |
| `reliability.quality_weight` | float | Weight for quality (consistency + speed) |

Weights in each `weights` object should sum to 1.0. The reliability `availability_weight + quality_weight` should sum to 1.0.

```yaml
scores:
  consistency:
    weights: { stall_count: 0.35, effective_itl_tail_ratio: 0.30, chunk_token_ratio: 0.20, burst_arrival_pct: 0.15 }
  speed:
    weights: { ttft_ms: 0.50, tps: 0.50 }
  reliability:
    availability_weight: 0.75
    quality_weight: 0.25
```

### `time_ranges` - modal chart time ranges

A list of time range definitions for modal chart views. The client uses these for range pills; the server uses them for bucketed aggregation.

| Field | Type | Description |
|-------|------|-------------|
| `key` | duration | Duration identifier (e.g. `"4h"`, `"7d"`, `"30d"`) |
| `label` | string | Display text shown on range pills |

`seconds` is auto-computed from `key` by `config.py`.

```yaml
time_ranges:
  - { key: "4h", label: "4h" }
  - { key: "12h", label: "12h" }
  - { key: "24h", label: "24h" }
  - { key: "3d", label: "3d" }
  - { key: "7d", label: "7d" }
  - { key: "14d", label: "14d" }
  - { key: "30d", label: "30d" }
  - { key: "90d", label: "90d" }
```

---

## models.yaml

Defines LLM providers and their models. API keys reference environment variables via `${VAR_NAME}` syntax, resolved at load time.

### Provider fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | yes | string | Provider display name (used in model keys: `"ProviderName::model_id"`, must be unique) |
| `api_url` | yes | string | Base API URL. `"anthropic"` in the URL triggers the Anthropic streaming path. `https://` added if missing. |
| `api_key` | yes | string | API key via `${VAR_NAME}` env var (unresolved vars stay as literal `${...}` strings) |
| `models` | yes | list | Non-empty list of model objects |
| `concurrent_models` | no | int | Models tested concurrently within this provider (default: 1 = sequential) |
| `provider_url` | no | string | Override provider homepage URL for favicon/title fetching and frontend link. Auto-derived from `api_url` when omitted. |
| `models_url` | no | string | Override the auto-detected `/models` endpoint for listing available models. |
| `model_info_url` | no | string | Per-model detail endpoint; `{model_id}` is replaced with the model ID. |
| `headers` | no | dict | Custom HTTP headers merged into all API requests to this provider |
| `request_options` | no | object | Control which request parameters are sent (see below) |
| `archived` | no | bool | `true` = archive all models; `false` = explicitly unarchive; omit = preserve existing state |
| `auto_archive` | no | bool | `false` = exempt this provider from auto-archiving |
| `reset_epoch` | no | bool | `true` = force immediate retest of all models after config reload (stripped from YAML after processing) |

### Model fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `id` | yes | string | API model identifier sent to the provider (e.g. `"gpt-4o"`) |
| `name` | no | string | Display name (defaults to `id`) |
| `hf_id` | no | string | Override HuggingFace model ID for metadata enrichment (e.g. `"org/Model-Name"`) |
| `api_url` | no | string | Per-model API URL override. `https://` added if missing. |
| `request_options` | no | object | Per-model overrides merged on top of provider-level `request_options` |
| `archived` | no | bool | Same semantics as provider-level `archived` |
| `auto_archive` | no | bool | `false` = exempt this model from auto-archiving |
| `reset_epoch` | no | bool | `true` = force immediate retest after config reload |

### `request_options`

All optional. Apply to OpenAI-compatible endpoints only (not Anthropic). Can be set at provider level and/or per-model (model overrides merge on top).

| Field | Default | Values | Description |
|-------|---------|--------|-------------|
| `token_param` | `"both"` | `"both"`, `"completion"`, `"legacy"` | `"both"` sends `max_completion_tokens` + `max_tokens`; `"completion"` sends `max_completion_tokens` only (modern APIs); `"legacy"` sends `max_tokens` only (older APIs like DeepSeek) |
| `stream_options` | `true` | bool | `true` includes `stream_options: {include_usage: true}`; `false` omits it |
| `logprobs` | `true` | bool | `true` includes `logprobs: true` in benchmark requests; `false` omits it |

```yaml
providers:
  - name: "Example OpenAI-Compatible"
    api_url: "https://api.example.com/v1"
    api_key: "${EXAMPLE_API_KEY}"
    models:
      - id: "model-id"
        name: "Display Name"

  - name: "Example Anthropic"
    api_url: "https://api.anthropic.com/v1"
    api_key: "${ANTHROPIC_API_KEY}"
    models:
      - id: "claude-sonnet-4-20250514"
        name: "Claude Sonnet 4"
```

---

## audits.yaml

Defines automated compliance test suites that run against monitored LLM endpoints. Currently, the only compatible suite is [SynBad](https://github.com/synthetic-lab/synbad). The suite system is designed to be extensible - additional suite providers will be added in the future.

### Top-level fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `enabled` | yes | bool | Master toggle for audit tests |
| `interval` | yes | duration | How often to run audit tests (seconds or duration string: `6h`, `1d`, etc.) |
| `suites` | yes | object | Dict of suite configurations (keyed by suite name) |

### Suite fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `url` | yes | string | Source/reference URL for the test suite |
| `enabled` | yes | bool | Whether this suite runs |
| `stream` | yes | bool | Test streaming API calls (vs non-streaming) |
| `count` | yes | int | Repetitions per eval (any failure = overall fail) |
| `skip_reasoning` | no | bool\|null | `null` (or omit) = auto-detect from model capabilities; `true` = always skip reasoning evals; `false` = always run reasoning evals |
| `reasoning_effort` | no | string\|null | `null` (or omit) = don't send; `"low"`, `"medium"`, `"high"` |
| `only` | no | string\|null | `null` (or omit) = run all evals; or a specific eval path (e.g. `"evals/reasoning"`) |

Audit test timeout uses `testing.timeout` from `app.yaml` (no per-audit timeout here). Suite version is auto-detected at runtime from the installed npm package.

```yaml
audit:
  enabled: true
  interval: 21600

  suites:
    synbad:
      url: "https://github.com/synthetic-lab/synbad"
      enabled: true
      stream: true
      count: 1
      # skip_reasoning: false
      # reasoning_effort: null
      # only: null
```

---

## Environment variables

### Provider API keys

API keys are referenced by `config/models.yaml` via `${VAR_NAME}` syntax. Set them in your shell environment, process manager, or container env. Unresolved vars stay as literal `${...}` strings (providers with unresolved keys fail their first test with an auth error).

```bash
# .env - add one per provider in config/models.yaml
DS_API_KEY=
LILAC_API_KEY=
NANOGPT_API_KEY=
NW_API_KEY=
OLLAMA_API_KEY=
WAFER_API_KEY=
ZAI_API_KEY=
HYPER_API_KEY=
```

Names must match the `${VAR_NAME}` references in your `models.yaml`. These are examples - rename to match your config.

### Config file path overrides

| Variable | Description |
|----------|-------------|
| `MW_MODELS_YAML` | Override models config file path |
| `MW_APP_YAML` | Override app config file path |
| `MW_AUDITS_YAML` | Override audits config file path |
| `MW_DB_NAME` | Override SQLite database filename (default: `metrics.db`) |
| `MW_BUILT_CSS_PATH` | Override built CSS path (default: `/opt/frontend/tailwind.min.css`) |

### Server bind

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8080` | Bind port |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy IPs for `X-Forwarded-*` headers |

### Diagnostics

| Variable | Description |
|----------|-------------|
| `MW_DISABLE_TESTS` | Set to skip scheduler, model_info, favicons, config_watcher, and BroadcastBatcher tasks (for running diagnostics without triggering tests) |

| Variable | Description |
|----------|-------------|
| `TZ` | Timezone (default: `UTC`). `tzdata` is installed in the Docker image; `state.py` calls `time.tzset()` at import. |

---

## Cross-file relationships

- **Scores weights must sum to 1.0** - Each `scores.<type>.weights` object is a weighted average; `consistency.weights` and `speed.weights` should each sum to 1.0. Reliability `availability_weight + quality_weight` should sum to 1.0.
- **Tier indices reference `color_thresholds.tiers`** - `notifications.degraded_tps_tier` and `degraded_ttft_tier` are 0-indexed tier positions (0=Excellent through 4=Critical) referencing the `color_thresholds.tiers` list.
- **Metric thresholds reference `color_thresholds.tiers`** - Each `color_thresholds.<metric>.thresholds` list has exactly 5 entries, one per tier.
- **`models.yaml` API keys reference env vars** - The `${VAR_NAME}` syntax in `api_key` fields is resolved from the process environment at load time.
- **`audits.yaml` suite `skip_reasoning` auto-detects from `model_info_cache`** - The `thinking` field (populated by probes and model metadata fetch) determines whether reasoning evals run.
- **`testing.timeout` is shared** - Used by benchmark, health, audit, and probe tests. Audit tests have no per-audit timeout field.
- **`metrics.uptime_window` and `recent_history`** - `uptime_window` (seconds) is the rolling window for uptime percentage. `recent_history` (duration) is the in-memory history cap, dynamically sized to cover both test intervals with a 1.2x buffer.
- **`stalls.visible_threshold_ms` feeds `c.stall_visible_ms`** - Used in `compute_stream_metrics()` for ITL classification, jitter-adjusted at runtime.
- **`time_ranges` is a list (not a mapping)** - Unlike other config sections which are mappings, `time_ranges` is a non-empty list of `{key, label}` objects.
- **`reset_epoch` is stripped in-place** - When `reset_epoch: true` is set in `models.yaml`, `config.py` rewrites the file to remove the line after processing. This requires write access to `config/` (the `:rw` volume mount in the compose file).
- **Environment variables are resolved at config load time** - `${VAR_NAME}` references in `models.yaml` are replaced with `os.environ.get(VAR_NAME)` when the config is loaded. If a variable is not set, the literal `${...}` string is kept (the provider will fail with an auth error). Changing env vars requires recreating the Docker container; changing config YAMLs hot-reloads.
