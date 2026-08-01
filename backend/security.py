"""PII scrubbing and API error formatting for security.

Template-based error messages are the primary PII defense: they replace raw
error.message (which may contain names, org IDs, billing URLs) with safe,
standardized text derived from error.code / error.type.  Regex scrubbing
(scrub_pii) is retained as defense-in-depth for stack traces and exception
messages from httpx/Python (which are not provider API errors).
"""

import json
import re

from backend.state import log

_TRACE_KEYS = ("stack_trace", "trace", "stack")

# Defense-in-depth PII scrubbing for stack traces and httpx/Python exception
# messages. NOT used for provider error.message (template messages replace those).
_PII_RE = re.compile(
    r'(sk-ant-|sk-proj-|sk-svcacct-|sk-None-|sk-|wfr_|gsk_|ak-|Bearer\s+)'
    r'([\w\-*]{4,})'                          # g1+g2: key prefix + value
    r'|'
    r'(org-[A-Za-z0-9]{20,})'                 # g3: OpenAI org IDs
    r'|'
    r'(proj_[A-Za-z0-9]{20,})'                # g4: OpenAI project IDs
    r'|'
    r'organization\s*\(([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\)'  # g5: Anthropic UUID
    r'|'
    r'(?<=/)([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?=[^\w.-]|$)',  # g6: UUID in URL path
    re.IGNORECASE,
)


def scrub_pii(text: str) -> str:
    """Redact PII from text: API keys, org/project IDs, auth tokens, and UUIDs in URLs."""
    def _replace(m):
        if m.group(1):
            return m.group(1) + '****'
        if m.group(3):
            return 'org-****'
        if m.group(4):
            return 'proj_****'
        if m.group(5):
            return 'organization(redacted)'
        if m.group(6):
            return '(redacted)'
        return '****'
    return _PII_RE.sub(_replace, text)


# Template-based safe error messages keyed by (status_code, error_code_or_type).
# status_code=None covers stream errors that have no HTTP status.
# Lookup chain: (status, code) -> (status, type) -> (None, code) -> (None, type)
#   -> _STATUS_MESSAGES -> generic.
_ERROR_TEMPLATES = {
    # --- OpenAI error codes (error.code) ---
    (401, "invalid_api_key"):                        "Invalid API key",
    (402, "insufficient_quota"):                     "Monthly quota exceeded",
    (403, "unsupported_country_region_territory"):  "Region not supported",
    (400, "model_not_found"):                        "Model not found",
    (404, "model_not_found"):                        "Model not found",
    (400, "context_length_exceeded"):                "Context length exceeded",
    (429, "rate_limit_exceeded"):                    "Rate limit exceeded",
    (429, "insufficient_quota"):                     "Monthly quota exceeded",
    (500, "server_error"):                            "Internal server error",
    (503, "server_error"):                            "Service unavailable",
    # --- Anthropic / shared error types (error.type) ---
    (401, "authentication_error"):                   "Authentication failed",
    (403, "permission_error"):                        "Permission denied",
    (403, "unsupported_country"):                     "Region not supported",
    (404, "not_found_error"):                         "Resource not found",
    (400, "invalid_request_error"):                   "Invalid request",
    (429, "rate_limit_error"):                        "Rate limit exceeded",
    (500, "api_error"):                                "Server error",
    (504, "timeout_error"):                            "Request timed out",
    (529, "overloaded_error"):                        "Server overloaded",
    # --- NanoGPT / smaller providers ---
    (429, "model_overloaded"):                        "Server overloaded",
    (503, "all_fallbacks_failed"):                    "Service unavailable",
    (402, "insufficient_credits"):                    "Insufficient credits",
    # --- Google / Gemini (error.status) ---
    (429, "RESOURCE_EXHAUSTED"):                     "Rate limit exceeded",
    (403, "PERMISSION_DENIED"):                      "Permission denied",
    (404, "NOT_FOUND"):                               "Resource not found",
    (400, "INVALID_ARGUMENT"):                        "Invalid request",
    (500, "INTERNAL"):                                 "Server error",
    # --- Stream errors (no HTTP status) ---
    (None, "overloaded_error"):                       "Server overloaded",
    (None, "rate_limit_error"):                       "Rate limit exceeded",
    (None, "rate_limit_exceeded"):                    "Rate limit exceeded",
    (None, "api_error"):                               "Server error",
    (None, "timeout_error"):                           "Request timed out",
    (None, "model_overloaded"):                        "Server overloaded",
    (None, "authentication_error"):                   "Authentication failed",
    (None, "invalid_request_error"):                   "Invalid request",
    (None, "permission_error"):                        "Permission denied",
}

_STATUS_MESSAGES = {
    400: "Bad request",       401: "Authentication failed",
    402: "Billing issue",     403: "Permission denied",
    404: "Not found",         408: "Request timed out",
    409: "Conflict",          413: "Request too large",
    415: "Unsupported media type", 422: "Invalid request",
    429: "Rate limit exceeded",
    500: "Internal server error",      502: "Bad gateway",
    503: "Service unavailable", 504: "Gateway timeout",
    529: "Server overloaded",
}


def _extract_and_scrub_trace(err_obj) -> str | None:
    """Extract a trace/stack from an error object, scrub PII, and truncate to 2000 chars."""
    raw = next((err_obj.get(k) for k in _TRACE_KEYS if err_obj.get(k)), None)
    return scrub_pii(str(raw))[:2000] if raw else None


