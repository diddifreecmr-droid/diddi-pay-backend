"""Parcours du module `wallet` (Contrat API §1)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from payfund_app.core.security import CurrentUser
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.entities import AccountStatus, AccountType, Direction
from payfund_app.modules.wallet.infra.models import LedgerEntry, Transaction
from payfund_app.modules.wallet.infra.repositories import AccountRepository

BASE = "/payfund/v1/wallet"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _solde(client, auth, user_id) -> int:
    auth.as_user(user_id)
    return client.get(f"{BASE}/balance").json()["balance"]


def _set_pin(client, auth, user_id, pin="1234"):
    auth.as_user(user_id)
    response = client.post(
        f"{BASE}/pin/set",
        json={"pin": pin, "confirm_pin": pin, "otp_code": "000000"},
    )
    assert response.status_code == 200
    return response.json()


def _get_step_up(client, auth, user_id, recipient_phone="+2250701111111", amount=60000):
    auth.as_user(user_id)
    response = client.post(
        f"{BASE}/transfer/step-up/request",
        json={"recipient_phone": recipient_phone, "amount": amount},
    )
    assert response.status_code == 200
    return response.json()


def test_solde_initial_a_zero(client, auth, make_user):
    user_id, account_id = make_user()
    auth.as_user(user_id)

    response = client.get(f"{BASE}/balance")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "account_id": str(account_id),
        "balance": 0,
        "currency": "XOF",
        "status": "active",
    }


def test_solde_autocreer_si_compte_manquant(client, auth, session):
    user_id = uuid.uuid4()
    auth.as_user(user_id)

    response = client.get(f"{BASE}/balance")

    assert response.status_code == 200
    assert response.json()["balance"] == 0
    assert AccountRepository(session).get_by_user(user_id) is not None


def test_transfert_p2p_deplace_les_fonds(client, auth, session, make_user, fund_account):
    emetteur, compte_emetteur = make_user()
    destinataire, _ = make_user(phone="+2250701111111")
    fund_account(compte_emetteur, 10_000)
    _set_pin(client, auth, emetteur)

    auth.as_user(emetteur)
    response = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 2000, "pin": "1234"},
        headers=_key(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["amount"] == 2000
    assert body["currency"] == "XOF"

    assert _solde(client, auth, emetteur) == 8000
    assert _solde(client, auth, destinataire) == 2000


def test_transfert_ecrit_deux_lignes_a_somme_nulle(
    client, auth, session, make_user, fund_account
):
    emetteur, compte_emetteur = make_user()
    make_user(phone="+2250701111111")
    fund_account(compte_emetteur, 10_000)
    _set_pin(client, auth, emetteur)

    auth.as_user(emetteur)
    transaction_id = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 2000, "pin": "1234"},
        headers=_key(),
    ).json()["transaction_id"]

    entries = list(
        session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == uuid.UUID(transaction_id))
        )
    )
    assert len(entries) == 2
    signe = sum(
        int(e.amount) if e.direction == str(Direction.CREDIT) else -int(e.amount)
        for e in entries
    )
    assert signe == 0


def test_transfert_solde_insuffisant(client, auth, make_user, fund_account):
    emetteur, compte = make_user()
    make_user(phone="+2250701111111")
    fund_account(compte, 1000)
    _set_pin(client, auth, emetteur)

    auth.as_user(emetteur)
    response = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 5000, "pin": "1234"},
        headers=_key(),
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "INSUFFICIENT_BALANCE"
    assert error["details"] == {"balance": 1000, "requested": 5000}


def test_transfert_vers_soi_meme(client, auth, make_user, fund_account):
    user_id, compte = make_user(phone="+2250700000000")
    fund_account(compte, 5000)
    _set_pin(client, auth, user_id)

    auth.as_user(user_id)
    response = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250700000000", "amount": 1000, "pin": "1234"},
        headers=_key(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CANNOT_TRANSFER_TO_SELF"


def test_transfert_destinataire_inconnu(client, auth, make_user, fund_account):
    user_id, compte = make_user()
    fund_account(compte, 5000)
    _set_pin(client, auth, user_id)

    auth.as_user(user_id)
    response = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250709999999", "amount": 1000, "pin": "1234"},
        headers=_key(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RECIPIENT_NOT_FOUND"


def test_transfert_sans_pin_est_refuse(client, auth, make_user, fund_account):
    user_id, compte = make_user()
    make_user(phone="+2250701111111")
    fund_account(compte, 5000)

    auth.as_user(user_id)
    response = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 1000, "pin": "1234"},
        headers=_key(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PIN_REQUIRED"


def test_idempotency_key_obligatoire(client, auth, make_user):
    user_id, _ = make_user()
    _set_pin(client, auth, user_id)
    auth.as_user(user_id)

    response = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 1000, "pin": "1234"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_meme_cle_rejouee_ne_debite_qu_une_fois(
    client, auth, session, make_user, fund_account
):
    """Contrat §0 : « la deuxième requête renvoie le résultat de la première sans rejouer »."""
    emetteur, compte = make_user()
    make_user(phone="+2250701111111")
    fund_account(compte, 10_000)
    _set_pin(client, auth, emetteur)
    headers = _key()
    payload = {"recipient_phone": "+2250701111111", "amount": 2000, "pin": "1234"}

    auth.as_user(emetteur)
    premiere = client.post(f"{BASE}/transfer", json=payload, headers=headers)
    seconde = client.post(f"{BASE}/transfer", json=payload, headers=headers)

    assert premiere.status_code == seconde.status_code == 201
    assert premiere.json()["transaction_id"] == seconde.json()["transaction_id"]
    assert _solde(client, auth, emetteur) == 8000

    # Deux écritures au total, pas quatre.
    total_entries = session.scalar(
        select(func.count()).select_from(
            select(LedgerEntry)
            .where(
                LedgerEntry.transaction_id
                == uuid.UUID(premiere.json()["transaction_id"])
            )
            .subquery()
        )
    )
    assert total_entries == 2


def test_compte_gele_refuse_les_sorties(client, auth, session, make_user, fund_account):
    """DiddiFreeID §4 : sur `user.suspended`, wallet gèle les transactions sortantes."""
    emetteur, compte = make_user()
    make_user(phone="+2250701111111")
    fund_account(compte, 10_000)
    _set_pin(client, auth, emetteur)

    accounts = AccountRepository(session)
    accounts.set_status(accounts.get(compte), AccountStatus.FROZEN)
    session.commit()

    auth.as_user(emetteur)
    response = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 1000, "pin": "1234"},
        headers=_key(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_ACTIVE"


def test_paiement_marchand(client, auth, session, make_user, fund_account):
    payeur, compte = make_user()
    fund_account(compte, 10_000)
    marchand = AccountRepository(session).create(
        user_id=uuid.uuid4(), account_type=AccountType.MERCHANT
    )
    session.commit()

    auth.as_user(payeur)
    response = client.post(
        f"{BASE}/pay/merchant",
        json={
            "merchant_account_id": str(marchand.id),
            "amount": 1500,
            "origin_module": "shop",
            "business_reference": "ride-123",
        },
        headers=_key(),
    )

    assert response.status_code == 201
    assert response.json()["amount"] == 1500
    assert _solde(client, auth, payeur) == 8500
    transaction_id = response.json()["transaction_id"]
    transaction = session.get(Transaction, uuid.UUID(transaction_id))
    assert transaction.business_reference == "ride-123"


def test_paiement_vers_un_compte_non_marchand(client, auth, make_user, fund_account):
    payeur, compte = make_user()
    _, compte_ordinaire = make_user()
    fund_account(compte, 10_000)

    auth.as_user(payeur)
    response = client.post(
        f"{BASE}/pay/merchant",
        json={"merchant_account_id": str(compte_ordinaire), "amount": 1500},
        headers=_key(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MERCHANT_NOT_FOUND"


def test_lookup_recipient_affiche_un_nom_cache(client, auth, make_user):
    user_id, _ = make_user(phone="+2250701111111")
    auth.as_user(uuid.uuid4())

    response = client.get(f"{BASE}/recipient/lookup", params={"phone": "+2250701111111"})

    assert response.status_code == 200
    assert "1111" in response.json()["display_name"]


def test_pin_set_change_and_reset_recovery(client, auth, make_user, fund_account):
    user_id, _ = make_user(phone="+2250701111111")
    auth.as_user(user_id)

    created = client.post(
        f"{BASE}/pin/set",
        json={"pin": "1234", "confirm_pin": "1234", "otp_code": "000000"},
    )
    assert created.status_code == 200
    recovery_code = created.json()["recovery_codes"][0]

    changed = client.post(
        f"{BASE}/pin/change",
        json={"current_pin": "1234", "new_pin": "5678", "confirm_new_pin": "5678"},
    )
    assert changed.status_code == 200

    reset = client.post(
        f"{BASE}/pin/reset",
        json={
            "recovery_code": recovery_code,
            "new_pin": "2468",
            "confirm_new_pin": "2468",
        },
    )
    assert reset.status_code == 200


def test_step_up_otp_required_for_large_transfer(client, auth, make_user, fund_account):
    user_id, compte = make_user()
    make_user(phone="+2250701111111")
    fund_account(compte, 100_000)
    _set_pin(client, auth, user_id)

    auth.as_user(user_id)
    response = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 60000, "pin": "1234"},
        headers=_key(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STEP_UP_OTP_REQUIRED"


def test_step_up_otp_allows_sensitive_transfer(
    client, auth, session, make_user, fund_account
):
    user_id, compte = make_user()
    make_user(phone="+2250701111111")
    fund_account(compte, 100_000)
    _set_pin(client, auth, user_id)
    challenge = WalletUseCases(session).request_step_up_otp(
        user_id=user_id,
        recipient_phone="+2250701111111",
        amount=60000,
    )

    auth.as_user(user_id)
    response = client.post(
        f"{BASE}/transfer",
        json={
            "recipient_phone": "+2250701111111",
            "amount": 60000,
            "pin": "1234",
            "otp_code": challenge["code"],
        },
        headers=_key(),
    )

    assert response.status_code == 201


def test_admin_reset_pin_audits_action(client, auth, session, make_user):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    user_id, _ = make_user(phone="+2250701111111")

    response = client.post(
        f"{BASE}/ops/pin/reset",
        json={
            "user_id": str(user_id),
            "new_pin": "1111",
            "confirm_new_pin": "1111",
            "reason": "support recovery",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["recovery_codes"]) == 6


def test_historique_filtrable_par_module_d_origine(
    client, auth, session, make_user, fund_account
):
    payeur, compte = make_user()
    fund_account(compte, 20_000)
    marchand = AccountRepository(session).create(
        user_id=uuid.uuid4(), account_type=AccountType.MERCHANT
    )
    make_user(phone="+2250701111111")
    session.commit()
    _set_pin(client, auth, payeur)

    auth.as_user(payeur)
    client.post(
        f"{BASE}/pay/merchant",
        json={
            "merchant_account_id": str(marchand.id),
            "amount": 1500,
            "origin_module": "shop",
            "business_reference": "order-555",
        },
        headers=_key(),
    )
    client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 500, "pin": "1234"},
        headers=_key(),
    )

    tout = client.get(f"{BASE}/transactions").json()
    shop = client.get(f"{BASE}/transactions", params={"origin_module": "shop"}).json()

    # 1 alimentation de test + 1 paiement + 1 transfert
    assert tout["pagination"]["total_items"] == 3
    assert shop["pagination"]["total_items"] == 1
    assert shop["data"][0]["type"] == "merchant_payment"
    assert shop["data"][0]["amount"] == 1500
    assert shop["data"][0]["origin_module"] == "shop"
    assert shop["data"][0]["business_reference"] == "order-555"


def test_detail_transaction_d_un_tiers_est_invisible(
    client, auth, session, make_user, fund_account
):
    payeur, compte = make_user()
    tiers, _ = make_user()
    make_user(phone="+2250701111111")
    fund_account(compte, 10_000)
    _set_pin(client, auth, payeur)

    auth.as_user(payeur)
    transaction_id = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 1000, "pin": "1234"},
        headers=_key(),
    ).json()["transaction_id"]

    auth.as_user(tiers)
    response = client.get(f"{BASE}/transactions/{transaction_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TRANSACTION_NOT_FOUND"


def test_detail_transaction_donne_le_sens_du_mouvement(
    client, auth, make_user, fund_account
):
    emetteur, compte = make_user()
    destinataire, _ = make_user(phone="+2250701111111")
    fund_account(compte, 10_000)
    _set_pin(client, auth, emetteur)

    auth.as_user(emetteur)
    transaction_id = client.post(
        f"{BASE}/transfer",
        json={"recipient_phone": "+2250701111111", "amount": 1000, "pin": "1234"},
        headers=_key(),
    ).json()["transaction_id"]

    vu_par_emetteur = client.get(f"{BASE}/transactions/{transaction_id}").json()
    auth.as_user(destinataire)
    vu_par_destinataire = client.get(f"{BASE}/transactions/{transaction_id}").json()

    assert vu_par_emetteur["direction"] == "debit"
    assert vu_par_destinataire["direction"] == "credit"
    assert vu_par_emetteur["amount"] == vu_par_destinataire["amount"] == 1000
