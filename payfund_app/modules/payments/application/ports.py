"""Clean Architecture ports implemented by payment infrastructure adapters."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    NextAction,
    PaymentAttempt,
    PaymentIntent,
    Payout,
    PayoutStatus,
    Refund,
    RefundStatus,
)


class PaymentDirection(StrEnum):
    COLLECTION = "collection"
    REFUND = "refund"
    PAYOUT = "payout"


class WebhookKind(StrEnum):
    """Kinds of provider callbacks an adapter can validate and normalize."""

    PAYMENT = "payment"
    REFUND = "refund"
    PAYOUT = "payout"


@dataclass(frozen=True, slots=True)
class ProcessorCapabilities:
    """What an adapter really implements. The application checks these flags before it
    calls an operation, so an adapter never needs a NotImplementedError stub to be safe."""

    currencies: frozenset[str]
    directions: frozenset[PaymentDirection]
    channels: frozenset[str] = frozenset()
    networks: frozenset[str] = frozenset()
    webhooks: frozenset[WebhookKind] = frozenset()

    def can(self, direction: PaymentDirection) -> bool:
        return direction in self.directions

    def receives(self, kind: WebhookKind) -> bool:
        return kind in self.webhooks

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
    fee: int | None = None
    # Provider-only data needed for later callbacks/reconciliation. This is persisted
    # separately from the public PaymentIntent metadata and is never returned to callers.
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    event_key: str
    event_type: str
    provider_reference: str | None
    status: AttemptStatus | None
    amount: int | None = None
    currency: str | None = None
    fee: int | None = None
    sanitized_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PayoutProviderEvent:
    """Validated provider callback data for a payout."""

    event_key: str
    event_type: str
    provider_reference: str | None
    status: PayoutStatus | None
    provider_status: str | None = None
    amount: int | None = None
    currency: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    sanitized_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RefundProviderEvent:
    """Validated provider callback data for a refund."""

    event_key: str
    event_type: str
    provider_reference: str | None
    status: RefundStatus | None
    provider_status: str | None = None
    amount: int | None = None
    currency: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    sanitized_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CallbackTarget:
    url: str
    secret: str


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


@dataclass(frozen=True, slots=True)
class PayoutRequest:
    payout_id: uuid.UUID
    business_reference: str
    beneficiary_reference: str
    money: Money
    idempotency_key: str
    # Where the money goes -- {"type": "mobile_money", "phone": ..., "network": ...} today,
    # other types added later without changing this port. Opaque to anything above the
    # processor that actually interprets it (see PawapayPaymentProcessor.create_payout).
    destination: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PayoutResult:
    provider_reference: str | None
    status: PayoutStatus
    provider_status: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None


# -- Processor ports -------------------------------------------------------------------
# One small port per role. An adapter implements only the roles its capabilities declare:
#   PaymentDirection.COLLECTION -> CollectionPort
#   PaymentDirection.REFUND     -> RefundPort
#   PaymentDirection.PAYOUT     -> PayoutPort
#   WebhookKind.PAYMENT/REFUND/PAYOUT -> the matching *WebhookPort
# Callers check the capability first (ProcessorRegistry.get_capable / get_receiver).


class ProcessorPort(Protocol):
    """Identity shared by every adapter; enough for the registry to route."""

    name: str
    capabilities: ProcessorCapabilities


class CollectionPort(ProcessorPort, Protocol):
    def initialize_payment(self, request: InitializePaymentRequest) -> ProviderResult: ...

    def verify_payment(self, provider_reference: str) -> ProviderResult: ...


class RefundPort(ProcessorPort, Protocol):
    def refund_payment(self, request: RefundRequest) -> RefundResult: ...


class PayoutPort(ProcessorPort, Protocol):
    def create_payout(self, request: PayoutRequest) -> PayoutResult: ...

    def verify_payout(self, provider_reference: str) -> PayoutResult: ...


class PaymentWebhookPort(ProcessorPort, Protocol):
    def parse_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> ProviderEvent: ...


class RefundWebhookPort(ProcessorPort, Protocol):
    def parse_refund_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        callback_url: str | None = None,
    ) -> RefundProviderEvent: ...


class PayoutWebhookPort(ProcessorPort, Protocol):
    def parse_payout_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        callback_url: str | None = None,
    ) -> PayoutProviderEvent: ...


# Kept for existing imports: the identity every registered adapter has.
PaymentProcessorPort = ProcessorPort


class PayoutRepositoryPort(Protocol):
    def add(self, payout: Payout) -> Payout: ...

    def get(self, payout_id: uuid.UUID) -> Payout | None: ...

    def get_by_idempotency(self, client_id: str, key: str) -> Payout | None: ...

    def get_by_business_reference(self, client_id: str, reference: str) -> Payout | None: ...

    def get_by_provider_reference(
        self, processor: str, provider_reference: str, *, for_update: bool = False
    ) -> Payout | None: ...

    def save(self, payout: Payout) -> Payout: ...

    def pending_for_reconciliation(
        self, *, older_than: datetime, limit: int = 100
    ) -> list[Payout]: ...


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

    def get_by_provider_reference(
        self, processor: str, provider_reference: str, *, for_update: bool = False
    ): ...

    def save(self, refund: Refund) -> Refund: ...


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
        payout_id: uuid.UUID | None = None,
        refund_id: uuid.UUID | None = None,
        error_message: str | None = None,
    ): ...


class PaymentOutboxEventPort(Protocol):
    id: uuid.UUID
    client_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class PaymentOutboxRepositoryPort(Protocol):
    def claim(self, limit: int = 100) -> list[PaymentOutboxEventPort]: ...

    def delivered(self, row: PaymentOutboxEventPort) -> None: ...

    def failed(
        self, row: PaymentOutboxEventPort, error: str, *, max_attempts: int = 10
    ) -> None: ...


class PaymentEventSenderPort(Protocol):
    def send(
        self,
        target: CallbackTarget,
        *,
        event_id: uuid.UUID,
        event_type: str,
        occurred_at: datetime,
        payload: Mapping[str, Any],
    ) -> None: ...

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
