"""Schémas du module `fund` — miroir du contrat API §2."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateCampaignRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal_amount: int = Field(gt=0)
    currency: str = Field(default="XOF", min_length=3, max_length=3)


class CreateCampaignResponse(BaseModel):
    campaign_id: uuid.UUID
    status: str


class CampaignItem(BaseModel):
    id: uuid.UUID
    title: str
    goal_amount: int
    raised_amount: int
    currency: str
    status: str


class InvestmentSummary(BaseModel):
    """Vue allégée d'un investissement dans le détail d'une campagne.

    Le contrat prévoit d'y afficher le nom de l'investisseur « s'il a choisi la visibilité
    publique ». Ce champ n'est pas exposé : ni le préférence de visibilité, ni la récupération du
    nom (qui passerait par `GET /users/{id}` en service-à-service, dont l'authentification est
    encore « à trancher » côté DiddiFreeID §5) ne sont spécifiés.
    """

    amount: int
    created_at: datetime


class CampaignDetail(CampaignItem):
    owner_user_id: uuid.UUID
    created_at: datetime
    closed_at: datetime | None
    latest_investments: list[InvestmentSummary]


class InvestRequest(BaseModel):
    amount: int = Field(gt=0)
    pin: str = Field(min_length=4, max_length=12)


class InvestResponse(BaseModel):
    investment_id: uuid.UUID
    campaign_id: uuid.UUID
    amount: int
    wallet_transaction_id: uuid.UUID


class ExternalInvestmentRequest(BaseModel):
    amount: int = Field(gt=0)
    channel: Literal["mobile_money", "card"] | None = None
    network: Literal["orange", "wave", "mtn"] | None = None
    customer_email: str | None = Field(default=None, max_length=254)
    customer_phone: str | None = Field(default=None, max_length=32)
    callback_url: str | None = Field(default=None, max_length=2048)


class FundNextActionResponse(BaseModel):
    type: str
    url: str | None = None
    instructions: str | None = None


class FundPaymentOrderResponse(BaseModel):
    id: uuid.UUID
    operation_type: str
    business_reference: str
    payment_intent_id: uuid.UUID
    amount: int
    currency: str
    status: str
    next_action: FundNextActionResponse | None = None


class DiddiPayEventRequest(BaseModel):
    id: uuid.UUID
    type: str
    occurred_at: datetime
    data: dict[str, Any]


class DiddiPayEventResponse(BaseModel):
    status: Literal["processed", "duplicate"]


# --- Prêts -------------------------------------------------------------------


class SimulateLoanRequest(BaseModel):
    amount: int = Field(gt=0)
    duration_months: int = Field(gt=0, le=60)


class SimulateLoanResponse(BaseModel):
    principal: int
    duration_months: int
    monthly_installment: int
    total_repayable: int
    interest_rate_applied: Decimal


class CreateLoanRequest(SimulateLoanRequest):
    # Absent du contrat API §2, ajouté parce que DiddiFund est du crowdlending : un prêt est
    # servi par le pool d'une campagne précise, il faut donc savoir laquelle.
    campaign_id: uuid.UUID


class CreateLoanResponse(BaseModel):
    loan_id: uuid.UUID
    status: str


class NextInstallment(BaseModel):
    due_date: date
    amount_due: int
    status: str


class LoanDetail(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    status: str
    principal_amount: int
    currency: str
    duration_months: int
    interest_rate_applied: Decimal
    total_repayable: int
    disbursed_at: datetime | None
    next_installment: NextInstallment | None


class InstallmentItem(BaseModel):
    installment_no: int
    due_date: date
    amount_due: int
    amount_paid: int
    status: str


class ScheduleResponse(BaseModel):
    data: list[InstallmentItem]


class RepayRequest(BaseModel):
    amount: int = Field(gt=0)
    pin: str = Field(min_length=4, max_length=12)


class RepayResponse(BaseModel):
    loan_id: uuid.UUID
    loan_status: str
    installment: InstallmentItem
