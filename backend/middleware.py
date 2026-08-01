"""Custom ASGI middleware: connection limiting, request size limiting, security headers.

SecurityHeadersMiddleware also sets per-path cache policies and injects
CSP with a per-request nonce for HTML/SW responses.
"""

import secrets

from fastapi.responses import JSONResponse

from backend.state import c, MAX_REQUEST_BODY_BYTES
import backend.state as st

_BODY_TOO_LARGE = JSONResponse(status_code=413, content={"error": "Request body too large"})


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
                except (ValueError, TypeError):
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
