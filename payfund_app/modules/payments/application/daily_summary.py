"""Daily confirmed payment totals; the financial journal is the event clock."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Africa/Abidjan")


@dataclass(frozen=True)
class PaymentDailySummary:
    date: date
    timezone: str
    currency: str
    confirmed_payments_count: int
    confirmed_payments_amount_xof: int
    confirmed_refunds_count: int
    confirmed_refunds_amount_xof: int
    calculated_at: datetime
    source: str = "payments.financial_journals"


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, BUSINESS_TZ)
    end = datetime.combine(day + timedelta(days=1), time.min, BUSINESS_TZ)
    return start.astimezone(UTC), end.astimezone(UTC)


class PaymentDailySummaryUseCases:
    def __init__(self, repository) -> None:
        self.repository = repository

    def get(self, day: date) -> PaymentDailySummary:
        start, end = day_bounds(day)
        totals = self.repository.totals(start, end)
        return PaymentDailySummary(
            date=day,
            timezone="Africa/Abidjan",
            currency="XOF",
            confirmed_payments_count=totals.get("capture", (0, 0))[0],
            confirmed_payments_amount_xof=totals.get("capture", (0, 0))[1],
            confirmed_refunds_count=totals.get("refund", (0, 0))[0],
            confirmed_refunds_amount_xof=totals.get("refund", (0, 0))[1],
            calculated_at=datetime.now(UTC),
        )
