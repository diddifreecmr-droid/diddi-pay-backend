"""CinetPay v1 adapter for provider-neutral PaymentIntent collections and payouts.

Targets the CinetPay **v1 mobile-money API** (api.cinetpay.net sandbox /
api.cinetpay.co production), NOT the older "Checkout" API (api-checkout.cinetpay.com/v2,
apikey+site_id). The two are different products with different credentials; this one
authenticates with an `sk_test_`/`sk_live_` api_key plus an api_password, exchanged for a
short-lived JWT at POST /v1/oauth/login and sent as `Authorization: Bearer`.

Collections go through the hosted Payment Page (POST /v1/payment returns a `payment_url`),
so initialize_payment returns NextActionType.REDIRECT -- every consuming app
(DiddiGo/DiddiSend/DiddiFood) already renders that for Paystack, no client change needed.
The direct `channel=PUSH` flow (no URL) is deliberately NOT used: it would need a
"check your phone" screen per app first, same reasoning as PawaPay's Payment-Page choice.

Payouts (create_payout/verify_payout) use POST /v1/transfer / GET /v1/transfer/{id}.
Refunds: CinetPay v1 has **no refund endpoint** (the SDK exposes only payment/transfer/
balance), so refund_payment raises NotImplementedError and REFUND is not in capabilities.

Webhooks: parse_webhook raises NotImplementedError like PawaPay's -- blocked on the BCEAO
agreement (SCRUM-555), a DiddiPay-side gate. CinetPay's own webhook auth is only a
notify_token echo (timing-safe compare, no HMAC secret), so verify_payment/verify_payout +
reconciliation remain the source of truth regardless.

!! NOT re-verified against the live sandbox yet !!  The account provided came with an
api_key but no api_password, and /v1/oauth/login is structurally impossible without both
(proven live: empty password -> 1004 INVALID_PARAMS, wrong password -> 1005
INVALID_CREDENTIALS). Field mappings below follow the official SDK source
(github.com/cinetpay/cinetpay-python) and MUST be re-checked end-to-end against the real
sandbox before this is considered production-ready -- see docs/evaluation-cinetpay.md.
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

# Generic network name (destination/request.network) -> CinetPay payment_method prefix.
_NETWORK_PREFIX = {
    "orange": "OM",
    "moov": "MOOV",
    "mtn": "MTN",
    "wave": "WAVE",
    "free": "FREE",
    "expresso": "EXPRESSO",
    "tmoney": "TMONEY",
    "airtel": "AIRTEL",
    "mpesa": "MPESA",
    "africell": "AFRICELL",
    "zamani": "ZAMANI",
}

# Dial code (no "+") -> CinetPay country code.
_COUNTRY_BY_DIAL_CODE = {
    "225": "CI",  # Cote d'Ivoire
    "226": "BF",  # Burkina Faso
    "223": "ML",  # Mali
    "221": "SN",  # Senegal
    "228": "TG",  # Togo
    "224": "GN",  # Guinea
    "237": "CM",  # Cameroon
    "229": "BJ",  # Benin
    "243": "CD",  # DR Congo
    "227": "NE",  # Niger
}

# payment_method values catalogued by the official SDK (constants/payment_methods.py).
# Used only to reject a payout to a {network, country} combo CinetPay doesn't list -- it is
# NOT proof the method is activated on this account (that needs a live check).
_SUPPORTED_METHODS = frozenset(
    {
        "OM_CI", "MOOV_CI", "MTN_CI", "WAVE_CI",
        "OM_BF", "MOOV_BF", "WAVE_BF",
        "OM_ML", "MOOV_ML",
        "OM_SN", "FREE_SN", "EXPRESSO_SN", "WAVE_SN",
        "MOOV_TG", "TMONEY_TG",
        "OM_GN", "MTN_GN",
        "OM_CM", "MTN_CM",
        "MOOV_BJ", "MTN_BJ",
        "OM_CD", "AIRTEL_CD", "MPESA_CD", "AFRICELL_CD",
        "AIRTEL_NE", "MOOV_NE", "ZAMANI_NE",
    }
)

# Transaction statuses CinetPay treats as terminal failures (constants/statuses.py +
# semantic reading of EXPIRED/USER_* as non-recoverable). Anything not here and not SUCCESS
# is left as "still in flight" so reconciliation keeps polling rather than failing early.
_FAILED_STATUSES = frozenset(
    {
        "FAILED",
        "EXPIRED",
        "NOT_ALLOWED",
        "USER_NOT_FOUND",
        "USER_IS_BLOCKED",
        "OTP_ERROR",
        "OTP_EXPIRED",
        "OPERATION_ERROR",
        "INSUFFICIENT_BALANCE",
    }
)
_SUCCESS_STATUSES = frozenset({"SUCCESS"})


def _digits_only(phone: str) -> str:
    return phone.lstrip("+").replace(" ", "")


def _resolve_base_url(api_key: str, base_url: str | None) -> str:
    if base_url:
        return base_url.rstrip("/")
    # Auto-detect from the key prefix, exactly as the official SDK does.
    return "https://api.cinetpay.co" if api_key.startswith("sk_live_") else "https://api.cinetpay.net"


class CinetpayPaymentProcessor:
    name = "cinetpay"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        # No REFUND: CinetPay v1 has no refund endpoint.
        directions=frozenset({PaymentDirection.COLLECTION, PaymentDirection.PAYOUT}),
        channels=frozenset({"mobile_money"}),
        # Empty, like Paystack: the hosted Payment Page lets the payer pick the network, and
        # declaring networks nobody else declares (e.g. wave) would hijack real traffic via
        # the "dedicated processor" preference -- the exact trap the handoff warns about.
        # Payout network restrictions are enforced inside create_payout instead.
        networks=frozenset(),
    )

    def __init__(
        self,
        *,
        api_key: str,
        api_password: str,
        country: str = "CI",
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("CINETPAY_API_KEY is required to register the cinetpay processor")
        if not api_password:
            raise ValueError(
                "CINETPAY_API_PASSWORD is required to register the cinetpay processor "
                "(an api_key alone cannot obtain the JWT -- /v1/oauth/login needs both)"
            )
        self._api_key = api_key
        self._api_password = api_password
        self._country = country.upper()
        self._base_url = _resolve_base_url(api_key, base_url)
        self._client = client
        self._token: str | None = None

    # -- HTTP plumbing ---------------------------------------------------------------

    def _raw_request(self, method: str, path: str, *, headers: dict[str, str], **kwargs) -> httpx.Response:
        if self._client is not None:
            return self._client.request(method, path, headers=headers, **kwargs)
        with httpx.Client(base_url=self._base_url, timeout=20.0) as client:
            return client.request(method, path, headers=headers, **kwargs)

    def _login(self) -> str:
        started_at = time.perf_counter()
        try:
            response = self._raw_request(
                "POST",
                "/v1/oauth/login",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"api_key": self._api_key, "api_password": self._api_password},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            observe_provider_call(
                provider=self.name,
                operation="login",
                outcome="transport_error",
                duration_seconds=time.perf_counter() - started_at,
            )
            raise ProcessorCallUncertain("login", "cinetpay authentication is unavailable") from exc
        observe_provider_call(
            provider=self.name,
            operation="login",
            outcome="http_error" if response.status_code >= 400 else "returned",
            duration_seconds=time.perf_counter() - started_at,
        )
        body = self._json(response)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        token = body.get("access_token") or data.get("access_token") or data.get("token")
        if not token:
            # Bad credentials or an unexpected shape: a request we structurally cannot make,
            # not an uncertain-outcome money movement.
            raise ProcessorRequestRejected(
                "cinetpay login rejected (check CINETPAY_API_KEY/CINETPAY_API_PASSWORD): "
                f"{body.get('description') or body.get('status') or f'http_{response.status_code}'}"
            )
        return str(token)

    def _token_value(self, *, force_refresh: bool = False) -> str:
        if force_refresh or not self._token:
            self._token = self._login()
        return self._token

    def _request(self, operation: str, method: str, path: str, **kwargs) -> tuple[httpx.Response, dict[str, Any]]:
        """Authenticated request with a single expired-token refresh+retry.

        Returns (response, parsed_json). Transport errors propagate to the caller, which
        maps them to ProcessorCallUncertain with the right provider reference.
        """
        for attempt in range(2):
            token = self._token_value(force_refresh=attempt == 1)
            started_at = time.perf_counter()
            try:
                response = self._raw_request(
                    method,
                    path,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    **kwargs,
                )
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
            body = self._json(response)
            if attempt == 0 and self._is_expired_token(body):
                self._token = None
                continue
            return response, body
        return response, body

    @staticmethod
    def _is_expired_token(body: dict[str, Any]) -> bool:
        return body.get("code") == 1003 or str(body.get("status") or "") == "EXPIRED_TOKEN"

    # -- Collection ------------------------------------------------------------------

    def initialize_payment(self, request: InitializePaymentRequest) -> ProviderResult:
        if not request.customer_email:
            raise ProcessorRequestRejected("customer_email is required by cinetpay checkout")
        if not request.callback_url:
            raise ProcessorRequestRejected(
                "callback_url is required by cinetpay (used as success_url/failed_url)"
            )

        # merchant_transaction_id is derived from attempt_id so a retried HTTP call resubmits
        # the same transaction instead of creating a duplicate; it is also the provider
        # reference we verify against later (GET /v1/payment/{merchant_transaction_id}).
        reference = str(request.attempt_id)
        payload: dict[str, Any] = {
            "currency": request.money.currency,
            "merchant_transaction_id": reference,
            # CinetPay uses the real amount (whole XOF francs); no x100 subunit like Paystack.
            "amount": int(request.money.amount),
            "lang": "fr",
            "designation": (request.business_reference or "DiddiPay payment")[:255],
            "client_email": request.customer_email,
            "client_first_name": str(request.metadata.get("customer_first_name") or "DiddiPay"),
            "client_last_name": str(request.metadata.get("customer_last_name") or "Client"),
            "success_url": request.callback_url,
            "failed_url": request.callback_url,
            # Webhooks are not consumed yet (BCEAO gate); set so CinetPay accepts the request.
            "notify_url": request.callback_url,
            # Verified live: channel="MOBILE_MONEY" returns OPERATION_ERROR on this account,
            # while channel="ALL" returns the hosted Payment Page (payment_url + details.status
            # INITIATED / must_be_redirected:true) and lets the payer pick MM/Wave/card there.
            "channel": "ALL",
        }
        if request.customer_phone:
            payload["client_phone_number"] = _digits_only(request.customer_phone)

        try:
            response, body = self._request("initialize", "POST", "/v1/payment", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(reference, "cinetpay payment outcome is unknown") from exc

        # CinetPay almost always answers HTTP 200 with top-level status "OK"; the real outcome
        # is in `details` (status INITIATED on success) plus the presence of payment_url.
        details = body.get("details") if isinstance(body.get("details"), dict) else {}
        payment_url = body.get("payment_url") or (
            body.get("data", {}).get("payment_url") if isinstance(body.get("data"), dict) else None
        )
        if response.status_code >= 400 or not payment_url:
            return ProviderResult(
                provider_reference=reference,
                status=AttemptStatus.FAILED,
                provider_status=str(details.get("status") or body.get("status") or f"http_{response.status_code}"),
                failure_code="CINETPAY_INITIALIZATION_FAILED",
                failure_message=str(
                    details.get("message")
                    or body.get("description")
                    or body.get("error")
                    or body.get("message")
                    or "cinetpay did not return a payment URL"
                )[:255],
            )
        return ProviderResult(
            provider_reference=reference,
            status=AttemptStatus.REQUIRES_ACTION,
            provider_status="payment_created",
            next_action=NextAction(NextActionType.REDIRECT, url=str(payment_url)),
        )

    def verify_payment(self, provider_reference: str) -> ProviderResult:
        try:
            response, body = self._request("verify", "GET", f"/v1/payment/{provider_reference}")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(provider_reference, "cinetpay verification is unavailable") from exc
        if response.status_code >= 400:
            return ProviderResult(
                provider_reference=provider_reference,
                status=AttemptStatus.UNKNOWN,
                provider_status=f"http_{response.status_code}",
                failure_code="CINETPAY_VERIFICATION_FAILED",
                failure_message=str(body.get("description") or "cinetpay payment verification failed")[:255],
            )
        # Verified live: a verify on an unpaid hosted payment is flat -- top-level
        # {code, status: INITIATED, transaction_id, user}. A finalized one may instead carry
        # the state in `details`. Prefer details.status, then top-level, then any data wrapper.
        details = body.get("details") if isinstance(body.get("details"), dict) else {}
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        provider_status = str(details.get("status") or body.get("status") or data.get("status") or "unknown")
        status = self._normalize_attempt_status(provider_status)
        amount, currency = self._parse_amount_currency(data)
        failure_code, failure_message = (
            ("CINETPAY_PAYMENT_FAILED", str(details.get("message") or provider_status)[:255])
            if status == AttemptStatus.FAILED
            else (None, None)
        )
        return ProviderResult(
            provider_reference=str(body.get("transaction_id") or data.get("transaction_id") or provider_reference),
            status=status,
            provider_status=provider_status.lower(),
            amount=amount,
            currency=currency,
            failure_code=failure_code,
            failure_message=failure_message,
        )

    # -- Payout ----------------------------------------------------------------------

    def create_payout(self, request: PayoutRequest) -> PayoutResult:
        destination = request.destination or {}
        if destination.get("type") != "mobile_money":
            raise ProcessorRequestRejected(
                f"cinetpay only supports mobile_money payout destinations, got type={destination.get('type')!r}"
            )
        phone = destination.get("phone")
        if not phone:
            raise ProcessorRequestRejected("destination.phone is required for a cinetpay payout")
        payment_method = self._resolve_payment_method(network=destination.get("network"), phone=phone)

        reference = str(request.payout_id)
        payload: dict[str, Any] = {
            "currency": request.money.currency,
            "merchant_transaction_id": reference,
            "phone_number": "+" + _digits_only(phone),
            "amount": int(request.money.amount),
            "payment_method": payment_method,
            "reason": (request.business_reference or "DiddiPay payout")[:255],
        }
        notify_url = request.metadata.get("notify_url")
        if notify_url:
            payload["notify_url"] = str(notify_url)

        try:
            response, body = self._request("payout", "POST", "/v1/transfer", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(reference, "cinetpay payout outcome is unknown") from exc

        # Verified live: top-level status is "OK"/"FAILED" and the real reason sits in
        # `details` (e.g. details.status INSUFFICIENT_BALANCE, details.message). transaction_id
        # ("ER-T-...") is top-level and is what GET /v1/transfer/{id} expects; fall back to our
        # merchant id if absent.
        details = body.get("details") if isinstance(body.get("details"), dict) else {}
        top_status = str(body.get("status") or "")
        detail_status = str(details.get("status") or "")
        provider_reference = str(body.get("transaction_id") or reference)
        if (
            response.status_code >= 400
            or top_status in _FAILED_STATUSES
            or detail_status in _FAILED_STATUSES
        ):
            return PayoutResult(
                provider_reference=provider_reference,
                status=PayoutStatus.FAILED,
                provider_status=(detail_status or top_status).lower() or f"http_{response.status_code}",
                failure_code="CINETPAY_PAYOUT_REJECTED",
                failure_message=str(
                    details.get("message")
                    or body.get("description")
                    or body.get("message")
                    or detail_status
                    or "cinetpay rejected the payout"
                )[:255],
            )
        return PayoutResult(
            provider_reference=provider_reference,
            status=PayoutStatus.PROCESSING,
            provider_status=(detail_status or top_status).lower() or "accepted",
        )

    def verify_payout(self, provider_reference: str) -> PayoutResult:
        try:
            response, body = self._request("verify_payout", "GET", f"/v1/transfer/{provider_reference}")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProcessorCallUncertain(provider_reference, "cinetpay payout verification is unavailable") from exc
        if response.status_code >= 400:
            return PayoutResult(
                provider_reference=provider_reference,
                status=PayoutStatus.PROCESSING,
                provider_status=f"http_{response.status_code}",
                failure_code="CINETPAY_VERIFICATION_FAILED",
                failure_message="cinetpay payout not found",
            )
        details = body.get("details") if isinstance(body.get("details"), dict) else {}
        provider_status = str(details.get("status") or body.get("status") or "unknown")
        status = self._normalize_payout_status(provider_status)
        failure_code, failure_message = (
            ("CINETPAY_PAYOUT_FAILED", str(details.get("message") or provider_status)[:255])
            if status == PayoutStatus.FAILED
            else (None, None)
        )
        return PayoutResult(
            provider_reference=str(body.get("transaction_id") or provider_reference),
            status=status,
            provider_status=provider_status.lower(),
            failure_code=failure_code,
            failure_message=failure_message,
        )

    # -- Unsupported -----------------------------------------------------------------

    def refund_payment(self, request: RefundRequest) -> RefundResult:
        raise NotImplementedError(
            "cinetpay v1 has no refund endpoint (only payment/transfer/balance); refunds are "
            "not available on this provider"
        )

    def parse_webhook(self, raw_body: bytes, headers: Mapping[str, str]):
        raise NotImplementedError(
            "cinetpay webhooks are not wired yet -- blocked on the BCEAO agreement (SCRUM-555); "
            "CinetPay's webhook auth is only a notify_token echo anyway, so use "
            "verify_payment / reconciliation instead"
        )

    # -- Helpers ---------------------------------------------------------------------

    def _resolve_payment_method(self, *, network: str | None, phone: str) -> str:
        prefix = _NETWORK_PREFIX.get((network or "").lower())
        if prefix is None:
            raise ProcessorRequestRejected(
                f"cinetpay does not support network={network!r} (expected one of {sorted(_NETWORK_PREFIX)})"
            )
        digits = _digits_only(phone)
        country = next(
            (code for dial, code in _COUNTRY_BY_DIAL_CODE.items() if digits.startswith(dial)),
            None,
        )
        if country is None:
            raise ProcessorRequestRejected(f"cinetpay does not recognise the dial code in phone={phone!r}")
        method = f"{prefix}_{country}"
        if method not in _SUPPORTED_METHODS:
            raise ProcessorRequestRejected(f"cinetpay payment_method {method!r} is not in the supported catalogue")
        return method

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _parse_amount_currency(data: dict[str, Any]) -> tuple[int | None, str | None]:
        raw_amount = data.get("amount")
        currency = data.get("currency")
        if raw_amount is None:
            return None, (str(currency).upper() if currency else None)
        try:
            amount = int(float(raw_amount))
        except (TypeError, ValueError):
            amount = None
        return amount, (str(currency).upper() if currency else None)

    @staticmethod
    def _normalize_attempt_status(status: str) -> AttemptStatus:
        upper = status.upper()
        if upper in _SUCCESS_STATUSES:
            return AttemptStatus.SUCCEEDED
        if upper in _FAILED_STATUSES:
            return AttemptStatus.FAILED
        # INITIATED, PENDING, WAITING_FOR_CUSTOMER, etc. -> still in flight.
        return AttemptStatus.PROCESSING

    @staticmethod
    def _normalize_payout_status(status: str) -> PayoutStatus:
        upper = status.upper()
        if upper in _SUCCESS_STATUSES:
            return PayoutStatus.SUCCEEDED
        if upper in _FAILED_STATUSES:
            return PayoutStatus.FAILED
        return PayoutStatus.PROCESSING
