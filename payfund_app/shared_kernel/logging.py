"""Structured JSON logging helpers for payment lifecycle events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


logger = logging.getLogger("payfund")


def emit(level: str, message: str, **fields) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        **fields,
    }
    text = json.dumps(payload, default=str, ensure_ascii=True)
    if level == "error":
        logger.error(text)
    elif level == "warning":
        logger.warning(text)
    else:
        logger.info(text)

