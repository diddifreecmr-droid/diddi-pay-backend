"""Module `fund` — prêts (Contrat API §2, crowdlending).

`demander()` ne vérifie ni le statut de la campagne ni un quelconque financement du pool — seule
l'appartenance de la campagne à l'emprunteur compte. Le décaissement (`pending -> disbursed`) est
back-office, sans route HTTP : un prêt créé par cette suite reste `pending` pour toujours, ce qui
rend `repay` systématiquement `LOAN_NOT_DISBURSED` — déterministe, pas un compromis du test.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests_live.conftest import assert_error, idem_headers

_RANDOM_ID = str(uuid.uuid4())


def test_simulate_loan(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/fund/loans/simulate", headers=headers_a, json={"amount": 100_000, "duration_months": 12}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["principal"] == 100_000
    assert body["duration_months"] == 12
    assert body["monthly_installment"] > 0
    assert body["total_repayable"] >= body["principal"]


def test_simulate_loan_rejects_excessive_duration(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/fund/loans/simulate", headers=headers_a, json={"amount": 100_000, "duration_months": 61}
    )
    assert resp.status_code == 422, resp.text


def test_simulate_loan_rejects_non_positive_amount(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/fund/loans/simulate", headers=headers_a, json={"amount": 0, "duration_months": 12}
    )
    assert resp.status_code == 422, resp.text


def test_create_loan_on_unknown_campaign(api: httpx.Client, headers_a: dict) -> None:
    resp = api.post(
        "/fund/loans",
        headers=headers_a,
        json={"amount": 50_000, "duration_months": 6, "campaign_id": _RANDOM_ID},
    )
    assert_error(resp, 404, "CAMPAIGN_NOT_FOUND")


def test_create_loan_as_non_owner_is_forbidden(
    api: httpx.Client, headers_b: dict, campaign_owned_by_a: dict
) -> None:
    resp = api.post(
        "/fund/loans",
        headers=headers_b,
        json={
            "amount": 50_000,
            "duration_months": 6,
            "campaign_id": campaign_owned_by_a["campaign_id"],
        },
    )
    assert_error(resp, 403, "NOT_CAMPAIGN_OWNER")


@pytest.fixture(scope="module")
def loan_owned_by_a(api: httpx.Client, headers_a: dict, campaign_owned_by_a: dict) -> dict:
    resp = api.post(
        "/fund/loans",
        headers=headers_a,
        json={
            "amount": 50_000,
            "duration_months": 6,
            "campaign_id": campaign_owned_by_a["campaign_id"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_loan_success(loan_owned_by_a: dict) -> None:
    assert loan_owned_by_a["status"] == "pending"
    assert uuid.UUID(loan_owned_by_a["loan_id"])


def test_get_loan_detail(api: httpx.Client, headers_a: dict, loan_owned_by_a: dict) -> None:
    resp = api.get(f"/fund/loans/{loan_owned_by_a['loan_id']}", headers=headers_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["disbursed_at"] is None


def test_get_loan_not_found(api: httpx.Client, headers_a: dict) -> None:
    resp = api.get(f"/fund/loans/{_RANDOM_ID}", headers=headers_a)
    assert_error(resp, 404, "LOAN_NOT_FOUND")


def test_get_loan_hides_other_users_loan(api: httpx.Client, headers_b: dict, loan_owned_by_a: dict) -> None:
    # Isolation, pas juste "introuvable pour un ID au hasard" : B connaît un ID réel, appartenant à
    # A, et doit recevoir la même réponse que pour un ID inexistant — jamais un 403 qui confirmerait
    # l'existence du prêt d'un tiers.
    resp = api.get(f"/fund/loans/{loan_owned_by_a['loan_id']}", headers=headers_b)
    assert_error(resp, 404, "LOAN_NOT_FOUND")


def test_get_schedule_empty_before_disbursement(api: httpx.Client, headers_a: dict, loan_owned_by_a: dict) -> None:
    resp = api.get(f"/fund/loans/{loan_owned_by_a['loan_id']}/schedule", headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


def test_repay_rejects_non_positive_amount(api: httpx.Client, headers_a: dict, loan_owned_by_a: dict) -> None:
    resp = api.post(
        f"/fund/loans/{loan_owned_by_a['loan_id']}/repay",
        headers={**headers_a, **idem_headers()},
        json={"amount": 0},
    )
    assert resp.status_code == 422, resp.text


def test_repay_undisbursed_loan_is_rejected(api: httpx.Client, headers_a: dict, loan_owned_by_a: dict) -> None:
    resp = api.post(
        f"/fund/loans/{loan_owned_by_a['loan_id']}/repay",
        headers={**headers_a, **idem_headers()},
        json={"amount": 1000},
    )
    assert_error(resp, 409, "LOAN_NOT_DISBURSED")
