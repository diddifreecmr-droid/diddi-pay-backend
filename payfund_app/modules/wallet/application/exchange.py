"""Use case `ConvertirDevise` (Architecture §1).

**Pourquoi deux transactions et non une seule.** Le ledger est en double entrée et une
transaction doit sommer à zéro (§2). Une écriture de 10 000 XOF face à une de 15,24 EUR ne somme
pas à zéro — ce ne sont pas les mêmes unités. Assouplir l'invariant reviendrait à renoncer à la
seule vérification qui détecte mécaniquement une incohérence.

La conversion est donc **deux transactions équilibrées**, chacune dans sa devise, reliées par une
ligne de `wallet.currency_conversions` :

    T1 (XOF) : débit compte client XOF   → crédit position de change XOF
    T2 (EUR) : débit position de change EUR → crédit compte client EUR

Les comptes de position de change sont techniques, un par devise, et autorisés à passer en
négatif — comme les comptes suspense d'opérateur. Leur solde est exactement l'exposition au
risque de change de la plateforme, et il absorbe au passage les reliquats d'arrondi.

Pas de route HTTP : aucune n'est spécifiée au contrat API. Comme la validation d'une campagne ou
le décaissement d'un prêt, l'entrée se fera par le canal que le produit retiendra.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from payfund_app.modules.wallet.domain.entities import (
    AccountType,
    TransactionType,
)
from payfund_app.modules.wallet.domain.errors import (
    AccountNotFound,
    ExchangeRateUnavailable,
    InvalidAmountError,
    SameCurrencyConversion,
)
from payfund_app.modules.wallet.domain.money import Money, devise
from payfund_app.modules.wallet.infra.exchange_rates import TableExchangeRates
from payfund_app.modules.wallet.infra.models import CurrencyConversion
from payfund_app.modules.wallet.infra.repositories import AccountRepository
from payfund_app.shared_kernel.contracts.exchange_rate_provider import (
    ForeignExchangeRatePort,
    RateUnavailable,
)

FX_POSITION_REFERENCE = "wallet:fx:position:{currency}"


@dataclass
class ConversionResult:
    conversion: CurrencyConversion
    source: Money
    target: Money
    rate: Decimal


class CurrencyExchange:
    def __init__(
        self, session: Session, rates: ForeignExchangeRatePort | None = None
    ) -> None:
        from payfund_app.modules.wallet.application.ledger import LedgerService

        self.session = session
        self.accounts = AccountRepository(session)
        self.ledger = LedgerService(session)
        self.rates = rates or TableExchangeRates(session)

    def compte_de_position(self, currency: str) -> uuid.UUID:
        """Compte de position de change de la devise, créé à la première conversion."""
        devise(currency)
        reference = FX_POSITION_REFERENCE.format(currency=currency)
        existant = self.accounts.get_by_reference(reference)
        if existant is not None:
            return existant.id
        compte = self.accounts.create(
            user_id=None,
            account_type=AccountType.TECHNICAL,
            currency=currency,
            reference=reference,
            allows_negative_balance=True,
        )
        return compte.id

    def convertir(
        self,
        *,
        source_account_id: uuid.UUID,
        target_account_id: uuid.UUID,
        montant: Money,
        idempotency_key: str,
    ) -> ConversionResult:
        source = self.accounts.get(source_account_id)
        target = self.accounts.get(target_account_id)
        if source is None or target is None:
            raise AccountNotFound()
        if source.currency == target.currency:
            raise SameCurrencyConversion()
        if montant.currency != source.currency:
            raise InvalidAmountError(
                f"Le montant est en {montant.currency}, le compte source en {source.currency}."
            )
        if not montant.is_positive():
            raise InvalidAmountError("Le montant doit être strictement positif.")

        try:
            taux = self.rates.taux(source.currency, target.currency)
        except RateUnavailable as exc:
            raise ExchangeRateUnavailable(
                str(exc), details={"base": source.currency, "quote": target.currency}
            ) from exc

        montant_cible = montant.convertir(target.currency, taux)
        if not montant_cible.is_positive():
            raise InvalidAmountError(
                "Le montant converti est nul : trop petit pour la devise cible."
            )

        position_source = self.compte_de_position(source.currency)
        position_cible = self.compte_de_position(target.currency)

        # Deux transactions, chacune équilibrée dans sa propre devise.
        transaction_source, _ = self.ledger.transfer(
            source_account_id=source.id,
            destination_account_id=position_source,
            montant=montant,
            type_=str(TransactionType.CURRENCY_CONVERSION),
            reference=f"wallet:fx:out:{source.currency}->{target.currency}",
            idempotency_key=f"{idempotency_key}:out",
            origin_module="wallet",
        )
        transaction_cible, _ = self.ledger.transfer(
            source_account_id=position_cible,
            destination_account_id=target.id,
            montant=montant_cible,
            type_=str(TransactionType.CURRENCY_CONVERSION),
            reference=f"wallet:fx:in:{source.currency}->{target.currency}",
            idempotency_key=f"{idempotency_key}:in",
            origin_module="wallet",
        )

        conversion = CurrencyConversion(
            account_id=source.id,
            source_transaction_id=transaction_source.id,
            target_transaction_id=transaction_cible.id,
            source_amount=montant.to_db(),
            source_currency=montant.currency,
            target_amount=montant_cible.to_db(),
            target_currency=montant_cible.currency,
            rate=taux,
        )
        self.session.add(conversion)
        self.session.flush()

        return ConversionResult(conversion, montant, montant_cible, taux)
