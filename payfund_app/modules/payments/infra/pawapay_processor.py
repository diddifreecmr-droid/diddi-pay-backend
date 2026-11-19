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

Known callback behavior:
  * PawaPay sends payout callbacks asynchronously. The callback is an inbox signal; the
    authenticated GET /v2/payouts/{payoutId} remains the source of truth before accounting.
  * Signed callbacks are supported when PAWAPAY_CALLBACK_SIGNATURE_REQUIRED=true. Unsigned
    staging callbacks remain supported explicitly so an existing sandbox callback can be
    migrated without taking the payout API offline.

Deliberately NOT wired here:
  * Wave -- not even active on this PawaPay account (checked GET /v2/active-conf: every
    live provider uses authType=PROVIDER_AUTH, Wave isn't listed at all). REDIRECT_AUTH
    needs a real example to design against and can't be requested until PawaPay enables it
    on the account. See ADR/ticket before adding it.
  * Deposit callbacks -- the payout callback is wired here; deposits still use
    verify_payment/reconciliation until their own callback contract is needed.

refund_payment (verified against the real sandbox, POST /v2/refunds) follows the same
async ACCEPTED -> COMPLETED/FAILED shape as deposits/payouts. Note: refunds.py has no
reconciliation path for refunds at all (RefundRepositoryPort only has add()), so a refund
that comes back PROCESSING here has no way to ever resolve to a final status today --
a pre-existing gap in the module, not something this file can fix alone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import threading
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx

from payfund_app.core.observability.business_metrics import observe_provider_call
from payfund_app.modules.payments.application.errors import (
    ProcessorCallUncertain,
    ProcessorRequestRejected,
    ProcessorWebhookRejected,
)
from payfund_app.modules.payments.application.ports import (
    InitializePaymentRequest,
    PaymentDirection,
    PayoutProviderEvent,
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
        callback_url: str | None = None,
        callback_signature_required: bool = False,
        public_keys_url: str | None = None,
    ) -> None:
        if not api_token:
            raise ValueError("PAWAPAY_API_TOKEN is required to register the pawapay processor")
        self._api_token = api_token
        self._base_url = base_url.rstrip("/")
        self._client = client
        self.callback_url = callback_url.rstrip("/") if callback_url else None
        self._callback_signature_required = callback_signature_required
        self._public_keys_url = public_keys_url or f"{self._base_url}/v2/public-key/http"
        self._public_key_cache: dict[str, tuple[Any, float]] = {}
        self._public_keys_fetched_at = 0.0
        self._public_key_lock = threading.Lock()

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

    def parse_payout_webhook(
        self,
        raw_body: bytes,
        headers,
        *,
        callback_url: str | None = None,
    ) -> PayoutProviderEvent:
        """Validate and normalize the official PawaPay payout callback payload."""
        self._verify_callback_signature(
            raw_body,
            headers,
            callback_url=callback_url or self.callback_url,
        )
        try:
            body = httpx.Response(200, content=raw_body).json()
        except ValueError as exc:
            raise ProcessorWebhookRejected("pawapay callback body is not valid JSON") from exc
        if not isinstance(body, dict):
            raise ProcessorWebhookRejected("pawapay callback body must be an object")

        payout_id = str(body.get("payoutId") or "")
        if not payout_id or len(payout_id) > 160:
            raise ProcessorWebhookRejected("pawapay callback payoutId is missing or invalid")
        provider_status = str(body.get("status") or "").upper()
        status_map = {
            "COMPLETED": PayoutStatus.SUCCEEDED,
            "FAILED": PayoutStatus.FAILED,
            "PROCESSING": PayoutStatus.PROCESSING,
            "ENQUEUED": PayoutStatus.PROCESSING,
            "ACCEPTED": PayoutStatus.PROCESSING,
        }
        status = status_map.get(provider_status)
        if status is None:
            raise ProcessorWebhookRejected(
                f"pawapay callback status is unsupported: {provider_status!r}"
            )
        try:
            amount = self._parse_amount(body.get("amount"))
        except (TypeError, ValueError) as exc:
            raise ProcessorWebhookRejected("pawapay callback amount is invalid") from exc
        if amount is None or amount <= 0:
            raise ProcessorWebhookRejected("pawapay callback amount is required")
        currency = str(body.get("currency") or "").upper()
        if not currency:
            raise ProcessorWebhookRejected("pawapay callback currency is required")

        failure_code, failure_message = (
            self._failure_reason(body) if status == PayoutStatus.FAILED else (None, None)
        )
        sanitized_payload = {
            key: body[key]
            for key in (
                "payoutId",
                "status",
                "amount",
                "currency",
                "country",
                "created",
                "providerTransactionId",
            )
            if key in body
        }
        if failure_code or failure_message:
            sanitized_payload["failureReason"] = {
                "failureCode": failure_code,
                "failureMessage": failure_message,
            }
        return PayoutProviderEvent(
            event_key=f"payout:{payout_id}:{provider_status.lower()}",
            event_type=f"payout.{provider_status.lower()}",
            provider_reference=payout_id,
            status=status,
            provider_status=provider_status.lower(),
            amount=amount,
            currency=currency,
            failure_code=failure_code,
            failure_message=failure_message,
            sanitized_payload=sanitized_payload,
        )

    def parse_webhook(self, raw_body: bytes, headers):
        raise NotImplementedError(
            "pawapay deposit webhooks are not wired yet (BCEAO compliance gate); "
            "use verify_payment / reconciliation"
        )

    def _verify_callback_signature(
        self,
        raw_body: bytes,
        headers,
        *,
        callback_url: str | None,
    ) -> None:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        signature_input = normalized.get("signature-input")
        signature = normalized.get("signature")
        digest = normalized.get("content-digest")
        signed = any((signature_input, signature, digest))
        if not signed:
            if self._callback_signature_required:
                raise ProcessorWebhookRejected("pawapay callback signature headers are required")
            return
        if not callback_url or not signature_input or not signature or not digest:
            raise ProcessorWebhookRejected("pawapay callback signature headers are incomplete")

        expected_digest = base64.b64encode(hashlib.sha512(raw_body).digest()).decode("ascii")
        digest_match = re.fullmatch(r"sha-512=:([^:]+):", digest)
        if digest_match is None or not hmac.compare_digest(
            digest_match.group(1), expected_digest
        ):
            raise ProcessorWebhookRejected("pawapay callback content digest is invalid")

        input_match = re.match(
            r"(?P<label>[A-Za-z0-9_-]+)=(?P<params>\([^)]*\)(?:;[^,]+)?)",
            signature_input,
        )
        if input_match is None:
            raise ProcessorWebhookRejected("pawapay callback signature-input is invalid")
        label = input_match.group("label")
        signature_params = input_match.group("params")
        components = re.findall(r'"([^"]+)"', signature_params.split(";", 1)[0])
        required = {"@method", "@authority", "@path", "signature-date", "content-digest"}
        if not required.issubset(components):
            raise ProcessorWebhookRejected("pawapay callback signature coverage is incomplete")
        expires_match = re.search(r";expires=(\d+)", signature_params)
        if expires_match and time.time() > int(expires_match.group(1)):
            raise ProcessorWebhookRejected("pawapay callback signature has expired")
        key_id_match = re.search(r';keyid="([^"]+)"', signature_params)
        if key_id_match is None:
            raise ProcessorWebhookRejected("pawapay callback signature keyid is missing")
        signature_match = re.search(
            rf"(?:^|,)\s*{re.escape(label)}=:([^:]+):", signature
        )
        if signature_match is None:
            raise ProcessorWebhookRejected("pawapay callback signature value is missing")
        try:
            signature_bytes = base64.b64decode(signature_match.group(1), validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ProcessorWebhookRejected("pawapay callback signature value is invalid") from exc

        url = urlsplit(callback_url)
        header_values = normalized
        signature_base: list[str] = []
        for component in components:
            if component == "@method":
                value = "POST"
            elif component == "@authority":
                value = url.netloc
            elif component == "@path":
                value = url.path or "/"
            elif component == "@query":
                value = f"?{url.query}" if url.query else "?"
            else:
                value = header_values.get(component.lower())
                if value is None:
                    raise ProcessorWebhookRejected(
                        f"pawapay callback signed component is missing: {component}"
                    )
            signature_base.append(f'"{component}": {value}')
        signature_base.append(f'"@signature-params": {signature_params}')

        public_key = self._get_callback_public_key(key_id_match.group(1))
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec

            public_key.verify(
                signature_bytes,
                "\n".join(signature_base).encode("utf-8"),
                ec.ECDSA(hashes.SHA256()),
            )
        except ImportError as exc:
            raise ProcessorWebhookRejected(
                "cryptography is required to verify pawapay callbacks"
            ) from exc
        except InvalidSignature as exc:
            raise ProcessorWebhookRejected("pawapay callback signature is invalid") from exc

    def _get_callback_public_key(self, key_id: str):
        now = time.time()
        with self._public_key_lock:
            cached = self._public_key_cache.get(key_id)
            if cached and cached[1] > now:
                return cached[0]
            if now - self._public_keys_fetched_at < 300 and not cached:
                raise ProcessorWebhookRejected("pawapay callback public key is unavailable")
            try:
                response = self._fetch_callback_public_keys()
                if response.status_code >= 400:
                    raise ProcessorWebhookRejected(
                        "pawapay callback public keys could not be loaded"
                    )
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProcessorWebhookRejected(
                    "pawapay callback public keys could not be loaded"
                ) from exc
            self._public_keys_fetched_at = now
            keys = body.get("keys", []) if isinstance(body, dict) else body
            if not isinstance(keys, list):
                raise ProcessorWebhookRejected("pawapay callback public key response is invalid")
            try:
                from cryptography.hazmat.primitives.serialization import (
                    load_pem_public_key,
                )

                for item in keys:
                    if not isinstance(item, dict) or not item.get("id") or not item.get("key"):
                        continue
                    self._public_key_cache[str(item["id"])] = (
                        load_pem_public_key(str(item["key"]).encode("ascii")),
                        now + 86400,
                    )
            except (ImportError, ValueError, TypeError) as exc:
                raise ProcessorWebhookRejected(
                    "pawapay callback public key response is invalid"
                ) from exc
            cached = self._public_key_cache.get(key_id)
            if cached is None:
                raise ProcessorWebhookRejected("pawapay callback keyid is unknown")
            return cached[0]

    def _fetch_callback_public_keys(self) -> httpx.Response:
        if self._client is not None:
            return self._client.get(self._public_keys_url, headers={})
        with httpx.Client(timeout=10.0) as client:
            return client.get(self._public_keys_url)

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
        if not isinstance(reason, dict):
            return None, None
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
