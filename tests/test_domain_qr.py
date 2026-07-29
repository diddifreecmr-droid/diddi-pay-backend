"""Signature et vérification du QR code de paiement — logique pure, sans base de données."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from payfund_app.modules.wallet.domain.qr import (
    InvalidQrCode,
    QrCodeExpired,
    QrPayload,
    sign,
    verify,
)

SECRET = "test-secret"
MERCHANT_ID = uuid.uuid4()


def test_signe_puis_verifie_redonne_le_meme_payload():
    payload = QrPayload(merchant_account_id=MERCHANT_ID, nonce="abc")
    token = sign(payload, SECRET)

    decoded = verify(token, SECRET)

    assert decoded.merchant_account_id == MERCHANT_ID
    assert decoded.currency == "XOF"
    assert decoded.amount is None


def test_qr_statique_n_a_pas_de_montant():
    """C'est le cas décrit par le contrat : le payeur saisit le montant."""
    payload = QrPayload(merchant_account_id=MERCHANT_ID)
    token = sign(payload, SECRET)
    assert verify(token, SECRET).amount is None


def test_qr_a_montant_fixe_conserve_le_montant_et_la_devise():
    payload = QrPayload(merchant_account_id=MERCHANT_ID, amount=1500, currency="XOF")
    decoded = verify(sign(payload, SECRET), SECRET)
    assert decoded.amount == 1500
    assert decoded.currency == "XOF"


def test_origin_module_est_conserve():
    payload = QrPayload(merchant_account_id=MERCHANT_ID, origin_module="shop")
    decoded = verify(sign(payload, SECRET), SECRET)
    assert decoded.origin_module == "shop"


def test_signature_falsifiee_est_rejetee():
    """Un QR altéré pour rediriger le paiement vers un autre compte doit être détecté.

    Le corps du jeton est encodé en base64url : on ne peut pas simplement remplacer l'UUID en
    clair dans la chaîne finale, il faut réencoder un corps modifié en conservant la signature
    d'origine, exactement ce que ferait un attaquant qui tenterait de rediriger le paiement.
    """
    from payfund_app.modules.wallet.domain.qr import _b64encode

    token = sign(QrPayload(merchant_account_id=MERCHANT_ID), SECRET)
    _body_b64, signature_b64 = token.split(".", 1)

    corps_falsifie = json.dumps(
        {
            "v": 1,
            "merchant_account_id": str(uuid.uuid4()),
            "currency": "XOF",
            "amount": None,
            "origin_module": None,
            "expires_at": None,
            "nonce": "",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    falsifie = f"{_b64encode(corps_falsifie)}.{signature_b64}"

    with pytest.raises(InvalidQrCode, match="Signature invalide"):
        verify(falsifie, SECRET)


def test_mauvais_secret_est_rejete():
    token = sign(QrPayload(merchant_account_id=MERCHANT_ID), SECRET)
    with pytest.raises(InvalidQrCode):
        verify(token, "un-autre-secret")


def test_format_illisible_est_rejete():
    with pytest.raises(InvalidQrCode):
        verify("ceci-n-est-pas-un-qr-valide", SECRET)


def test_signature_tronquee_est_rejetee():
    token = sign(QrPayload(merchant_account_id=MERCHANT_ID), SECRET)
    body, _signature = token.split(".", 1)
    with pytest.raises(InvalidQrCode):
        verify(f"{body}.abc", SECRET)


def test_qr_expire_est_rejete():
    payload = QrPayload(
        merchant_account_id=MERCHANT_ID,
        amount=1500,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(QrCodeExpired):
        verify(sign(payload, SECRET), SECRET)


def test_qr_non_expire_passe():
    payload = QrPayload(
        merchant_account_id=MERCHANT_ID,
        amount=1500,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    assert verify(sign(payload, SECRET), SECRET).amount == 1500
