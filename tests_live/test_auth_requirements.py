"""Toutes les routes hors `/health` exigent un `access_token` DiddiFreeID valide.

Un couple (méthode, chemin, corps) par route protégée, avec un corps par ailleurs valide : on
veut isoler l'échec sur l'authentification, pas sur la validation des champs.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests_live.conftest import assert_error

_RANDOM_ID = str(uuid.uuid4())

PROTECTED_ROUTES: list[tuple[str, str, dict | None]] = [
    ("GET", "/wallet/balance", None),
    ("POST", "/wallet/deposit", {"provider": "orange_money", "amount": 1000, "phone": "+2250700000000"}),
    ("POST", "/wallet/withdraw", {"provider": "orange_money", "amount": 1000, "phone": "+2250700000000"}),
    ("POST", "/wallet/transfer", {"recipient_phone": "+2250700000000", "amount": 1000}),
    ("POST", "/wallet/pay/merchant", {"merchant_account_id": _RANDOM_ID, "amount": 1000}),
    ("POST", "/wallet/qr/generate", {"merchant_account_id": _RANDOM_ID}),
    ("POST", "/wallet/qr/verify", {"payload": "irrelevant"}),
    ("GET", "/wallet/transactions", None),
    ("GET", f"/wallet/transactions/{_RANDOM_ID}", None),
    ("POST", "/fund/campaigns", {"title": "x", "goal_amount": 1000}),
    ("GET", "/fund/campaigns", None),
    ("GET", f"/fund/campaigns/{_RANDOM_ID}", None),
    ("POST", f"/fund/campaigns/{_RANDOM_ID}/invest", {"amount": 1000}),
    ("POST", "/fund/loans/simulate", {"amount": 1000, "duration_months": 6}),
    ("POST", "/fund/loans", {"amount": 1000, "duration_months": 6, "campaign_id": _RANDOM_ID}),
    ("GET", f"/fund/loans/{_RANDOM_ID}", None),
    ("GET", f"/fund/loans/{_RANDOM_ID}/schedule", None),
    ("POST", f"/fund/loans/{_RANDOM_ID}/repay", {"amount": 1000}),
]


def _route_id(route: tuple[str, str, dict | None]) -> str:
    method, path, _ = route
    return f"{method} {path}"


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=_route_id)
def test_missing_token_is_rejected(api: httpx.Client, route: tuple[str, str, dict | None]) -> None:
    method, path, body = route
    resp = api.request(method, path, json=body)
    assert_error(resp, 401)


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=_route_id)
def test_garbage_token_is_rejected(api: httpx.Client, route: tuple[str, str, dict | None]) -> None:
    method, path, body = route
    resp = api.request(method, path, json=body, headers={"Authorization": "Bearer not-a-real-jwt"})
    assert_error(resp, 401)
