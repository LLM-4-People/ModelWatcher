"""Push subscription API routes and push delivery helpers.

Split from notifications.py: push routes and push internals are a
self-contained concern that does not belong in the notification dispatch module.
Holds VAPID key lifecycle, subscription CRUD, prefs sync, and rate-limited
test-push delivery.
"""

import asyncio
import hashlib
import time
from urllib.parse import urlparse

import orjson
from fastapi import Request
from fastapi.responses import JSONResponse

import backend.db as db
import backend.db_push as db_push
from backend.state import (
    c, log, log_error, push_available,
    VAPID_KEY_FILE, VAPID_PUB_FILE,
    VALID_PUSH_HOSTS, VALID_PUSH_SUFFIXES,
    MAX_ENDPOINT_LEN, MAX_CLIENT_ID_LEN,
    sanitize_prefs,
)
from backend.validation import validate_push_key
from backend.routes import check_rate_limit, client_ip, orjson_response, error_response
from backend.schemas import PushSubscribeBody, PushUnsubscribeBody, PushUpdatePrefsBody, PushTestBody


# ── Push state ───────────────────────────────────────────────────────────────

# Live VAPID key material; populated by init_vapid() at startup.
vapid_private: str | None = None
vapid_public: str | None = None

# HTTP statuses that indicate a subscription is permanently dead and should be removed.
_DEAD_HTTP_STATUSES = {410, 404, 403, 401}

_prefs_update_times: dict[str, list[float]] = {}
_push_test_times: dict[str, list[float]] = {}
_subscribe_times: dict[str, list[float]] = {}
_validate_times: dict[str, list[float]] = {}


def _is_dead_push_sub(exc: Exception) -> bool:
    """Check if a push exception indicates a dead/expired subscription."""
    resp = getattr(exc, 'response', None)
    if resp is not None:
        status = getattr(resp, 'status_code', None) or getattr(resp, 'status', None)
        if status in _DEAD_HTTP_STATUSES:
            return True
    msg = str(exc).lower()
    if any(k in msg for k in ("invalid p256dh", "missing keys", "missing endpoint")):
        return True
    return False


def _sub_hash(identifier: str) -> str:
    """Short anonymized hash for logging - first 8 hex chars of SHA-256."""
    return hashlib.sha256(identifier.encode()).hexdigest()[:8]


def _ep_origin(endpoint: str) -> str:
    """Extract scheme://netloc from a push endpoint URL to avoid logging subscription tokens."""
    p = urlparse(endpoint)
    return f"{p.scheme}://{p.netloc}"


def _remove_dead_sub(endpoint: str):
    """Remove a dead push subscription from SQLite."""
    db_push.delete_push_sub(endpoint)


def _log_push_error(endpoint: str, e: Exception, prefix: str = "Push failed"):
    """Log a push delivery error with endpoint origin, HTTP status, and response body."""
    resp = getattr(e, 'response', None)
    status = getattr(resp, 'status_code', '?') if resp else '?'
    body = getattr(resp, 'text', '')[:200] if resp else ''
    log.warning("%s for %s status=%s body=%s", prefix, _ep_origin(endpoint), status, body[:100])


def _has_null(s: str) -> bool:
    return "\x00" in s


def _validate_push_endpoint(endpoint: str) -> str | None:
    """Validate push endpoint origin using proper URL parsing.
    Returns error message string if invalid, None if valid."""
    if not endpoint:
        return "Missing endpoint"
    try:
        parsed = urlparse(endpoint)
    except Exception:
        log.debug("Push endpoint URL parse failed: %s", _sub_hash(endpoint))
        return "Invalid endpoint URL"
    if parsed.scheme != "https":
        return "Invalid push endpoint origin"
    host = parsed.hostname
    if not host:
        return "Invalid push endpoint origin"
    host = host.lower()
    if host in VALID_PUSH_HOSTS:
        return None
    if any(host.endswith(s) for s in VALID_PUSH_SUFFIXES):
        return None
    return "Invalid push endpoint origin"


def _validate_endpoint_str(endpoint) -> JSONResponse | None:
    if not isinstance(endpoint, str):
        return error_response("Invalid endpoint type", 400)
    if _has_null(endpoint):
        return error_response("Null bytes not allowed", 400)
    if len(endpoint) > MAX_ENDPOINT_LEN:
        return error_response("Endpoint too long", 400)
    ep_err = _validate_push_endpoint(endpoint)
    if ep_err:
        return error_response(ep_err, 400)
    return None


def _validate_client_id_str(client_id, required=False, check_len=True) -> JSONResponse | None:
    if not isinstance(client_id, str):
        return error_response("Invalid client_id type", 400)
    if required and not client_id:
        return error_response("Missing client_id", 400)
    if _has_null(client_id):
        return error_response("Null bytes not allowed", 400)
    if check_len and client_id and len(client_id) > MAX_CLIENT_ID_LEN:
        return error_response("client_id too long", 400)
    return None


# ── VAPID init ───────────────────────────────────────────────────────────────

