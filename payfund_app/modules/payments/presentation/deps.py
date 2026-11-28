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
from payfund_app.modules.payments.application.ports import WebhookKind
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.infra.cinetpay_processor import (
    CinetpayPaymentProcessor,
)
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.infra.paystack_processor import (
    PaystackPaymentProcessor,
)
from payfund_app.modules.payments.infra.processor_plugins import (
    build_registry,
    build_webhook_processor,
)


@dataclass(frozen=True, slots=True)
class PaymentClient:
    client_id: str
    authentication_method: str
    scopes: frozenset[str] = frozenset()


def _payment_client_dependency(
    scope_setting: str,
    *,
    allow_legacy_key: bool = True,
    expected_subject: str | None = None,
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
            if expected_subject is not None and principal.subject != expected_subject:
                raise Unauthenticated("Identite de service invalide pour cette route.")
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
    "payment_intent_verify_scope",
    allow_legacy_key=False,
    expected_subject="service:diddisend",
)
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
    which processor new operations use by default (see `PaymentUseCases.default_processor` /
    and `PayoutUseCases.default_processor`); a request-level `processor` may explicitly override
    that default. It no longer gates what gets registered.

    Which processors exist, what they need and how they are built lives in
    `infra/processor_plugins.py` (ticket 110); this function only caches the result.
    """
    global _registry
    if _registry is None:
        registry = ProcessorRegistry()
        # Built into a local first: a configuration error must not cache a partial registry.
        build_registry(get_settings(), registry)
        _registry = registry
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


def webhook_processor(name: str, kind: WebhookKind):
    """Adapter validating `kind` callbacks for processor `name` (see processor_plugins)."""
    return build_webhook_processor(name, kind, get_settings())


# Named dependencies kept for the current provider routes and test overrides.
def get_paystack_webhook_processor() -> PaystackPaymentProcessor:
    return webhook_processor("paystack", WebhookKind.PAYMENT)


PaystackWebhookProcessorDep = Annotated[
    PaystackPaymentProcessor, Depends(get_paystack_webhook_processor)
]


def get_cinetpay_webhook_processor() -> CinetpayPaymentProcessor:
    return webhook_processor("cinetpay", WebhookKind.PAYMENT)


CinetpayWebhookProcessorDep = Annotated[
    CinetpayPaymentProcessor, Depends(get_cinetpay_webhook_processor)
]


def get_pawapay_webhook_processor() -> PawapayPaymentProcessor:
    """PawaPay payout callbacks."""
    return webhook_processor("pawapay", WebhookKind.PAYOUT)


PawapayWebhookProcessorDep = Annotated[
    PawapayPaymentProcessor, Depends(get_pawapay_webhook_processor)
]


def get_pawapay_deposit_webhook_processor() -> PawapayPaymentProcessor:
    return webhook_processor("pawapay", WebhookKind.PAYMENT)


PawapayDepositWebhookProcessorDep = Annotated[
    PawapayPaymentProcessor, Depends(get_pawapay_deposit_webhook_processor)
]


def get_pawapay_refund_webhook_processor() -> PawapayPaymentProcessor:
    return webhook_processor("pawapay", WebhookKind.REFUND)


PawapayRefundWebhookProcessorDep = Annotated[
    PawapayPaymentProcessor, Depends(get_pawapay_refund_webhook_processor)
]
