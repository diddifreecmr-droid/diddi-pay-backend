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
    global _registry
    if _registry is None:
        _registry = ProcessorRegistry()
        settings = get_settings()
        if settings.payment_processor_mode == "sandbox":
            _registry.register(SandboxPaymentProcessor())
        elif settings.payment_processor_mode == "paystack":
            _registry.register(
                PaystackPaymentProcessor(
                    secret_key=settings.paystack_secret_key,
                    base_url=settings.paystack_base_url,
                    webhook_secret=settings.paystack_webhook_secret or None,
                )
            )
        else:
            raise ValueError(
                f"Unsupported PAYMENT_PROCESSOR_MODE={settings.payment_processor_mode!r}"
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
