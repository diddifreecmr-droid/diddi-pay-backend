"""Processor capability contract (ticket 110, step 1).

Each adapter declares what it implements. The application checks those flags before calling
an operation, so the declaration must be honest: every declared role has a real method, and
an undeclared role is refused before anything is persisted.
"""

import inspect
import uuid

import pytest
from sqlalchemy import func, select

from payfund_app.modules.payments.application.ports import (
    PaymentDirection,
    ProcessorCapabilities,
    WebhookKind,
)
from payfund_app.modules.payments.application.processor_router import (
    ProcessorCapabilityMissing,
    ProcessorRegistry,
    require_webhook,
)
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
)
from payfund_app.modules.payments.infra.cinetpay_processor import CinetpayPaymentProcessor
from payfund_app.modules.payments.infra.jeko_processor import JekoPaymentProcessor
from payfund_app.modules.payments.infra.models import RefundRecord
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
)
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor

ADAPTERS = [
    CinetpayPaymentProcessor,
    JekoPaymentProcessor,
    PawapayPaymentProcessor,
    PaystackPaymentProcessor,
    SandboxPaymentProcessor,
]

DIRECTION_METHODS = {
    PaymentDirection.COLLECTION: ("initialize_payment", "verify_payment"),
    PaymentDirection.REFUND: ("refund_payment",),
    PaymentDirection.PAYOUT: ("create_payout", "verify_payout"),
}
WEBHOOK_METHODS = {
    WebhookKind.PAYMENT: "parse_webhook",
    WebhookKind.REFUND: "parse_refund_webhook",
    WebhookKind.PAYOUT: "parse_payout_webhook",
}


def _is_real(adapter, method_name: str) -> bool:
    method = getattr(adapter, method_name, None)
    if method is None:
        return False
    return "raise NotImplementedError" not in inspect.getsource(method)


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
def test_declared_directions_have_real_methods(adapter):
    for direction in adapter.capabilities.directions:
        for method_name in DIRECTION_METHODS[direction]:
            assert _is_real(adapter, method_name), (
                f"{adapter.name} declares {direction} but {method_name} is missing or a stub"
            )


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
def test_declared_webhooks_have_real_parsers(adapter):
    for kind in adapter.capabilities.webhooks:
        assert _is_real(adapter, WEBHOOK_METHODS[kind]), (
            f"{adapter.name} declares {kind} callbacks but {WEBHOOK_METHODS[kind]} is a stub"
        )


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
def test_declared_refund_verification_has_real_method(adapter):
    if adapter.capabilities.refund_verification:
        assert _is_real(adapter, "verify_refund")
        assert adapter.capabilities.can(PaymentDirection.REFUND)


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
def test_stubbed_operations_are_not_declared(adapter):
    for direction, methods in DIRECTION_METHODS.items():
        if any(hasattr(adapter, m) and not _is_real(adapter, m) for m in methods):
            assert direction not in adapter.capabilities.directions
    for kind, method_name in WEBHOOK_METHODS.items():
        if hasattr(adapter, method_name) and not _is_real(adapter, method_name):
            assert kind not in adapter.capabilities.webhooks


def test_known_capabilities():
    assert PaystackPaymentProcessor.capabilities.can(PaymentDirection.REFUND)
    assert PawapayPaymentProcessor.capabilities.can(PaymentDirection.REFUND)
    assert not CinetpayPaymentProcessor.capabilities.can(PaymentDirection.REFUND)
    assert not JekoPaymentProcessor.capabilities.can(PaymentDirection.REFUND)
    assert PawapayPaymentProcessor.capabilities.webhooks == frozenset(WebhookKind)
    assert JekoPaymentProcessor.capabilities.webhooks == frozenset()
    assert SandboxPaymentProcessor.capabilities.webhooks == frozenset()


class _CollectOnly:
    name = "collect-only"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        directions=frozenset({PaymentDirection.COLLECTION}),
    )


def test_registry_get_capable_refuses_missing_direction():
    registry = ProcessorRegistry()
    registry.register(_CollectOnly())
    registry.register(SandboxPaymentProcessor())

    assert registry.get_capable("sandbox", PaymentDirection.REFUND).name == "sandbox"
    with pytest.raises(ProcessorCapabilityMissing) as exc:
        registry.get_capable("collect-only", PaymentDirection.REFUND)
    assert exc.value.processor == "collect-only"


def test_require_webhook_refuses_undeclared_kind():
    with pytest.raises(ProcessorCapabilityMissing):
        require_webhook(_CollectOnly(), WebhookKind.PAYMENT)
    require_webhook(PawapayPaymentProcessor, WebhookKind.REFUND)


def _succeeded_payment(session, processor: str):
    intent = PaymentIntent(
        client_id="diddigo",
        business_reference=f"ride:{uuid.uuid4()}",
        money=Money(10_000, "XOF"),
        idempotency_key=str(uuid.uuid4()),
        request_fingerprint="f" * 64,
        status=PaymentIntentStatus.SUCCEEDED,
    )
    attempt = PaymentAttempt(
        payment_intent_id=intent.id,
        processor=processor,
        money=intent.money,
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        provider_reference=f"{processor}-{uuid.uuid4()}",
    )
    PaymentIntentRepository(session).add(intent)
    PaymentAttemptRepository(session).add(attempt)
    session.commit()
    return intent


def test_refund_on_processor_without_refund_is_refused_and_not_persisted(client, session):
    # CinetPay has no refund endpoint. Before ticket 110 this answered 500 and left a
    # pending refund row behind; now it is a clean 409 and nothing is written.
    intent = _succeeded_payment(session, "cinetpay")

    response = client.post(
        f"/payfund/v1/payment-intents/{intent.id}/refunds",
        headers={
            "X-Client-ID": "diddigo",
            "X-Service-Key": "test-service-key",
            "Idempotency-Key": "refund-cinetpay-refused",
        },
        json={"amount": 1_000, "reason": "Service cancelled"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAYMENT_OPERATION_CONFLICT"
    session.expire_all()
    count = session.scalar(
        select(func.count()).select_from(RefundRecord).where(
            RefundRecord.payment_intent_id == intent.id
        )
    )
    assert count == 0
