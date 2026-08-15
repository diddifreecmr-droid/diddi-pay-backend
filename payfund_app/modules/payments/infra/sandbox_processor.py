"""Deterministic processor used by local development and contract tests."""

from payfund_app.modules.payments.application.ports import (
    InitializePaymentRequest,
    PaymentDirection,
    ProcessorCapabilities,
    ProviderResult,
    RefundRequest,
    RefundResult,
)
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    NextAction,
    NextActionType,
    RefundStatus,
)


class SandboxPaymentProcessor:
    name = "sandbox"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        directions=frozenset({PaymentDirection.COLLECTION, PaymentDirection.REFUND}),
        channels=frozenset({"mobile_money", "card"}),
        networks=frozenset({"orange", "wave", "mtn"}),
    )

    def initialize_payment(self, request: InitializePaymentRequest) -> ProviderResult:
        reference = f"sandbox-{request.attempt_id}"
        return ProviderResult(
            provider_reference=reference,
            status=AttemptStatus.REQUIRES_ACTION,
            provider_status="initialized",
            next_action=NextAction(
                NextActionType.REDIRECT,
                url=f"https://sandbox.diddipay.local/pay/{request.attempt_id}",
            ),
        )

    def verify_payment(self, provider_reference: str) -> ProviderResult:
        return ProviderResult(
            provider_reference=provider_reference,
            status=AttemptStatus.REQUIRES_ACTION,
            provider_status="initialized",
        )

    def parse_webhook(self, raw_body, headers):
        raise NotImplementedError("sandbox webhooks are not exposed")

    def refund_payment(self, request: RefundRequest) -> RefundResult:
        return RefundResult(
            provider_reference=f"sandbox-refund-{request.refund_id}",
            status=RefundStatus.SUCCEEDED,
            provider_status="processed",
        )
