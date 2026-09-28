# REST API reference

ModelWatcher exposes 16 REST endpoints across 7 tags. Interactive documentation is available at runtime:

- **Swagger UI**: `/api/docs`
- **ReDoc**: `/api/redoc`
- **OpenAPI JSON**: `/api/openapi.json`

## Conventions

- **Base URL**: The host and port the server is bound to (e.g. `http://localhost:8080`).
- **Content type**: All request and response bodies are `application/json`.
- **Error format**: All errors return `{"error": "<message>"}` - including 422 validation errors and 404s. No `{"detail": [...]}` format is used anywhere.
- **Host check**: requests whose `Host` header does not name the server (an IP address, `localhost`, the host of `app.site_url`, or an entry of `server.allowed_hosts`) get `400 {"error": "Invalid host header"}`; a WebSocket handshake is refused.
- **Rate limiting**: Several endpoints are rate-limited (configured in `app.yaml` under `notifications.rate_limits`). Rate-limited responses return HTTP 429.

## Table of contents

- [Conventions](#conventions) - Error format, rate limiting, content type
- [Metrics](#metrics) - Model status, scores, chart data, history
  - [GET /api/metrics](#get-apimetrics) - Model metrics, chart data, and history
- [Providers](#providers) - Provider and model registry
  - [GET /api/providers](#get-apiproviders) - Provider and model registry listings
- [Config](#config) - Runtime configuration, deploy version, client errors
  - [GET /api/config](#get-apiconfig) - Runtime configuration (thresholds, labels, intervals)
  - [GET /api/deploy-version](#get-apideploy-version) - Deployment version hash
  - [POST /api/client-error](#post-apiclient-error) - Client-side error reporting
- [Audit](#audit) - SynBad audit test results
  - [GET /api/audit](#get-apiaudit) - Audit test results
- [Model info](#model-info) - Model capability and metadata
  - [GET /api/model-info](#get-apimodel-info) - Model capability and metadata
- [Notifications](#notifications) - Push subscriptions, preferences, history
  - [GET /api/notifications](#get-apinotifications) - Notification config and history
  - [GET /api/vapid-key](#get-apivapid-key) - VAPID public key for web push
  - [POST /api/push/subscribe](#post-apipushsubscribe) - Subscribe to web push
  - [DELETE /api/push/subscribe](#delete-apipushsubscribe) - Unsubscribe from web push
  - [PUT /api/push/preferences](#put-apipushpreferences) - Update notification preferences
  - [POST /api/push/test](#post-apipushtest) - Send a test push notification
  - [GET /api/push/validate](#get-apipushvalidate) - Validate push endpoint registration
- [Health](#health) - Readiness and liveness checks
  - [GET /health](#get-health) - Readiness check
  - [GET /health/live](#get-healthlive) - Liveness check
- [WebSocket](#websocket) - Real-time update channel

---

## Metrics

### GET /api/metrics

Get model metrics and chart data. Operates in two modes:

- **Collection mode** (no `model` param): Returns all model metrics. ETag-cached, rebuilt from `model_cache` when dirty.
- **Single-model mode** (`model` param): Returns per-model data. `type` param selects between card chart data, modal chart data, or history rows.

| Parameter | Type | Required | Default | Enum | Description |
|-----------|------|----------|---------|------|-------------|
| `model` | string | no | - | - | Model key (`Provider::model_id`). Triggers single-model mode. Max 256 chars. |
| `type` | string | no | - | `card`, `modal`, `history` | Response type (requires `model`). |
| `since` | number | no | - | - | Unix epoch (float) - only results at or after this timestamp. |
| `until` | number | no | - | - | Unix epoch (float) - only results before this timestamp. |
| `before` | number | no | - | - | Unix epoch (float) - history pagination cursor (return rows before this timestamp). |
| `buckets` | integer | no | 20 | - | Number of chart buckets for `card`/`modal` types (single-model mode). |
| `test_type` | string | no | `benchmark` | `benchmark`, `health` | Filter chart data by test type. |
| `view` | string | no | `speed` | `speed`, `consistency`, `scores`, `health` | Chart view for `card`/`modal` types. |
| `providers` | string | no | - | - | Comma-separated provider names to filter collection-mode results. Max 512 chars. |
| `detail_providers` | string | no | - | - | Comma-separated provider names for per-model detail filtering (collection mode). Use empty value for summaries only. |
| `card_buckets` | string | no | - | `1` | Set to `1` to include pre-computed card chart buckets in collection-mode response. |
| `limit` | integer | no | 50 | - | Maximum history rows to return (`type=history` only). |
| `sort` | string | no | - | - | Comma-separated sort keys for `type=history`. Valid keys: `time`, `ttft`, `tps`, `stalls`, `p99`, `batch`, `tail`. Prefix with `-` for descending. |

**Responses**:
- `200`: Collection mode returns an object keyed by model key; single-model mode returns a single model object or history array.
- `400`: Invalid parameter (bad type, bad time range, `type` without `model`).
- `422`: Validation error.

**Example - collection mode**:
```bash
curl http://localhost:8080/api/metrics
```
```json
{
  "DeepSeek::deepseek-v4-flash": {
    "status": "online",
    "testing": false,
    "uptime_pct": 100.0,
    "last_benchmark_epoch": 1785545522.8,
    "last_success_epoch": 1785545522.8,
    "data_start_epoch": 1784000000.0,
    "scores": {"consistency": 85.2, "speed": 72.0, "reliability": 91.3},
    "trends": {
      "since_ts": 1785372722.8,
      "tps": {"direction": "improving", "change": 6.4, "delta": 6.4, "unit": "t/s", "data_points": 9},
      "ttft_ms": {"direction": "stable", "change": 120.0, "delta": -120.0, "unit": "ms", "data_points": 9}
    }
  }
}
```

Scores are 0-100. Each trend compares the median of the last `metrics.trend_window` with the median before it: `delta` is signed with positive meaning better (for lower-is-better metrics such as TTFT a drop is positive), `change` is its size, `data_points` the results in the recent window, and a move smaller than the metric's `metrics.trend_deadbands` entry is `stable`. A metric with fewer than `metrics.min_data_points_trend` recent results has no entry. `since_ts` is the time of the oldest result the trends cover. Provider summaries carry the same entries, taken as the median of their models' deltas, plus `models`, the number of models with a trend.

**Example - single-model history**:
```bash
curl "http://localhost:8080/api/metrics?model=DeepSeek::deepseek-v4-flash&type=history&limit=10"
```

---

## Providers

### GET /api/providers

List providers and their models. Logos are base64 data URIs (e.g. `data:image/png;base64,...`).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `providers` | string | no | - | Comma-separated provider name filter. Max 512 chars. |

**Responses**:
- `200`: Object keyed by provider name, each with `models` (list), `api_url` (string\|null), `logo` (data URI\|null), `title` (string\|null).
- `422`: Validation error.

**Example**:
```bash
curl http://localhost:8080/api/providers
```
```json
{
  "DeepSeek": {
    "models": [
      {"id": "DeepSeek::deepseek-v4-flash", "provider": "DeepSeek", "model_id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"}
    ],
    "api_url": "https://deepseek.com",
    "logo": "data:image/png;base64,...",
    "title": "DeepSeek"
  }
}
```

---

## Config

### GET /api/config

Get runtime configuration. Returns merged config: app name, intervals, audit/probe settings, whether the scheduler runs, scoring rules, color thresholds, time ranges, the browser settings (`ui`) and every label table. The response is cached until the config reloads or the scheduler starts or stops.

**Responses**:
- `200`: Config object (see below).

**Example**:
```bash
curl http://localhost:8080/api/config
```
```json
{
  "app_name": "ModelWatcher",
  "benchmark_interval_seconds": 7200,
  "health_interval_seconds": 300,
  "health_enabled": true,
  "audit_enabled": true,
  "audit_interval_seconds": 21600,
  "audit_suites": {"synbad": {"enabled": true, "url": "https://github.com/synthetic-lab/synbad"}},
  "probe_enabled": true,
  "probe_interval_seconds": 86400,
  "scheduler": {"running": true, "paused": false},
  "degraded_critical_metrics": 2,
  "stalls": {"visible_threshold_ms": 500, "hiccup_multiplier": 3},
  "scores": {"consistency": {...}, "speed": {...}, "reliability": {"availability_weight": 0.75, "quality_weight": 0.25}},
  "color_thresholds": {"tiers": [...], "uptime": {...}, "tps": {...}, "scores": {...}},
  "time_ranges": [{"key": "4h", "label": "4h"}, ...],
  "ui": {"check_line_refresh": 30, "metrics_poll": 30, "deploy_poll": 60, "toast_duration_ms": 5000, ...},
  "status_values": ["online", "degraded", "error", "unknown"],
  "status_labels": {"online": "Online", "degraded": "Degraded", "error": "Offline", "unknown": "Untested"},
  "test_types": ["benchmark", "health", "audit", "probe"],
  "test_type_labels": {"health": {"full": "Health", "short": "HC"}, "benchmark": {"full": "Bench", "short": "BM"}, ...},
  "chart_views": ["speed", "consistency", "scores", "health"],
  "chart_view_labels": {"speed": "TPS + TTFT", ...},
  "capabilities": [{"key": "thinking", "label": "Thinking", "desc": "chain-of-thought reasoning"}, ...],
  "event_labels": {"offline": "Offline", ...},
  "metric_labels": {"tps": "TPS", ...},
  "metric_short_labels": {"stall_count": "Stalls", ...}
}
```

`scheduler.running` says whether tests run; `paused` is true when the server started with `MW_DISABLE_TESTS`. `ui` holds the `app.yaml` `ui` section plus the in-app toast duration and history size; the page also gets it in its bootstrap. The label and value lists come from `backend/state.py` and are the only copy the frontend uses.

### GET /api/deploy-version

Get the deployment version: the newest mtime of the frontend files and the built stylesheet, read on every call, so an edit or a rebuilt stylesheet shows without any page load. The frontend polls it every `ui.deploy_poll` seconds and reloads when it changes.

**Responses**:
- `200`: `{"version": <float>}`

**Example**:
```bash
curl http://localhost:8080/api/deploy-version
```
```json
{"version": 1785545522.8961272}
```

### POST /api/client-error

Report a client-side error (from `window.onerror` / `unhandledrejection`). Rate-limited (default 10/min per IP).

**Request body** - `ClientErrorBody`:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `message` | string | yes | - | Error message |
| `source` | string | no | `""` | Source script URL |
| `line` | integer\|null | no | null | Line number |
| `col` | integer\|null | no | null | Column number |
| `stack` | string | no | `""` | Stack trace |
| `type` | string | no | `""` | Error type: `"error"` or `"rejection"` |
| `url` | string | no | `""` | Page URL |
| `ua` | string | no | `""` | User agent string |

**Responses**:
- `200`: Success (acknowledged).
- `429`: Rate limited.
- `422`: Validation error.

**Example**:
```bash
curl -X POST http://localhost:8080/api/client-error \
  -H "Content-Type: application/json" \
  -d '{"message": "Uncaught TypeError", "type": "error", "url": "/"}'
```

---

## Audit

### GET /api/audit

Get audit test results (SynBad-based compliance tests).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `model` | string | no | - | Model key (`Provider::model_id`). Triggers single-model mode with history. Max 256 chars. |
| `limit` | integer | no | 50 | Maximum history rows to return (single-model mode). |
| `since` | number | no | - | Unix epoch (float) - only return results at or after this timestamp. |

**Responses**:
- `200`: Without `model`: all models' latest results. With `model`: latest result + history for that model.
- `400`: Invalid model key.
- `422`: Validation error.

**Example**:
```bash
curl "http://localhost:8080/api/audit?model=DeepSeek::deepseek-v4-flash&limit=10"
```

---

## Model info

### GET /api/model-info

Get model capability and metadata (populated by probes and provider API fetches).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `model` | string | no | - | Model key (`Provider::model_id`). Triggers single-model detail mode. Max 256 chars. |
| `history` | integer | no | 0 | Set to `1` to include probe history (single-model mode). |

**Responses**:
- `200`: Model metadata (context window, pricing, capabilities, etc.) or a collection of all models' metadata.
- `400`: Invalid model key.
- `404`: Model not found.
- `422`: Validation error.

**Example**:
```bash
curl "http://localhost:8080/api/model-info?model=DeepSeek::deepseek-v4-flash"
```

---

## Notifications

### GET /api/notifications

Get notification config and in-app history. Server-side config is returned in a stripped form (only `app_name`, `enabled`, and `in_app` settings) to prevent information leakage.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `since` | string | no | - | ISO 8601 datetime string - only return notifications after this timestamp. |
| `client_id` | string | no | - | Client identifier. Scopes history to notifications after the client's subscription `created_at`. Max 64 chars. |

**Responses**:
- `200`: Config + history array.
- `400`: Invalid `client_id` or `since` parameter.
- `422`: Validation error.

**Example**:
```bash
curl "http://localhost:8080/api/notifications"
```
```json
{
  "app_name": "ModelWatcher",
  "enabled": true,
  "in_app": {"enabled": true, "toast_duration_ms": 5000, "history_size": 50},
  "history": [
    {
      "id": "n1",
      "timestamp": "2026-08-01T00:55:25.588438+00:00",
      "model_key": "DeepSeek::deepseek-v4-flash",
      "event_type": "offline",
      "message": "DeepSeek - DeepSeek V4 Flash - Offline",
      "body": "Before: Online → Current: Offline. HTTP 503: Service unavailable",
      "prev_status": "online",
      "new_status": "error",
      "error": "HTTP 503: Service unavailable"
    }
  ]
}
```

### GET /api/vapid-key

Get the VAPID public key for web push subscriptions.

**Responses**:
- `200`: `{"public_key": "<base64url>"}`
- `503`: Push not available (web push not installed/configured).

**Example**:
```bash
curl http://localhost:8080/api/vapid-key
```
```json
{"public_key": "BG93qK9Rzmts84-mGuIC1gmLo1d4So_GDZO-6aAv3JAcQYpqC3e9yVmNgPqp190t4qny5wIqQr8fHUQUzPmb2Lk"}
```

### POST /api/push/subscribe

Subscribe to web push notifications. Rate-limited (default 20/min per IP). Returns 409 if the endpoint is already registered to a different `client_id`.

**Request body** - `PushSubscribeBody`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `endpoint` | string | yes | Push endpoint URL from `PushSubscription` |
| `keys.p256dh` | string | yes | P-256 public key (base64url, 65 bytes, validated on the P-256 curve) |
| `keys.auth` | string | yes | Auth secret (base64url, 16 bytes) |
| `client_id` | string | yes | Client identifier (from localStorage) |
| `prefs` | object\|null | no | Notification preferences dict (validated server-side via `sanitize_prefs`) |

**Responses**:
- `200`: Subscribed/updated.
- `400`: Invalid endpoint, keys, or client_id.
- `409`: Endpoint already registered to another client.
- `429`: Rate limited.
- `503`: Push not available.
- `422`: Validation error.

**Example**:
```bash
curl -X POST http://localhost:8080/api/push/subscribe \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "https://fcm.googleapis.com/fcm/send/...",
    "keys": {"p256dh": "...", "auth": "..."},
    "client_id": "c_a1b2c3d4",
    "prefs": {"enabled": true, "offline": true}
  }'
```

### DELETE /api/push/subscribe

Unsubscribe from web push. When `client_id` is provided, deletes ALL subscriptions for that client (bulk unsubscribe).

**Request body** - `PushUnsubscribeBody`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `endpoint` | string\|null | no | Push endpoint URL to remove |
| `client_id` | string\|null | no | Client identifier - if present, removes ALL subscriptions for this client |

**Responses**:
- `200`: Unsubscribed.
- `400`: Missing both `endpoint` and `client_id`.
- `422`: Validation error.

**Example**:
```bash
curl -X DELETE http://localhost:8080/api/push/subscribe \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "https://fcm.googleapis.com/fcm/send/..."}'
```

### PUT /api/push/preferences

Update notification preferences. Rate-limited (12/min per IP).

**Request body** - `PushUpdatePrefsBody`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prefs` | object | yes | Notification preferences (validated server-side via `sanitize_prefs`) |
| `client_id` | string\|null | no | Client identifier - if present, updates ALL subscriptions for this client |
| `endpoint` | string\|null | no | Push endpoint URL (used if `client_id` is absent) |

**Responses**:
- `200`: Preferences updated.
- `400`: Invalid prefs or missing endpoint/client_id.
- `404`: Unknown subscription.
- `429`: Rate limited.
- `422`: Validation error.

**Example**:
```bash
curl -X PUT http://localhost:8080/api/push/preferences \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "https://...", "prefs": {"enabled": true, "offline": true, "degraded_tps_tier": 2}}'
```

### POST /api/push/test

Send a test push notification. Rate-limited (6/min global).

**Request body** - `PushTestBody`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `endpoint` | string | yes | Push endpoint URL to send the test to |

**Responses**:
- `200`: Test sent.
- `400`: No matching push subscription.
- `429`: Rate limited.
- `502`: Push delivery failed.
- `503`: Push not available.
- `422`: Validation error.

**Example**:
```bash
curl -X POST http://localhost:8080/api/push/test \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "https://fcm.googleapis.com/fcm/send/..."}'
```

### GET /api/push/validate

Validate push endpoint registration. Requires both `client_id` and `endpoint` - returns `{"valid": false}` without `client_id` (blocks endpoint enumeration).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `endpoint` | string | no | `""` | Push endpoint URL (from the browser's `PushSubscription`). Max 2048 chars. |
| `client_id` | string | no | `""` | Client identifier (generated by the frontend, stored in localStorage). Max 64 chars. |

**Responses**:
- `200`: `{"valid": true\|false}` - `true` only if subscription exists AND `client_id` matches.
- `429`: Rate limited.
- `422`: Validation error.

**Example**:
```bash
curl "http://localhost:8080/api/push/validate?endpoint=https://...&client_id=c_a1b2c3d4"
```
```json
{"valid": true}
```

---

## Health

### GET /health

Readiness check. Returns only `{"status": "healthy" | "degraded"}` - server-side details are stripped to prevent information leakage. Used by the Docker `HEALTHCHECK`.

**Responses**:
- `200`: `{"status": "healthy"}` - scheduler alive, models exist, at least one online or degraded.
- `503`: Service degraded or unhealthy.

**Example**:
```bash
curl http://localhost:8080/health
```
```json
{"status": "healthy"}
```

---

### GET /health/live

Liveness check: `200 {"status": "alive"}` whenever the server process answers, whatever the scheduler or the models are doing. The dashboard probes it while the server looks unreachable, so a total model outage (when `/health` answers 503) never shows as "Server unreachable".

**Example**:
```bash
curl http://localhost:8080/health/live
```
```json
{"status": "alive"}
```

---

## WebSocket

In addition to the REST API, a WebSocket endpoint is available at `/ws` for real-time push updates. The server broadcasts `testing`, `result`, `result_batch`, `audit_result`, `probe_result`, `notification`, `config_updated` and `server_shutdown` messages (see [ARCHITECTURE.md](ARCHITECTURE.md) for the data flow behind them), plus the connection frames below.

- **Origin check**: a page served by this server (its `Origin` host equals the `Host` header) is always accepted; other origins must be listed in `websocket.allowed_origins` (empty list = all).
- **`hello`**: the first frame of every accepted socket, `{"type": "hello", "config": {...}, "scheduler": {"running": ..., "paused": ...}}`. The client treats it as the proof of acceptance and closes a socket that sends none within `stale_after` seconds (counted as a failed attempt). `config` is the connection policy the page also gets in its bootstrap as `window.__MW_BOOT__.conn`: `ws_path`, `liveness_path`, `stale_after`, `reconnect`, `unreachable` and `close_codes`. `scheduler` is the same object as in `/api/config`.
- **`result_batch`**: the results of one flush window, `{"type": "result_batch", "results": {"<model key>": {"type": "result", "model": ..., "record": {...}, "test_type": ..., "status": ..., "uptime_pct": ..., "scores": {...}, "trends": {...}}}}`. After a final (not retried) benchmark the entry also carries `card_buckets`, that model's card chart buckets in the `/api/metrics?card_buckets=1` shape, so the page redraws that card without refetching.
- **`heartbeat`**: `{"type": "heartbeat"}` every `websocket.heartbeat_interval` seconds, even with `MW_DISABLE_TESTS`. The client reconnects after `websocket.stale_after` seconds without any frame.
- **Close codes**: 1008 origin not allowed (permanent; the client retries at `reconnect.max_delay`), 1013 connection limit reached (transient), 1012 server restarting, 1009 message over `websocket.max_message_bytes`, 1011 handler error, 4000 client-side stale close. The client sends `{"type": "sync_prefs", "prefs": {...}}`, at most `websocket.sync_prefs_per_minute` times per minute.