def init_vapid():
    """Load or generate VAPID key pair for web push."""
    global vapid_private, vapid_public
    if not push_available:
        return
    if VAPID_KEY_FILE.exists():
        vapid_private = str(VAPID_KEY_FILE)
        vapid_public = VAPID_PUB_FILE.read_text().strip()
        log.info("Loaded VAPID key from %s", VAPID_KEY_FILE)
        return
    from py_vapid import Vapid
    v = Vapid()
    v.generate_keys()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    from py_vapid import b64urlencode
    pub_bytes = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    vapid_public = b64urlencode(pub_bytes)
    pem = v.private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    VAPID_KEY_FILE.write_bytes(pem)
    VAPID_PUB_FILE.write_text(vapid_public)
    vapid_private = str(VAPID_KEY_FILE)
    log.info("Generated new VAPID key pair at %s", VAPID_KEY_FILE)


def cleanup_invalid_push_subs():
    """Remove push subscriptions with invalid endpoints or keys from the database.

    Called at startup to purge subscriptions that predate endpoint-origin
    validation and P-256 key checks (SSRF bypass, missing key validation).
    """
    subs = db_push.all_push_subs()
    removed = 0
    for sub in subs:
        endpoint = sub.get("endpoint", "")
        err_msg = _validate_push_endpoint(endpoint)
        if err_msg:
            db_push.delete_push_sub(endpoint)
            removed += 1
            continue
        p256dh = sub.get("keys", {}).get("p256dh", "")
        auth = sub.get("keys", {}).get("auth", "")
        if validate_push_key(p256dh, 65, "p256dh") or validate_push_key(auth, 16, "auth"):
            db_push.delete_push_sub(endpoint)
            removed += 1
    if removed:
        log.info("Cleaned up %d invalid push subscription(s)", removed)


# ── Push API route handlers ──────────────────────────────────────────────────

async def handle_push_subscribe(request: Request, body: PushSubscribeBody):
    """POST /api/push/subscribe - Subscribe to web push notifications.

    Returns 409 if the endpoint is already registered to a different client
    (prevents endpoint hijacking) unless the key material matches (reclaim).
    Deletes prior subscriptions for the same client_id before upserting to
    avoid zombie subs.
    """
    if not push_available:
        return error_response("Push not available", 503)
    rl = check_rate_limit(_subscribe_times, client_ip(request), 60, c.notif_rate_limit_subscribe)
    if rl:
        return rl
    endpoint = body.endpoint
    err = _validate_endpoint_str(endpoint)
    if err:
        return err
    keys = body.keys
    p256dh_err = validate_push_key(keys.p256dh, 65, "p256dh")
    if p256dh_err:
        return error_response(p256dh_err, 400)
    auth_err = validate_push_key(keys.auth, 16, "auth")
    if auth_err:
        return error_response(auth_err, 400)
    client_id = body.client_id
    err = _validate_client_id_str(client_id, required=True)
    if err:
        return err
    prefs = sanitize_prefs(body.prefs or {})
    now = time.time()
    existing = await asyncio.to_thread(db_push.get_push_sub_on_write, endpoint)
    if existing and existing.get("client_id") != client_id:
        existing_keys = existing.get("keys", {})
        if existing_keys.get("p256dh") == keys.p256dh and existing_keys.get("auth") == keys.auth:
            log.info("Push subscription client_id reclaimed: %s -> %s for %s",
                     _sub_hash(existing.get("client_id", "")), _sub_hash(client_id), _ep_origin(endpoint))
        else:
            return error_response("Endpoint already registered to another client", 409)
    if client_id and not (existing and existing.get("endpoint") == endpoint):
        try:
            await asyncio.to_thread(db_push.delete_push_subs_by_client, client_id)
        except Exception as e:
            log_error("Failed to delete zombie push subs for client %s", e)
    created_at = existing.get("created_at", now) if existing else now
    ok = await asyncio.to_thread(
        db_push.upsert_push_sub, endpoint, keys.p256dh, keys.auth,
        client_id, prefs, created_at, now,
    )
    if not ok:
        log_error("Push subscription write failed")
        return error_response("Failed to save subscription", 500)
    log.info("Push subscription added: %s (client %s)", _ep_origin(endpoint), _sub_hash(client_id))
    return orjson_response({"ok": True})


async def handle_push_unsubscribe(request: Request, body: PushUnsubscribeBody):
    """DELETE /api/push/subscribe - Unsubscribe from web push notifications.

    If client_id is provided, deletes ALL subscriptions for that client
    (bulk unsubscribe across tabs/devices). Otherwise deletes by endpoint.
    """
    client_id = body.client_id or ""
    endpoint = body.endpoint or ""
    if client_id:
        err = _validate_client_id_str(client_id, check_len=False)
        if err:
            return err
    if endpoint:
        err = _validate_endpoint_str(endpoint)
        if err:
            return err
    if client_id and len(client_id) <= MAX_CLIENT_ID_LEN:
        deleted = await asyncio.to_thread(db_push.delete_push_subs_by_client, client_id)
        if deleted > 0:
            log.info("Push subscription(s) removed: %d for client %s", deleted, _sub_hash(client_id))
    elif endpoint:
        deleted = await asyncio.to_thread(db_push.delete_push_sub, endpoint)
        if deleted > 0:
            log.info("Push subscription removed: %s", _ep_origin(endpoint))
    elif client_id and len(client_id) > MAX_CLIENT_ID_LEN:
        return error_response("client_id too long", 400)
    else:
        return error_response("Missing endpoint and client_id", 400)
    return orjson_response({"ok": True, "deleted": deleted > 0})


