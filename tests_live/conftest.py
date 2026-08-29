"""Suite boîte noire : tape le déploiement staging par HTTP, comme le ferait le frontend.

Ne partage rien avec `tests/` (qui teste `payfund_app` en process, base Postgres dédiée). Se
lance séparément : `pytest tests_live/` — voir `tests_live/README.md`.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

from tests_live.identity_client import LiveUser, get_or_create_user

PAYFUND_BASE_URL = os.environ.get(
    "PAYFUND_BASE_URL", "https://pay-api-staging.diddifree.com/payfund/v1"
).rstrip("/")
REQUEST_TIMEOUT = float(os.environ.get("LIVE_REQUEST_TIMEOUT", "20"))
DEPOSIT_POLL_SECONDS = float(os.environ.get("LIVE_DEPOSIT_POLL_SECONDS", "15"))
ADMIN_ACCESS_TOKEN = os.environ.get("LIVE_ADMIN_ACCESS_TOKEN")


@pytest.fixture(scope="session")
def api() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=PAYFUND_BASE_URL, timeout=REQUEST_TIMEOUT) as client:
        yield client


@pytest.fixture(scope="session")
def user_a() -> LiveUser:
    return get_or_create_user("user_a")


@pytest.fixture(scope="session")
def user_b() -> LiveUser:
    return get_or_create_user("user_b")


def _bearer(user: LiveUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.access_token}"}


@pytest.fixture
def headers_a(user_a: LiveUser) -> dict[str, str]:
    return _bearer(user_a)


@pytest.fixture
def headers_b(user_b: LiveUser) -> dict[str, str]:
    return _bearer(user_b)


@pytest.fixture(scope="session")
def campaign_owned_by_a(api: httpx.Client, user_a: LiveUser) -> dict:
    """One draft campaign, shared read-only across the fund tests that just need *a* campaign
    to point at. Campaigns never leave `draft` through the public API (activation is
    back-office-only), so every test here treats that status as a given, not a setup step."""
    resp = api.post(
        "/fund/campaigns",
        headers=_bearer(user_a),
        json={"title": "Payfund Live Suite Campaign", "goal_amount": 1_000_000},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture(scope="session")
def admin_headers() -> dict[str, str]:
    if not ADMIN_ACCESS_TOKEN:
        pytest.skip("LIVE_ADMIN_ACCESS_TOKEN not set — admin-only route left untested.")
    return {"Authorization": f"Bearer {ADMIN_ACCESS_TOKEN}"}


def idem_headers() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def assert_error(response: httpx.Response, status_code: int, code: str | None = None) -> dict:
    """Asserts the unified envelope from `core/errors.py`: `{"error": {"code", "message", ...}}`."""
    assert response.status_code == status_code, (
        f"expected {status_code}, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "error" in body and "code" in body["error"], body
    if code is not None:
        assert body["error"]["code"] == code, body
    return body["error"]


def try_fund_wallet(api: httpx.Client, headers: dict[str, str], amount: int = 100_000) -> bool:
    """Deposits into the caller's wallet and polls for completion.

    Whether this actually completes depends on staging's `PAYMENT_GATEWAY_MODE` /
    `PAYMENT_GATEWAY_AUTOCONFIRM`, which this suite doesn't control. Returns `False` (never
    raises) when the deposit stays `pending` after the poll window — callers should `pytest.skip`
    the funds-dependent assertion rather than fail the whole run over an environment setting.
    """
    resp = api.post(
        "/wallet/deposit",
        headers={**headers, **idem_headers()},
        json={"provider": "orange_money", "amount": amount, "phone": "+2250700000001"},
    )
    if resp.status_code != 202:
        return False
    transaction_id = resp.json()["transaction_id"]

    deadline = time.monotonic() + DEPOSIT_POLL_SECONDS
    while time.monotonic() < deadline:
        check = api.get(f"/wallet/transactions/{transaction_id}", headers=headers)
        if check.status_code == 200 and check.json()["status"] == "completed":
            return True
        time.sleep(1)
    return False
