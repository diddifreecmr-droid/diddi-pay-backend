"""PawaPay adapter for provider-neutral PaymentIntent collections (mtn_momo/orange_money/moov).

Collections go through PawaPay's **Payment Page** (POST /v2/paymentpage), not the direct
push/PIN API -- deliberate choice: the Payment Page returns a real redirectUrl, so
initialize_payment can return NextActionType.REDIRECT, which every consuming app
(DiddiGo/DiddiSend/DiddiFood) already knows how to render for Paystack. The direct
/v2/deposits push API (no URL, NextActionType.MOBILE_MONEY_PROMPT) is the better long-term
UX for mobile money but needs each app to build a "check your phone" screen first -- ship
Payment Page now, migrate to the native push per-app later once that UI exists. Both reuse
the same wallet-proven operator/country resolution (SCRUM-406).

Known Payment Page constraint (verified against the real API): there is no field to pin a
specific operator, only `country` -- the customer could complete with a different operator
in the same country than the one checked in _resolve_operator here. verify_payment's read of
the real deposit afterwards is the source of truth regardless. Also verified live: the
deposit does not exist in PawaPay's system at all until the customer opens and acts on the
page, so NOT_FOUND from verify_payment is treated as still-pending, not a failure.

Payouts (create_payout/verify_payout) use PayoutRequest.destination -- a generic, provider-
agnostic {"type": "mobile_money", "phone": ..., "network": ...} dict (see
presentation/schemas.py's BeneficiaryDestination). This processor is the only thing that
knows "mobile_money" maps to PawaPay's phone+operator-family model; callers and the
application layer never see PawaPay-specific shapes.

Deliberately NOT wired here:
  * Wave -- not even active on this PawaPay account (checked GET /v2/active-conf: every
    live provider uses authType=PROVIDER_AUTH, Wave isn't listed at all). REDIRECT_AUTH
    needs a real example to design against and can't be requested until PawaPay enables it
    on the account. See ADR/ticket before adding it.
  * Webhooks -- parse_webhook raises NotImplementedError like SandboxPaymentProcessor's.
    Blocked on the BCEAO agreement (SCRUM-555), a DiddiPay-side compliance gate, not a
    PawaPay limitation. Confirmation goes through verify_payment/verify_payout /
    reconciliation.

refund_payment (verified against the real sandbox, POST /v2/refunds) follows the same
async ACCEPTED -> COMPLETED/FAILED shape as deposits/payouts. Note: refunds.py has no
reconciliation path for refunds at all (RefundRepositoryPort only has add()), so a refund
that comes back PROCESSING here has no way to ever resolve to a final status today --
a pre-existing gap in the module, not something this file can fix alone.
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
    RefundStatus,
)

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
        directions=frozenset(
            {PaymentDirection.COLLECTION, PaymentDirection.PAYOUT, PaymentDirection.REFUND}
        ),
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
        if not request.callback_url:
            raise ProcessorRequestRejected(
                "callback_url is required by pawapay's Payment Page (used as returnUrl)"
            )

        # Sanity-check the intended network before sending the customer anywhere -- but note
        # the Payment Page itself has no field to pin the exact operator (verified against the
        # real API: only country-level restriction exists), so the customer could still
        # complete with a different operator in the same country than the one checked here.
        # verify_payment's eventual read of the real deposit is the source of truth either way.
        operator = self._resolve_operator(network=request.network, phone=request.customer_phone)
        country = operator.rsplit("_", 1)[-1]
        # Deterministic from attempt_id (unlike the wallet's fresh uuid4 per call) so a retried
        # HTTP call resubmits the same session instead of minting a new one.
        deposit_id = str(request.attempt_id)
        payload: dict[str, Any] = {
            "depositId": deposit_id,
            "returnUrl": request.callback_url,
            "amountDetails": {"amount": str(request.money.amount), "currency": request.money.currency},
            "phoneNumber": _digits_only(request.customer_phone),
            "country": country,
            "reason": (request.business_reference or "DiddiPay payment")[:50],
        }
        try:
            response = self._request("initialize", "POST", "/v2/paymentpage", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                deposit_id, "pawapay payment page outcome is unknown"
            ) from exc

        body = self._json(response)
        redirect_url = body.get("redirectUrl")
        if response.status_code >= 400 or not redirect_url:
            return ProviderResult(
                provider_reference=deposit_id,
                status=AttemptStatus.FAILED,
                provider_status=f"http_{response.status_code}",
                failure_code="PAWAPAY_PAYMENT_PAGE_REJECTED",
                failure_message=str(
                    body.get("message") or "pawapay did not return a payment page"
                )[:255],
            )
        return ProviderResult(
            provider_reference=deposit_id,
            status=AttemptStatus.REQUIRES_ACTION,
            provider_status="page_created",
            next_action=NextAction(NextActionType.REDIRECT, url=str(redirect_url)),
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
        if response.status_code >= 400:
            return ProviderResult(
                provider_reference=provider_reference,
                status=AttemptStatus.UNKNOWN,
                provider_status=f"http_{response.status_code}",
                failure_code="PAWAPAY_VERIFICATION_FAILED",
                failure_message="pawapay deposit verification failed",
            )
        if body.get("status") != "FOUND":
            # Normal, not a failure: with the Payment Page flow, the deposit does not exist in
            # PawaPay's system at all until the customer actually opens and acts on the hosted
            # page (verified live) -- NOT_FOUND here just means "still waiting", same as
            # PENDING/PROCESSING. The page link expires after 15 minutes; a genuinely abandoned
            # session is left to the existing max-age reconciliation timeout, not detected here.
            return ProviderResult(
                provider_reference=provider_reference,
                status=AttemptStatus.PROCESSING,
                provider_status="page_not_yet_completed",
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
        status = self._normalize_status(provider_status)
        failure_code, failure_message = (
            self._failure_reason(data) if status == AttemptStatus.FAILED else (None, None)
        )
        return ProviderResult(
            provider_reference=str(data.get("depositId") or provider_reference),
            status=status,
            provider_status=provider_status,
            amount=amount,
            currency=str(data.get("currency") or "").upper() or None,
            failure_code=failure_code,
            failure_message=failure_message,
        )

    def create_payout(self, request: PayoutRequest) -> PayoutResult:
        destination = request.destination or {}
        if destination.get("type") != "mobile_money":
            # Unreachable via correct dispatch (capabilities only ever claim "mobile_money"),
            # but a destination type this processor doesn't understand must fail loud, not
            # silently misinterpret a field that means something else for another type.
            raise ProcessorRequestRejected(
                f"pawapay only supports mobile_money payout destinations, got "
                f"type={destination.get('type')!r}"
            )
        phone = destination.get("phone")
        if not phone:
            raise ProcessorRequestRejected("destination.phone is required for a pawapay payout")
        operator = self._resolve_operator(network=destination.get("network"), phone=phone)
        payout_id = str(request.payout_id)
        payload: dict[str, Any] = {
            "payoutId": payout_id,
            "recipient": {
                "type": "MMO",
                "accountDetails": {"phoneNumber": _digits_only(phone), "provider": operator},
            },
            "amount": str(request.money.amount),
            "currency": request.money.currency,
            "clientReferenceId": str(request.payout_id),
        }
        try:
            response = self._request("payout", "POST", "/v2/payouts", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                payout_id, "pawapay payout outcome is unknown"
            ) from exc

        body = self._json(response)
        if response.status_code >= 400:
            return PayoutResult(
                provider_reference=payout_id,
                status=PayoutStatus.FAILED,
                provider_status=f"http_{response.status_code}",
                failure_code="PAWAPAY_PAYOUT_REJECTED",
                failure_message=str(
                    (body.get("failureReason") or {}).get("failureMessage")
                    or body.get("message")
                    or "pawapay rejected the payout"
                )[:255],
            )
        provider_status = str(body.get("status") or "")
        if provider_status == "REJECTED":
            return PayoutResult(
                provider_reference=payout_id,
                status=PayoutStatus.FAILED,
                provider_status="rejected",
                failure_code="PAWAPAY_PAYOUT_REJECTED",
                failure_message=str(
                    (body.get("failureReason") or {}).get("failureMessage")
                    or "pawapay rejected the payout"
                )[:255],
            )
        return PayoutResult(
            provider_reference=payout_id,
            status=PayoutStatus.PROCESSING,
            provider_status=provider_status.lower() or "accepted",
        )

    def verify_payout(self, provider_reference: str) -> PayoutResult:
        try:
            response = self._request("verify_payout", "GET", f"/v2/payouts/{provider_reference}")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                provider_reference, "pawapay payout verification is unavailable"
            ) from exc
        body = self._json(response)
        if response.status_code >= 400 or body.get("status") != "FOUND":
            return PayoutResult(
                provider_reference=provider_reference,
                status=PayoutStatus.PROCESSING,
                provider_status=f"http_{response.status_code}",
                failure_code="PAWAPAY_VERIFICATION_FAILED",
                failure_message="pawapay payout not found",
            )
        data = body.get("data") or {}
        provider_status = str(data.get("status") or "unknown").lower()
        status = self._normalize_payout_status(provider_status)
        failure_code, failure_message = (
            self._failure_reason(data) if status == PayoutStatus.FAILED else (None, None)
        )
        return PayoutResult(
            provider_reference=str(data.get("payoutId") or provider_reference),
            status=status,
            provider_status=provider_status,
            failure_code=failure_code,
            failure_message=failure_message,
        )

    def parse_webhook(self, raw_body: bytes, headers):
        raise NotImplementedError(
            "pawapay webhooks are not wired yet -- blocked on the BCEAO agreement "
            "(SCRUM-555); use verify_payment / reconciliation instead"
        )

    def refund_payment(self, request: RefundRequest) -> RefundResult:
        # Verified against the real PawaPay sandbox (POST /v2/refunds, GET /v2/refunds/{id}):
        # refunds are real and follow the same async ACCEPTED/REJECTED -> COMPLETED/FAILED
        # shape as deposits and payouts. request.provider_reference is the original deposit's
        # depositId (see RefundUseCases.create: provider_reference=attempt.provider_reference).
        #
        # refunds.py has no reconciliation/verify path for refunds at all (RefundRepositoryPort
        # only has add()) -- unlike deposits/payouts, a refund that's still PROCESSING after
        # this call has no way to ever resolve to a final status today. That's a pre-existing
        # gap in the module, not something added here; this just doesn't paper over it by
        # pretending ACCEPTED means SUCCEEDED.
        refund_id = str(request.refund_id)
        payload: dict[str, Any] = {
            "refundId": refund_id,
            "depositId": request.provider_reference,
            "amount": str(request.money.amount),
            "currency": request.money.currency,
        }
        try:
            response = self._request("refund", "POST", "/v2/refunds", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(
                refund_id, "pawapay refund outcome is unknown"
            ) from exc

        body = self._json(response)
        if response.status_code >= 400:
            return RefundResult(
                provider_reference=refund_id,
                status=RefundStatus.FAILED,
                provider_status=f"http_{response.status_code}",
                failure_code="PAWAPAY_REFUND_REJECTED",
                failure_message=str(
                    (body.get("failureReason") or {}).get("failureMessage")
                    or body.get("message")
                    or "pawapay rejected the refund"
                )[:255],
            )
        provider_status = str(body.get("status") or "")
        if provider_status == "REJECTED":
            return RefundResult(
                provider_reference=refund_id,
                status=RefundStatus.FAILED,
                provider_status="rejected",
                failure_code="PAWAPAY_REFUND_REJECTED",
                failure_message=str(
                    (body.get("failureReason") or {}).get("failureMessage")
                    or "pawapay rejected the refund"
                )[:255],
            )
        return RefundResult(
            provider_reference=refund_id,
            status=RefundStatus.PROCESSING,
            provider_status=provider_status.lower() or "accepted",
        )

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _failure_reason(data: dict[str, Any]) -> tuple[str | None, str | None]:
        # Only present when a deposit/payout is found to have failed *asynchronously*
        # (discovered via verify_payment/verify_payout polling) -- the immediate REJECTED
        # response from initiate_* carries the same shape but is handled inline there.
        reason = data.get("failureReason") or {}
        message = reason.get("failureMessage")
        return reason.get("failureCode"), (str(message)[:255] if message else None)

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
    def _normalize_payout_status(status: str) -> PayoutStatus:
        if status == "completed":
            return PayoutStatus.SUCCEEDED
        if status == "failed":
            return PayoutStatus.FAILED
        # submitted, processing, accepted -> still in flight.
        return PayoutStatus.PROCESSING

    @staticmethod
    def _normalize_status(status: str) -> AttemptStatus:
        if status == "completed":
            return AttemptStatus.SUCCEEDED
        if status == "failed":
            return AttemptStatus.FAILED
        # submitted, processing, accepted -> still in flight.
        return AttemptStatus.PROCESSING
