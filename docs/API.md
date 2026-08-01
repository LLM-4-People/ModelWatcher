# REST API reference

ModelWatcher exposes 15 REST endpoints across 7 tags. Interactive documentation is available at runtime:

- **Swagger UI**: `/api/docs`
- **ReDoc**: `/api/redoc`
- **OpenAPI JSON**: `/api/openapi.json`

## Conventions

- **Base URL**: The host and port the server is bound to (e.g. `http://localhost:8080`).
- **Content type**: All request and response bodies are `application/json`.
- **Error format**: All errors return `{"error": "<message>"}` - including 422 validation errors and 404s. No `{"detail": [...]}` format is used anywhere.
- **Rate limiting**: Several endpoints are rate-limited (configured in `app.yaml` under `notifications.rate_limits`). Rate-limited responses return HTTP 429.

## Table of contents

- [Conventions](#conventions) - Error format, rate limiting, content type
- [Metrics](#metrics)
  - [GET /api/metrics](#get-apimetrics) - Model metrics, chart data, and history
- [Providers](#providers)
  - [GET /api/providers](#get-apiproviders) - Provider and model registry listings
- [Config](#config)
  - [GET /api/config](#get-apiconfig) - Runtime configuration (thresholds, labels, intervals)
  - [GET /api/deploy-version](#get-apideploy-version) - Deployment version hash
  - [POST /api/client-error](#post-apiclient-error) - Client-side error reporting
- [Audit](#audit)
  - [GET /api/audit](#get-apiaudit) - Audit test results
- [Model info](#model-info)
  - [GET /api/model-info](#get-apimodel-info) - Model capability and metadata
- [Notifications](#notifications)
  - [GET /api/notifications](#get-apinotifications) - Notification config and history
  - [GET /api/vapid-key](#get-apivapid-key) - VAPID public key for web push
  - [POST /api/push/subscribe](#post-apipushsubscribe) - Subscribe to web push
  - [DELETE /api/push/subscribe](#delete-apipushsubscribe) - Unsubscribe from web push
  - [PUT /api/push/preferences](#put-apipushpreferences) - Update notification preferences
  - [POST /api/push/test](#post-apipushtest) - Send a test push notification
  - [GET /api/push/validate](#get-apipushvalidate) - Validate push endpoint registration
- [Health](#health)
  - [GET /health](#get-health) - Service health check
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
    "scores": {"consistency": 0.85, "speed": 0.72, "reliability": 0.91},
    "trends": {"tps": "up", "ttft": "flat"}
  }
}
```

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

Get runtime configuration. Returns merged config: app name, intervals, audit/probe settings, color thresholds, and time ranges.

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
  "last_run_ago_seconds": 13,
  "next_run_in_seconds": 1,
  "color_thresholds": {"tiers": [...], "uptime": {...}, "tps": {...}},
  "time_ranges": [{"key": "4h", "label": "4h"}, ...]
}
```

### GET /api/deploy-version

Get the deployment version (mtime of most recently modified static file). The frontend polls this every 60s to detect deployments and trigger a reload.

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

Service health check. Returns only `{"status": "healthy" | "degraded"}` - server-side details are stripped to prevent information leakage. Used by the Docker `HEALTHCHECK`.

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

## WebSocket

In addition to the REST API, a WebSocket endpoint is available at `/ws` for real-time push updates. See [ARCHITECTURE.md](ARCHITECTURE.md) for the message types broadcast by the server.
