from __future__ import annotations

import uuid

from payfund_app.core.security import CurrentUser
from payfund_app.modules.wallet.infra.models import Transaction
from payfund_app.modules.wallet.infra.repositories import AccountRepository, TransactionRepository

BASE = "/payfund/v1/wallet"


def test_ops_backfill_cree_le_wallet_manquant(client, auth, session):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    user_id = uuid.uuid4()

    response = client.post(
        f"{BASE}/ops/backfill",
        json={"user_id": str(user_id), "phone": "+2250700000000"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert AccountRepository(session).get_by_user(user_id) is not None


def test_ops_reconcile_paystack_finalise_un_webhook_manque(
    client, auth, session, make_user, monkeypatch
):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    user_id, account_id = make_user()
    transaction = TransactionRepository(session).create(
        type_="deposit",
        status="pending",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=None,
        provider_reference="ps-ref-1",
    )
    transaction.amount = 5000
    transaction.currency = "XOF"
    session.commit()

    class FakeGateway:
        def verifier_depot(self, reference):
            from payfund_app.modules.wallet.infra.gateways import GatewayOperation, GatewayStatus

            return GatewayOperation(provider_reference=reference, status=GatewayStatus.COMPLETED)

    monkeypatch.setattr("payfund_app.modules.wallet.presentation.routers.PaystackGateway", FakeGateway)

    response = client.post(f"{BASE}/ops/paystack/reconcile/{transaction.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    session.expire_all()
    updated = session.get(Transaction, transaction.id)
    assert updated.status == "completed"
    assert AccountRepository(session).balance(account_id).amount == 5000
