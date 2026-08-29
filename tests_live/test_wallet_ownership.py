"""Isolation entre utilisateurs : un utilisateur ne doit jamais voir/agir sur les ressources
d'un autre, même en devinant un identifiant valide."""

from __future__ import annotations

import httpx
import pytest

from tests_live.conftest import assert_error, idem_headers


@pytest.fixture(scope="module")
def user_a_transaction_id(api: httpx.Client, headers_a: dict) -> str:
    resp = api.post(
        "/wallet/deposit",
        headers={**headers_a, **idem_headers()},
        json={"provider": "orange_money", "amount": 1000, "phone": "+2250700000000"},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["transaction_id"]


def test_user_cannot_see_another_users_transaction(
    api: httpx.Client, headers_b: dict, user_a_transaction_id: str
) -> None:
    resp = api.get(f"/wallet/transactions/{user_a_transaction_id}", headers=headers_b)
    # Ne doit pas confirmer l'existence de la transaction d'un tiers (core/use_cases.py :
    # `consulter_transaction`) — 404, jamais 403.
    assert_error(resp, 404, "TRANSACTION_NOT_FOUND")


def test_user_cannot_generate_qr_for_someone_elses_merchant_account(
    api: httpx.Client, headers_a: dict, admin_headers: dict, user_b
) -> None:
    backfill = api.post(
        "/wallet/ops/backfill",
        headers=admin_headers,
        json={"user_id": user_b.user_id, "account_type": "merchant"},
    )
    assert backfill.status_code == 200, backfill.text
    merchant_account_id = backfill.json()["account_id"]

    resp = api.post(
        "/wallet/qr/generate",
        headers=headers_a,
        json={"merchant_account_id": merchant_account_id},
    )
    assert_error(resp, 403, "NOT_MERCHANT_ACCOUNT_OWNER")
