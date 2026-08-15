"""Provider-neutral PaymentIntent orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from payfund_app.modules.payments.application.errors import (
    IdempotencyConflict,
    PaymentNotFound,
    PaymentOperationConflict,
    PersistenceConflict,
)
from payfund_app.modules.payments.application.fingerprints import request_fingerprint
from payfund_app.modules.payments.application.ports import (
    InitializePaymentRequest,
    PaymentAttemptRepositoryPort,
    PaymentDirection,
    PaymentIntentRepositoryPort,
    ProviderResult,
    UnitOfWorkPort,
)
from payfund_app.modules.payments.application.processor_router import ProcessorRegistry
from payfund_app.modules.payments.domain import (
    AttemptStatus,
    Money,
    PaymentAttempt,
    PaymentIntent,
    PaymentIntentStatus,
)


@dataclass(frozen=True, slots=True)
class CreatePaymentIntentCommand:
    client_id: str
    business_reference: str
    amount: int
    currency: str
    idempotency_key: str
    payer_user_id: uuid.UUID | None = None
    payee_user_id: uuid.UUID | None = None
    channel: str | None = None
    network: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    callback_url: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return request_fingerprint(
            {
                "client_id": self.client_id,
                "business_reference": self.business_reference,
                "amount": self.amount,
                "currency": self.currency.upper(),
                "payer_user_id": self.payer_user_id,
                "payee_user_id": self.payee_user_id,
                "channel": self.channel,
                "network": self.network,
                "description": self.description,
                "metadata": self.metadata,
            }
        )


@dataclass(frozen=True, slots=True)
class PaymentView:
    intent: PaymentIntent
    attempts: tuple[PaymentAttempt, ...]


class PaymentUseCases:
    def __init__(
        self,
        intents: PaymentIntentRepositoryPort,
        attempts: PaymentAttemptRepositoryPort,
        processors: ProcessorRegistry,
        uow: UnitOfWorkPort,
    ) -> None:
        self.intents = intents
        self.attempts = attempts
        self.processors = processors
        self.uow = uow

    def create(self, command: CreatePaymentIntentCommand) -> PaymentView:
        fingerprint = command.fingerprint()
        existing = self.intents.get_by_idempotency(command.client_id, command.idempotency_key)
        if existing:
            return self._idempotent_view(existing, fingerprint)

        processor = self.processors.select(
            currency=command.currency,
            direction=PaymentDirection.COLLECTION,
            channel=command.channel,
            network=command.network,
        )
        intent = PaymentIntent(
            client_id=command.client_id,
            business_reference=command.business_reference,
            payer_user_id=command.payer_user_id,
            payee_user_id=command.payee_user_id,
            money=Money(command.amount, command.currency),
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
            description=command.description,
            metadata=command.metadata,
        )
        attempt = PaymentAttempt(
            payment_intent_id=intent.id,
            processor=processor.name,
            channel=command.channel,
            network=command.network,
            money=intent.money,
            attempt_number=1,
        )
        try:
            self.intents.add(intent)
            self.attempts.add(attempt)
            # The intent and pending attempt survive a process crash before the network call.
            self.uow.commit()
        except PersistenceConflict:
            existing = self.intents.get_by_idempotency(
                command.client_id, command.idempotency_key
            )
            if existing is None:
                raise
            return self._idempotent_view(existing, fingerprint)

        result = self._initialize(processor, command, intent, attempt)
        self._apply_provider_result(intent, attempt, result)
        self.intents.save(intent)
        self.attempts.save(attempt)
        self.uow.commit()
        return PaymentView(intent, (attempt,))

    def get(self, client_id: str, intent_id: uuid.UUID) -> PaymentView:
        intent = self.intents.get(intent_id)
        if intent is None or intent.client_id != client_id:
            raise PaymentNotFound()
        return PaymentView(intent, tuple(self.attempts.list_for_intent(intent.id)))

    def list(self, client_id: str, *, limit: int = 50) -> list[PaymentView]:
        return [
            PaymentView(intent, tuple(self.attempts.list_for_intent(intent.id)))
            for intent in self.intents.list_for_client(client_id, limit=limit)
        ]

    def cancel(self, client_id: str, intent_id: uuid.UUID) -> PaymentView:
        view = self.get(client_id, intent_id)
        attempt = view.attempts[-1] if view.attempts else None
        if (
            intent_id != view.intent.id
            or attempt is None
            or attempt.status != AttemptStatus.PENDING
            or attempt.provider_reference is not None
        ):
            raise PaymentOperationConflict(
                "a payment already sent to a processor cannot be cancelled locally"
            )
        attempt.transition_to(AttemptStatus.CANCELLED)
        view.intent.transition_to(PaymentIntentStatus.CANCELLED)
        self.attempts.save(attempt)
        self.intents.save(view.intent)
        self.uow.commit()
        return PaymentView(view.intent, view.attempts)

    def _idempotent_view(self, intent: PaymentIntent, fingerprint: str) -> PaymentView:
        if intent.request_fingerprint != fingerprint:
            raise IdempotencyConflict()
        return PaymentView(intent, tuple(self.attempts.list_for_intent(intent.id)))

    def _initialize(self, processor, command, intent, attempt) -> ProviderResult:
        try:
            return processor.initialize_payment(
                InitializePaymentRequest(
                    payment_intent_id=intent.id,
                    attempt_id=attempt.id,
                    business_reference=intent.business_reference,
                    money=intent.money,
                    idempotency_key=command.idempotency_key,
                    channel=command.channel,
                    network=command.network,
                    customer_email=command.customer_email,
                    customer_phone=command.customer_phone,
                    callback_url=command.callback_url,
                    metadata=command.metadata,
                )
            )
        except Exception as exc:
            # The processor may have accepted the request before the connection failed.
            return ProviderResult(
                provider_reference=f"unknown-{attempt.id}",
                status=AttemptStatus.UNKNOWN,
                provider_status="network_error",
                failure_code="PROCESSOR_UNCERTAIN",
                failure_message=str(exc)[:255],
            )

    @staticmethod
    def _apply_provider_result(
        intent: PaymentIntent, attempt: PaymentAttempt, result: ProviderResult
    ) -> None:
        attempt.provider_reference = result.provider_reference
        attempt.provider_status = result.provider_status
        attempt.next_action = result.next_action
        attempt.failure_code = result.failure_code
        attempt.failure_message = result.failure_message
        attempt.transition_to(result.status)
        targets = {
            AttemptStatus.REQUIRES_ACTION: PaymentIntentStatus.REQUIRES_ACTION,
            AttemptStatus.SUCCEEDED: PaymentIntentStatus.SUCCEEDED,
            AttemptStatus.FAILED: PaymentIntentStatus.FAILED,
            AttemptStatus.CANCELLED: PaymentIntentStatus.CANCELLED,
        }
        target = targets.get(result.status)
        if target is not None:
            intent.transition_to(target)
