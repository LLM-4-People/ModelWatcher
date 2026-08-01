# Deployment guide

This guide covers deploying ModelWatcher with Docker Compose, including nginx reverse proxy configuration.

## Table of contents

- [Prerequisites](#prerequisites)
- [Docker compose setup](#docker-compose-setup)
  - [1. clone the repository](#1-clone-the-repository) - Get the code
  - [2. create environment and config files](#2-create-environment-and-config-files) - Copy examples and edit
  - [3. configure compose](#3-configure-compose) - Set paths and ports
  - [4. build and run](#4-build-and-run) - Start the container- [Volume mounts](#volume-mounts)
- [Environment variables](#environment-variables)
  - [Changing environment variables](#changing-environment-variables) - Requires container recreate
  - [Adding a new provider (operational workflow)](#adding-a-new-provider-operational-workflow) - Env var, recreate, then edit YAML
  - [Server bind](#server-bind) - HOST, PORT, FORWARDED_ALLOW_IPS, TZ, restart
  - [Provider API keys](#provider-api-keys) - Env var references via ${VAR_NAME} syntax
  - [Diagnostics](#diagnostics) - MW_DISABLE_TESTS
  - [Config overrides](#config-overrides) - MW_MODELS_YAML, MW_APP_YAML, etc.
- [Nginx reverse proxy](#nginx-reverse-proxy)
- [Health check](#health-check)
- [PWA and push notifications](#pwa-and-push-notifications)
  - [VAPID keys](#vapid-keys) - Auto-generated on first run
  - [HTTPS requirement](#https-requirement) - Required for web push
  - [Installing the PWA](#installing-the-pwa) - Browser install and background push
  - [Push notification flow](#push-notification-flow) - 7-step subscription lifecycle
- [Updating](#updating)
- [Scaling considerations](#scaling-considerations)

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (v24+ recommended)
- [Docker Compose](https://docs.docker.com/compose/install/) (v2+ recommended)

## Docker compose setup

### 1. clone the repository

```bash
git clone <repo-url> ModelWatcher && cd ModelWatcher
```

### 2. create environment and config files

Copy the example files and edit them for your deployment:

```bash
cp .env.example .env.modelwatcher
cp config/app.yaml.example config/app.yaml
cp config/models.yaml.example config/models.yaml
cp config/audits.yaml.example config/audits.yaml
```

**Edit `.env.modelwatcher`** - add your provider API keys:

```bash
DS_API_KEY=sk-your-deepseek-key
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key
# ... one per provider referenced in models.yaml
```

**Edit `config/app.yaml`** - set your site URL and VAPID email:

```yaml
app:
  site_url: "https://your-domain.example.com"
  vapid_email: "mailto:you@your-domain.example.com"
```

**Edit `config/models.yaml`** - add your providers and models (see [CONFIGURATION.md](CONFIGURATION.md#modelsyaml)).

### 3. configure compose

Copy the example compose file and adjust the paths:

```bash
cp compose.example.yaml compose.yaml
```

Edit `compose.yaml` - replace `/path/to/ModelWatcher` with your actual clone path:

```yaml
services:
  modelwatcher:
    build: "/path/to/ModelWatcher/"
    container_name: 'modelwatcher'
    env_file: .env.modelwatcher
    volumes:
      - "/path/to/ModelWatcher:/app:ro"
      - "/path/to/ModelWatcher/config:/app/config:rw"
      - "/path/to/ModelWatcher/data:/app/data:rw"
    ports:
      - '127.0.0.1:${PORT}:${PORT}'
    restart: "${restart}"
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"
```

All runtime values (`PORT`, `restart`, `TZ`, `FORWARDED_ALLOW_IPS`, API keys) come from `.env.modelwatcher`, not from inline `environment:` blocks.

### 4. build and run

```bash
docker compose up -d --build
```

The dashboard is available at `http://localhost:8080`.

## Volume mounts

| Mount | Mode | Purpose |
|-------|------|---------|
| `/path/to/ModelWatcher:/app:ro` | read-only | Application code (backend, frontend, etc.). uvicorn `--reload` watches via inotify and works on read-only mounts. |
| `/path/to/ModelWatcher/config:/app/config:rw` | read-write | Config files. `config.py` strips `reset_epoch: true` from `models.yaml` in-place after processing. |
| `/path/to/ModelWatcher/data:/app/data:rw` | read-write | SQLite database (`metrics.db` + WAL files), VAPID keys, cached favicons. |

The `data/` directory is created automatically if it does not exist.

## Environment variables

### Changing environment variables

Environment variables (`.env.modelwatcher`) are read by Docker Compose at container creation time, not at runtime. After editing `.env.modelwatcher`, you **must recreate the container**:

```bash
docker compose -f compose.yaml up -d --build
```

The `--reload` flag only watches `backend/` source files and `config/*.yaml` for changes. It does not re-read environment variables.

### Adding a new provider (operational workflow)

1. **Add the API key to `.env.modelwatcher`**:
   ```bash
   NEW_PROVIDER_API_KEY=sk-your-key
   ```
2. **Recreate the container** so the new env var is available:
   ```bash
   docker compose -f compose.yaml up -d --build
   ```
3. **Edit `config/models.yaml`** - add the provider entry referencing `${NEW_PROVIDER_API_KEY}`:
   ```yaml
   providers:
     - name: "NewProvider"
       api_url: "https://api.newprovider.com/v1"
       api_key: "${NEW_PROVIDER_API_KEY}"
       models:
         - id: "model-name"
   ```
4. **Save** - the config watcher hot-reloads automatically. The new provider's models appear immediately and are tested on the next scheduler cycle. No restart needed.

> The order matters: the env var must be available before the config watcher resolves `${VAR_NAME}` references. If you edit `models.yaml` before recreating the container, the `${NEW_PROVIDER_API_KEY}` reference stays as a literal string until the next container recreation.

### Server bind

| Variable | Example | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind host (container-internal; always `0.0.0.0` for Docker) |
| `PORT` | `8080` | Bind port |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy IPs for `X-Forwarded-*` headers. Set to your nginx proxy IP when behind a reverse proxy. |
| `TZ` | `UTC` | Timezone. `tzdata` is installed in the Docker image. Set to your local timezone (e.g. `America/Toronto`) for correct log timestamps. |
| `restart` | `unless-stopped` | Docker Compose restart policy. Must be set in `.env.modelwatcher` (no fallback in compose file). |

### Provider API keys

Set one per provider referenced in `config/models.yaml` via `${VAR_NAME}` syntax. See [.env.example](../.env.example) for the full list. Names must match the references in your `models.yaml`.

### Diagnostics

| Variable | Description |
|----------|-------------|
| `MW_DISABLE_TESTS` | Set to skip scheduler, model_info, favicons, config_watcher, and BroadcastBatcher tasks. For running diagnostics without triggering tests. |

### Config overrides

| Variable | Description |
|----------|-------------|
| `MW_MODELS_YAML` | Override models config file path |
| `MW_APP_YAML` | Override app config file path |
| `MW_AUDITS_YAML` | Override audits config file path |
| `MW_DB_NAME` | Override SQLite database filename (example: `metrics.db`) |
| `MW_BUILT_CSS_PATH` | Override built CSS path (example: `/opt/frontend/tailwind.min.css`) |
| `TZ` | Timezone (set to `UTC` in the Dockerfile). `tzdata` is installed in the image. |

## Nginx reverse proxy

Example nginx configuration for HTTPS termination and WebSocket proxying:

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # ModelWatcher backend
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";

        # HTTP/1.1 for chunked transfer and keep-alive
        proxy_http_version 1.1;
    }

    # WebSocket upgrade
    location /ws {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket timeout (should exceed uvicorn --ws-ping-timeout of 90s)
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

Set `FORWARDED_ALLOW_IPS` to your nginx server's IP (e.g. `127.0.0.1` if nginx is on the same host) so uvicorn trusts the `X-Forwarded-*` headers.

## Health check

The Dockerfile includes a `HEALTHCHECK` that polls the `/health` endpoint:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8080}/health').read()" || exit 1
```

The `/health` endpoint returns `{"status": "healthy"}` (200) when the scheduler is alive, models exist, and at least one is online or degraded. Otherwise it returns 503.

Check container health:

```bash
docker inspect --format='{{.State.Health.Status}}' modelwatcher
```

## PWA and push notifications

ModelWatcher is an installable progressive web app (PWA). Push notifications require HTTPS and a valid VAPID configuration.

### VAPID keys

VAPID keys are auto-generated on first startup and stored in `data/vapid_private.pem` and `data/vapid_public.txt`. No manual setup needed.

If you need to regenerate them (e.g., after clearing `data/`), delete both files and restart the container.

### HTTPS requirement

Web push requires a secure context (HTTPS). The nginx reverse proxy configuration above handles TLS termination. If running without nginx (local development), `http://localhost` is also a secure context.

### Installing the PWA

Users can install the app via the browser's "Install app" / "Add to home screen" option. Once installed:
- The app runs in its own window (not a browser tab).
- Push notifications are delivered even when the app window is closed (browser-dependent).
- The service worker auto-updates the app on each deploy (force-reload with no stale content).

### Push notification flow

1. User opens the dashboard and clicks the notification bell.
2. Browser prompts for notification permission.
3. Browser creates a push subscription (requires installed PWA or open tab).
4. Subscription is sent to `POST /api/push/subscribe` with client ID and prefs.
5. Server stores the subscription in SQLite (`push_subscriptions` table).
6. On status change (offline, degraded, recovered), server sends push via VAPID.
7. Service worker receives the push and displays a system notification.

## Updating

The Docker image runs uvicorn with `--reload`, which watches the `backend/` directory for file changes. To update:

```bash
cd /path/to/ModelWatcher
git pull origin main
# uvicorn hot-reloads on backend/ file changes - no restart needed
# For frontend CSS changes, rebuild the CSS:
#   Npm run build:css
# For frontend jS/HTML changes, the browser auto-reloads via service worker / deploy-version polling
```

To rebuild the Docker image (e.g. after dependency changes in `requirements.txt` or `package.json`):

```bash
git pull origin main
docker compose up -d --build
```

## Scaling considerations

ModelWatcher is designed as a **single-process** application:

- **uvloop** - The event loop uses `uvloop` for high-performance async I/O.
- **`max_concurrent_tests`** (example: 50) - Limits total concurrent test tasks. This prevents overwhelming the httpx connection pool and remote provider APIs. The pool size is `server.http_pool_max` (example: 20).
- **Per-provider concurrency** - `concurrent_models` (example: 1) limits concurrent tests per provider. Most providers should be tested sequentially to avoid rate-limiting.
- **Connection limits** - `server.max_connections` (example: 10000) caps concurrent HTTP connections as a safety net; nginx should be the primary limiter.
- **Memory** - The `model_cache` holds all models in memory, including `recent_history` (capped to cover both test intervals). For large fleets, the 512M memory limit in the compose example may need adjustment.

For monitoring large numbers of models (>100), consider:
- Increasing `server.http_pool_max` and `testing.max_concurrent_tests` if your providers can handle the load.
- Increasing the container memory limit.
- Tuning `metrics.recent_history` (shorter = less memory per model).
