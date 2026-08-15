"""External collection workflows owned by DiddiFund."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from payfund_app.modules.fund.application.payment_ports import (
    FundPaymentResult,
    PaymentOrchestratorPort,
)
from payfund_app.modules.fund.domain.entities import CampaignStatus
from payfund_app.modules.fund.domain.errors import (
    CampaignGoalAlreadyReached,
    CampaignNotActive,
    CampaignNotFound,
    CannotInvestInOwnCampaign,
)
from payfund_app.modules.fund.infra.repositories import (
    CampaignRepository,
    FundPaymentEventInboxRepository,
    FundPaymentOrderRepository,
    InvestmentRepository,
)


@dataclass(frozen=True, slots=True)
class FundPaymentOrderView:
    order: object
    payment: FundPaymentResult | None = None


class FundPaymentUseCases:
    def __init__(
        self, session: Session, payments: PaymentOrchestratorPort | None = None
    ) -> None:
        self.session = session
        self.payments = payments
        self.campaigns = CampaignRepository(session)
        self.investments = InvestmentRepository(session)
        self.orders = FundPaymentOrderRepository(session)
        self.inbox = FundPaymentEventInboxRepository(session)

    def start_investment(
        self,
        *,
        campaign_id: uuid.UUID,
        investor_user_id: uuid.UUID,
        amount: int,
        idempotency_key: str,
        channel: str | None,
        network: str | None,
        customer_email: str | None,
        customer_phone: str | None,
        callback_url: str | None,
    ) -> FundPaymentOrderView:
        existing = self.orders.by_idempotency(idempotency_key)
        if existing is not None:
            if (
                existing.campaign_id != campaign_id
                or existing.payer_user_id != investor_user_id
                or existing.amount != amount
            ):
                raise ValueError("IDEMPOTENCY_CONFLICT")
            if self.payments is None:
                return FundPaymentOrderView(existing)
            return FundPaymentOrderView(
                existing,
                self.payments.get_collection(str(existing.payment_intent_id)),
            )

        campaign = self.campaigns.get(campaign_id)
        if campaign is None:
            raise CampaignNotFound()
        if campaign.status != str(CampaignStatus.ACTIVE):
            raise CampaignNotActive(details={"status": campaign.status})
        if campaign.owner_user_id == investor_user_id:
            raise CannotInvestInOwnCampaign()
        remaining = int(campaign.goal_amount - campaign.raised_amount)
        if remaining <= 0 or amount > remaining:
            raise CampaignGoalAlreadyReached(details={"remaining_amount": max(0, remaining)})

        reference = f"fund:investment:{campaign_id}:{idempotency_key[:24]}"
        if self.payments is None:
            raise RuntimeError("payment orchestrator is required to start a collection")
        payment = self.payments.create_collection(
            business_reference=reference,
            amount=amount,
            currency=campaign.currency,
            payer_user_id=str(investor_user_id),
            idempotency_key=idempotency_key,
            channel=channel,
            network=network,
            customer_email=customer_email,
            customer_phone=customer_phone,
            callback_url=callback_url,
            metadata={"campaign_id": str(campaign_id), "operation": "investment"},
        )
        try:
            order = self.orders.create(
                operation_type="investment",
                business_reference=reference,
                payer_user_id=investor_user_id,
                campaign_id=campaign_id,
                payment_intent_id=uuid.UUID(payment.payment_intent_id),
                idempotency_key=idempotency_key,
                amount=amount,
                currency=campaign.currency,
                status=payment.status,
            )
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            order = self.orders.by_idempotency(idempotency_key)
            if order is None:
                raise
            if (
                order.campaign_id != campaign_id
                or order.payer_user_id != investor_user_id
                or order.amount != amount
            ):
                raise ValueError("IDEMPOTENCY_CONFLICT")
        return FundPaymentOrderView(order, payment)

    def get_order(self, order_id: uuid.UUID, user_id: uuid.UUID) -> FundPaymentOrderView:
        order = self.orders.get(order_id)
        if order is None or order.payer_user_id != user_id:
            raise LookupError("PAYMENT_ORDER_NOT_FOUND")
        payment = (
            self.payments.get_collection(str(order.payment_intent_id))
            if self.payments is not None
            else None
        )
        return FundPaymentOrderView(order, payment)

    def apply_event(
        self, *, event_id: uuid.UUID, event_type: str, data: dict
    ) -> str:
        if not self.inbox.add_once(event_id=event_id, event_type=event_type, payload=data):
            self.session.commit()
            return "duplicate"
        payment_intent_id = uuid.UUID(data["payment_intent_id"])
        order = self.orders.by_payment_intent(payment_intent_id, for_update=True)
        if order is None:
            self.session.rollback()
            raise ValueError("PAYMENT_ORDER_NOT_FOUND")
        if data.get("business_reference") != order.business_reference:
            self.session.rollback()
            raise ValueError("PAYMENT_REFERENCE_MISMATCH")
        if int(data.get("amount", 0)) != order.amount or data.get("currency") != order.currency:
            self.session.rollback()
            raise ValueError("PAYMENT_AMOUNT_MISMATCH")
        if event_type != "payment.succeeded":
            order.status = str(data.get("status") or order.status)
            self.session.commit()
            return "processed"
        if order.status != "succeeded":
            campaign = self.campaigns.get_for_update(order.campaign_id)
            if campaign is None:
                self.session.rollback()
                raise CampaignNotFound()
            self.investments.create(
                campaign_id=campaign.id,
                investor_user_id=order.payer_user_id,
                amount=order.amount,
                payment_intent_id=order.payment_intent_id,
            )
            campaign.raised_amount += Decimal(order.amount)
            order.status = "succeeded"
            order.completed_at = datetime.now(timezone.utc)
        self.session.commit()
        return "processed"
