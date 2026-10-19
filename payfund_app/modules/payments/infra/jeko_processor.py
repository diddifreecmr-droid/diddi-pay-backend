"""Jèko (jeko.africa) adapter for provider-neutral PaymentIntent collections and payouts.

Targets the Jèko **Partner API** (https://api.jeko.africa, routes under /partner_api).
Authentication is two headers on every request -- `X-API-KEY` (the secret `jeko_...`) and
`X-API-KEY-ID` (its UUID identifier); there is no login/JWT step (unlike CinetPay v1). A
separate dashboard step must *enable API access* on the business or every call returns 403
`business_not_enabled_for_api_access` (a 401 means the keys/headers themselves are wrong).
Every call also needs a `storeId` -- the merchant store the money moves through -- resolved
once from GET /partner_api/stores and configured as JEKO_STORE_ID.

There is **no sandbox**: integration calls hit production data; isolation is a dedicated test
store. Amounts are in **cents** (XOF x 100, multiples of 100) -- the same minor-units trick
as Paystack, so _to_jeko_amount/_from_jeko_amount mirror PaystackPaymentProcessor's.

Collections go through the hosted payment page (POST /partner_api/payment_requests returns a
`redirectUrl`), so initialize_payment returns NextActionType.REDIRECT -- every consuming app
(DiddiGo/DiddiSend/DiddiFood) already renders that for Paystack, no client change needed.
Unlike CinetPay's channel=ALL, Jèko fixes the paymentMethod at creation time, so the caller's
generic `network` (wave/orange/mtn/moov/djamo) is mapped to Jèko's paymentMethod up front; the
direct-push `forceProviderDirect` flow is deliberately NOT used (it would need a per-app
"check your phone" screen first, same reasoning as PawaPay's Payment-Page choice). Verified
live against api.jeko.africa (store DIDDIFREE): hosted page + Wave + verify all work; see
docs/evaluation-jeko.md section 11.

Payouts (create_payout/verify_payout) use POST /partner_api/transfers /
GET /partner_api/transfers/{id} with an inline mobile-money beneficiary
(name + paymentMethod + identifier.reference=phone). PayoutRequest.destination is the generic
{"type": "mobile_money", "phone": ..., "network": ...} dict (see PawapayPaymentProcessor), so
callers never see Jèko-specific shapes. A min of 500 cents (5 XOF) is enforced server-side.

Deliberately NOT wired here (same decisions as PawaPay / CinetPay):
  * refund_payment -> NotImplementedError. Jèko has no generic /refunds; refunds exist only
    through the escrow flow (POST /partner_api/escrows/{id}/refund), which is *platform-key
    only* (403 "Escrow requires a platform API key" on the merchant test key) and tied to a
    Service Provider onboarding. REFUND is therefore not in capabilities.
  * parse_webhook -> NotImplementedError. Blocked on the BCEAO agreement (SCRUM-555), a
    DiddiPay-side gate, not a Jèko limitation -- Jèko signs webhooks with a real
    HMAC-SHA256(raw body) using JEKO_WEBHOOK_SECRET (wired in config for later). Confirmation
    goes through verify_payment / verify_payout / reconciliation meanwhile.

Known limitation: Jèko assigns the payment_request/transfer `id` server-side, and verify_*
looks it up only by that id (no lookup by our client `reference`). If initialize/create times
out before we read the response, we never learn Jèko's id -> ProcessorCallUncertain, left to
the reconciliation max-age timeout, same end state as PawaPay's NOT_FOUND-is-pending case.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from payfund_app.core.observability.business_metrics import observe_provider_call
from payfund_app.modules.payments.application.errors import (
    ProcessorCallUncertain,
    ProcessorRequestRejected,
)
from payfund_app.modules.payments.application.ports import (
    InitializePaymentRequest,
    PaymentDirection,
    PayoutRequest,
    PayoutResult,
    ProcessorCapabilities,
    ProviderResult,
    RefundRequest,
    RefundResult,
)
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    NextAction,
    NextActionType,
    PayoutStatus,
)

# Generic DiddiPay network name (InitializePaymentRequest.network / destination.network) ->
# Jèko paymentMethod. The names already line up one-to-one; the map is explicit so an
# unsupported network is rejected loudly instead of forwarded as-is. Verified live: the enum
# the API accepts is exactly {orange, wave, mtn, moov, djamo} (plus jeko/bank, not routed here).
_PAYMENT_METHODS = {
    "orange": "orange",
    "wave": "wave",
    "mtn": "mtn",
    "moov": "moov",
    "djamo": "djamo",
}


class JekoPaymentProcessor:
    _XOF_SUBUNIT_FACTOR = 100

    name = "jeko"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        directions=frozenset({PaymentDirection.COLLECTION, PaymentDirection.PAYOUT}),
        channels=frozenset({"mobile_money"}),
        # Hosted page / per-network init -- empty networks means Jèko never becomes the
        # "dedicated handler" that hijacks another processor's traffic (same rationale as
        # CinetPay; see SIMULATED_PROCESSOR_NAMES in processor_router.py). The exact network
        # is still required and mapped at initialize_payment/create_payout time.
        networks=frozenset(),
    )

    def __init__(
        self,
        *,
        api_key: str,
        api_key_id: str,
        store_id: str,
        base_url: str = "https://api.jeko.africa",
        client: httpx.Client | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("JEKO_API_KEY is required to register the jeko processor")
        if not api_key_id:
            raise ValueError("JEKO_API_KEY_ID is required to register the jeko processor")
        if not store_id:
            raise ValueError("JEKO_STORE_ID is required to register the jeko processor")
        self._api_key = api_key
        self._api_key_id = api_key_id
        self._store_id = store_id
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._webhook_secret = webhook_secret

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-API-KEY": self._api_key,
            "X-API-KEY-ID": self._api_key_id,
            "Content-Type": "application/json",
        }

    def _request(self, operation: str, method: str, path: str, **kwargs) -> httpx.Response:
        started_at = time.perf_counter()
        try:
            if self._client is not None:
                response = self._client.request(method, path, headers=self._headers, **kwargs)
            else:
                with httpx.Client(base_url=self._base_url, timeout=20.0) as client:
                    response = client.request(method, path, headers=self._headers, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError):
            observe_provider_call(
                provider=self.name,
                operation=operation,
                outcome="transport_error",
                duration_seconds=time.perf_counter() - started_at,
            )
            raise
        observe_provider_call(
            provider=self.name,
            operation=operation,
            outcome="http_error" if response.status_code >= 400 else "returned",
            duration_seconds=time.perf_counter() - started_at,
        )
        return response

    def initialize_payment(self, request: InitializePaymentRequest) -> ProviderResult:
        if not request.callback_url:
            raise ProcessorRequestRejected(
                "callback_url is required by jeko's payment page (used as success/error URL)"
            )
        method = self._resolve_method(request.network)

        reference = f"dpi_{request.attempt_id.hex}"
        payload: dict[str, Any] = {
            "storeId": self._store_id,
            "amountCents": self._to_jeko_amount(request.money.amount, request.money.currency),
            "currency": request.money.currency,
            "reference": reference,
            "paymentDetails": {
                "type": "redirect",
                "data": {
                    "paymentMethod": method,
                    "successUrl": request.callback_url,
                    "errorUrl": request.callback_url,
                },
            },
        }
        try:
            response = self._request(
                "initialize", "POST", "/partner_api/payment_requests", json=payload
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                reference, "jeko payment request outcome is unknown"
            ) from exc

        body = self._json(response)
        redirect_url = body.get("redirectUrl")
        provider_reference = str(body.get("id") or reference)
        if response.status_code >= 400 or not redirect_url:
            return ProviderResult(
                provider_reference=provider_reference,
                status=AttemptStatus.FAILED,
                provider_status=f"http_{response.status_code}",
                failure_code="JEKO_PAYMENT_REQUEST_REJECTED",
                failure_message=self._error_message(body, "jeko did not return a payment page"),
            )
        return ProviderResult(
            provider_reference=provider_reference,
            status=AttemptStatus.REQUIRES_ACTION,
            provider_status=str(body.get("status") or "pending"),
            next_action=NextAction(NextActionType.REDIRECT, url=str(redirect_url)),
        )

    def verify_payment(self, provider_reference: str) -> ProviderResult:
        try:
            response = self._request(
                "verify", "GET", f"/partner_api/payment_requests/{provider_reference}"
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                provider_reference, "jeko verification is unavailable"
            ) from exc
        body = self._json(response)
        if response.status_code >= 400:
            return ProviderResult(
                provider_reference=provider_reference,
                status=AttemptStatus.UNKNOWN,
                provider_status=f"http_{response.status_code}",
                failure_code="JEKO_VERIFICATION_FAILED",
                failure_message=self._error_message(body, "jeko payment verification failed"),
            )
        provider_status = str(body.get("status") or "unknown").lower()
        status = self._normalize_status(provider_status)
        transaction = body.get("transaction") if isinstance(body.get("transaction"), dict) else {}
        amount_block = transaction.get("amount") if isinstance(transaction.get("amount"), dict) else {}
        fees_block = transaction.get("fees") if isinstance(transaction.get("fees"), dict) else {}
        currency = amount_block.get("currency")
        try:
            amount = self._from_jeko_amount(amount_block.get("amount"), currency)
            fee = self._from_jeko_amount(fees_block.get("amount"), currency)
        except ValueError as exc:
            return ProviderResult(
                provider_reference=str(body.get("id") or provider_reference),
                status=AttemptStatus.UNKNOWN,
                provider_status="invalid_response",
                failure_code="JEKO_RESPONSE_INVALID",
                failure_message=str(exc),
            )
        return ProviderResult(
            provider_reference=str(body.get("id") or provider_reference),
            status=status,
            provider_status=provider_status,
            amount=amount,
            currency=str(currency).upper() if currency else None,
            fee=fee,
        )

    def create_payout(self, request: PayoutRequest) -> PayoutResult:
        destination = request.destination or {}
        if destination.get("type") != "mobile_money":
            # Unreachable via correct dispatch (capabilities only claim "mobile_money"), but a
            # destination type this processor doesn't understand must fail loud rather than
            # silently misread a field. Jèko also does bank/RIB payouts; not routed here yet.
            raise ProcessorRequestRejected(
                f"jeko only supports mobile_money payout destinations, got "
                f"type={destination.get('type')!r}"
            )
        phone = destination.get("phone")
        if not phone:
            raise ProcessorRequestRejected("destination.phone is required for a jeko payout")
        method = self._resolve_method(destination.get("network"))

        reference = f"dpo_{request.payout_id.hex}"
        payload: dict[str, Any] = {
            "storeId": self._store_id,
            "amountCents": self._to_jeko_amount(request.money.amount, request.money.currency),
            "currency": request.money.currency,
            "reference": reference,
            "name": request.beneficiary_reference or "DiddiPay beneficiary",
            "paymentMethod": method,
            "identifier": {"reference": str(phone)},
        }
        try:
            response = self._request("payout", "POST", "/partner_api/transfers", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                reference, "jeko payout outcome is unknown"
            ) from exc

        body = self._json(response)
        provider_reference = str(body.get("id") or reference)
        if response.status_code >= 400:
            return PayoutResult(
                provider_reference=provider_reference,
                status=PayoutStatus.FAILED,
                provider_status=str(body.get("id") or f"http_{response.status_code}"),
                failure_code="JEKO_PAYOUT_REJECTED",
                failure_message=self._error_message(body, "jeko rejected the payout"),
            )
        provider_status = str(body.get("status") or "pending").lower()
        return PayoutResult(
            provider_reference=provider_reference,
            status=self._normalize_payout_status(provider_status),
            provider_status=provider_status,
        )

    def verify_payout(self, provider_reference: str) -> PayoutResult:
        try:
            response = self._request(
                "verify_payout", "GET", f"/partner_api/transfers/{provider_reference}"
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                provider_reference, "jeko payout verification is unavailable"
            ) from exc
        body = self._json(response)
        if response.status_code >= 400:
            return PayoutResult(
                provider_reference=provider_reference,
                status=PayoutStatus.PROCESSING,
                provider_status=f"http_{response.status_code}",
                failure_code="JEKO_VERIFICATION_FAILED",
                failure_message=self._error_message(body, "jeko payout not found"),
            )
        provider_status = str(body.get("status") or "unknown").lower()
        return PayoutResult(
            provider_reference=str(body.get("id") or provider_reference),
            status=self._normalize_payout_status(provider_status),
            provider_status=provider_status,
        )

    def parse_webhook(self, raw_body: bytes, headers: Mapping[str, str]):
        raise NotImplementedError(
            "jeko webhooks are not wired yet -- blocked on the BCEAO agreement (SCRUM-555); "
            "use verify_payment / verify_payout / reconciliation instead"
        )

    def refund_payment(self, request: RefundRequest) -> RefundResult:
        raise NotImplementedError(
            "jeko has no generic refund endpoint; refunds exist only via the escrow flow "
            "(POST /partner_api/escrows/{id}/refund), which needs a platform API key and a "
            "Service Provider onboarding -- see docs/evaluation-jeko.md"
        )

    def _resolve_method(self, network: str | None) -> str:
        method = _PAYMENT_METHODS.get(network or "")
        if method is None:
            raise ProcessorRequestRejected(
                f"jeko does not support network={network!r} (expected one of "
                f"{sorted(_PAYMENT_METHODS)})"
            )
        return method

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _error_message(body: dict[str, Any], default: str) -> str:
        # Jèko returns two error shapes, both seen live: validation is
        # {"errors": [{"message", "rule", "field"}]} (422) and business/runtime is
        # {"id", "message", "extras"} (400/401/403, e.g. id="insufficient_balance").
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            message = errors[0].get("message")
            if message:
                return str(message)[:255]
        message = body.get("message") or body.get("id")
        return str(message or default)[:255]

    @classmethod
    def _to_jeko_amount(cls, amount: int, currency: str) -> int:
        # Jèko expects amountCents (XOF x 100, multiple of 100) even though XOF has no subunit.
        return amount * cls._XOF_SUBUNIT_FACTOR if currency.upper() == "XOF" else amount

    @classmethod
    def _from_jeko_amount(cls, value: Any, currency: Any) -> int | None:
        if value is None:
            return None
        amount = int(value)
        if str(currency or "").upper() != "XOF":
            return amount
        quotient, remainder = divmod(amount, cls._XOF_SUBUNIT_FACTOR)
        if remainder:
            raise ValueError("jeko returned a fractional XOF amount")
        return quotient

    @staticmethod
    def _normalize_status(status: str) -> AttemptStatus:
        if status == "success":
            return AttemptStatus.SUCCEEDED
        if status == "error":
            return AttemptStatus.FAILED
        # pending -> still in flight.
        return AttemptStatus.PROCESSING

    @staticmethod
    def _normalize_payout_status(status: str) -> PayoutStatus:
        if status == "success":
            return PayoutStatus.SUCCEEDED
        if status == "error":
            return PayoutStatus.FAILED
        # pending -> still in flight.
        return PayoutStatus.PROCESSING
