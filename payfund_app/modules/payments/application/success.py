"""One durable business effect for a newly confirmed payment."""


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
