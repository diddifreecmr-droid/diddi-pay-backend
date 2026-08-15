"""In-process DiddiPay adapter; replaceable by HTTP when DiddiFund is extracted."""

import uuid

from sqlalchemy.orm import Session

from payfund_app.modules.fund.application.payment_ports import (
    FundPaymentAction,
    FundPaymentResult,
)
from payfund_app.modules.payments.application.use_cases import (
    CreatePaymentIntentCommand,
    PaymentUseCases,
)
from payfund_app.modules.payments.infra.repositories import (
    PaymentAttemptRepository,
    PaymentIntentRepository,
)
from payfund_app.modules.payments.infra.unit_of_work import SqlAlchemyUnitOfWork
from payfund_app.modules.payments.presentation.deps import get_processor_registry


class InProcessPaymentOrchestrator:
    def __init__(self, session: Session) -> None:
        self.use_cases = PaymentUseCases(
            PaymentIntentRepository(session),
            PaymentAttemptRepository(session),
            get_processor_registry(),
            SqlAlchemyUnitOfWork(session),
        )

    def create_collection(self, **values) -> FundPaymentResult:
        values["payer_user_id"] = uuid.UUID(values["payer_user_id"])
        view = self.use_cases.create(
            CreatePaymentIntentCommand(client_id="diddifund", **values)
        )
        return self._result(view)

    def get_collection(self, payment_intent_id: str) -> FundPaymentResult:
        view = self.use_cases.get("diddifund", uuid.UUID(payment_intent_id))
        return self._result(view)

    @staticmethod
    def _result(view) -> FundPaymentResult:
        attempt = view.attempts[-1]
        action = attempt.next_action
        return FundPaymentResult(
            payment_intent_id=str(view.intent.id),
            status=str(view.intent.status),
            next_action=FundPaymentAction(
                type=str(action.type), url=action.url, instructions=action.instructions
            )
            if action
            else None,
        )
