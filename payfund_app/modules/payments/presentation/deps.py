"""Authentication and dependency wiring for module-to-module payments."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from payfund_app.core.config import get_settings
from payfund_app.core.database import get_session
from payfund_app.core.errors import Unauthenticated
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor


@dataclass(frozen=True, slots=True)
class PaymentClient:
    client_id: str


def get_payment_client(
    client_id: Annotated[str | None, Header(alias="X-Client-ID")] = None,
    service_key: Annotated[str | None, Header(alias="X-Service-Key")] = None,
) -> PaymentClient:
    expected = get_settings().payment_service_key_map.get(client_id or "")
    if not client_id or not service_key or not expected or not hmac.compare_digest(service_key, expected):
        raise Unauthenticated("Identifiants de service DiddiPay invalides.")
    return PaymentClient(client_id=client_id)


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
PaymentClientDep = Annotated[PaymentClient, Depends(get_payment_client)]
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
