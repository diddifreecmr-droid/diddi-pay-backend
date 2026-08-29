"""Module `wallet` (Contrat API §1) — boîte noire contre le déploiement staging.

Les cas « insuffisance de solde » utilisent un montant délibérément énorme (`_HUGE_AMOUNT`)
plutôt qu'un solde nul supposé : un run précédent peut avoir laissé le compte de test partiellement
approvisionné (le dépôt réel dépend de `PAYMENT_GATEWAY_MODE` côté staging, hors de portée de cette
suite), et aucun montant réaliste ne peut jamais le dépasser.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests_live.conftest import assert_error, idem_headers, try_fund_wallet

_HUGE_AMOUNT = 10**12
_RANDOM_ID = str(uuid.uuid4())


# --- Solde -------------------------------------------------------------------


def test_balance_shape(api: httpx.Client, headers_a: dict) -> None:
    resp = api.get("/wallet/balance", headers=headers_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"account_id", "balance", "currency", "status"} <= body.keys()
    assert body["currency"] == "XOF"
    assert body["balance"] >= 0


# --- Dépôt ---------------------------------------------------------------


def test_deposit_rejects_unknown_provider(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/deposit",
        headers={**headers_a, **idem_headers()},
        json={"provider": "bitcoin", "amount": 1000, "phone": "+2250700000000"},
    )
    assert resp.status_code == 422, resp.text


def test_deposit_rejects_non_positive_amount(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/deposit",
        headers={**headers_a, **idem_headers()},
        json={"provider": "orange_money", "amount": 0, "phone": "+2250700000000"},
    )
    assert resp.status_code == 422, resp.text


def test_deposit_requires_idempotency_key(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/deposit",
        headers=headers_a,
        json={"provider": "orange_money", "amount": 1000, "phone": "+2250700000000"},
    )
    assert_error(resp, 400, "IDEMPOTENCY_KEY_REQUIRED")


def test_deposit_accepted(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/deposit",
        headers={**headers_a, **idem_headers()},
        json={"provider": "orange_money", "amount": 1000, "phone": "+2250700000000"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] in {"pending", "completed"}

    check = api.get(f"/wallet/transactions/{body['transaction_id']}", headers=headers_a)
    assert check.status_code == 200, check.text


# --- Retrait ---------------------------------------------------------------


def test_withdraw_rejects_non_positive_amount(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/withdraw",
        headers={**headers_a, **idem_headers()},
        json={"provider": "orange_money", "amount": -5, "phone": "+2250700000000"},
    )
    assert resp.status_code == 422, resp.text


def test_withdraw_insufficient_balance(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/withdraw",
        headers={**headers_a, **idem_headers()},
        json={"provider": "orange_money", "amount": _HUGE_AMOUNT, "phone": "+2250700000000"},
    )
    assert_error(resp, 409, "INSUFFICIENT_BALANCE")


# --- Transfert P2P -----------------------------------------------------------


def test_transfer_unknown_recipient(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/transfer",
        headers={**headers_a, **idem_headers()},
        json={"recipient_phone": "+2250799999999", "amount": 1000},
    )
    assert_error(resp, 404, "RECIPIENT_NOT_FOUND")


def test_transfer_to_self_is_rejected(api: httpx.Client, headers_a: dict, user_a) -> None:
    resp = api.post(
        "/wallet/transfer",
        headers={**headers_a, **idem_headers()},
        json={"recipient_phone": user_a.phone, "amount": 1000},
    )
    assert_error(resp, 422, "CANNOT_TRANSFER_TO_SELF")


def test_transfer_insufficient_balance(api: httpx.Client, headers_a: dict, user_b) -> None:
    resp = api.post(
        "/wallet/transfer",
        headers={**headers_a, **idem_headers()},
        json={"recipient_phone": user_b.phone, "amount": _HUGE_AMOUNT},
    )
    assert_error(resp, 409, "INSUFFICIENT_BALANCE")


def test_transfer_succeeds_when_funded(api: httpx.Client, headers_a: dict, user_b) -> None:
    if not try_fund_wallet(api, headers_a, amount=5_000):
        pytest.skip("Deposit stayed pending on staging — gateway not auto-confirming; skipping funded path.")
    resp = api.post(
        "/wallet/transfer",
        headers={**headers_a, **idem_headers()},
        json={"recipient_phone": user_b.phone, "amount": 1_000},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["amount"] == 1_000
    assert body["status"] == "completed"


# --- Paiement marchand / QR (sans compte marchand admin-provisionné) --------


def test_pay_merchant_unknown_merchant(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/pay/merchant",
        headers={**headers_a, **idem_headers()},
        json={"merchant_account_id": _RANDOM_ID, "amount": 1000},
    )
    assert_error(resp, 404, "MERCHANT_NOT_FOUND")


def test_qr_generate_unknown_merchant(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/wallet/qr/generate",
        headers=headers_a,
        json={"merchant_account_id": _RANDOM_ID},
    )
    assert_error(resp, 404, "MERCHANT_NOT_FOUND")


def test_qr_verify_rejects_invalid_payload(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post("/wallet/qr/verify", headers=headers_a, json={"payload": "not-a-valid-qr-token"})
    assert_error(resp, 422, "INVALID_QR_CODE")


# --- Marchand provisionné (admin uniquement) --------------------------------


@pytest.fixture(scope="module")
def merchant_account_id_b(api: httpx.Client, admin_headers: dict, user_b) -> str:
    resp = api.post(
        "/wallet/ops/backfill",
        headers=admin_headers,
        json={"user_id": user_b.user_id, "account_type": "merchant"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["account_id"]


def test_qr_generate_verify_and_pay_merchant_roundtrip(
    api: httpx.Client, headers_a: dict, headers_b: dict, merchant_account_id_b: str
) -> None:
    generate = api.post(
        "/wallet/qr/generate",
        headers=headers_b,
        json={"merchant_account_id": merchant_account_id_b, "amount": 500},
    )
    assert generate.status_code == 201, generate.text
    qr = generate.json()
    assert qr["type"] == "dynamic"
    assert qr["amount"] == 500

    verify = api.post("/wallet/qr/verify", headers=headers_a, json={"payload": qr["payload"]})
    assert verify.status_code == 200, verify.text
    assert verify.json()["merchant_account_id"] == merchant_account_id_b

    if not try_fund_wallet(api, headers_a, amount=5_000):
        pytest.skip("Deposit stayed pending on staging — cannot exercise the funded payment path.")
    pay = api.post(
        "/wallet/pay/merchant",
        headers={**headers_a, **idem_headers()},
        json={"merchant_account_id": merchant_account_id_b, "amount": 500},
    )
    assert pay.status_code == 201, pay.text
    assert pay.json()["status"] == "completed"


# --- Historique --------------------------------------------------------------


def test_transactions_list_shape(api: httpx.Client, headers_a: dict) -> None:
    resp = api.get("/wallet/transactions", headers=headers_a, params={"page": 1, "page_size": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body and "pagination" in body
    assert body["pagination"]["page"] == 1


def test_transaction_detail_not_found(api: httpx.Client, headers_a: dict) -> None:
    resp = api.get(f"/wallet/transactions/{_RANDOM_ID}", headers=headers_a)
    assert_error(resp, 404, "TRANSACTION_NOT_FOUND")
