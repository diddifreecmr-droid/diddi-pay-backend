"""PawaPay adapter for provider-neutral PaymentIntent collections (mobile money push/PIN
rail only: mtn_momo/orange_money/moov via POST /v2/deposits). Mirrors the proven wallet
integration (`wallet/infra/pawapay_gateway.py`, verified against the real PawaPay sandbox,
SCRUM-406) but conforms to this module's own PaymentProcessorPort contract instead of the
wallet's simpler one.

Deliberately NOT wired here:
  * Wave -- PawaPay's docs confirm Wave uses REDIRECT_AUTH, a fundamentally different flow
    (the customer is redirected to authorise, not prompted on their phone). That needs its
    own design, not a variant of this one. See ADR/ticket before adding it.
  * Payouts -- PaymentDirection.PAYOUT is not declared. PawaPay's payout API needs a real
    phone number + operator family per recipient; PayoutRequest only carries an opaque
    beneficiary_reference today (a restaurant/driver UUID for DiddiFood). Needs a contract
    change coordinated with the calling modules before this can be real.
  * Refunds -- PawaPay has no refund concept for a mobile money push; a "refund" would have
    to be a payout back to the original payer. Deferred.
  * Webhooks -- parse_webhook raises NotImplementedError like SandboxPaymentProcessor's.
    Blocked on the BCEAO agreement (SCRUM-555), a DiddiPay-side compliance gate, not a
    PawaPay limitation. Confirmation goes through verify_payment / the reconciliation worker.
"""

from __future__ import annotations

import time
from decimal import Decimal
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
    ProcessorCapabilities,
    ProviderResult,
    RefundRequest,
    RefundResult,
)
from payfund_app.modules.payments.domain import AttemptStatus, NextAction, NextActionType

# Generic network name (InitializePaymentRequest.network) -> PawaPay operator family.
_PROVIDER_FAMILIES = {
    "mtn": "MTN_MOMO",
    "orange": "ORANGE",
    "moov": "MOOV",
}

# Dial code (no "+") -> PawaPay country code. Kept in sync with wallet/infra/pawapay_gateway.py
# by hand -- small and stable enough that duplicating it beats a cross-module import between
# payments and wallet.
_COUNTRY_BY_DIAL_CODE = {
    "225": "CIV",  # Cote d'Ivoire
    "229": "BEN",  # Benin
    "226": "BFA",  # Burkina Faso
    "221": "SEN",  # Senegal
}

# Family+country combinations actually active on the PawaPay account (verified via
# GET /v2/active-conf). Keep in sync with the wallet's copy if new rails get activated.
_SUPPORTED_OPERATORS = {
    "MOOV_BEN",
    "MTN_MOMO_BEN",
    "MOOV_BFA",
    "MOOV_CIV",
    "MTN_MOMO_CIV",
    "ORANGE_CIV",
    "ORANGE_SEN",
}


def _digits_only(phone: str) -> str:
    return phone.lstrip("+").replace(" ", "")