def _safe_error_msg(status_code: int | None, err: dict) -> str:
    """Build a safe, standardized error message from structured error fields.

    Never passes through error.message - uses template lookup from
    error.code / error.type / error.status (machine identifiers, never PII),
    falling back to generic status-code messages.
    """
    def _safe_tag(value):
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return value[:120]
        return None

    err_code = _safe_tag(err.get("code"))
    err_type = _safe_tag(err.get("type")) or _safe_tag(err.get("status"))
    safe_msg = None
    if status_code is not None:
        safe_msg = (
            _ERROR_TEMPLATES.get((status_code, err_code))
            or _ERROR_TEMPLATES.get((status_code, err_type))
        )
    if not safe_msg:
        safe_msg = (
            _ERROR_TEMPLATES.get((None, err_code))
            or _ERROR_TEMPLATES.get((None, err_type))
        )
    if not safe_msg and status_code is not None:
        safe_msg = _STATUS_MESSAGES.get(status_code)
    if not safe_msg:
        safe_msg = _generic(status_code or 0)
    tag = err_code or err_type
    if tag and tag.isdigit():
        tag = err_type or None   # numeric code is just status code - prefer type/status
    suffix = f" [{tag}]" if tag else ""
    return f"{safe_msg}{suffix}"


def format_api_error(status_code: int, body: str) -> tuple[str, str | None]:
    """Parse API error response into a safe, standardized error string.

    For JSON responses, uses template-based safe messages derived from
    error.code / error.type - never passes through error.message (which may
    contain PII like names, org IDs, billing URLs).  For non-JSON bodies,
    uses the status-code message directly.  Returns (message, trace) tuple.
    """
    prefix = f"HTTP {status_code}"
    status_msg = _STATUS_MESSAGES.get(status_code) or _generic(status_code)
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            err = data.get("error", {})
            if isinstance(err, dict):
                return f"{prefix}: {_safe_error_msg(status_code, err)}", _extract_and_scrub_trace(err)
            # Non-standard JSON (top-level detail/message) carries the same PII
            # risk as error.message - use the status-code fallback.
            if data.get("detail") or data.get("message"):
                return f"{prefix}: {status_msg}", None
        # Array or other non-dict JSON
        return f"{prefix}: {status_msg}", None
    except json.JSONDecodeError:
        log.debug("Non-JSON API error body (HTTP %d)", status_code)
        pass
    # Non-JSON: status code message only (no HTML parsing, no raw text passthrough)
    return f"{prefix}: {status_msg}", None


def _generic(status_code: int) -> str:
    return "Request error" if status_code < 500 else "Server error"


def extract_stream_error(chunk, is_anthropic=False) -> tuple[str, str | None]:
    """Extract error message and trace from an in-stream error chunk.

    Uses template-based messages when the error object has structured fields
    (error.type / error.code).  Falls back to generic "Stream error" for
    unstructured errors.  Never passes through error.message.
    Returns (error_str, error_trace_str) tuple.
    """
    err_obj = chunk.get("error", chunk) if is_anthropic else chunk.get("error", {})
    trace = _extract_and_scrub_trace(err_obj) if isinstance(err_obj, dict) else None
    if isinstance(err_obj, dict):
        # Only use template when structured fields (type/code/status) are present
        if err_obj.get("type") or err_obj.get("code") or err_obj.get("status"):
            return f"Stream error: {_safe_error_msg(None, err_obj)}", trace
    # No structured fields - generic fallback
    return "Stream error", trace


# Maps Python exception types to safe user-facing messages. Internal errors
# (NameError, AttributeError, KeyError, etc.) must never reach the UI or affect
# model status - they're code bugs, not endpoint failures.  is_internal_error()
# distinguishes model-availability exceptions (genuine endpoint failures) from
# code bugs that should not affect model status, uptime, or trigger notifications.

# Exception types indicating the MODEL ENDPOINT is unreachable. Only these
# cause status="error", uptime impact, and notifications.
_MODEL_AVAILABILITY_EXCEPTIONS = frozenset({
    # httpx - genuine network/API failures
    "ConnectTimeout", "ConnectError", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "TimeoutException",
    # stdlib - genuine connection/timeout failures
    "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
    "ConnectionAbortedError", "TimeoutError", "OSError",
    # domain - stream stalled
    "StreamStalledError",
})

# Maps exception type names to user-facing messages.
# Covers both availability exceptions and some internal ones (for logging).
_KNOWN_EXCEPTION_MESSAGES = {
    # httpx
    "ConnectTimeout": "Connection timed out",
    "ConnectError": "Connection failed",
    "ReadTimeout": "Request timed out",
    "WriteTimeout": "Request timed out",
    "PoolTimeout": "Request timed out",
    "TimeoutException": "Request timed out",
    # stdlib
    "ConnectionError": "Connection failed",
    "TimeoutError": "Request timed out",
    "ConnectionRefusedError": "Connection refused",
    "ConnectionResetError": "Connection reset",
    "ConnectionAbortedError": "Connection aborted",
    "OSError": "Network error",
    # domain
    "StreamStalledError": "Stream stalled",
}


def is_internal_error(exc: BaseException) -> bool:
    """True if the exception is a code bug, not a model availability issue.

    Internal errors (NameError, AttributeError, KeyError, etc.) should never
    affect model status, uptime, or trigger notifications - they're problems
    in our code, not the model endpoint being down.
    """
    return type(exc).__name__ not in _MODEL_AVAILABILITY_EXCEPTIONS


def safe_internal_error(exc: BaseException) -> str:
    """Return a safe, user-facing message for a Python exception.

    Maps known exception type names to standard messages; returns a generic
    fallback for anything else (including NameError, AttributeError, KeyError,
    and all other internal/breakage exceptions).  The real exception is always
    logged server-side - this function only controls what reaches the UI.
    """
    name = type(exc).__name__
    return _KNOWN_EXCEPTION_MESSAGES.get(name, "Internal error")
