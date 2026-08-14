"""Dépôt et retrait — instant de passage des écritures (voir en-tête de wallet/application).

Rappel de la règle retenue : dépôt écrit **à la confirmation**, retrait écrit **à l'initiation**.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.entities import Direction, TransactionStatus
from payfund_app.modules.wallet.infra.models import LedgerEntry
from payfund_app.modules.wallet.infra.repositories import (
    AccountRepository,
    GatewayAccountRepository,
)

BASE = "/payfund/v1/wallet"
PROVIDER = "orange_money"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _entries(session, transaction_id: str) -> list[LedgerEntry]:
    return list(
        session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == uuid.UUID(transaction_id))
        )
    )


def _confirmer(session, transaction_id: str):
    return WalletUseCases(session).confirmer_operation(
        uuid.UUID(transaction_id), provider=PROVIDER
    )


def _echouer(session, transaction_id: str):
    return WalletUseCases(session).echouer_operation(
        uuid.UUID(transaction_id), provider=PROVIDER
    )


# --- Dépôt -------------------------------------------------------------------


def test_depot_renvoie_202_sans_rien_ecrire(client, auth, session, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)

    response = client.post(
        f"{BASE}/deposit",
        json={"provider": PROVIDER, "amount": 5000, "phone": "+2250700000000"},
        headers=_key(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"

    # L'argent n'existe pas tant que l'opérateur n'a pas confirmé.
    assert _entries(session, body["transaction_id"]) == []
    assert client.get(f"{BASE}/balance").json()["balance"] == 0


def test_depot_confirme_credite_le_client_et_debite_le_suspense(
    client, auth, session, make_user
):
    user_id, account_id = make_user()
    auth.as_user(user_id)
    transaction_id = client.post(
        f"{BASE}/deposit",
        json={"provider": PROVIDER, "amount": 5000, "phone": "+2250700000000"},
        headers=_key(),
    ).json()["transaction_id"]

    _confirmer(session, transaction_id)
    session.commit()

    assert client.get(f"{BASE}/balance").json()["balance"] == 5000

    entries = _entries(session, transaction_id)
    assert len(entries) == 2
    assert sum(
        int(e.amount) if e.direction == str(Direction.CREDIT) else -int(e.amount)
        for e in entries
    ) == 0

    # Le compte suspense part à découvert jusqu'à réconciliation (§2).
    suspense_id = GatewayAccountRepository(session).account_id_for(PROVIDER)
    assert AccountRepository(session).balance(suspense_id).amount == -5000


def test_depot_echoue_reste_sans_ecriture(client, auth, session, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)
    transaction_id = client.post(
        f"{BASE}/deposit",
        json={"provider": PROVIDER, "amount": 5000, "phone": "+2250700000000"},
        headers=_key(),
    ).json()["transaction_id"]

    transaction = _echouer(session, transaction_id)
    session.commit()

    assert transaction.status == str(TransactionStatus.FAILED)
    assert _entries(session, transaction_id) == []
    assert client.get(f"{BASE}/balance").json()["balance"] == 0


def test_depot_en_attente_est_visible_dans_l_historique(client, auth, make_user):
    """Sans en-tête de transaction porteur du montant, l'opération serait invisible."""
    user_id, _ = make_user()
    auth.as_user(user_id)
    client.post(
        f"{BASE}/deposit",
        json={"provider": PROVIDER, "amount": 5000, "phone": "+2250700000000"},
        headers=_key(),
    )

    historique = client.get(f"{BASE}/transactions").json()

    assert historique["pagination"]["total_items"] == 1
    ligne = historique["data"][0]
    assert ligne["type"] == "deposit"
    assert ligne["status"] == "pending"
    assert ligne["amount"] == 5000


def test_polling_du_detail_apres_depot(client, auth, session, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)
    transaction_id = client.post(
        f"{BASE}/deposit",
        json={"provider": PROVIDER, "amount": 5000, "phone": "+2250700000000"},
        headers=_key(),
    ).json()["transaction_id"]

    avant = client.get(f"{BASE}/transactions/{transaction_id}").json()
    assert avant["status"] == "pending"
    assert avant["direction"] is None

    _confirmer(session, transaction_id)
    session.commit()

    apres = client.get(f"{BASE}/transactions/{transaction_id}").json()
    assert apres["status"] == "completed"
    assert apres["direction"] == "credit"
    assert apres["amount"] == 5000


