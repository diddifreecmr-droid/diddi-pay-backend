"""Clean Architecture ports implemented by payment infrastructure adapters."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol

from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    NextAction,
    PaymentAttempt,
    PaymentIntent,
    Refund,
    RefundStatus,
)


class PaymentDirection(StrEnum):
    COLLECTION = "collection"
    REFUND = "refund"
    PAYOUT = "payout"


@dataclass(frozen=True, slots=True)
class ProcessorCapabilities:
    currencies: frozenset[str]
    directions: frozenset[PaymentDirection]
    channels: frozenset[str] = frozenset()
    networks: frozenset[str] = frozenset()

    def supports(
        self,
        *,
        currency: str,
        direction: PaymentDirection,
        channel: str | None,
        network: str | None,
    ) -> bool:
        if currency.upper() not in self.currencies or direction not in self.directions:
            return False
        if channel and self.channels and channel not in self.channels:
            return False
        return not (network and self.networks and network not in self.networks)


@dataclass(frozen=True, slots=True)
class InitializePaymentRequest:
    payment_intent_id: uuid.UUID
    attempt_id: uuid.UUID
    business_reference: str
    money: Money
    idempotency_key: str
    channel: str | None = None
    network: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    callback_url: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    provider_reference: str
    status: AttemptStatus
    provider_status: str | None = None
    next_action: NextAction | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    amount: int | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    event_key: str
    event_type: str
    provider_reference: str | None
    status: AttemptStatus | None
    amount: int | None = None
    currency: str | None = None
    sanitized_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RefundRequest:
    refund_id: uuid.UUID
    provider_reference: str
    money: Money
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RefundResult:
    provider_reference: str | None
    status: RefundStatus
    provider_status: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None


class PaymentProcessorPort(Protocol):
    name: str
    capabilities: ProcessorCapabilities

    def initialize_payment(self, request: InitializePaymentRequest) -> ProviderResult: ...

    def verify_payment(self, provider_reference: str) -> ProviderResult: ...

    def parse_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> ProviderEvent: ...

    def refund_payment(self, request: RefundRequest) -> RefundResult: ...


class PaymentIntentRepositoryPort(Protocol):
    def add(self, intent: PaymentIntent) -> PaymentIntent: ...

    def get(self, intent_id: uuid.UUID, *, for_update: bool = False) -> PaymentIntent | None: ...

    def get_by_idempotency(self, client_id: str, key: str) -> PaymentIntent | None: ...

    def list_for_client(self, client_id: str, *, limit: int = 50) -> list[PaymentIntent]: ...

    def save(self, intent: PaymentIntent) -> PaymentIntent: ...


class PaymentAttemptRepositoryPort(Protocol):
    def add(self, attempt: PaymentAttempt) -> PaymentAttempt: ...

    def get(self, attempt_id: uuid.UUID, *, for_update: bool = False) -> PaymentAttempt | None: ...

    def get_by_provider_reference(
        self, processor: str, provider_reference: str, *, for_update: bool = False
    ) -> PaymentAttempt | None: ...

    def list_for_intent(self, intent_id: uuid.UUID) -> list[PaymentAttempt]: ...

    def next_attempt_number(self, intent_id: uuid.UUID) -> int: ...

    def save(self, attempt: PaymentAttempt) -> PaymentAttempt: ...


class RefundRepositoryPort(Protocol):
    def add(self, refund: Refund, *, client_id: str, processor: str) -> Refund: ...


class ProviderEventRepositoryPort(Protocol):
    def get(self, processor: str, event_key: str): ...

    def add(
        self,
        *,
        processor: str,
        event_key: str,
        event_type: str,
        payload_hash: str,
        payload: dict,
    ): ...

    def mark(
        self,
        row,
        *,
        status: str,
        payment_attempt_id: uuid.UUID | None = None,
        error_message: str | None = None,
    ): ...


class UnitOfWorkPort(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...
