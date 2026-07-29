"""Conversion de devise — deux transactions reliées par un compte de position de change."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from payfund_app.modules.wallet.application.exchange import (
    FX_POSITION_REFERENCE,
    CurrencyExchange,
)
from payfund_app.modules.wallet.domain.errors import (
    ExchangeRateUnavailable,
    SameCurrencyConversion,
)
from payfund_app.modules.wallet.domain.money import Money
from payfund_app.modules.wallet.infra.exchange_rates import TableExchangeRates
from payfund_app.modules.wallet.infra.models import (
    Account,
    CurrencyConversion,
    LedgerEntry,
)
from payfund_app.modules.wallet.infra.repositories import AccountRepository


@pytest.fixture
def taux(session):
    rates = TableExchangeRates(session)
    rates.poser("XOF", "EUR", Decimal("0.001524"), source="test")
    session.commit()
    return rates


@pytest.fixture
def comptes(session, make_user, fund_account):
    """Un même utilisateur avec un compte XOF approvisionné et un compte EUR vide."""
    user_id, compte_xof = make_user()
    fund_account(compte_xof, 100_000)
    compte_eur = AccountRepository(session).create(user_id=user_id, currency="EUR")
    session.commit()
    return user_id, compte_xof, compte_eur.id


def test_un_utilisateur_peut_avoir_un_compte_par_devise(session, comptes):
    """L'unicité porte désormais sur (user_id, currency), plus sur user_id seul."""
    user_id, _, _ = comptes
    comptes_du_user = list(
        session.scalars(select(Account).where(Account.user_id == user_id))
    )
    assert {c.currency for c in comptes_du_user} == {"XOF", "EUR"}


def test_conversion_deplace_les_fonds_au_taux_cote(session, comptes, taux):
    user_id, compte_xof, compte_eur = comptes
    accounts = AccountRepository(session)

    resultat = CurrencyExchange(session).convertir(
        source_account_id=compte_xof,
        target_account_id=compte_eur,
        montant=Money(10_000, "XOF"),
        idempotency_key=str(uuid.uuid4()),
    )
    session.commit()

    assert resultat.target.amount == 1524  # 15,24 EUR
    assert accounts.balance(compte_xof).amount == 90_000
    assert accounts.balance(compte_eur).amount == 1524


def test_conversion_produit_deux_transactions_chacune_equilibree(session, comptes, taux):
    user_id, compte_xof, compte_eur = comptes

    resultat = CurrencyExchange(session).convertir(
        source_account_id=compte_xof,
        target_account_id=compte_eur,
        montant=Money(10_000, "XOF"),
        idempotency_key=str(uuid.uuid4()),
    )
    session.commit()

    assert resultat.conversion.source_transaction_id != resultat.conversion.target_transaction_id

    for transaction_id, devise_attendue in (
        (resultat.conversion.source_transaction_id, "XOF"),
        (resultat.conversion.target_transaction_id, "EUR"),
    ):
        entries = list(
            session.scalars(
                select(LedgerEntry).where(LedgerEntry.transaction_id == transaction_id)
            )
        )
        assert len(entries) == 2
        assert {e.currency for e in entries} == {devise_attendue}
        assert sum(
            int(e.amount) if e.direction == "credit" else -int(e.amount) for e in entries
        ) == 0


def test_position_de_change_porte_l_exposition(session, comptes, taux):
    user_id, compte_xof, compte_eur = comptes
    accounts = AccountRepository(session)

    CurrencyExchange(session).convertir(
        source_account_id=compte_xof,
        target_account_id=compte_eur,
        montant=Money(10_000, "XOF"),
        idempotency_key=str(uuid.uuid4()),
    )
    session.commit()

    position_xof = accounts.get_by_reference(FX_POSITION_REFERENCE.format(currency="XOF"))
    position_eur = accounts.get_by_reference(FX_POSITION_REFERENCE.format(currency="EUR"))

    # La plateforme a encaissé des XOF et livré des EUR : c'est exactement son exposition.
    assert accounts.balance(position_xof.id).amount == 10_000
    assert accounts.balance(position_eur.id).amount == -1524


def test_taux_fige_sur_la_conversion(session, comptes, taux):
    """Rejouer un historique doit redonner le même résultat, quelles que soient les cotations
    ultérieures."""
    user_id, compte_xof, compte_eur = comptes

    resultat = CurrencyExchange(session).convertir(
        source_account_id=compte_xof,
        target_account_id=compte_eur,
        montant=Money(10_000, "XOF"),
        idempotency_key=str(uuid.uuid4()),
    )
    session.commit()

    taux.poser("XOF", "EUR", Decimal("0.002"), source="test-2")
    session.commit()

    conservee = session.get(CurrencyConversion, resultat.conversion.id)
    assert conservee.rate == Decimal("0.00152400")


def test_sans_cotation_la_conversion_est_refusee(session, comptes):
    user_id, compte_xof, compte_eur = comptes

    with pytest.raises(ExchangeRateUnavailable):
        CurrencyExchange(session).convertir(
            source_account_id=compte_xof,
            target_account_id=compte_eur,
            montant=Money(10_000, "XOF"),
            idempotency_key=str(uuid.uuid4()),
        )


def test_taux_inverse_deduit_de_la_cotation_existante(session, comptes, taux):
    """Coter les deux sens séparément ouvrirait la porte à deux taux incohérents."""
    assert TableExchangeRates(session).taux("EUR", "XOF") == Decimal(1) / Decimal("0.00152400")


def test_conversion_vers_la_meme_devise_refusee(session, make_user, fund_account):
    user_id, compte = make_user()
    autre, compte_autre = make_user()

    with pytest.raises(SameCurrencyConversion):
        CurrencyExchange(session).convertir(
            source_account_id=compte,
            target_account_id=compte_autre,
            montant=Money(1000, "XOF"),
            idempotency_key=str(uuid.uuid4()),
        )


def test_conversion_sans_provision_refusee(session, make_user, taux):
    user_id, compte_xof = make_user()
    compte_eur = AccountRepository(session).create(user_id=user_id, currency="EUR")
    session.commit()

    from payfund_app.modules.wallet.domain.errors import InsufficientBalance

    with pytest.raises(InsufficientBalance):
        CurrencyExchange(session).convertir(
            source_account_id=compte_xof,
            target_account_id=compte_eur.id,
            montant=Money(10_000, "XOF"),
            idempotency_key=str(uuid.uuid4()),
        )


def test_transfert_direct_entre_devises_differentes_refuse(session, comptes, taux):
    """Un transfert ne convertit jamais : il faut passer par `CurrencyExchange`."""
    from payfund_app.modules.wallet.application.ledger import LedgerService
    from payfund_app.modules.wallet.domain.errors import CurrencyMismatch

    user_id, compte_xof, compte_eur = comptes

    with pytest.raises(CurrencyMismatch):
        LedgerService(session).transfer(
            source_account_id=compte_xof,
            destination_account_id=compte_eur,
            montant=Money(10_000, "XOF"),
            type_="p2p_transfer",
            reference="test",
            idempotency_key=str(uuid.uuid4()),
        )
