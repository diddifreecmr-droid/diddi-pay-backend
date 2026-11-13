"""HTTP adapter for signed module callbacks."""

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx
import redis

from payfund_app.core.observability.async_context import capture_context
from payfund_app.core.observability.context import get_request_id
from payfund_app.modules.payments.application.errors import CallbackDeliveryFailed
from payfund_app.modules.payments.application.ports import CallbackTarget


class HttpSignedCallbackSender:
    def __init__(
        self, client: httpx.Client | None = None, *, timeout: float = 10.0
    ) -> None:
        self.client = client
        self.timeout = timeout

    def send(
        self,
        target: CallbackTarget,
        *,
        event_id: uuid.UUID,
        event_type: str,
        occurred_at: datetime,
        payload: Mapping[str, Any],
    ) -> None:
        envelope = {
            "id": str(event_id),
            "type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "data": payload,
        }
        body = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        signature = hmac.new(
            target.secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-DiddiPay-Event-ID": str(event_id),
            "X-DiddiPay-Signature": signature,
        }
        context = capture_context()
        headers.update(
            {k: v for k, v in context.items() if k in {"traceparent", "tracestate"}}
        )
        request_id = get_request_id() or payload.get("request_id")
        if isinstance(request_id, str) and re.fullmatch(
            r"[A-Za-z0-9._-]{1,128}", request_id
        ):
            headers["X-Request-ID"] = str(request_id)
        try:
            if self.client is not None:
                response = self.client.post(target.url, content=body, headers=headers)
            else:
                response = httpx.post(
                    target.url,
                    content=body,
                    headers=headers,
                    timeout=self.timeout,
                )
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise CallbackDeliveryFailed(str(exc)) from exc


class RedisStreamEventSender:
    """Publish the same durable event envelope to a Redis Stream."""

    def __init__(self, url: str, *, maxlen: int = 100_000) -> None:
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.maxlen = maxlen

    def send(
        self,
        target: CallbackTarget,
        *,
        event_id: uuid.UUID,
        event_type: str,
        occurred_at: datetime,
        payload: Mapping[str, Any],
    ) -> None:
        envelope = {
            "id": str(event_id),
            "type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "data": payload,
        }
        try:
            self.client.xadd(
                target.url,
                {
                    "event": json.dumps(
                        envelope, sort_keys=True, separators=(",", ":")
                    ),
                    **capture_context(),
                },
                maxlen=self.maxlen,
                approximate=True,
            )
        except redis.RedisError as exc:
            raise CallbackDeliveryFailed(str(exc)) from exc
