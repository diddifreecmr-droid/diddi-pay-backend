"""Central redaction applied before structured data reaches a log sink."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"
MAX_LOG_STRING_LENGTH = 2048
MAX_REDACTION_DEPTH = 8

_SENSITIVE_KEY_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "password",
        "secret",
        "signature",
        "access_token",
        "refresh_token",
        "service_key",
        "api_key",
        "apikey",
        "pin",
        "otp",
        "cvv",
        "card_number",
        "phone",
        "email",
    }
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_PAYSTACK_KEY_RE = re.compile(r"\bsk_(?:test|live)_[A-Za-z0-9_-]+\b", re.IGNORECASE)


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return any(
        normalized == part
        or normalized.startswith(f"{part}_")
        or normalized.endswith(f"_{part}")
        or f"_{part}_" in normalized
        for part in _SENSITIVE_KEY_PARTS
    )


def _sanitize_string(value: str) -> str:
    value = _BEARER_RE.sub(REDACTED, value)
    value = _JWT_RE.sub(REDACTED, value)
    value = _PAYSTACK_KEY_RE.sub(REDACTED, value)
    if len(value) > MAX_LOG_STRING_LENGTH:
        return f"{value[:MAX_LOG_STRING_LENGTH]}...[TRUNCATED]"
    return value


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a JSON-compatible copy with credentials and common PII removed."""

    if _depth >= MAX_REDACTION_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            result[name] = (
                REDACTED
                if _is_sensitive_key(key)
                else redact(item, _depth=_depth + 1)
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, bytes):
        return f"[BINARY:{len(value)}]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_string(str(value))
