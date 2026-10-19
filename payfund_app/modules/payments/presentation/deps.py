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
from payfund_app.core.errors import Unauthenticated
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


def _payment_client_dependency(scope_setting: str) -> Callable[..., PaymentClient]:
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
            settings.payment_service_key_fallback_enabled
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
get_payment_refunder = _payment_client_dependency("payment_refund_scope")
get_payment_payout_reader = _payment_client_dependency("payment_payout_read_scope")
get_payment_payout_writer = _payment_client_dependency("payment_payout_write_scope")


_registry: ProcessorRegistry | None = None


def get_processor_registry() -> ProcessorRegistry:
    """Register every processor that's actually configured, not just the current mode.

    SCRUM-511: this used to register only whichever single processor
    `PAYMENT_PROCESSOR_MODE` named, and raise on anything else. That breaks reconciliation
    (`ProcessorRegistry.get(attempt.processor)`) for any attempt created under a different mode
    than whatever this process currently has — including across the app/worker process split,
    since each builds its own registry independently. `PAYMENT_PROCESSOR_MODE` still controls
    which processor *new* intents prefer (see `PaymentUseCases.default_processor` /
    `preferred_processor`); it no longer gates what gets registered.
    """
    global _registry
    if _registry is None:
        _registry = ProcessorRegistry()
        settings = get_settings()
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
        # Priority 50 (< Paystack's default 100) so capability-based auto-selection prefers
        # the direct mtn/orange/moov rail over Paystack's hosted mobile-money checkout when
        # no preferred_processor forces otherwise -- see PaymentUseCases.create().
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
        # Priority 110 (> Paystack's 100 = lower precedence) so auto-selection keeps
        # preferring the existing default; cinetpay is used when explicitly chosen via
        # preferred_processor, or as the payout fallback for a network the direct rails
        # (pawapay mtn/orange/moov) don't cover -- e.g. Wave. Empty capability.networks means
        # it never hijacks another processor's dedicated network (see SIMULATED_PROCESSOR_NAMES
        # rationale in processor_router.py).
        if settings.cinetpay_api_key and settings.cinetpay_api_password:
            _registry.register(
                CinetpayPaymentProcessor(
                    api_key=settings.cinetpay_api_key,
                    api_password=settings.cinetpay_api_password,
                    country=settings.cinetpay_country,
                    base_url=settings.cinetpay_base_url or None,
                ),
                priority=110,
            )
        # Jèko (hosted payment page -> REDIRECT, + mobile-money payouts incl. Wave). Needs all
        # three of api_key + api_key_id + store_id; a key alone can't authenticate and no call
        # works without a store_id. Priority 120 (> Paystack 100, > CinetPay 110 = lowest
        # precedence) so capability-based auto-selection keeps preferring the existing
        # processors; jeko is used when explicitly chosen via preferred_processor, or as a
        # payout fallback. Empty capability.networks means it never hijacks another processor's
        # dedicated network (see SIMULATED_PROCESSOR_NAMES rationale in processor_router.py).
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
PaymentRefunderDep = Annotated[PaymentClient, Depends(get_payment_refunder)]
PaymentPayoutReaderDep = Annotated[PaymentClient, Depends(get_payment_payout_reader)]
PaymentPayoutWriterDep = Annotated[PaymentClient, Depends(get_payment_payout_writer)]
ProcessorRegistryDep = Annotated[ProcessorRegistry, Depends(get_processor_registry)]


def get_paystack_webhook_processor() -> PaystackPaymentProcessor:
    settings = get_settings()
    secret = settings.paystack_webhook_secret or settings.paystack_secret_key
    if not secret:
        raise Unauthenticated("Webhook Paystack non configure.")
    return PaystackPaymentProcessor(
        secret_key=settings.paystack_secret_key or secret,
        base_url=settings.paystack_base_url,
        webhook_secret=secret,
    )


PaystackWebhookProcessorDep = Annotated[
    PaystackPaymentProcessor, Depends(get_paystack_webhook_processor)
]
