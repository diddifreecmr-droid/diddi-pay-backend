"""Prêts DiddiFund — crowdlending : le pool d'une campagne finance le prêt de son porteur."""

from __future__ import annotations

import uuid
from datetime import date

from payfund_app.modules.fund.application.use_cases import LoanUseCases
from payfund_app.modules.fund.domain.entities import CampaignStatus, LoanStatus
from payfund_app.modules.fund.infra.models import Campaign, LoanStatusHistory
from payfund_app.modules.fund.infra.scoring import get_scoring
from payfund_app.modules.fund.infra.wallet_client import get_wallet_service
from payfund_app.modules.wallet.infra.repositories import AccountRepository
from sqlalchemy import select

BASE = "/payfund/v1/fund"
WALLET = "/payfund/v1/wallet"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _campagne_financee(client, session, auth, owner, investisseur, montant=250_000):
    """Campagne active dont le pool a réellement été alimenté par un investissement."""
    auth.as_user(owner)
    campaign_id = uuid.UUID(
        client.post(
            f"{BASE}/campaigns", json={"title": "Atelier de couture", "goal_amount": 500_000}
        ).json()["campaign_id"]
    )
    campaign = session.get(Campaign, campaign_id)
    campaign.status = str(CampaignStatus.ACTIVE)
    session.commit()

    auth.as_user(investisseur)
    client.post(
        f"{BASE}/campaigns/{campaign_id}/invest", json={"amount": montant}, headers=_key()
    )
    return campaign_id


def _decaisser(session, loan_id, jour=date(2026, 7, 20)):
    use_cases = LoanUseCases(
        session, wallet=get_wallet_service(session), scoring=get_scoring()
    )
    loan = use_cases.decaisser(loan_id, aujourd_hui=jour)
    session.commit()
    return loan


# --- Simulation --------------------------------------------------------------


def test_simulation_reproduit_l_exemple_du_contrat(client, auth, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)

    response = client.post(
        f"{BASE}/loans/simulate", json={"amount": 200_000, "duration_months": 6}
    )

    assert response.status_code == 200
    assert response.json() == {
        "principal": 200_000,
        "duration_months": 6,
        "monthly_installment": 35_500,
        "total_repayable": 213_000,
        "interest_rate_applied": "6.5",
    }


def test_simulation_ne_cree_rien(client, auth, session, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)
    client.post(f"{BASE}/loans/simulate", json={"amount": 200_000, "duration_months": 6})

    from payfund_app.modules.fund.infra.models import Loan

    assert list(session.scalars(select(Loan))) == []


def test_duree_hors_bornes_refusee(client, auth, make_user):
    user_id, _ = make_user()
    auth.as_user(user_id)
    response = client.post(
        f"{BASE}/loans/simulate", json={"amount": 200_000, "duration_months": 120}
    )
    assert response.status_code == 422


# --- Demande -----------------------------------------------------------------


def test_demande_de_pret_est_en_pending(client, auth, session, make_user, fund_account):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)

    auth.as_user(owner)
    response = client.post(
        f"{BASE}/loans",
        json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "pending"


def test_seul_le_porteur_peut_emprunter_sur_son_pool(
    client, auth, session, make_user, fund_account
):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)

    auth.as_user(investisseur)
    response = client.post(
        f"{BASE}/loans",
        json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_CAMPAIGN_OWNER"


def test_demande_trace_le_statut_initial(client, auth, session, make_user, fund_account):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)

    auth.as_user(owner)
    loan_id = client.post(
        f"{BASE}/loans",
        json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
    ).json()["loan_id"]

    historique = list(
        session.scalars(
            select(LoanStatusHistory).where(LoanStatusHistory.loan_id == uuid.UUID(loan_id))
        )
    )
    assert [h.to_status for h in historique] == ["pending"]


# --- Décaissement ------------------------------------------------------------