async def handle_push_update_prefs(request: Request, body: PushUpdatePrefsBody):
    """PUT /api/push/preferences - Update notification prefs for a push subscriber.

    Rate limited per-IP. If client_id is provided, updates ALL subscriptions
    for that client (ensures sync across tabs/devices); otherwise updates by
    endpoint. Returns 404 if no matching subscription exists.
    """
    rl = check_rate_limit(_prefs_update_times, client_ip(request), 60, c.notif_rate_limit_prefs)
    if rl:
        return rl
    prefs = sanitize_prefs(body.prefs)
    client_id = body.client_id or ""
    now = time.time()
    if client_id:
        err = _validate_client_id_str(client_id, check_len=False)
        if err:
            return err
    if client_id and len(client_id) <= MAX_CLIENT_ID_LEN:
        rows = await asyncio.to_thread(db_push.update_all_push_sub_prefs_by_client, client_id, prefs, now)
    else:
        endpoint = body.endpoint or ""
        if endpoint:
            err = _validate_endpoint_str(endpoint)
            if err:
                return err
        if not endpoint:
            if client_id and len(client_id) > MAX_CLIENT_ID_LEN:
                return error_response("client_id too long", 400)
            return error_response("Missing endpoint and client_id", 400)
        rows = await asyncio.to_thread(db_push.update_push_sub_prefs, endpoint, prefs, now)
    if rows == 0:
        return error_response("Unknown subscription", 404)
    return orjson_response({"ok": True, "updated": rows})


async def handle_push_test(request: Request, body: PushTestBody):
    """POST /api/push/test - Send a test push notification. Rate limited globally."""
    if not push_available or not vapid_private:
        return error_response("Push not available", 503)
    rl = check_rate_limit(_push_test_times, "_g", 60, c.notif_rate_limit_push_test, "Rate limited - max test pushes per minute exceeded")
    if rl:
        return rl
    endpoint = body.endpoint
    if endpoint:
        verr = _validate_endpoint_str(endpoint)
        if verr:
            return verr
    sub = await asyncio.to_thread(db_push.get_push_sub_on_write, endpoint) if endpoint else None
    if not endpoint or not sub:
        return error_response("No matching push subscription - please re-enable notifications", 400)
    payload = orjson.dumps({"title": f"{c.app_name} test", "body": "Push notifications are working!", "tag": "mw-test", "url": c.site_url}).decode()
    from backend.state import webpush
    claims = {"sub": c.vapid_email}
    log.debug("Push test: %s", _sub_hash(endpoint))
    try:
        resp = await asyncio.to_thread(
            webpush, subscription_info=sub, data=payload,
            vapid_private_key=vapid_private,
            vapid_claims=claims, ttl=c.notif_push_ttl,
            timeout=30,
        )
        status = getattr(resp, 'status_code', None)
        body_text = getattr(resp, 'text', '')[:200] if resp else ''
        log.debug("Push test response: status=%s body=%s", status, body_text)
        return orjson_response({"ok": True})
    except Exception as e:
        _log_push_error(endpoint, e, "Push test failed")
        if _is_dead_push_sub(e):
            await asyncio.to_thread(_remove_dead_sub, endpoint)
            from backend.notifications import invalidate_push_subs_cache
            invalidate_push_subs_cache()
            log.info("Removed expired push subscription during test: %s", _sub_hash(endpoint))
        return error_response("Push delivery failed", 502)


async def handle_push_validate(request: Request, endpoint: str = "", client_id: str = ""):
    """GET /api/push/validate - Check whether a push endpoint is still registered for a client.

    Requires both endpoint and client_id: returning valid=false without
    client_id blocks endpoint enumeration. Returns valid=true only when the
    subscription exists AND the client_id matches.
    """
    if not push_available:
        return {"valid": False}
    rl = check_rate_limit(_validate_times, client_ip(request), 60, c.notif_rate_limit_validate)
    if rl:
        return rl
    if not endpoint or len(endpoint) > MAX_ENDPOINT_LEN:
        return {"valid": False}
    if not client_id or len(client_id) > MAX_CLIENT_ID_LEN:
        return {"valid": False}
    sub = await asyncio.to_thread(db_push.get_push_sub_on_write, endpoint)
    if not sub:
        return {"valid": False, "reason": "not_found"}
    if sub.get("client_id") == client_id:
        return {"valid": True}
    return {"valid": False, "reason": "client_mismatch"}


def handle_vapid_key():
    """GET /api/vapid-key - Return VAPID public key for web push."""
    if not push_available or not vapid_public:
        return error_response("Push not available", 503)
    return {"public_key": vapid_public}
