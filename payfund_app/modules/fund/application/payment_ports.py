"""Ports used by DiddiFund to initiate provider-neutral collections."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FundPaymentAction:
    type: str
    url: str | None = None
    instructions: str | None = None


@dataclass(frozen=True, slots=True)
class FundPaymentResult:
    payment_intent_id: str
    status: str
    next_action: FundPaymentAction | None


class PaymentOrchestratorPort(Protocol):
    def create_collection(
        self,
        *,
        business_reference: str,
        amount: int,
        currency: str,
        payer_user_id: str,
        idempotency_key: str,
        channel: str | None,
        network: str | None,
        customer_email: str | None,
        customer_phone: str | None,
        callback_url: str | None,
        metadata: dict,
    ) -> FundPaymentResult: ...

    def get_collection(self, payment_intent_id: str) -> FundPaymentResult: ...
