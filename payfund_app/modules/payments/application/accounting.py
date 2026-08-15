"""Double-entry accounting policies for orchestrated payments."""


class PaymentAccountingService:
    def __init__(self, ledger) -> None:
        self.ledger = ledger

    def record_capture(self, intent, attempt, *, event_reference: str, fee: int = 0) -> None:
        self.ledger.post(
            payment_intent_id=intent.id,
            event_type="capture",
            event_reference=event_reference,
            amount=intent.money.amount,
            currency=intent.money.currency,
            debit_account=f"processor_receivable:{attempt.processor}",
            credit_account=f"module_payable:{intent.client_id}",
        )
        if fee > 0:
            self.ledger.post(
                payment_intent_id=intent.id,
                event_type="processor_fee",
                event_reference=event_reference,
                amount=fee,
                currency=intent.money.currency,
                debit_account="processor_fee_expense",
                credit_account=f"processor_receivable:{attempt.processor}",
            )

    def record_refund(self, intent, attempt, refund) -> None:
        self.ledger.post(
            payment_intent_id=intent.id,
            event_type="refund",
            event_reference=str(refund.id),
            amount=refund.money.amount,
            currency=refund.money.currency,
            debit_account=f"module_payable:{intent.client_id}",
            credit_account=f"processor_receivable:{attempt.processor}",
        )

    def record_settlement(
        self, intent, *, processor: str, amount: int, settlement_reference: str
    ) -> None:
        summary = self.ledger.summary(intent.id)
        if amount <= 0 or amount > summary["outstanding"]:
            raise ValueError("settlement exceeds the outstanding provider receivable")
        self.ledger.post(
            payment_intent_id=intent.id,
            event_type="settlement",
            event_reference=settlement_reference,
            amount=amount,
            currency=intent.money.currency,
            debit_account="bank_cash",
            credit_account=f"processor_receivable:{processor}",
        )
