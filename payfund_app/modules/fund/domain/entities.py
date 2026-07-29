"""Entités du domaine `fund`."""

from enum import StrEnum


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class LoanStatus(StrEnum):
    PENDING = "pending"
    DISBURSED = "disbursed"
    REPAYING = "repaying"
    CLOSED = "closed"
    DEFAULTED = "defaulted"


class InstallmentStatus(StrEnum):
    DUE = "due"
    PAID = "paid"
    LATE = "late"
    DEFAULTED = "defaulted"
