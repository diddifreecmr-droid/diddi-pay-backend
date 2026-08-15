"""Canonical request fingerprints used to enforce semantic idempotency."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def request_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
