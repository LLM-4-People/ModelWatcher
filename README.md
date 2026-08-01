# ModelWatcher

Real-time dashboard that monitors LLM API endpoints by periodically sending streaming completion requests and benchmarking them.

Live instance: <https://stats.ai4fun.dev>

## Table of contents

- [Features](#features) - What ModelWatcher does
- [Tech stack](#tech-stack) - Backend, frontend, database, real-time
- [Quick start (Docker compose)](#quick-start-docker-compose) - 3-step setup
- [Configuration](#configuration) - Three YAML config files
- [API](#api) - 15 REST endpoints, Swagger UI, ReDoc
- [Architecture](#architecture) - System design overview
- [Development](#development) - Local setup and testing
- [PWA and notifications](#pwa-and-notifications) - Installable app, push notifications
- [License](#license) - MIT

## Features

- **Benchmarks** - Long streaming tests measuring TTFT, TPS, inter-token latency (ITL), stall/hiccup detection, batching, and tail latency
- **Health checks** - Lightweight requests verifying endpoint reachability and measuring TTFT only, running more frequently than benchmarks
- **Audits** - Automated compliance test suites for evaluating model quality. Currently compatible with [SynBad](https://github.com/synthetic-lab/synbad); additional suite providers will be added in the future
- **Probes** - Capability detection tests for vision, tool use, structured output, cache support, and thinking/reasoning
- **WebSocket live updates** - Push-based real-time updates with no polling required for active connections
- **Push notifications** - Web Push API (VAPID) for offline and degraded alerts; webhook delivery with HMAC signing
- **Tier system** - Five quality tiers (Excellent / Good / OK / Bad / Critical) with per-metric thresholds and composite C/S/R scores
- **Charts** - Chart.js time-scale charts with four views (speed, consistency, scores, health) and bucketed server-side aggregation
- **Hot-reload** - Configuration changes (providers, intervals, thresholds) take effect without a server restart
- **PWA** - Installable progressive web app with offline-capable shell, background push notifications, and service-worker-based auto-update

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13, FastAPI, uvicorn (uvloop) |
| Database | SQLite with WAL mode |
| Frontend | Vanilla JS (ES modules, no bundler), HTML |
| Charts | Chart.js (loaded locally, not CDN) |
| Styling | Tailwind CSS v4 (CSS-native config, no `tailwind.config.js`) |
| Real-time | WebSocket (FastAPI `WebSocket`) |

## Quick start (Docker compose)

```bash
# 1. clone the repository
git clone <repo-url> ModelWatcher && cd ModelWatcher

# 2. create environment and config files from the examples
cp .env.example .env.modelwatcher
cp config/app.yaml.example config/app.yaml
cp config/models.yaml.example config/models.yaml
cp config/audits.yaml.example config/audits.yaml
# Edit .env.modelwatcher - add your provider API keys
# Edit config/app.yaml - set app.site_url and app.vapid_email
# Edit config/models.yaml - add your providers and models

# 3. build and run
docker compose -f compose.example.yaml up -d --build
```

The dashboard is available at <http://localhost:8080>. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full deployment guide including nginx reverse proxy configuration.

## Configuration

ModelWatcher uses three YAML config files in `config/`:

| File | Purpose |
|------|---------|
| `app.yaml` | Runtime tuning - intervals, thresholds, notifications, color tiers, scores |
| `models.yaml` | Provider definitions - API URLs, keys, model lists |
| `audits.yaml` | Audit suite definitions - SynBad test configurations |

Example files are provided (`*.example`). See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the complete field reference.

## API

15 REST endpoints across 7 tags. Interactive docs are available at runtime:

- **Swagger UI**: `/api/docs`
- **ReDoc**: `/api/redoc`
- **OpenAPI JSON**: `/api/openapi.json`

See [docs/API.md](docs/API.md) for the full reference. All errors return a uniform `{"error": "message"}` format, including 422 validation errors and 404s.

## Architecture

```
config/*.yaml ──► reload_config() ──► model_registry
                                        │
                          scheduler ────┘│ (per-model due checks)
                              │         │
                    ┌─────────┴─────────┴─────────┐
                    ▼                            ▼
              stream_test()                  run_probe/audit()
              (SSE parsing)                       │
                    │                            │
                    ▼                            ▼
              compute_stream_metrics ──► record_result_async()
                                                │
                              ┌─────────────────┤
                              ▼                 ▼
                        SQLite (WAL)      model_cache (in-memory)
                              │                 │
                              └────────┬────────┘
                                       ▼
                              /api/metrics (ETag cached)
                                       │
                                       ▼
                              WebSocket broadcast ──► Frontend
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system design document covering the four test types, streaming flow, module graphs, and SQLite schema.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for local setup, project structure, testing, and conventions.

## PWA and notifications

ModelWatcher is an installable progressive web app (PWA):

- **Install** - Use the browser's "Install app" / "Add to home screen" option. The app runs full-screen with its own window.
- **Background push** - Push notifications (offline, degraded, recovery alerts) require the app to be installed or the browser tab to be open. The service worker receives push events even when the tab is closed (browser-dependent; some browsers require installation).
- **Auto-update** - The service worker force-reloads all clients on deploy. No stale content is served because the service worker has no fetch handler (it never caches page content).
- **Push prerequisites** - HTTPS is required for web push (the VAPID protocol needs a secure context). Set `app.site_url` and `app.vapid_email` in `config/app.yaml`. VAPID keys are auto-generated on first run and stored in `data/vapid_private.pem` + `data/vapid_public.txt`.

```bash
# Local setup
python3 -m pip install -r requirements.txt -r requirements-dev.txt
npm install
npm run build:css   # build Tailwind CSS

# Run tests
npm test            # 89 pytest tests across 9 files
```

## License

[MIT](LICENSE)
