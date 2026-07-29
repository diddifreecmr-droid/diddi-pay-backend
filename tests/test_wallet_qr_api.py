"""QR code de paiement marchand — génération et vérification (Contrat API §1, §3)."""

from __future__ import annotations

import uuid

from payfund_app.modules.wallet.domain.entities import AccountStatus, AccountType
from payfund_app.modules.wallet.infra.repositories import AccountRepository

BASE = "/payfund/v1/wallet"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _marchand(session, owner_user_id):
    marchand = AccountRepository(session).create(
        user_id=owner_user_id, account_type=AccountType.MERCHANT
    )
    session.commit()
    return marchand.id


# --- Génération ---------------------------------------------------------------


def test_le_marchand_genere_son_propre_qr_statique(client, auth, session, make_user):
    proprietaire, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    response = client.post(
        f"{BASE}/qr/generate", json={"merchant_account_id": str(marchand_id)}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "static"
    assert body["merchant_account_id"] == str(marchand_id)
    assert body["amount"] is None
    assert body["currency"] == "XOF"
    assert body["expires_at"] is None
    assert body["payload"]


def test_qr_a_montant_fixe(client, auth, session, make_user):
    proprietaire, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    response = client.post(
        f"{BASE}/qr/generate",
        json={
            "merchant_account_id": str(marchand_id),
            "amount": 1500,
            "origin_module": "shop",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "dynamic"
    assert body["amount"] == 1500
    assert body["origin_module"] == "shop"


def test_qr_a_montant_fixe_avec_expiration(client, auth, session, make_user):
    proprietaire, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    response = client.post(
        f"{BASE}/qr/generate",
        json={
            "merchant_account_id": str(marchand_id),
            "amount": 1500,
            "expires_in_seconds": 300,
        },
    )

    assert response.status_code == 201
    assert response.json()["expires_at"] is not None


def test_expiration_sans_montant_refusee(client, auth, session, make_user):
    """Un QR statique (montant saisi par le payeur) n'a pas de raison d'expirer."""
    proprietaire, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    response = client.post(
        f"{BASE}/qr/generate",
        json={"merchant_account_id": str(marchand_id), "expires_in_seconds": 300},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QR_CODE"


def test_seul_le_proprietaire_peut_generer_le_qr(client, auth, session, make_user):
    proprietaire, _ = make_user()
    tiers, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(tiers)
    response = client.post(
        f"{BASE}/qr/generate", json={"merchant_account_id": str(marchand_id)}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_MERCHANT_ACCOUNT_OWNER"


def test_generation_sur_compte_non_marchand_refusee(client, auth, make_user):
    proprietaire, compte_ordinaire = make_user()

    auth.as_user(proprietaire)
    response = client.post(
        f"{BASE}/qr/generate", json={"merchant_account_id": str(compte_ordinaire)}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MERCHANT_NOT_FOUND"


def test_generation_sur_compte_marchand_gele_refusee(client, auth, session, make_user):
    proprietaire, _ = make_user()
    marchand_id = _marchand(session, proprietaire)
    accounts = AccountRepository(session)
    accounts.set_status(accounts.get(marchand_id), AccountStatus.FROZEN)
    session.commit()

    auth.as_user(proprietaire)
    response = client.post(
        f"{BASE}/qr/generate", json={"merchant_account_id": str(marchand_id)}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_ACTIVE"


def test_generation_ne_deplace_aucun_fonds_pas_de_cle_d_idempotence(
    client, auth, session, make_user
):
    """Contrairement au paiement, générer un QR n'exige pas `Idempotency-Key`."""
    proprietaire, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    response = client.post(
        f"{BASE}/qr/generate", json={"merchant_account_id": str(marchand_id)}
    )
    assert response.status_code == 201


# --- Vérification --------------------------------------------------------------


def test_verification_decode_le_qr_genere(client, auth, session, make_user):
    proprietaire, _ = make_user()
    payeur, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    token = client.post(
        f"{BASE}/qr/generate",
        json={"merchant_account_id": str(marchand_id), "amount": 1500},
    ).json()["payload"]

    auth.as_user(payeur)
    response = client.post(f"{BASE}/qr/verify", json={"payload": token})

    assert response.status_code == 200
    body = response.json()
    assert body["merchant_account_id"] == str(marchand_id)
    assert body["amount"] == 1500


def test_verification_d_un_payload_altere_refusee(client, auth, session, make_user):
    proprietaire, _ = make_user()
    payeur, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    token = client.post(
        f"{BASE}/qr/generate", json={"merchant_account_id": str(marchand_id)}
    ).json()["payload"]

    auth.as_user(payeur)
    response = client.post(f"{BASE}/qr/verify", json={"payload": token[:-2] + "xx"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QR_CODE"


def test_verification_d_un_qr_gele_apres_generation(client, auth, session, make_user):
    """Un compte peut être gelé après l'impression du QR : la vérification doit le refléter."""
    proprietaire, _ = make_user()
    payeur, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    token = client.post(
        f"{BASE}/qr/generate", json={"merchant_account_id": str(marchand_id)}
    ).json()["payload"]

    accounts = AccountRepository(session)
    accounts.set_status(accounts.get(marchand_id), AccountStatus.FROZEN)
    session.commit()

    auth.as_user(payeur)
    response = client.post(f"{BASE}/qr/verify", json={"payload": token})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_ACTIVE"


def test_verification_d_un_qr_expire(client, auth, session, make_user):
    proprietaire, _ = make_user()
    payeur, _ = make_user()
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    token = client.post(
        f"{BASE}/qr/generate",
        json={
            "merchant_account_id": str(marchand_id),
            "amount": 1500,
            "expires_in_seconds": 1,
        },
    ).json()["payload"]

    # On force l'expiration en resignant un payload déjà passé, plutôt que d'attendre 1s réel.
    from datetime import datetime, timedelta, timezone

    from payfund_app.core.config import get_settings
    from payfund_app.modules.wallet.domain.qr import QrPayload, sign

    perime = sign(
        QrPayload(
            merchant_account_id=marchand_id,
            amount=1500,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ),
        get_settings().qr_signing_secret,
    )

    auth.as_user(payeur)
    response = client.post(f"{BASE}/qr/verify", json={"payload": perime})

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "QR_CODE_EXPIRED"


# --- Parcours complet : générer → vérifier → payer -----------------------------


def test_parcours_complet_qr_puis_paiement(
    client, auth, session, make_user, fund_account
):
    proprietaire, _ = make_user()
    payeur, compte_payeur = make_user()
    fund_account(compte_payeur, 10_000)
    marchand_id = _marchand(session, proprietaire)

    auth.as_user(proprietaire)
    token = client.post(
        f"{BASE}/qr/generate",
        json={"merchant_account_id": str(marchand_id), "origin_module": "shop"},
    ).json()["payload"]

    auth.as_user(payeur)
    decoded = client.post(f"{BASE}/qr/verify", json={"payload": token}).json()

    paiement = client.post(
        f"{BASE}/pay/merchant",
        json={
            "merchant_account_id": decoded["merchant_account_id"],
            "amount": 1500,
            "origin_module": decoded["origin_module"],
        },
        headers=_key(),
    )

    assert paiement.status_code == 201
    assert paiement.json()["amount"] == 1500
    assert client.get(f"{BASE}/balance").json()["balance"] == 8500
