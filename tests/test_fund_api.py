"""Parcours du module `fund` (Contrat API §2) — campagnes et investissement."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from payfund_app.modules.fund.domain.entities import CampaignStatus
from payfund_app.modules.fund.infra.models import Campaign, Investment
from payfund_app.modules.wallet.domain.entities import AccountType
from payfund_app.modules.wallet.infra.repositories import AccountRepository

BASE = "/payfund/v1/fund"
WALLET = "/payfund/v1/wallet"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _creer_campagne(client, session, auth, owner_id, *, goal=500_000, active=True):
    auth.as_user(owner_id)
    campaign_id = client.post(
        f"{BASE}/campaigns",
        json={"title": "Extension atelier de couture", "goal_amount": goal},
    ).json()["campaign_id"]
    if active:
        # `draft → active` relève du back-office, hors contrat public (§3) : on force l'état.
        campaign = session.get(Campaign, uuid.UUID(campaign_id))
        campaign.status = str(CampaignStatus.ACTIVE)
        session.commit()
    return uuid.UUID(campaign_id)


def test_campagne_creee_en_draft(client, auth, session, make_user):
    owner, _ = make_user()
    auth.as_user(owner)

    response = client.post(
        f"{BASE}/campaigns",
        json={"title": "Extension atelier de couture", "goal_amount": 500_000},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"

    campaign = session.get(Campaign, uuid.UUID(body["campaign_id"]))
    assert campaign.owner_user_id == owner
    assert int(campaign.raised_amount) == 0


def test_creation_ouvre_un_compte_technique_de_pool(client, auth, session, make_user):
    owner, _ = make_user()
    auth.as_user(owner)

    campaign_id = client.post(
        f"{BASE}/campaigns", json={"title": "Atelier", "goal_amount": 100_000}
    ).json()["campaign_id"]

    campaign = session.get(Campaign, uuid.UUID(campaign_id))
    pool = AccountRepository(session).get(campaign.wallet_account_id)
    assert pool is not None
    assert pool.account_type == str(AccountType.TECHNICAL)
    # Un compte de pool n'a pas de propriétaire : c'est précisément ce que `user_id NULL` permet.
    assert pool.user_id is None


def test_investissement_debite_l_investisseur_et_credite_le_pool(
    client, auth, session, make_user, fund_account
):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 50_000)
    campaign_id = _creer_campagne(client, session, auth, owner)

    auth.as_user(investisseur)
    response = client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 10_000}, headers=_key()
    )

    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == 10_000
    assert body["campaign_id"] == str(campaign_id)
    assert body["wallet_transaction_id"]

    assert client.get(f"{WALLET}/balance").json()["balance"] == 40_000

    session.expire_all()
    campaign = session.get(Campaign, campaign_id)
    assert int(campaign.raised_amount) == 10_000
    pool_balance = AccountRepository(session).balance(campaign.wallet_account_id)
    assert pool_balance.amount == 10_000


def test_investissement_impossible_sur_campagne_draft(
    client, auth, session, make_user, fund_account
):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 50_000)
    campaign_id = _creer_campagne(client, session, auth, owner, active=False)

    auth.as_user(investisseur)
    response = client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 10_000}, headers=_key()
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CAMPAIGN_NOT_ACTIVE"
    assert error["details"] == {"status": "draft"}


def test_investissement_dans_sa_propre_campagne(
    client, auth, session, make_user, fund_account
):
    owner, compte = make_user()
    fund_account(compte, 50_000)
    campaign_id = _creer_campagne(client, session, auth, owner)

    auth.as_user(owner)
    response = client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 10_000}, headers=_key()
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CANNOT_INVEST_IN_OWN_CAMPAIGN"


def test_investissement_objectif_deja_atteint(
    client, auth, session, make_user, fund_account
):
    owner, _ = make_user()
    premier, compte_premier = make_user()
    second, compte_second = make_user()
    fund_account(compte_premier, 50_000)
    fund_account(compte_second, 50_000)
    campaign_id = _creer_campagne(client, session, auth, owner, goal=10_000)

    auth.as_user(premier)
    client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 10_000}, headers=_key()
    )

    auth.as_user(second)
    response = client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 5_000}, headers=_key()
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CAMPAIGN_GOAL_ALREADY_REACHED"


def test_investissement_solde_insuffisant_ne_laisse_aucune_trace(
    client, auth, session, make_user, fund_account
):
    """Atomicité : pas d'`investment` orphelin si le mouvement wallet échoue (§2)."""
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 1_000)
    campaign_id = _creer_campagne(client, session, auth, owner)

    auth.as_user(investisseur)
    response = client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 10_000}, headers=_key()
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INSUFFICIENT_BALANCE"

    session.rollback()
    investissements = session.scalar(
        select(func.count()).select_from(
            select(Investment).where(Investment.campaign_id == campaign_id).subquery()
        )
    )
    assert investissements == 0
    campaign = session.get(Campaign, campaign_id)
    assert int(campaign.raised_amount) == 0


def test_investissement_rejoue_ne_compte_qu_une_fois(
    client, auth, session, make_user, fund_account
):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 50_000)
    campaign_id = _creer_campagne(client, session, auth, owner)
    headers = _key()

    auth.as_user(investisseur)
    premiere = client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 10_000}, headers=headers
    )
    seconde = client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 10_000}, headers=headers
    )

    assert premiere.status_code == seconde.status_code == 201
    assert premiere.json()["investment_id"] == seconde.json()["investment_id"]
    assert client.get(f"{WALLET}/balance").json()["balance"] == 40_000

    session.expire_all()
    assert int(session.get(Campaign, campaign_id).raised_amount) == 10_000


def test_liste_des_campagnes_filtrable_par_statut(client, auth, session, make_user):
    owner, _ = make_user()
    _creer_campagne(client, session, auth, owner, active=True)
    _creer_campagne(client, session, auth, owner, active=False)

    auth.as_user(owner)
    actives = client.get(f"{BASE}/campaigns", params={"status": "active"}).json()
    toutes = client.get(f"{BASE}/campaigns").json()

    assert actives["pagination"]["total_items"] == 1
    assert toutes["pagination"]["total_items"] == 2
    assert actives["data"][0]["status"] == "active"
    assert actives["data"][0]["goal_amount"] == 500_000


def test_detail_campagne_liste_les_derniers_investissements(
    client, auth, session, make_user, fund_account
):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 50_000)
    campaign_id = _creer_campagne(client, session, auth, owner)

    auth.as_user(investisseur)
    client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": 10_000}, headers=_key()
    )

    detail = client.get(f"{BASE}/campaigns/{campaign_id}").json()

    assert detail["raised_amount"] == 10_000
    assert len(detail["latest_investments"]) == 1
    assert detail["latest_investments"][0]["amount"] == 10_000


def test_campagne_inexistante(client, auth, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)
    response = client.get(f"{BASE}/campaigns/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CAMPAIGN_NOT_FOUND"
