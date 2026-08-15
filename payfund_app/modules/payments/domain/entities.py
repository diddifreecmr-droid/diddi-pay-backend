"""Provider-neutral payment entities and their state machines."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from payfund_app.modules.payments.domain.errors import (
    InvalidAmount,
    InvalidCurrency,
    InvalidStateTransition,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class PaymentIntentStatus(StrEnum):
    REQUIRES_ACTION = "requires_action"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


class AttemptStatus(StrEnum):
    PENDING = "pending"
    REQUIRES_ACTION = "requires_action"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RefundStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class NextActionType(StrEnum):
    REDIRECT = "redirect"
    MOBILE_MONEY_PROMPT = "mobile_money_prompt"
    DISPLAY_INSTRUCTIONS = "display_instructions"
    AWAIT_CONFIRMATION = "await_confirmation"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Money:
    amount: int
    currency: str = "XOF"

    def __post_init__(self) -> None:
        if isinstance(self.amount, bool) or not isinstance(self.amount, int) or self.amount <= 0:
            raise InvalidAmount("amount must be a positive integer in minor units")
        normalized = self.currency.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise InvalidCurrency("currency must be a three-letter ISO code")
        object.__setattr__(self, "currency", normalized)


@dataclass(frozen=True, slots=True)
class NextAction:
    type: NextActionType
    url: str | None = None
    instructions: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.type == NextActionType.REDIRECT and not self.url:
            raise ValueError("redirect next action requires a URL")


_INTENT_TRANSITIONS: dict[PaymentIntentStatus, frozenset[PaymentIntentStatus]] = {
    PaymentIntentStatus.PROCESSING: frozenset(
        {
            PaymentIntentStatus.REQUIRES_ACTION,
            PaymentIntentStatus.SUCCEEDED,
            PaymentIntentStatus.FAILED,
            PaymentIntentStatus.CANCELLED,
        }
    ),
    PaymentIntentStatus.REQUIRES_ACTION: frozenset(
        {
            PaymentIntentStatus.PROCESSING,
            PaymentIntentStatus.SUCCEEDED,
            PaymentIntentStatus.FAILED,
            PaymentIntentStatus.CANCELLED,
        }
    ),
    PaymentIntentStatus.FAILED: frozenset({PaymentIntentStatus.PROCESSING}),
    PaymentIntentStatus.SUCCEEDED: frozenset(
        {PaymentIntentStatus.PARTIALLY_REFUNDED, PaymentIntentStatus.REFUNDED}
    ),
    PaymentIntentStatus.PARTIALLY_REFUNDED: frozenset(
        {PaymentIntentStatus.PARTIALLY_REFUNDED, PaymentIntentStatus.REFUNDED}
    ),
    PaymentIntentStatus.CANCELLED: frozenset(),
    PaymentIntentStatus.REFUNDED: frozenset(),
}


_ATTEMPT_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    AttemptStatus.PENDING: frozenset(
        {
            AttemptStatus.REQUIRES_ACTION,
            AttemptStatus.PROCESSING,
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
            AttemptStatus.UNKNOWN,
        }
    ),
    AttemptStatus.REQUIRES_ACTION: frozenset(
        {
            AttemptStatus.PROCESSING,
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
            AttemptStatus.UNKNOWN,
        }
    ),
    AttemptStatus.PROCESSING: frozenset(
        {AttemptStatus.SUCCEEDED, AttemptStatus.FAILED, AttemptStatus.UNKNOWN}
    ),
    AttemptStatus.UNKNOWN: frozenset(
        {AttemptStatus.PROCESSING, AttemptStatus.SUCCEEDED, AttemptStatus.FAILED}
    ),
    AttemptStatus.SUCCEEDED: frozenset(),
    AttemptStatus.FAILED: frozenset(),
    AttemptStatus.CANCELLED: frozenset(),
}


_REFUND_TRANSITIONS: dict[RefundStatus, frozenset[RefundStatus]] = {
    RefundStatus.PENDING: frozenset(
        {RefundStatus.PROCESSING, RefundStatus.SUCCEEDED, RefundStatus.FAILED}
    ),
    RefundStatus.PROCESSING: frozenset({RefundStatus.SUCCEEDED, RefundStatus.FAILED}),
    RefundStatus.SUCCEEDED: frozenset(),
    RefundStatus.FAILED: frozenset(),
}


@dataclass(slots=True)
class PaymentIntent:
    client_id: str
    business_reference: str
    money: Money
    idempotency_key: str
    request_fingerprint: str
    payer_user_id: uuid.UUID | None = None
    payee_user_id: uuid.UUID | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: PaymentIntentStatus = PaymentIntentStatus.PROCESSING
    refunded_amount: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def transition_to(self, target: PaymentIntentStatus) -> None:
        if target == self.status:
            return
        if target not in _INTENT_TRANSITIONS[self.status]:
            raise InvalidStateTransition("payment_intent", self.status, target)
        self.status = target
        self.updated_at = utc_now()

    def apply_refund(self, amount: int) -> None:
        if self.status not in {
            PaymentIntentStatus.SUCCEEDED,
            PaymentIntentStatus.PARTIALLY_REFUNDED,
        }:
            raise InvalidStateTransition(
                "payment_intent", self.status, PaymentIntentStatus.REFUNDED
            )
        if amount <= 0 or self.refunded_amount + amount > self.money.amount:
            raise InvalidAmount("refund exceeds the captured amount")
        self.refunded_amount += amount
        target = (
            PaymentIntentStatus.REFUNDED
            if self.refunded_amount == self.money.amount
            else PaymentIntentStatus.PARTIALLY_REFUNDED
        )
        self.transition_to(target)

    @property
    def is_final(self) -> bool:
        return self.status in {
            PaymentIntentStatus.CANCELLED,
            PaymentIntentStatus.REFUNDED,
        }


@dataclass(slots=True)
class PaymentAttempt:
    payment_intent_id: uuid.UUID
    processor: str
    money: Money
    attempt_number: int
    channel: str | None = None
    network: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: AttemptStatus = AttemptStatus.PENDING
    provider_reference: str | None = None
    provider_status: str | None = None
    next_action: NextAction | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def transition_to(self, target: AttemptStatus) -> None:
        if target == self.status:
            return
        if target not in _ATTEMPT_TRANSITIONS[self.status]:
            raise InvalidStateTransition("payment_attempt", self.status, target)
        self.status = target
        self.updated_at = utc_now()


@dataclass(slots=True)
class Refund:
    payment_intent_id: uuid.UUID
    payment_attempt_id: uuid.UUID
    money: Money
    idempotency_key: str
    request_fingerprint: str
    reason: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: RefundStatus = RefundStatus.PENDING
    provider_reference: str | None = None
    provider_status: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def transition_to(self, target: RefundStatus) -> None:
        if target == self.status:
            return
        if target not in _REFUND_TRANSITIONS[self.status]:
            raise InvalidStateTransition("refund", self.status, target)
        self.status = target
        self.updated_at = utc_now()