def test_operateur_inconnu_refuse(client, auth, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)

    response = client.post(
        f"{BASE}/deposit",
        json={"provider": "paypal", "amount": 5000, "phone": "+2250700000000"},
        headers=_key(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_depot_rejoue_ne_cree_qu_une_transaction(client, auth, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)
    headers = _key()
    payload = {"provider": PROVIDER, "amount": 5000, "phone": "+2250700000000"}

    premiere = client.post(f"{BASE}/deposit", json=payload, headers=headers)
    seconde = client.post(f"{BASE}/deposit", json=payload, headers=headers)

    assert premiere.json()["transaction_id"] == seconde.json()["transaction_id"]
    assert client.get(f"{BASE}/transactions").json()["pagination"]["total_items"] == 1


# --- Retrait -----------------------------------------------------------------


def test_retrait_sans_pin_est_refuse_par_le_contrat(client, auth, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)

    response = client.post(
        f"{BASE}/withdraw",
        json={"provider": PROVIDER, "amount": 3000, "phone": "+2250700000000"},
        headers=_key(),
    )

    assert response.status_code == 422


def test_retrait_reserve_les_fonds_des_l_initiation(
    client, auth, session, make_user, fund_account, set_pin
):
    user_id, account_id = make_user()
    set_pin(user_id)
    fund_account(account_id, 10_000)
    auth.as_user(user_id)

    response = client.post(
        f"{BASE}/withdraw",
        json={
            "provider": PROVIDER,
            "amount": 3000,
            "phone": "+2250700000000",
            "pin": "1234",
        },
        headers=_key(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"

    # Les fonds ont déjà quitté le compte : ils ne peuvent plus être dépensés ailleurs.
    assert client.get(f"{BASE}/balance").json()["balance"] == 7000
    assert len(_entries(session, body["transaction_id"])) == 2


def test_retrait_au_dela_du_solde(client, auth, make_user, fund_account, set_pin):
    user_id, account_id = make_user()
    set_pin(user_id)
    fund_account(account_id, 1000)
    auth.as_user(user_id)

    response = client.post(
        f"{BASE}/withdraw",
        json={
            "provider": PROVIDER,
            "amount": 5000,
            "phone": "+2250700000000",
            "pin": "1234",
        },
        headers=_key(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INSUFFICIENT_BALANCE"


def test_deux_retraits_ne_peuvent_pas_depasser_le_solde(
    client, auth, make_user, fund_account, set_pin
):
    """C'est précisément ce que l'écriture à l'initiation protège."""
    user_id, account_id = make_user()
    set_pin(user_id)
    fund_account(account_id, 10_000)
    auth.as_user(user_id)
    payload = {
        "provider": PROVIDER,
        "amount": 6000,
        "phone": "+2250700000000",
        "pin": "1234",
    }

    premier = client.post(f"{BASE}/withdraw", json=payload, headers=_key())
    second = client.post(f"{BASE}/withdraw", json=payload, headers=_key())

    assert premier.status_code == 202
    assert second.status_code == 409
    assert client.get(f"{BASE}/balance").json()["balance"] == 4000


def test_retrait_echoue_est_contre_passe(
    client, auth, session, make_user, fund_account, set_pin
):
    """§2 : une correction se fait par écriture inverse, jamais par UPDATE/DELETE."""
    user_id, account_id = make_user()
    set_pin(user_id)
    fund_account(account_id, 10_000)
    auth.as_user(user_id)
    transaction_id = client.post(
        f"{BASE}/withdraw",
        json={
            "provider": PROVIDER,
            "amount": 3000,
            "phone": "+2250700000000",
            "pin": "1234",
        },
        headers=_key(),
    ).json()["transaction_id"]

    transaction = _echouer(session, transaction_id)
    session.commit()

    assert transaction.status == str(TransactionStatus.REVERSED)
    # Les écritures d'origine sont intactes...
    assert len(_entries(session, transaction_id)) == 2
    # ...et le client a récupéré ses fonds.
    assert client.get(f"{BASE}/balance").json()["balance"] == 10_000


def test_retrait_confirme_cloture_sans_nouvelle_ecriture(
    client, auth, session, make_user, fund_account, set_pin
):
    user_id, account_id = make_user()
    set_pin(user_id)
    fund_account(account_id, 10_000)
    auth.as_user(user_id)
    transaction_id = client.post(
        f"{BASE}/withdraw",
        json={
            "provider": PROVIDER,
            "amount": 3000,
            "phone": "+2250700000000",
            "pin": "1234",
        },
        headers=_key(),
    ).json()["transaction_id"]

    transaction = _confirmer(session, transaction_id)
    session.commit()

    assert transaction.status == str(TransactionStatus.COMPLETED)
    assert len(_entries(session, transaction_id)) == 2
    assert client.get(f"{BASE}/balance").json()["balance"] == 7000
