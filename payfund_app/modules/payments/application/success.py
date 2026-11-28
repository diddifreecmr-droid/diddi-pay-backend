"""Durable business effects of a payment reaching a final state."""

from payfund_app.modules.payments.domain import PaymentIntentStatus

# Ticket 116: modules hear about a failure as fast as about a success. Only asynchronous
# outcomes (provider callback, reconciliation) are published here; a failure at creation or a
# local cancel is already returned in the HTTP response of that call.
_OUTCOME_EVENTS = {
    PaymentIntentStatus.FAILED: "payment.failed",
    PaymentIntentStatus.CANCELLED: "payment.cancelled",
}


def record_payment_success(intent, attempt, *, event_key, outbox, accounting, fee=0) -> None:
    if outbox is not None:
        outbox.enqueue(
            client_id=intent.client_id,
            event_type="payment.succeeded",
            aggregate_id=intent.id,
            payload={
                "event_id": event_key,
                "payment_intent_id": str(intent.id),
                "business_reference": intent.business_reference,
                "amount": intent.money.amount,
                "currency": intent.money.currency,
                "status": str(intent.status),
            },
        )
    if accounting is not None:
        accounting.record_capture(intent, attempt, event_reference=event_key, fee=fee)


def record_payment_outcome(intent, attempt, *, previous_status, event_key, outbox) -> None:
    """Publish payment.failed / payment.cancelled when the intent just entered that state."""
    if outbox is None or intent.status == previous_status:
        return
    event_type = _OUTCOME_EVENTS.get(intent.status)
    if event_type is None:
        return
    outbox.enqueue(
        client_id=intent.client_id,
        event_type=event_type,
        aggregate_id=intent.id,
        payload={
            "event_id": f"{event_key}:{intent.status}",
            "payment_intent_id": str(intent.id),
            "business_reference": intent.business_reference,
            "amount": intent.money.amount,
            "currency": intent.money.currency,
            "status": str(intent.status),
            "failure_code": getattr(attempt, "failure_code", None),
            "failure_message": getattr(attempt, "failure_message", None),
        },
    )
