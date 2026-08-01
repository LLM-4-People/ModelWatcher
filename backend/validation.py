"""Push subscription key validation.

Validates base64url-encoded P-256 public keys and auth secrets against
the Web Push spec. Uses cryptography's curve-point decoding to reject
points that are well-formed but NOT on the P-256 curve.
"""

MAX_KEY_LEN = 256  # also used for model_key length checks


def is_on_p256_curve(raw: bytes) -> bool:
    """Verify a 65-byte uncompressed point is actually on the P-256 curve.

    A 0x04 prefix + 64-byte coordinates can encode a point NOT on the curve;
    cryptography's from_encoded_point() raises ValueError for such inputs.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey, SECP256R1
        EllipticCurvePublicKey.from_encoded_point(SECP256R1(), raw)
        return True
    except Exception as e:
        from backend.state import log_error
        log_error("P-256 curve validation failed", e)
        return False


def validate_push_key(key: str, expected_bytes: int, label: str) -> str | None:
    """Validate a base64url-encoded push key. Returns an error message or None if valid.

    For p256dh keys, verifies the 0x04 prefix and curve membership in addition
    to length. auth keys are length-checked only (16 bytes per Web Push spec).
    """
    import base64
    from backend.state import log_error

    if not key:
        return f"Missing {label} key"
    if len(key) > MAX_KEY_LEN:
        return f"{label} key too long"
    try:
        padded = key + "=" * (-len(key) % 4)
        raw = base64.urlsafe_b64decode(padded)
    except Exception as e:
        log_error(f"Invalid base64 in {label} key", e)
        return f"Invalid {label} key encoding"
    if len(raw) != expected_bytes:
        return f"Invalid {label} key length"
    if label == "p256dh":
        if raw[0] != 0x04:
            return "Invalid p256dh key - not an uncompressed P-256 point"
        if not is_on_p256_curve(raw):
            return "Invalid p256dh key - point not on P-256 curve"
    return None
