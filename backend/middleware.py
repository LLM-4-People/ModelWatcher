"""Custom ASGI middleware: connection limiting, request size limiting, Host check, security headers.

SecurityHeadersMiddleware also sets per-path cache policies and injects
CSP with a per-request nonce for HTML/SW responses.
"""

import secrets

from fastapi.responses import JSONResponse

from backend.state import c, MAX_REQUEST_BODY_BYTES
import backend.state as st

_BODY_TOO_LARGE = JSONResponse(status_code=413, content={"error": "Request body too large"})
_BAD_HOST = JSONResponse(status_code=400, content={"error": "Invalid host header"})


class RequestSizeLimitMiddleware:
    """Pure ASGI middleware that rejects oversized request bodies (1MB cap).

    Checks Content-Length upfront and wraps receive() to count streaming
    bytes, handling chunked encoding (missing Content-Length) as well.
    Returns 413 before the request reaches the application.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Reject upfront when Content-Length advertises an oversized body
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > MAX_REQUEST_BODY_BYTES:
                        await _BODY_TOO_LARGE(scope, receive, send)
                        return
                except ValueError:
                    pass
                break

        # Wrap receive() so chunked bodies without Content-Length are also capped
        bytes_received = 0
        limit = MAX_REQUEST_BODY_BYTES

        async def limited_receive():
            nonlocal bytes_received
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                bytes_received += len(body)
                if bytes_received > limit:
                    raise _BodyTooLargeError()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLargeError:
            await _BODY_TOO_LARGE(scope, receive, send)


class _BodyTooLargeError(Exception):
    pass


def _host_name(host_header: str) -> str:
    """The Host header without its port, lower-cased, IPv6 brackets removed ('' when malformed)."""
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else ""
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host_header: str) -> bool:
    """Whether a Host header names this server.

    IP addresses and localhost always do: a DNS-rebinding page reaches the server under
    the attacker's host name, never under an address. So does the host of app.site_url;
    server.allowed_hosts adds names ("*.example.com" for subdomains, "*" for any host).
    """
    host = _host_name(host_header)
    if not host:
        return False
    if st.is_ip_literal(host) or host in ("localhost", c.site_host):
        return True
    return any(pattern == "*" or host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]))
               for pattern in c.allowed_hosts)


class HostCheckMiddleware:
    """Reject requests and WebSockets whose Host header does not name this server (finding F48).

    The WebSocket same-origin rule trusts the Host header, and a DNS-rebinding page is
    same-origin by construction, so the Host itself is checked here, for HTTP and WS alike.
    Rejections: HTTP 400 with the usual error body; a WebSocket is closed with 1008 before
    it is accepted (the handshake fails with 403).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), "")
            if not host_allowed(host):
                # Client-controlled: warn once with the fix, then keep the log quiet
                if st.condition_changed("host_rejected", True):
                    st.log.warning("Rejected Host header %r - add it to server.allowed_hosts if it names this server "
                                   "(later rejections log at debug level)", host[:100])
                else:
                    st.log.debug("Rejected Host header %r", host[:100])
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await _BAD_HOST(scope, receive, send)
                return
        await self.app(scope, receive, send)


class ConnectionLimiterMiddleware:
    """ASGI middleware returning 503 when active HTTP connections exceed the cap.

    Non-HTTP (WebSocket) requests bypass the limit. Passes exceptions
    through after logging so the global handler still catches them.
    """

    def __init__(self, app):
        self.app = app
        self._active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._active >= c.max_connections:
            response = JSONResponse(
                status_code=503,
                content={"error": "Server overloaded - too many concurrent connections"},
            )
            await response(scope, receive, send)
            return
        self._active += 1
        try:
            await self.app(scope, receive, send)
        except Exception as e:
            st.log_error("Unhandled exception in ConnectionLimiterMiddleware", e)
            raise
        finally:
            self._active -= 1


class SecurityHeadersMiddleware:
    """ASGI middleware adding security headers, per-path cache policy, and CSP.

    Differentiates cache policy by path: hashed assets (?v=) are immutable,
    JS/CSS revalidate, images long-cached, API routes private/no-cache.
    Forces no-cache on all error responses (status >= 400) so 404s and
    5xxs are never cached. Injects a per-request CSP nonce for HTML and
    SW responses. Only intercepts http.response.start - WebSocket
    upgrades pass through untouched.
    """
    _CACHE_IMMUTABLE = b"public, max-age=31536000, immutable"
    _CACHE_STATIC_ASSET = b"public, max-age=2592000"
    _CACHE_REVALIDATE = b"no-cache"
    _CACHE_PRIVATE = b"no-cache, private"

    def __init__(self, app):
        self.app = app

    def _cache_policy(self, path: str, query_string: bytes = b"") -> bytes:
        has_version = query_string.startswith(b"v=")
        if path.startswith(st.c.static_url_prefix + "/"):
            if path == f"{st.c.static_url_prefix}/manifest.json":
                return SecurityHeadersMiddleware._CACHE_REVALIDATE
            if has_version:
                return SecurityHeadersMiddleware._CACHE_IMMUTABLE
            if path.endswith((".js", ".mjs", ".css")):
                return SecurityHeadersMiddleware._CACHE_REVALIDATE
            return SecurityHeadersMiddleware._CACHE_STATIC_ASSET
        if path in ("/", "/sw.js"):
            return SecurityHeadersMiddleware._CACHE_REVALIDATE
        if path.startswith("/api/"):
            return SecurityHeadersMiddleware._CACHE_PRIVATE
        return SecurityHeadersMiddleware._CACHE_REVALIDATE

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        query_string = scope.get("query_string", b"")
        needs_csp = path in ("/", "/sw.js")
        nonce = secrets.token_urlsafe(16) if needs_csp else ""
        scope.setdefault("state", {})["csp_nonce"] = nonce
        cache_header = self._cache_policy(path, query_string)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
                # Never cache error responses - prevents 404s from being cached for 30 days
                effective_cache = b"no-cache" if status_code >= 400 else cache_header
                headers = list(message.get("headers", []))
                headers = [h for h in headers if h[0] != b"cache-control" and h[0] != b"pragma" and h[0] != b"expires"]
                headers.append([b"cache-control", effective_cache])
                if effective_cache not in (self._CACHE_IMMUTABLE, self._CACHE_STATIC_ASSET):
                    headers.append([b"pragma", b"no-cache"])
                    headers.append([b"expires", b"0"])
                headers.append([b"x-content-type-options", b"nosniff"])
                headers.append([b"x-frame-options", b"SAMEORIGIN"])
                headers.append([b"referrer-policy", b"strict-origin-when-cross-origin"])
                if needs_csp:
                    csp = (
                        f"default-src 'self'; "
                        f"script-src 'self' 'nonce-{nonce}' https://static.cloudflareinsights.com; "
                        f"style-src 'self' 'unsafe-inline'; "
                        f"img-src 'self' data:; "
                        f"connect-src 'self' ws: wss: https://static.cloudflareinsights.com; "
                        f"font-src 'self' https://cdn.jsdelivr.net; "
                        f"manifest-src 'self'; "
                        f"worker-src 'self'; "
                        f"object-src 'none'; "
                        f"frame-ancestors 'self'; "
                        f"base-uri {c.site_url};"
                    )
                    headers.append([b"content-security-policy", csp.encode()])
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception as e:
            st.log_error("Unhandled exception in SecurityHeadersMiddleware", e)
            raise
