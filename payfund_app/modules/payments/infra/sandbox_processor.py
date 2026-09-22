"""Deterministic processor used by local development and contract tests."""

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


class SandboxPaymentProcessor:
    name = "sandbox"
    capabilities = ProcessorCapabilities(
        currencies=frozenset({"XOF"}),
        directions=frozenset(
            {PaymentDirection.COLLECTION, PaymentDirection.REFUND, PaymentDirection.PAYOUT}
        ),
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

    def create_payout(self, request: PayoutRequest) -> PayoutResult:
        return PayoutResult(
            provider_reference=f"sandbox-payout-{request.payout_id}",
            status=PayoutStatus.SUCCEEDED,
            provider_status="processed",
        )

    def verify_payout(self, provider_reference: str) -> PayoutResult:
        return PayoutResult(
            provider_reference=provider_reference,
            status=PayoutStatus.SUCCEEDED,
            provider_status="processed",
        )
