"""Authentication and dependency wiring for module-to-module payments."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from payfund_app.core.config import get_settings
from payfund_app.core.database import get_session
from payfund_app.core.errors import ServiceUnavailable, Unauthenticated
from payfund_app.core.security import ServicePrincipal, decode_service_token
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.infra.cinetpay_processor import (
    CinetpayPaymentProcessor,
)
from payfund_app.modules.payments.infra.jeko_processor import JekoPaymentProcessor
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.infra.paystack_processor import (
    PaystackPaymentProcessor,
)
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor


@dataclass(frozen=True, slots=True)
class PaymentClient:
    client_id: str
    authentication_method: str
    scopes: frozenset[str] = frozenset()


def _payment_client_dependency(
    scope_setting: str, *, allow_legacy_key: bool = True
) -> Callable[..., PaymentClient]:
    def dependency(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        client_id: Annotated[str | None, Header(alias="X-Client-ID")] = None,
        service_key: Annotated[str | None, Header(alias="X-Service-Key")] = None,
    ) -> PaymentClient:
        settings = get_settings()
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token:
                raise Unauthenticated("Authorization Bearer invalide.")
            principal: ServicePrincipal = decode_service_token(
                token,
                audience=settings.payment_service_audience,
                client_id_header=client_id,
                required_scopes={getattr(settings, scope_setting)},
                allowed_client_ids=settings.payment_service_client_id_set,
            )
            return PaymentClient(
                client_id=principal.client_id,
                authentication_method="service_token",
                scopes=principal.scopes,
            )

        expected = settings.payment_service_key_map.get(client_id or "")
        if (
            allow_legacy_key
            and settings.payment_service_key_fallback_enabled
            and client_id
            and service_key
            and expected
            and hmac.compare_digest(service_key, expected)
        ):
            return PaymentClient(client_id=client_id, authentication_method="legacy_key")
        raise Unauthenticated("Jeton de service DiddiPay invalide.")

    return dependency


get_payment_intent_reader = _payment_client_dependency("payment_intent_read_scope")
get_payment_intent_writer = _payment_client_dependency("payment_intent_write_scope")
get_payment_intent_verifier = _payment_client_dependency(
    "payment_intent_verify_scope", allow_legacy_key=False
)
get_payment_refunder = _payment_client_dependency("payment_refund_scope")
get_payment_payout_reader = _payment_client_dependency("payment_payout_read_scope")
get_payment_payout_writer = _payment_client_dependency("payment_payout_write_scope")


_registry: ProcessorRegistry | None = None


def _validate_selected_processor_configuration(settings) -> None:
    """Fail clearly when an explicitly selected external processor is incomplete."""
    mode = settings.payment_processor_mode.strip().lower()
    if mode != "cinetpay":
        return

    missing = [
        name
        for name, value in (
            ("CINETPAY_API_KEY", settings.cinetpay_api_key),
            ("CINETPAY_API_PASSWORD", settings.cinetpay_api_password),
        )
        if not str(value or "").strip()
    ]
    if missing:
        raise ServiceUnavailable(
            "CinetPay est selectionne mais sa configuration est incomplete.",
            code="PROCESSOR_NOT_CONFIGURED",
            details={"processor": "cinetpay", "missing": missing},
        )


def get_processor_registry() -> ProcessorRegistry:
    """Register every processor that's actually configured, not just the current mode.

    SCRUM-511: this used to register only whichever single processor
    `PAYMENT_PROCESSOR_MODE` named, and raise on anything else. That breaks reconciliation
    (`ProcessorRegistry.get(attempt.processor)`) for any attempt created under a different mode
    than whatever this process currently has — including across the app/worker process split,
    since each builds its own registry independently. `PAYMENT_PROCESSOR_MODE` still controls
    which processor new operations use by default (see `PaymentUseCases.default_processor` /
    and `PayoutUseCases.default_processor`); a request-level `processor` may explicitly override
    that default. It no longer gates what gets registered.
    """
    global _registry
    if _registry is None:
        settings = get_settings()
        _validate_selected_processor_configuration(settings)
        _registry = ProcessorRegistry()
        # Free, no external credentials — always available so reconciliation can resolve any
        # attempt stamped "sandbox", regardless of the currently configured mode.
        _registry.register(SandboxPaymentProcessor())
        if settings.paystack_secret_key:
            _registry.register(
                PaystackPaymentProcessor(
                    secret_key=settings.paystack_secret_key,
                    base_url=settings.paystack_base_url,
                    webhook_secret=settings.paystack_webhook_secret or None,
                )
            )
        elif settings.payment_processor_mode == "paystack":
            raise ValueError(
                "PAYMENT_PROCESSOR_MODE=paystack but PAYSTACK_SECRET_KEY is empty."
            )
        # Shares the same PawaPay sandbox/live credentials as the legacy wallet
        # (wallet/infra/pawapay_gateway.py) -- same account, two independent adapters.
        # Keep a priority for registry fallback callers, but API operations always provide the
        # configured default or an explicit request-level processor.
        if settings.pawapay_api_token:
            _registry.register(
                PawapayPaymentProcessor(
                    api_token=settings.pawapay_api_token,
                    base_url=settings.pawapay_base_url,
                ),
                priority=50,
            )
        # CinetPay v1 (hosted Payment Page -> REDIRECT, + mobile-money payouts incl. Wave).
        # Needs BOTH the api_key and the api_password; an api_key alone can't authenticate.
        # Keep a priority for registry fallback callers. API operations use CinetPay only when
        # PAYMENT_PROCESSOR_MODE=cinetpay or processor=cinetpay is sent explicitly.
        if settings.cinetpay_api_key and settings.cinetpay_api_password:
            _registry.register(
                CinetpayPaymentProcessor(
                    api_key=settings.cinetpay_api_key,
                    api_password=settings.cinetpay_api_password,
                    country=settings.cinetpay_country,
                    base_url=settings.cinetpay_base_url or None,
                    webhook_url=settings.cinetpay_webhook_url or None,
                ),
                priority=110,
            )
        # Jèko (hosted payment page -> REDIRECT, + mobile-money payouts incl. Wave). Needs all
        # three of api_key + api_key_id + store_id; a key alone can't authenticate and no call
        # works without a store_id. API operations use Jèko only when
        # PAYMENT_PROCESSOR_MODE=jeko or processor=jeko is sent explicitly.
        if (
            settings.jeko_api_key
            and settings.jeko_api_key_id
            and settings.jeko_store_id
        ):
            _registry.register(
                JekoPaymentProcessor(
                    api_key=settings.jeko_api_key,
                    api_key_id=settings.jeko_api_key_id,
                    store_id=settings.jeko_store_id,
                    base_url=settings.jeko_base_url,
                    webhook_secret=settings.jeko_webhook_secret or None,
                ),
                priority=120,
            )
    return _registry


def reset_processor_registry() -> None:
    global _registry
    _registry = None


SessionDep = Annotated[Session, Depends(get_session)]
PaymentIntentReaderDep = Annotated[PaymentClient, Depends(get_payment_intent_reader)]
PaymentIntentWriterDep = Annotated[PaymentClient, Depends(get_payment_intent_writer)]
PaymentIntentVerifierDep = Annotated[
    PaymentClient, Depends(get_payment_intent_verifier)
]
PaymentRefunderDep = Annotated[PaymentClient, Depends(get_payment_refunder)]
PaymentPayoutReaderDep = Annotated[PaymentClient, Depends(get_payment_payout_reader)]
PaymentPayoutWriterDep = Annotated[PaymentClient, Depends(get_payment_payout_writer)]
ProcessorRegistryDep = Annotated[ProcessorRegistry, Depends(get_processor_registry)]


def get_paystack_webhook_processor() -> PaystackPaymentProcessor:
    settings = get_settings()
    secret = settings.paystack_webhook_secret or settings.paystack_secret_key
    if not secret:
        raise ServiceUnavailable(
            "Webhook Paystack non configure.",
            code="PROCESSOR_NOT_CONFIGURED",
            details={"provider": "paystack"},
        )
    return PaystackPaymentProcessor(
        secret_key=settings.paystack_secret_key or secret,
        base_url=settings.paystack_base_url,
        webhook_secret=secret,
    )


PaystackWebhookProcessorDep = Annotated[
    PaystackPaymentProcessor, Depends(get_paystack_webhook_processor)
]


def get_cinetpay_webhook_processor() -> CinetpayPaymentProcessor:
    settings = get_settings()
    if not settings.cinetpay_api_key or not settings.cinetpay_api_password:
        raise ServiceUnavailable(
            "Webhook CinetPay non configure.",
            code="PROCESSOR_NOT_CONFIGURED",
            details={"provider": "cinetpay"},
        )
    return CinetpayPaymentProcessor(
        api_key=settings.cinetpay_api_key,
        api_password=settings.cinetpay_api_password,
        country=settings.cinetpay_country,
        base_url=settings.cinetpay_base_url or None,
        webhook_url=settings.cinetpay_webhook_url or None,
    )


CinetpayWebhookProcessorDep = Annotated[
    CinetpayPaymentProcessor, Depends(get_cinetpay_webhook_processor)
]


def _pawapay_webhook_processor(callback_url: str | None) -> PawapayPaymentProcessor:
    settings = get_settings()
    if not settings.pawapay_api_token:
        raise ServiceUnavailable(
            "Webhook PawaPay non configure.",
            code="PROCESSOR_NOT_CONFIGURED",
            details={"provider": "pawapay"},
        )
    return PawapayPaymentProcessor(
        api_token=settings.pawapay_api_token,
        base_url=settings.pawapay_base_url,
        callback_url=callback_url or settings.pawapay_callback_url or None,
        callback_signature_required=settings.pawapay_callback_signature_required,
        public_keys_url=settings.pawapay_public_keys_url or None,
    )


def get_pawapay_webhook_processor() -> PawapayPaymentProcessor:
    settings = get_settings()
    return _pawapay_webhook_processor(
        settings.pawapay_payout_callback_url or settings.pawapay_callback_url
    )


PawapayWebhookProcessorDep = Annotated[
    PawapayPaymentProcessor, Depends(get_pawapay_webhook_processor)
]


def get_pawapay_deposit_webhook_processor() -> PawapayPaymentProcessor:
    settings = get_settings()
    return _pawapay_webhook_processor(
        settings.pawapay_deposit_callback_url or settings.pawapay_callback_url
    )


PawapayDepositWebhookProcessorDep = Annotated[
    PawapayPaymentProcessor, Depends(get_pawapay_deposit_webhook_processor)
]


def get_pawapay_refund_webhook_processor() -> PawapayPaymentProcessor:
    settings = get_settings()
    return _pawapay_webhook_processor(
        settings.pawapay_refund_callback_url or settings.pawapay_callback_url
    )


PawapayRefundWebhookProcessorDep = Annotated[
    PawapayPaymentProcessor, Depends(get_pawapay_refund_webhook_processor)
]
