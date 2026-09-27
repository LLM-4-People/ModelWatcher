# Security policy

## Supported versions

We provide security fixes for the latest released version only. Older versions may not receive backports.

| Version | Supported          |
|---------|--------------------|
| Latest  | :white_check_mark: |
| Older   | :x:                |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities privately via [GitHub's private vulnerability reporting](https://github.com/LLM-4-People/ModelWatcher/security/advisories/new). This allows us to fix the issue before it becomes public knowledge.

Include the following in your report:

- Description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected component(s) (backend, frontend, WebSocket, push notifications, etc.)
- Suggested fix, if you have one

## Response timeline

- **Acknowledgment**: within 7 days
- **Assessment**: within 30 days
- **Fix or mitigation**: depends on severity and complexity

We will keep you informed of our progress and coordinate a disclosure timeline with you.

## Security considerations

ModelWatcher is a self-hosted monitoring dashboard. When deploying:

- **API keys** are stored in `config/models.yaml` or environment variables - never commit them to version control. The `config/*.yaml` files are gitignored by default.
- **HTTPS** is required for web push notifications (VAPID protocol). Use a reverse proxy (nginx, Caddy, Traefik) with TLS.
- **VAPID keys** are auto-generated on first run in `data/vapid_private.pem` and `data/vapid_public.txt`. Back these up - if lost, all existing push subscriptions become invalid.
- **SQLite database** (`data/metrics.db`) contains test results and push subscription endpoints. Protect the `data/` directory with appropriate filesystem permissions.
- **Rate limiting** is applied to push subscribe, validate, test, and client error endpoints. Adjust in `config/app.yaml` under `notifications.rate_limits`.
- **Request body size** is limited to 1MB by `MAX_REQUEST_BODY_BYTES` in `backend/state.py`.
- **Host header** is checked for every request and WebSocket: IP addresses, `localhost`, the host of `app.site_url` and the names in `server.allowed_hosts` are served, anything else gets 400 (a WebSocket is refused). This stops DNS-rebinding pages, which reach the server under the attacker's host name.
- **WebSocket connections** are origin-checked and connection-limited. Pages served by the server itself (the `Origin` host matches the checked `Host` header) are always accepted; `websocket.allowed_origins` lists any other origin allowed to connect. Configure it and `server.max_connections` in `config/app.yaml`.
- **Error messages** are template-based (never pass through raw provider error messages) and PII-scrubbed via regex patterns for API keys, org IDs, and UUIDs.