def test_decaissement_vide_le_pool_vers_l_emprunteur(
    client, auth, session, make_user, fund_account
):
    owner, compte_owner = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)

    auth.as_user(owner)
    loan_id = uuid.UUID(
        client.post(
            f"{BASE}/loans",
            json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
        ).json()["loan_id"]
    )

    loan = _decaisser(session, loan_id)

    assert loan.status == str(LoanStatus.DISBURSED)
    assert loan.wallet_transaction_id is not None
    assert client.get(f"{WALLET}/balance").json()["balance"] == 200_000

    campaign = session.get(Campaign, campaign_id)
    pool = AccountRepository(session).balance(campaign.wallet_account_id)
    assert pool.amount == 50_000


def test_decaissement_trace_le_mouvement_wallet(
    client, auth, session, make_user, fund_account
):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)

    auth.as_user(owner)
    loan_id = uuid.UUID(
        client.post(
            f"{BASE}/loans",
            json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
        ).json()["loan_id"]
    )

    loan = _decaisser(session, loan_id)

    assert loan.wallet_transaction_id is not None
    detail = client.get(f"{WALLET}/transactions/{loan.wallet_transaction_id}").json()
    assert detail["type"] == "fund_disbursement"
    assert detail["status"] == "completed"
    assert detail["amount"] == 200_000


def test_pool_insuffisant_bloque_le_decaissement(
    client, auth, session, make_user, fund_account
):
    """Un pool de campagne n'est pas un compte suspense : il ne peut pas passer en négatif."""
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(
        client, session, auth, owner, investisseur, montant=50_000
    )

    auth.as_user(owner)
    loan_id = uuid.UUID(
        client.post(
            f"{BASE}/loans",
            json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
        ).json()["loan_id"]
    )

    from payfund_app.modules.wallet.domain.errors import InsufficientBalance

    try:
        _decaisser(session, loan_id)
        raise AssertionError("le décaissement aurait dû échouer")
    except InsufficientBalance as exc:
        assert exc.details["balance"] == 50_000


def test_decaissement_pose_l_echeancier(client, auth, session, make_user, fund_account):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)

    auth.as_user(owner)
    loan_id = uuid.UUID(
        client.post(
            f"{BASE}/loans",
            json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
        ).json()["loan_id"]
    )
    _decaisser(session, loan_id)

    echeancier = client.get(f"{BASE}/loans/{loan_id}/schedule").json()["data"]

    assert len(echeancier) == 6
    assert echeancier[0] == {
        "installment_no": 1,
        "due_date": "2026-08-20",
        "amount_due": 35_500,
        "amount_paid": 0,
        "status": "due",
    }
    assert sum(e["amount_due"] for e in echeancier) == 213_000


def test_double_decaissement_refuse(client, auth, session, make_user, fund_account):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)

    auth.as_user(owner)
    loan_id = uuid.UUID(
        client.post(
            f"{BASE}/loans",
            json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
        ).json()["loan_id"]
    )
    _decaisser(session, loan_id)

    from payfund_app.modules.fund.domain.errors import LoanAlreadyDisbursed

    try:
        _decaisser(session, loan_id)
        raise AssertionError("le second décaissement aurait dû échouer")
    except LoanAlreadyDisbursed:
        pass


# --- Remboursement -----------------------------------------------------------


def _pret_decaisse(client, auth, session, make_user, fund_account):
    owner, compte_owner = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)
    auth.as_user(owner)
    loan_id = uuid.UUID(
        client.post(
            f"{BASE}/loans",
            json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
        ).json()["loan_id"]
    )
    _decaisser(session, loan_id)
    return owner, campaign_id, loan_id


