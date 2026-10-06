"""Accounting projection for Pilotage, derived from immutable financial journals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from payfund_app.modules.payments.application.daily_summary import day_bounds

SERVICES = {"diddigo", "diddisend", "diddifood", "diddifund", "diddifree"}
FLOW_TYPES = {
    "service_payment",
    "wallet_topup",
    "refund",
    "settlement",
    "payout",
    "wallet_deposit",
    "wallet_withdrawal",
    "wallet_transfer",
    "adjustment",
    "unknown",
}


@dataclass(frozen=True, slots=True)
class AccountingSourceRow:
    client_id: str
    processor: str
    business_reference: str
    metadata: dict[str, Any]
    event_type: str
    transactions_count: int
    amount_xof: int


@dataclass(frozen=True, slots=True)
class AccountingEntry:
    service: str
    processor: str
    flow_type: str
    status: str
    transactions_count: int
    gross_amount_xof: int
    refund_amount_xof: int
    processor_fees_xof: int
    net_expected_xof: int
    settled_amount_xof: int
    outstanding_amount_xof: int


@dataclass(frozen=True, slots=True)
class PaymentAccountingSummary:
    date: date
    timezone: str
    currency: str
    is_final: bool
    entries: tuple[AccountingEntry, ...]
    calculated_at: datetime


def normalize_service(client_id: str) -> str:
    value = client_id.strip().lower()
    if value in SERVICES:
        return value
    for service in SERVICES:
        if value.startswith(f"{service}-") or value.startswith(f"{service}_"):
            return service
    return "unknown"


def classify_flow(business_reference: str, metadata: dict[str, Any]) -> str:
    explicit = str(metadata.get("flow_type") or "").strip().lower()
    if explicit in FLOW_TYPES:
        return explicit

    reference = business_reference.strip().lower()
    if "topup" in reference or "top-up" in reference:
        return "wallet_topup"
    if "wallet_deposit" in reference or "wallet-deposit" in reference:
        return "wallet_deposit"
    if "wallet_withdrawal" in reference or "wallet-withdrawal" in reference:
        return "wallet_withdrawal"
    if "wallet_transfer" in reference or "wallet-transfer" in reference:
        return "wallet_transfer"
    return "service_payment"


class PaymentAccountingSummaryUseCases:
    def __init__(self, repository, *, now=lambda: datetime.now(UTC)) -> None:
        self.repository = repository
        self.now = now

    def get(self, day: date) -> PaymentAccountingSummary:
        start, end = day_bounds(day)
        rows = self.repository.rows(start, end)
        grouped: dict[tuple[str, str, str, str], dict[str, int]] = {}

        for row in rows:
            service = normalize_service(row.client_id)
            flow_type = (
                "payout"
                if row.event_type == "payout"
                else classify_flow(row.business_reference, row.metadata)
            )
            key = (service, row.processor or "unknown", flow_type, "succeeded")
            totals = grouped.setdefault(
                key,
                {
                    "transactions_count": 0,
                    "capture": 0,
                    "refund": 0,
                    "processor_fee": 0,
                    "settlement": 0,
                    "payout": 0,
                },
            )
            totals[row.event_type] += row.amount_xof
            if row.event_type in {"capture", "payout"}:
                totals["transactions_count"] += row.transactions_count

        entries = []
        for (service, processor, flow_type, status), totals in sorted(grouped.items()):
            gross = totals["capture"] or totals["payout"]
            refunds = totals["refund"]
            fees = totals["processor_fee"]
            settled = totals["settlement"]
            net_expected = 0 if flow_type == "payout" else gross - refunds - fees
            entries.append(
                AccountingEntry(
                    service=service,
                    processor=processor,
                    flow_type=flow_type,
                    status=status,
                    transactions_count=totals["transactions_count"],
                    gross_amount_xof=gross,
                    refund_amount_xof=refunds,
                    processor_fees_xof=fees,
                    net_expected_xof=net_expected,
                    settled_amount_xof=settled,
                    outstanding_amount_xof=(
                        0 if flow_type == "payout" else net_expected - settled
                    ),
                )
            )

        calculated_at = self.now()
        from zoneinfo import ZoneInfo

        today = calculated_at.astimezone(ZoneInfo("Africa/Abidjan")).date()
        return PaymentAccountingSummary(
            date=day,
            timezone="Africa/Abidjan",
            currency="XOF",
            is_final=day < today,
            entries=tuple(entries),
            calculated_at=calculated_at,
        )
