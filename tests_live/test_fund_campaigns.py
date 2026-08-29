"""Module `fund` — campagnes (Contrat API §2).

Une campagne créée par l'API publique reste en `draft` pour toujours : le passage `draft -> active`
est back-office (pas de route HTTP). `POST .../invest` est donc systématiquement testable en
`CAMPAIGN_NOT_ACTIVE`, jamais en succès, depuis cette suite boîte noire.
"""

from __future__ import annotations

import uuid

import httpx

from tests_live.conftest import assert_error, idem_headers

_RANDOM_ID = str(uuid.uuid4())


def test_create_campaign(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/fund/campaigns",
        headers=headers_a,
        json={"title": "Test campaign", "goal_amount": 500_000, "currency": "XOF"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert uuid.UUID(body["campaign_id"])


def test_create_campaign_rejects_empty_title(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post("/fund/campaigns", headers=headers_a, json={"title": "", "goal_amount": 1000})
    assert resp.status_code == 422, resp.text


def test_create_campaign_rejects_non_positive_goal(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post("/fund/campaigns", headers=headers_a, json={"title": "x", "goal_amount": 0})
    assert resp.status_code == 422, resp.text


def test_list_campaigns_shape(api: httpx.Client, headers_a: dict, campaign_owned_by_a: dict) -> None:
    resp = api.get("/fund/campaigns", headers=headers_a, params={"page": 1, "page_size": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body and "pagination" in body
    assert any(c["id"] == campaign_owned_by_a["campaign_id"] for c in body["data"]) or body["pagination"]["total_pages"] > 1


def test_get_campaign_detail(api: httpx.Client, headers_a: dict, campaign_owned_by_a: dict, user_a) -> None:
    resp = api.get(f"/fund/campaigns/{campaign_owned_by_a['campaign_id']}", headers=headers_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["owner_user_id"] == user_a.user_id
    assert body["status"] == "draft"


def test_get_campaign_not_found(api: httpx.Client, headers_a: dict) -> None:
    resp = api.get(f"/fund/campaigns/{_RANDOM_ID}", headers=headers_a)
    assert_error(resp, 404, "CAMPAIGN_NOT_FOUND")


def test_invest_in_unknown_campaign(api: httpx.Client, headers_b: dict) -> None:
    resp = api.post(
        f"/fund/campaigns/{_RANDOM_ID}/invest",
        headers={**headers_b, **idem_headers()},
        json={"amount": 1000},
    )
    assert_error(resp, 404, "CAMPAIGN_NOT_FOUND")


def test_invest_rejects_non_positive_amount(
    api: httpx.Client, headers_b: dict, campaign_owned_by_a: dict
) -> None:
    resp = api.post(
        f"/fund/campaigns/{campaign_owned_by_a['campaign_id']}/invest",
        headers={**headers_b, **idem_headers()},
        json={"amount": 0},
    )
    assert resp.status_code == 422, resp.text


def test_invest_requires_idempotency_key(
    api: httpx.Client, headers_b: dict, campaign_owned_by_a: dict
) -> None:
    resp = api.post(
        f"/fund/campaigns/{campaign_owned_by_a['campaign_id']}/invest",
        headers=headers_b,
        json={"amount": 1000},
    )
    assert_error(resp, 400, "IDEMPOTENCY_KEY_REQUIRED")


def test_invest_in_draft_campaign_is_rejected(
    api: httpx.Client, headers_b: dict, campaign_owned_by_a: dict
) -> None:
    resp = api.post(
        f"/fund/campaigns/{campaign_owned_by_a['campaign_id']}/invest",
        headers={**headers_b, **idem_headers()},
        json={"amount": 1000},
    )
    assert_error(resp, 409, "CAMPAIGN_NOT_ACTIVE")
