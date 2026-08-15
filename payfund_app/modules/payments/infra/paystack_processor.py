"""Paystack adapter for provider-neutral PaymentIntent collections."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import json
from typing import Any

import httpx

from payfund_app.modules.payments.application.errors import (
    ProcessorCallUncertain,
    ProcessorRequestRejected,
    ProcessorWebhookRejected,
)
from payfund_app.modules.payments.application.ports import (
    InitializePaymentRequest,
    PaymentDirection,
    ProcessorCapabilities,
    ProviderEvent,
    ProviderResult,
    RefundRequest,
    RefundResult,
)
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    NextAction,
    NextActionType,
    RefundStatus,
)


class PaystackPaymentProcessor:
    name = "paystack"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        directions=frozenset({PaymentDirection.COLLECTION}),
        channels=frozenset({"mobile_money", "card"}),
        # Hosted checkout lets the payer choose the available Mobile Money network.
        networks=frozenset(),
    )

    def __init__(
        self,
        *,
        secret_key: str,
        base_url: str = "https://api.paystack.co",
        client: httpx.Client | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        if not secret_key:
            raise ValueError("PAYSTACK_SECRET_KEY is required in paystack processor mode")
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._webhook_secret = webhook_secret or secret_key

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._secret_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._client is not None:
            return self._client.request(method, path, headers=self._headers, **kwargs)
        with httpx.Client(base_url=self._base_url, timeout=15.0) as client:
            return client.request(method, path, headers=self._headers, **kwargs)

    def initialize_payment(self, request: InitializePaymentRequest) -> ProviderResult:
        if not request.customer_email:
            raise ProcessorRequestRejected("customer_email is required by Paystack checkout")

        reference = f"dpi_{request.attempt_id.hex}"
        metadata: dict[str, Any] = {
            "payment_intent_id": str(request.payment_intent_id),
            "payment_attempt_id": str(request.attempt_id),
            "business_reference": request.business_reference,
            "requested_network": request.network,
            **dict(request.metadata),
        }
        payload: dict[str, Any] = {
            "email": request.customer_email,
            "amount": request.money.amount,
            "currency": request.money.currency,
            "reference": reference,
            "metadata": metadata,
        }
        if request.callback_url:
            payload["callback_url"] = request.callback_url
        if request.channel:
            payload["channels"] = [request.channel]

        try:
            response = self._request("POST", "/transaction/initialize", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(reference, "Paystack initialization outcome is unknown") from exc

        body = self._json(response)
        if response.status_code >= 400 or not body.get("status"):
            return ProviderResult(
                provider_reference=reference,
                status=AttemptStatus.FAILED,
                provider_status=f"http_{response.status_code}",
                failure_code="PAYSTACK_INITIALIZATION_FAILED",
                failure_message=str(body.get("message") or "Paystack rejected the payment")[:255],
            )
        data = body.get("data") or {}
        provider_reference = str(data.get("reference") or reference)
        authorization_url = data.get("authorization_url")
        if not authorization_url:
            return ProviderResult(
                provider_reference=provider_reference,
                status=AttemptStatus.UNKNOWN,
                provider_status="invalid_response",
                failure_code="PAYSTACK_RESPONSE_INVALID",
                failure_message="Paystack did not return an authorization URL",
            )
        return ProviderResult(
            provider_reference=provider_reference,
            status=AttemptStatus.REQUIRES_ACTION,
            provider_status="initialized",
            next_action=NextAction(NextActionType.REDIRECT, url=str(authorization_url)),
        )

    def verify_payment(self, provider_reference: str) -> ProviderResult:
        try:
            response = self._request("GET", f"/transaction/verify/{provider_reference}")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(provider_reference, "Paystack verification is unavailable") from exc
        body = self._json(response)
        if response.status_code >= 400 or not body.get("status"):
            return ProviderResult(
                provider_reference=provider_reference,
                status=AttemptStatus.UNKNOWN,
                provider_status=f"http_{response.status_code}",
                failure_code="PAYSTACK_VERIFICATION_FAILED",
                failure_message=str(body.get("message") or "Paystack verification failed")[:255],
            )
        data = body.get("data") or {}
        provider_status = str(data.get("status") or "unknown").lower()
        status = self._normalize_status(provider_status)
        return ProviderResult(
            provider_reference=str(data.get("reference") or provider_reference),
            status=status,
            provider_status=provider_status,
            amount=int(data["amount"]) if data.get("amount") is not None else None,
            currency=str(data["currency"]).upper() if data.get("currency") else None,
        )

    def parse_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> ProviderEvent:
        signature = next(
            (value for key, value in headers.items() if key.lower() == "x-paystack-signature"),
            "",
        )
        expected = hmac.new(
            self._webhook_secret.encode("utf-8"), raw_body, hashlib.sha512
        ).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            raise ProcessorWebhookRejected("invalid Paystack webhook signature")
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProcessorWebhookRejected("invalid Paystack webhook JSON") from exc
        if not isinstance(payload, dict):
            raise ProcessorWebhookRejected("invalid Paystack webhook payload")

        event_type = str(payload.get("event") or "unknown")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        reference = str(data.get("reference")) if data.get("reference") else None
        provider_status = str(data.get("status") or "").lower()
        status = (
            AttemptStatus.SUCCEEDED
            if event_type == "charge.success"
            else self._normalize_status(provider_status)
            if provider_status
            else None
        )
        event_key = ":".join(
            [event_type, reference or "no-reference", provider_status or "no-status"]
        )
        sanitized = {
            "event": event_type,
            "reference": reference,
            "status": provider_status or None,
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "channel": data.get("channel"),
            "paid_at": data.get("paid_at"),
            "gateway_response": data.get("gateway_response"),
        }
        return ProviderEvent(
            event_key=event_key,
            event_type=event_type,
            provider_reference=reference,
            status=status,
            amount=int(data["amount"]) if data.get("amount") is not None else None,
            currency=str(data["currency"]).upper() if data.get("currency") else None,
            sanitized_payload=sanitized,
        )

    def refund_payment(self, request: RefundRequest) -> RefundResult:
        return RefundResult(
            provider_reference=None,
            status=RefundStatus.FAILED,
            provider_status="not_implemented",
            failure_code="PAYSTACK_REFUND_NOT_IMPLEMENTED",
        )

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _normalize_status(status: str) -> AttemptStatus:
        if status == "success":
            return AttemptStatus.SUCCEEDED
        if status in {"failed", "abandoned", "reversed"}:
            return AttemptStatus.FAILED
        if status in {"pending", "ongoing", "processing", "queued"}:
            return AttemptStatus.PROCESSING
        return AttemptStatus.UNKNOWN