def test_remboursement_solde_l_echeance_et_recredite_le_pool(
    client, auth, session, make_user, fund_account
):
    owner, campaign_id, loan_id = _pret_decaisse(
        client, auth, session, make_user, fund_account
    )

    auth.as_user(owner)
    response = client.post(
        f"{BASE}/loans/{loan_id}/repay", json={"amount": 35_500}, headers=_key()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["loan_status"] == "repaying"
    assert body["installment"]["installment_no"] == 1
    assert body["installment"]["status"] == "paid"

    assert client.get(f"{WALLET}/balance").json()["balance"] == 164_500
    campaign = session.get(Campaign, campaign_id)
    assert AccountRepository(session).balance(campaign.wallet_account_id).amount == 85_500


def test_remboursement_partiel_laisse_l_echeance_ouverte(
    client, auth, session, make_user, fund_account
):
    owner, _, loan_id = _pret_decaisse(client, auth, session, make_user, fund_account)

    auth.as_user(owner)
    response = client.post(
        f"{BASE}/loans/{loan_id}/repay", json={"amount": 10_000}, headers=_key()
    )

    installment = response.json()["installment"]
    assert installment["status"] == "due"
    assert installment["amount_paid"] == 10_000

    detail = client.get(f"{BASE}/loans/{loan_id}").json()
    assert detail["next_installment"]["amount_due"] == 25_500


def test_remboursement_superieur_a_l_echeance_refuse(
    client, auth, session, make_user, fund_account
):
    owner, _, loan_id = _pret_decaisse(client, auth, session, make_user, fund_account)

    auth.as_user(owner)
    response = client.post(
        f"{BASE}/loans/{loan_id}/repay", json={"amount": 71_000}, headers=_key()
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "REPAYMENT_EXCEEDS_INSTALLMENT"
    assert error["details"] == {"installment_no": 1, "remaining": 35_500}


def test_pret_entierement_rembourse_est_cloture(
    client, auth, session, make_user, fund_account
):
    owner, campaign_id, loan_id = _pret_decaisse(
        client, auth, session, make_user, fund_account
    )
    auth.as_user(owner)
    # Les 200 000 décaissés ne suffisent pas à rembourser capital + intérêts (213 000).
    fund_account(AccountRepository(session).get_by_user(owner).id, 13_000)

    echeancier = client.get(f"{BASE}/loans/{loan_id}/schedule").json()["data"]
    for echeance in echeancier:
        response = client.post(
            f"{BASE}/loans/{loan_id}/repay",
            json={"amount": echeance["amount_due"]},
            headers=_key(),
        )
        assert response.status_code == 200

    assert response.json()["loan_status"] == "closed"

    # Le pool a récupéré la totalité du remboursement : 250 000 investis − 200 000 décaissés
    # + 213 000 remboursés. Les 13 000 d'intérêts sont le gain des investisseurs.
    campaign = session.get(Campaign, campaign_id)
    assert AccountRepository(session).balance(campaign.wallet_account_id).amount == 263_000


def test_remboursement_apres_cloture_refuse(
    client, auth, session, make_user, fund_account
):
    owner, _, loan_id = _pret_decaisse(client, auth, session, make_user, fund_account)
    auth.as_user(owner)
    fund_account(AccountRepository(session).get_by_user(owner).id, 13_000)

    for echeance in client.get(f"{BASE}/loans/{loan_id}/schedule").json()["data"]:
        client.post(
            f"{BASE}/loans/{loan_id}/repay",
            json={"amount": echeance["amount_due"]},
            headers=_key(),
        )

    response = client.post(
        f"{BASE}/loans/{loan_id}/repay", json={"amount": 1000}, headers=_key()
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INSTALLMENT_ALREADY_PAID"


def test_remboursement_avant_decaissement_refuse(
    client, auth, session, make_user, fund_account
):
    owner, _ = make_user()
    investisseur, compte = make_user()
    fund_account(compte, 300_000)
    campaign_id = _campagne_financee(client, session, auth, owner, investisseur)

    auth.as_user(owner)
    loan_id = client.post(
        f"{BASE}/loans",
        json={"campaign_id": str(campaign_id), "amount": 200_000, "duration_months": 6},
    ).json()["loan_id"]

    response = client.post(
        f"{BASE}/loans/{loan_id}/repay", json={"amount": 35_500}, headers=_key()
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOAN_NOT_DISBURSED"


def test_pret_d_un_tiers_est_invisible(client, auth, session, make_user, fund_account):
    owner, _, loan_id = _pret_decaisse(client, auth, session, make_user, fund_account)
    tiers, _ = make_user()

    auth.as_user(tiers)
    assert client.get(f"{BASE}/loans/{loan_id}").status_code == 404
    assert client.get(f"{BASE}/loans/{loan_id}/schedule").status_code == 404