class PawapayPaymentProcessor:
    name = "pawapay"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        directions=frozenset({PaymentDirection.COLLECTION}),
        channels=frozenset({"mobile_money"}),
        networks=frozenset(_PROVIDER_FAMILIES),
    )

    def __init__(
        self,
        *,
        api_token: str,
        base_url: str = "https://api.sandbox.pawapay.io",
        client: httpx.Client | None = None,
    ) -> None:
        if not api_token:
            raise ValueError("PAWAPAY_API_TOKEN is required to register the pawapay processor")
        self._api_token = api_token
        self._base_url = base_url.rstrip("/")
        self._client = client

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
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

    def _resolve_operator(self, *, network: str | None, phone: str) -> str:
        family = _PROVIDER_FAMILIES.get(network or "")
        if family is None:
            raise ProcessorRequestRejected(
                f"pawapay does not support network={network!r} (expected one of "
                f"{sorted(_PROVIDER_FAMILIES)})"
            )
        digits = _digits_only(phone)
        country = next(
            (code for prefix, code in _COUNTRY_BY_DIAL_CODE.items() if digits.startswith(prefix)),
            None,
        )
        if country is None:
            raise ProcessorRequestRejected(
                f"pawapay does not recognise the dial code in phone={phone!r}"
            )
        operator = f"{family}_{country}"
        if operator not in _SUPPORTED_OPERATORS:
            raise ProcessorRequestRejected(
                f"pawapay operator {operator!r} is not active on this account"
            )
        return operator

    def initialize_payment(self, request: InitializePaymentRequest) -> ProviderResult:
        if not request.customer_phone:
            raise ProcessorRequestRejected("customer_phone is required by pawapay deposits")

        operator = self._resolve_operator(network=request.network, phone=request.customer_phone)
        # Deterministic from attempt_id (unlike the wallet's fresh uuid4 per call) so a retried
        # HTTP call reuses the same depositId -- PawaPay documents POST /v2/deposits as safe to
        # resubmit with the same depositId.
        deposit_id = str(request.attempt_id)
        payload: dict[str, Any] = {
            "depositId": deposit_id,
            "payer": {
                "type": "MMO",
                "accountDetails": {
                    "phoneNumber": _digits_only(request.customer_phone),
                    "provider": operator,
                },
            },
            # PawaPay expects an integer XOF amount (decimalsInAmount=NONE), unlike Paystack's
            # artificial x100.
            "amount": str(request.money.amount),
            "currency": request.money.currency,
            "clientReferenceId": str(request.attempt_id),
        }
        try:
            response = self._request("initialize", "POST", "/v2/deposits", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                deposit_id, "pawapay deposit outcome is unknown"
            ) from exc

        body = self._json(response)
        if response.status_code >= 400:
            return ProviderResult(
                provider_reference=deposit_id,
                status=AttemptStatus.FAILED,
                provider_status=f"http_{response.status_code}",
                failure_code="PAWAPAY_DEPOSIT_REJECTED",
                failure_message=str(
                    (body.get("failureReason") or {}).get("failureMessage")
                    or body.get("message")
                    or "pawapay rejected the deposit"
                )[:255],
            )
        provider_status = str(body.get("status") or "")
        if provider_status == "REJECTED":
            return ProviderResult(
                provider_reference=deposit_id,
                status=AttemptStatus.FAILED,
                provider_status="rejected",
                failure_code="PAWAPAY_DEPOSIT_REJECTED",
                failure_message=str(
                    (body.get("failureReason") or {}).get("failureMessage")
                    or "pawapay rejected the deposit"
                )[:255],
            )
        # ACCEPTED / DUPLICATE_IGNORED: the request is queued, PawaPay pushes a PIN prompt to
        # the payer's phone. There is no URL to redirect to -- the frontend must tell the
        # customer to check their phone instead of rendering a checkout link.
        return ProviderResult(
            provider_reference=deposit_id,
            status=AttemptStatus.REQUIRES_ACTION,
            provider_status=provider_status.lower() or "accepted",
            next_action=NextAction(
                NextActionType.MOBILE_MONEY_PROMPT,
                instructions=(
                    "Composez votre code PIN Mobile Money sur votre telephone pour confirmer "
                    "le paiement."
                ),
            ),
        )

    def verify_payment(self, provider_reference: str) -> ProviderResult:
        try:
            response = self._request(
                "verify", "GET", f"/v2/deposits/{provider_reference}"
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                provider_reference, "pawapay verification is unavailable"
            ) from exc
        body = self._json(response)
        if response.status_code >= 400 or body.get("status") != "FOUND":
            return ProviderResult(
                provider_reference=provider_reference,
                status=AttemptStatus.UNKNOWN,
                provider_status=f"http_{response.status_code}",
                failure_code="PAWAPAY_VERIFICATION_FAILED",
                failure_message="pawapay deposit not found",
            )
        data = body.get("data") or {}
        provider_status = str(data.get("status") or "unknown").lower()
        try:
            amount = self._parse_amount(data.get("amount"))
        except ValueError as exc:
            return ProviderResult(
                provider_reference=str(data.get("depositId") or provider_reference),
                status=AttemptStatus.UNKNOWN,
                provider_status="invalid_response",
                failure_code="PAWAPAY_RESPONSE_INVALID",
                failure_message=str(exc),
            )
        return ProviderResult(
            provider_reference=str(data.get("depositId") or provider_reference),
            status=self._normalize_status(provider_status),
            provider_status=provider_status,
            amount=amount,
            currency=str(data.get("currency") or "").upper() or None,
        )

    def parse_webhook(self, raw_body: bytes, headers):
        raise NotImplementedError(
            "pawapay webhooks are not wired yet -- blocked on the BCEAO agreement "
            "(SCRUM-555); use verify_payment / reconciliation instead"
        )

    def refund_payment(self, request: RefundRequest) -> RefundResult:
        # PawaPay has no refund concept for a mobile money push -- reversing one means a
        # payout back to the original payer, not an API call on the original deposit. Raising
        # (rather than faking a RefundResult) keeps this honest: refunds.py's RefundUseCases
        # does not currently catch ProcessorRequestRejected around this call (only
        # ProcessorCallUncertain), so this surfaces as an unhandled error today -- a
        # pre-existing gap in refunds.py, not something this change papers over.
        raise ProcessorRequestRejected(
            "pawapay collections cannot be refunded via this API -- reverse with a manual "
            "payout to the original payer instead"
        )

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _parse_amount(value: object) -> int | None:
        if value is None:
            return None
        # PawaPay returns a decimal string ("100.00") even though XOF has no subunit.
        amount = Decimal(str(value))
        if amount != amount.to_integral_value():
            raise ValueError("pawapay returned a fractional XOF deposit amount")
        return int(amount)

    @staticmethod
    def _normalize_status(status: str) -> AttemptStatus:
        if status == "completed":
            return AttemptStatus.SUCCEEDED
        if status == "failed":
            return AttemptStatus.FAILED
        # submitted, processing, accepted -> still in flight.
        return AttemptStatus.PROCESSING
