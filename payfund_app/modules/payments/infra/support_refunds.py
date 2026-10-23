"""Persistence for delegated Support refund actions."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from payfund_app.modules.payments.infra.models import SupportRefundActionRecord


class SupportRefundActionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_idempotency(self, client_id: str, key: str):
        return self.session.scalar(
            select(SupportRefundActionRecord).where(
                SupportRefundActionRecord.client_id == client_id,
                SupportRefundActionRecord.idempotency_key == key,
            )
        )

    def get(self, client_id: str, action_id: uuid.UUID):
        return self.session.scalar(
            select(SupportRefundActionRecord).where(
                SupportRefundActionRecord.client_id == client_id,
                SupportRefundActionRecord.id == action_id,
            )
        )

    def create(self, **values):
        row = SupportRefundActionRecord(status="processing", result={}, **values)
        self.session.add(row)
        self.session.flush()
        return row

    def complete(self, row, *, status: str, result: dict, refund_id: uuid.UUID | None):
        row.status = status
        row.result = result
        row.refund_id = refund_id
        self.session.flush()
        self.session.commit()
        return row
