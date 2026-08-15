"""Executable OpenAPI schemas for the PaymentIntent API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CreatePaymentIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_reference: str = Field(min_length=1, max_length=128)
    amount: int = Field(gt=0, description="Positive amount in minor units")
    currency: Literal["XOF"] = "XOF"
    payer_user_id: uuid.UUID | None = None
    payee_user_id: uuid.UUID | None = None
    channel: Literal["mobile_money", "card"] | None = None
    network: Literal["orange", "wave", "mtn"] | None = None
    customer_email: str | None = Field(default=None, max_length=254)
    customer_phone: str | None = Field(default=None, max_length=32)
    callback_url: str | None = Field(default=None, max_length=2048)
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
    next_action: NextActionResponse | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime


class PaymentIntentResponse(BaseModel):
    id: uuid.UUID
    client_id: str
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
    payer_user_id: uuid.UUID | None = None
    payee_user_id: uuid.UUID | None = None
    description: str | None = None
    metadata: dict[str, Any]
    refunded_amount: int
    attempts: list[PaymentAttemptResponse]
    created_at: datetime
    updated_at: datetime


class PaymentIntentListResponse(BaseModel):
    data: list[PaymentIntentResponse]


class PaymentWebhookResponse(BaseModel):
    status: Literal["processed", "duplicate", "ignored", "failed"]
    event_key: str
    payment_intent_id: str | None = None


class CreateRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0, description="Amount to refund in minor units")
    reason: str | None = Field(default=None, max_length=255)


class RefundResponse(BaseModel):
    id: uuid.UUID
    payment_intent_id: uuid.UUID
    amount: int
    currency: str
    status: Literal["pending", "processing", "succeeded", "failed"]
    provider_status: str | None = None
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
