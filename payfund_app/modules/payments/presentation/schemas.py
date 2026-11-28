"""Executable OpenAPI schemas for the PaymentIntent API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreatePaymentIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_reference: str = Field(min_length=1, max_length=128)
    processor: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="Optional processor override; otherwise PAYMENT_PROCESSOR_MODE is used",
    )
    amount: int = Field(gt=0, description="Positive amount; for XOF, 5000 means 5,000 XOF")
    currency: Literal["XOF"] = "XOF"
    payer_user_id: uuid.UUID | None = None
    payee_user_id: uuid.UUID | None = None
    channel: Literal["mobile_money", "card"] | None = None
    # Whether a network is actually available is decided by the selected processor's
    # capabilities (422 PAYMENT_METHOD_UNAVAILABLE otherwise). "moov": ticket 051.
    network: Literal["orange", "wave", "mtn", "moov"] | None = None
    customer_email: str | None = Field(default=None, max_length=254)
    customer_phone: str | None = Field(default=None, max_length=32)
    return_target: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Server-configured checkout return target name",
    )
    callback_url: str | None = Field(
        default=None,
        max_length=2048,
        deprecated=True,
        description="Staging migration only; use return_target",
    )
    description: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NextActionResponse(BaseModel):
    type: Literal[
        "redirect",
        "mobile_money_prompt",
        "display_instructions",
        "await_confirmation",
        "none",
    ]
    url: str | None = None
    instructions: str | None = None
    expires_at: datetime | None = None


class PaymentAttemptResponse(BaseModel):
    id: uuid.UUID
    processor: str
    status: Literal[
        "pending",
        "requires_action",
        "processing",
        "succeeded",
        "failed",
        "cancelled",
        "unknown",
    ]
    channel: str | None = None
    network: str | None = None
    provider_status: str | None = None
    next_action: NextActionResponse | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime
    updated_at: datetime


class PaymentIntentResponse(BaseModel):
    id: uuid.UUID
    client_id: str
    processor: str | None = None
    business_reference: str
    amount: int
    currency: str
    status: Literal[
        "requires_action",
        "processing",
        "succeeded",
        "failed",
        "cancelled",
        "partially_refunded",
        "refunded",
    ]
    provider_status: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    payer_user_id: uuid.UUID | None = None
    payee_user_id: uuid.UUID | None = None
    description: str | None = None
    metadata: dict[str, Any]
    refunded_amount: int
    next_action: NextActionResponse | None = None
    attempts: list[PaymentAttemptResponse]
    created_at: datetime
    updated_at: datetime


class PaymentIntentListResponse(BaseModel):
    data: list[PaymentIntentResponse]


class VerifyPaymentIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_mode: Literal["exact", "coverage"] = Field(
        default="exact",
        description=(
            "exact preserves the original equality check; coverage accepts a "
            "succeeded payment whose payment amount is at least required_amount."
        ),
    )
    amount: int | None = Field(
        default=None,
        gt=0,
        description="Expected exact amount in XOF when verification_mode=exact",
    )
    required_amount: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Minimum amount to cover in XOF when verification_mode=coverage"
        ),
    )
    currency: Literal["XOF"] = "XOF"
    business_reference: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_amount_for_mode(self) -> VerifyPaymentIntentRequest:
        if self.verification_mode == "exact":
            if self.amount is None or self.required_amount is not None:
                raise ValueError(
                    "exact verification requires amount and forbids required_amount"
                )
        elif self.required_amount is None or self.amount is not None:
            raise ValueError(
                "coverage verification requires required_amount and forbids amount"
            )
        return self


class VerifyPaymentIntentResponse(BaseModel):
    verified: Literal[True] = True
    payment_intent_id: uuid.UUID
    owner_client_id: str
    business_reference: str
    verification_mode: Literal["exact", "coverage"]
    amount: int | None = None
    payment_amount: int | None = None
    required_amount: int | None = None
    currency: Literal["XOF"]
    status: Literal["succeeded"]


class PaymentWebhookResponse(BaseModel):
    status: Literal["processed", "duplicate", "ignored", "failed"]
    event_key: str
    payment_intent_id: str | None = None


class PayoutWebhookResponse(BaseModel):
    status: Literal["processed", "duplicate", "ignored", "failed"]
    event_key: str
    payout_id: str | None = None


class RefundWebhookResponse(BaseModel):
    status: Literal["processed", "duplicate", "ignored", "failed"]
    event_key: str
    refund_id: str | None = None


class CreateRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0, description="Amount to refund; for XOF, 5000 means 5,000 XOF")
    reason: str | None = Field(default=None, max_length=255)
    recover_from_payee: bool = Field(
        default=False,
        description=(
            "Default false: the refund is not taken back from the payee (e.g. the driver). "
            "Set true only on an admin decision; the owning module receives it in the "
            "diddipay.refund.*.v1 event and debits the payee itself."
        ),
    )


class RefundResponse(BaseModel):
    id: uuid.UUID
    payment_intent_id: uuid.UUID
    amount: int
    currency: str
    status: Literal["pending", "processing", "succeeded", "failed"]
    provider_status: str | None = None
    recover_from_payee: bool = False
    created_at: datetime
    updated_at: datetime


class PaymentFinancialSummaryResponse(BaseModel):
    payment_intent_id: uuid.UUID
    currency: str
    gross_captured: int
    refunded: int
    processor_fees: int
    net_expected: int
    settled: int
    outstanding: int


class MobileMoneyDestination(BaseModel):
    """One beneficiary_destination type. Add a sibling model (bank transfer,
    wallet-to-wallet, ...) and list it in BeneficiaryDestination's union to extend --
    callers and the application layer never need to change, only processors that
    declare support for the new type."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["mobile_money"] = "mobile_money"
    phone: str = Field(min_length=1, max_length=32)
    # Free-form, same spirit as InitializePaymentRequest.network: the schema layer does
    # not know or care which processor validates this value against its own capabilities.
    network: str = Field(min_length=1, max_length=32)


BeneficiaryDestination = Annotated[MobileMoneyDestination, Field(discriminator="type")]


class CreatePayoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_reference: str = Field(min_length=1, max_length=128)
    processor: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="Optional processor override; otherwise PAYMENT_PROCESSOR_MODE is used",
    )
    beneficiary_reference: str = Field(min_length=1, max_length=128)
    # Where the money actually goes -- distinct from beneficiary_reference (the opaque
    # business identity, e.g. a restaurant UUID). None outside local/test, where payouts
    # resolve to a simulated processor and no real destination is needed.
    beneficiary_destination: BeneficiaryDestination | None = None
    amount: int = Field(gt=0, description="Positive integer amount in XOF")
    currency: Literal["XOF"] = "XOF"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PayoutResponse(BaseModel):
    id: uuid.UUID
    client_id: str
    business_reference: str
    beneficiary_reference: str
    beneficiary_destination: dict[str, Any] | None = None
    amount: int
    currency: str
    status: Literal["pending", "processing", "succeeded", "failed", "disputed"]
    processor: str
    provider_status: str | None = None
    failure_code: str | None = None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PayoutFinancialSummaryResponse(BaseModel):
    payout_id: uuid.UUID
    currency: str
    paid_out: int
