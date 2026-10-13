"""Provider-neutral, module-owned payout orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from payfund_app.modules.payments.application.errors import (
    IdempotencyConflict,
    PaymentNotFound,
    PersistenceConflict,
    ProcessorCallUncertain,
    ProcessorRequestRejected,
)
from payfund_app.modules.payments.application.fingerprints import request_fingerprint
from payfund_app.modules.payments.application.ports import (
    PaymentDirection,
    PaymentOutboxRepositoryPort,
    PayoutRepositoryPort,
    PayoutRequest,
    PayoutResult,
    UnitOfWorkPort,
)
from payfund_app.modules.payments.application.processor_router import (
    SIMULATED_PROCESSOR_NAMES,
    ProcessorRegistry,
    ProcessorRoutingError,
)
from payfund_app.modules.payments.domain import Money, Payout, PayoutStatus


@dataclass(frozen=True, slots=True)
class CreatePayoutCommand:
    client_id: str
    business_reference: str
    beneficiary_reference: str
    amount: int
    currency: str
    idempotency_key: str
    # {"type": "mobile_money", "phone": ..., "network": ...} today; other types added later
    # without touching this command or any caller. None outside local/test.
    beneficiary_destination: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return request_fingerprint(
            {
                "client_id": self.client_id,
                "business_reference": self.business_reference,
                "beneficiary_reference": self.beneficiary_reference,
                "beneficiary_destination": self.beneficiary_destination,
                "amount": self.amount,
                "currency": self.currency.upper(),
                "metadata": self.metadata,
            }
        )


@dataclass(frozen=True, slots=True)
class PayoutView:
    payout: Payout
    created: bool = False


@dataclass(frozen=True, slots=True)
class PayoutReconciliationSummary:
    scanned: int
    succeeded: int
    failed: int
    pending: int


class PayoutUseCases:
    def __init__(
        self,
        payouts: PayoutRepositoryPort,
        outbox: PaymentOutboxRepositoryPort,
        processors: ProcessorRegistry,
        uow: UnitOfWorkPort,
        accounting=None,
        allow_simulated_payouts: bool = False,
    ) -> None:
        self.payouts = payouts
        self.outbox = outbox
        self.processors = processors
        self.uow = uow
        self.accounting = accounting
        # Safe by default: a caller must explicitly opt in (local/test only, see
        # payout_router._use_cases) before a payout is allowed to resolve to a processor that
        # reports SUCCEEDED without ever moving real money (SandboxPaymentProcessor today --
        # no real PSP currently implements PaymentDirection.PAYOUT at all).
        self.allow_simulated_payouts = allow_simulated_payouts

    def create(self, command: CreatePayoutCommand) -> PayoutView:
        fingerprint = command.fingerprint()
        existing = self.payouts.get_by_idempotency(command.client_id, command.idempotency_key)
        if existing:
            return self._idempotent_view(existing, fingerprint)

        # Routing is derived from the destination, never hardcoded to a provider: a
        # mobile_money destination's "channel"/"network" are the same generic hints
        # InitializePaymentRequest already uses for collections. A processor that doesn't
        # recognise a given type/network simply never matches -- see ProcessorCapabilities.
        destination = command.beneficiary_destination or {}
        channel = destination.get("type")
        network = destination.get("network")
        processor = self.processors.select(
            currency=command.currency,
            direction=PaymentDirection.PAYOUT,
            channel=channel,
            network=network,
        )
        if processor.name in SIMULATED_PROCESSOR_NAMES and not self.allow_simulated_payouts:
            raise ProcessorRoutingError(
                f"no real payout processor is configured for currency={command.currency!r} "
                "(only a simulated processor matched -- refusing to report a fake payout as "
                "successful outside local/test)"
            )
        payout = Payout(
            client_id=command.client_id,
            business_reference=command.business_reference,
            beneficiary_reference=command.beneficiary_reference,
            destination=command.beneficiary_destination,
            money=Money(command.amount, command.currency),
            idempotency_key=command.idempotency_key,
            request_fingerprint=fingerprint,
            processor=processor.name,
            metadata=command.metadata,
        )
        try:
            self.payouts.add(payout)
            # Persist the idempotency barrier before the external call.
            self.uow.commit()
        except PersistenceConflict:
            existing = self.payouts.get_by_idempotency(
                command.client_id, command.idempotency_key
            )
            if existing is None:
                raise
            return self._idempotent_view(existing, fingerprint)

        result = self._execute(processor, payout)
        payout.apply_provider_status(
            result.status,
            provider_reference=result.provider_reference,
            provider_status=result.provider_status,
            failure_code=result.failure_code,
            failure_message=result.failure_message,
        )
        self.payouts.save(payout)
        if payout.status == PayoutStatus.SUCCEEDED and self.accounting is not None:
            self.accounting.record_success(payout)
        self._enqueue_status(payout)
        self.uow.commit()
        return PayoutView(payout, created=True)

    def get(self, client_id: str, payout_id: uuid.UUID) -> PayoutView:
        payout = self.payouts.get(payout_id)
        if payout is None or payout.client_id != client_id:
            raise PaymentNotFound()
        return PayoutView(payout)

    def get_by_business_reference(self, client_id: str, reference: str) -> PayoutView:
        payout = self.payouts.get_by_business_reference(client_id, reference)
        if payout is None:
            raise PaymentNotFound()
        return PayoutView(payout)

    def _idempotent_view(self, payout: Payout, fingerprint: str) -> PayoutView:
        if payout.request_fingerprint != fingerprint:
            raise IdempotencyConflict()
        return PayoutView(payout)

    @staticmethod
    def _execute(processor, payout: Payout) -> PayoutResult:
        try:
            return processor.create_payout(
                PayoutRequest(
                    payout_id=payout.id,
                    business_reference=payout.business_reference,
                    beneficiary_reference=payout.beneficiary_reference,
                    money=payout.money,
                    idempotency_key=payout.idempotency_key,
                    destination=payout.destination,
                    metadata=payout.metadata,
                )
            )
        except ProcessorRequestRejected as exc:
            return PayoutResult(
                provider_reference=None,
                status=PayoutStatus.FAILED,
                provider_status="rejected_before_call",
                failure_code="PROCESSOR_REQUEST_REJECTED",
                failure_message=str(exc)[:255],
            )
        except ProcessorCallUncertain as exc:
            return PayoutResult(
                provider_reference=exc.provider_reference,
                status=PayoutStatus.PROCESSING,
                provider_status="network_error",
                failure_code="PROCESSOR_UNCERTAIN",
                failure_message=str(exc)[:255],
            )
        except Exception as exc:  # noqa: BLE001 - provider outcome may be uncertain
            return PayoutResult(
                provider_reference=None,
                status=PayoutStatus.PROCESSING,
                provider_status="network_error",
                failure_code="PROCESSOR_UNCERTAIN",
                failure_message=str(exc)[:255],
            )

    def _enqueue_status(self, payout: Payout) -> None:
        self.outbox.enqueue(
            client_id=payout.client_id,
            event_type=f"payout.{payout.status}",
            aggregate_id=payout.id,
            payload={
                "payout_id": str(payout.id),
                "business_reference": payout.business_reference,
                "beneficiary_reference": payout.beneficiary_reference,
                "amount": payout.money.amount,
                "currency": payout.money.currency,
                "status": str(payout.status),
                "metadata": payout.metadata,
            },
        )

    def reconcile(
        self, *, minimum_age_seconds: int = 300, limit: int = 100
    ) -> PayoutReconciliationSummary:
        cutoff = datetime.now(UTC) - timedelta(seconds=minimum_age_seconds)
        candidates = self.payouts.pending_for_reconciliation(
            older_than=cutoff, limit=limit
        )
        succeeded = failed = pending = 0
        for payout in candidates:
            try:
                processor = self.processors.get(payout.processor)
            except ProcessorRoutingError:
                pending += 1
                self.uow.commit()
                continue
            try:
                result = processor.verify_payout(payout.provider_reference)
            except (ProcessorCallUncertain, OSError):
                pending += 1
                self.uow.commit()
                continue
            previous = payout.status
            payout.apply_provider_status(
                result.status,
                provider_reference=result.provider_reference,
                provider_status=result.provider_status,
                failure_code=result.failure_code,
                failure_message=result.failure_message,
            )
            self.payouts.save(payout)
            if payout.status == PayoutStatus.SUCCEEDED and self.accounting is not None:
                self.accounting.record_success(payout)
            if payout.status != previous:
                self._enqueue_status(payout)
            if payout.status == PayoutStatus.SUCCEEDED:
                succeeded += 1
            elif payout.status == PayoutStatus.FAILED:
                failed += 1
            else:
                pending += 1
            self.uow.commit()
        return PayoutReconciliationSummary(len(candidates), succeeded, failed, pending)
