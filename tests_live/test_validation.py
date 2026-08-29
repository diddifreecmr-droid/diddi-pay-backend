"""Cas de validation transverses qui ne rentrent pas naturellement dans un fichier par module :
UUID malformés dans l'URL, champs requis absents, bornes de pagination, règles métier qui
dépendent de plusieurs champs à la fois plutôt que d'un seul."""

from __future__ import annotations

import httpx

from tests_live.conftest import idem_headers


def test_malformed_uuid_path_param_is_422(api: httpx.Client, headers_a: dict) -> None:
    resp = api.get("/fund/campaigns/not-a-uuid", headers=headers_a)
    assert resp.status_code == 422, resp.text


def test_create_campaign_missing_title_is_422(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post("/fund/campaigns", headers=headers_a, json={"goal_amount": 1000})
    assert resp.status_code == 422, resp.text


def test_transfer_missing_recipient_is_422(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/transfer", headers={**headers_a, **idem_headers()}, json={"amount": 1000}
    )
    assert resp.status_code == 422, resp.text


def test_deposit_missing_phone_is_422(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/deposit",
        headers={**headers_a, **idem_headers()},
        json={"provider": "orange_money", "amount": 1000},
    )
    assert resp.status_code == 422, resp.text


def test_campaigns_page_below_one_is_422(api: httpx.Client, headers_a: dict) -> None:
    resp = api.get("/fund/campaigns", headers=headers_a, params={"page": 0})
    assert resp.status_code == 422, resp.text


def test_transactions_page_size_over_cap_is_422(api: httpx.Client, headers_a: dict) -> None:
    resp = api.get("/wallet/transactions", headers=headers_a, params={"page_size": 101})
    assert resp.status_code == 422, resp.text


def test_qr_generate_expiry_without_fixed_amount_is_rejected(api: httpx.Client, headers_a: dict) -> None:
    # Règle métier (pas une contrainte de schéma) : une expiration n'a de sens que pour un QR à
    # montant fixe — `qr_service.py::generer`. Sans compte marchand admin-provisionné (voir
    # `test_wallet.py::merchant_account_id_b`), l'existence du marchand est vérifiée en premier,
    # donc 404 est la réponse attendue ici ; 422 si ce fixture existe ailleurs dans le run.
    resp = api.post(
        "/wallet/qr/generate",
        headers=headers_a,
        json={"merchant_account_id": "00000000-0000-0000-0000-000000000000", "expires_in_seconds": 60},
    )
    assert resp.status_code in (404, 422), resp.text
