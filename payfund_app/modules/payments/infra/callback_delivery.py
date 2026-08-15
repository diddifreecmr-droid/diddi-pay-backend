"""HTTP adapter for signed module callbacks."""

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from typing import Any

import httpx

from payfund_app.modules.payments.application.errors import CallbackDeliveryFailed
from payfund_app.modules.payments.application.ports import CallbackTarget


class HttpSignedCallbackSender:
    def __init__(self, client: httpx.Client | None = None, *, timeout: float = 10.0) -> None:
        self.client = client
        self.timeout = timeout

    def send(
        self,
        target: CallbackTarget,
        *,
        event_id: uuid.UUID,
        payload: Mapping[str, Any],
    ) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(
            target.secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-DiddiPay-Event-ID": str(event_id),
            "X-DiddiPay-Signature": signature,
        }
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
