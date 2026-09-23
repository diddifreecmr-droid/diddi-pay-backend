"""Audited and idempotent commands initiated by DiddiAdmin Backoffice."""

import uuid
from dataclasses import dataclass
from typing import Any

from payfund_app.core.errors import Conflict, NotFound
from payfund_app.modules.payments.application.fingerprints import request_fingerprint


@dataclass(frozen=True, slots=True)
class BackofficeCommand:
    id: uuid.UUID
    client_id: str
    command_id: str
    actor_user_id: str
    idempotency_key: str
    action: str
    target_type: str
    target_id: uuid.UUID
    reason: str
    request_fingerprint: str
    status: str
    result: dict[str, Any]


class BackofficeCommandService:
    def __init__(self, repository, operations) -> None:
        self.repository = repository
        self.operations = operations

    def execute(
        self,
        *,
        client_id: str,
        command_id: str,
        actor_user_id: str,
        idempotency_key: str,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        reason: str,
        payload: dict[str, Any],
    ) -> BackofficeCommand:
        fingerprint = request_fingerprint(
            {
                "action": action,
                "target_type": target_type,
                "target_id": str(target_id),
                "reason": reason,
                "payload": payload,
            }
        )
        existing = self.repository.find_replay(
            client_id=client_id,
            command_id=command_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise Conflict(
                    "Cette clé a déjà été utilisée pour une autre commande.",
                    code="IDEMPOTENCY_CONFLICT",
                )
            return existing

        command = self.repository.create(
            client_id=client_id,
            command_id=command_id,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            request_fingerprint=fingerprint,
        )
        if action == "payment.callback.retry":
            result = self.operations.retry_callback(
                payment_intent_id=target_id,
                event_id=uuid.UUID(payload["event_id"]),
            )
        elif action == "payment.settlement.record":
            result = self.operations.record_settlement(
                payment_intent_id=target_id,
                amount=int(payload["amount"]),
                settlement_reference=str(payload["settlement_reference"]),
            )
        else:
            raise NotFound("Commande Backoffice inconnue.", code="COMMAND_NOT_FOUND")
        return self.repository.complete(command, result)

    def get(self, *, client_id: str, command_id: str) -> BackofficeCommand | None:
        return self.repository.get(client_id=client_id, command_id=command_id)
