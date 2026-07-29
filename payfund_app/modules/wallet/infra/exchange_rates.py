"""Implémentation du `ForeignExchangeRatePort` adossée à `wallet.exchange_rates`."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from payfund_app.modules.wallet.infra.models import ExchangeRate
from payfund_app.shared_kernel.contracts.exchange_rate_provider import RateUnavailable


class TableExchangeRates:
    def __init__(self, session: Session) -> None:
        self.session = session

    def taux(self, base: str, quote: str) -> Decimal:
        if base == quote:
            return Decimal(1)

        direct = self._dernier(base, quote)
        if direct is not None:
            return direct

        # Une paire cotée dans un sens sert dans l'autre : coter XOF/EUR et EUR/XOF séparément
        # ouvrirait la porte à deux taux incohérents.
        inverse = self._dernier(quote, base)
        if inverse is not None:
            return Decimal(1) / inverse

        raise RateUnavailable(f"Aucun taux connu pour {base}/{quote}.")

    def _dernier(self, base: str, quote: str) -> Decimal | None:
        return self.session.scalar(
            select(ExchangeRate.rate)
            .where(
                ExchangeRate.base_currency == base,
                ExchangeRate.quote_currency == quote,
            )
            .order_by(ExchangeRate.valid_from.desc())
            .limit(1)
        )

    def poser(
        self, base: str, quote: str, rate: Decimal, source: str | None = None
    ) -> ExchangeRate:
        """Enregistre une cotation. Les anciennes sont conservées : un taux n'est jamais écrasé,
        pour qu'on puisse toujours expliquer une conversion passée."""
        cotation = ExchangeRate(
            base_currency=base, quote_currency=quote, rate=rate, source=source
        )
        self.session.add(cotation)
        self.session.flush()
        return cotation
