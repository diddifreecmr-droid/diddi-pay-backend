"""SQLAlchemy records for provider-neutral payment orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from payfund_app.core.database import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )


class PaymentIntentRecord(Base):
    __tablename__ = "payment_intents"
    __table_args__ = (
        UniqueConstraint(
            "client_id", "idempotency_key", name="uq_payment_intents_client_idempotency"
        ),
        CheckConstraint("amount > 0", name="ck_payment_intents_amount_positive"),
        CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= amount",
            name="ck_payment_intents_refunded_amount",
        ),
        CheckConstraint(
            "status IN ('requires_action','processing','succeeded','failed','cancelled',"
            "'partially_refunded','refunded')",
            name="ck_payment_intents_status",
        ),
        Index("idx_payment_intents_business", "client_id", "business_reference"),
        Index("idx_payment_intents_status_updated", "status", "updated_at"),
        {"schema": "payments"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    payer_user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    payee_user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    refunded_amount: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaymentAttemptRecord(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "payment_intent_id", "attempt_number", name="uq_payment_attempt_number"
        ),
        CheckConstraint("amount > 0", name="ck_payment_attempts_amount_positive"),
        CheckConstraint("attempt_number > 0", name="ck_payment_attempt_number_positive"),
        CheckConstraint(
            "status IN ('pending','requires_action','processing','succeeded','failed',"
            "'cancelled','unknown')",
            name="ck_payment_attempts_status",
        ),
        Index("idx_payment_attempts_intent", "payment_intent_id", "attempt_number"),
        Index(
            "uq_payment_attempt_provider_reference",
            "processor",
            "provider_reference",
            unique=True,
            postgresql_where=text("provider_reference IS NOT NULL"),
        ),
        Index(
            "uq_payment_attempt_success",
            "payment_intent_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
        Index("idx_payment_attempts_status_updated", "status", "updated_at"),
        {"schema": "payments"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("payments.payment_intents.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    processor: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(40))
    network: Mapped[str | None] = mapped_column(String(40))
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(160))
    provider_status: Mapped[str | None] = mapped_column(String(80))
    next_action: Mapped[dict | None] = mapped_column(JSONB)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProviderEventRecord(Base):
    __tablename__ = "provider_events"
    __table_args__ = (
        UniqueConstraint("processor", "event_key", name="uq_provider_event_key"),
        CheckConstraint(
            "status IN ('received','processed','ignored','failed')",
            name="ck_provider_events_status",
        ),
        Index("idx_provider_events_status_created", "status", "created_at"),
        {"schema": "payments"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    processor: Mapped[str] = mapped_column(String(64), nullable=False)
    event_key: Mapped[str] = mapped_column(String(180), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    payment_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("payments.payment_attempts.id")
    )
    error_message: Mapped[str | None] = mapped_column(String(255))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RefundRecord(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("client_id", "idempotency_key", name="uq_refunds_client_idempotency"),
        CheckConstraint("amount > 0", name="ck_refunds_amount_positive"),
        CheckConstraint(
            "status IN ('pending','processing','succeeded','failed')",
            name="ck_refunds_status",
        ),
        Index(
            "uq_refund_provider_reference",
            "processor",
            "provider_reference",
            unique=True,
            postgresql_where=text("provider_reference IS NOT NULL"),
        ),
        Index("idx_refunds_intent_created", "payment_intent_id", "created_at"),
        {"schema": "payments"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("payments.payment_intents.id"), nullable=False
    )
    payment_attempt_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("payments.payment_attempts.id"), nullable=False
    )
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    processor: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    provider_reference: Mapped[str | None] = mapped_column(String(160))
    provider_status: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaymentOutboxRecord(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','delivered','dead_letter')", name="ck_payment_outbox_status"
        ),
        Index("idx_payment_outbox_pending", "status", "next_attempt_at", "created_at"),
        {"schema": "payments"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(255))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
