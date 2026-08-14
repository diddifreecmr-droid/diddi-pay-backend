from __future__ import annotations

import uuid

from payfund_app.core.security import CurrentUser
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.money import Money
from payfund_app.modules.wallet.infra.models import Transaction
from payfund_app.modules.wallet.infra.repositories import (
    AccountRepository,
    ReconciliationLogRepository,
    TransactionRepository,
)
from payfund_app.shared_kernel.events.bus import InMemoryEventBus, set_bus

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


def test_ops_inspect_provisioning_status(client, auth, session, make_user):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    user_id, account_id = make_user("+2250700000000")

    response = client.get(f"{BASE}/ops/provisioning/{user_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["wallet_exists"] is True
    assert body["account_id"] == str(account_id)
    assert body["phone"] == "+2250700000000"


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
    logs = ReconciliationLogRepository(session).latest_for_transaction(transaction.id)
    assert len(logs) == 1
    assert logs[0].outcome == "completed"
    assert logs[0].event == "manual_reconcile"
    assert logs[0].reason == "provider_completed"


def test_ops_inspect_paystack_transaction(client, auth, session, make_user):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    _, account_id = make_user()
    transaction = TransactionRepository(session).create(
        type_="deposit",
        status="pending",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=Money(5000, "XOF"),
        provider_reference="ps-audit-1",
    )
    session.commit()

    response = client.get(f"{BASE}/ops/paystack/{transaction.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["transaction_id"] == str(transaction.id)
    assert body["provider_reference"] == "ps-audit-1"
    assert body["status"] == "pending"
    assert body["amount"] == 5000


def test_ops_list_paystack_reconciliations(client, auth, session, make_user):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    _, account_id = make_user()
    transaction = TransactionRepository(session).create(
        type_="deposit",
        status="pending",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=Money(5000, "XOF"),
        provider_reference="ps-history-1",
    )
    ReconciliationLogRepository(session).append(
        transaction_id=transaction.id,
        provider="paystack",
        provider_reference="ps-history-1",
        event="webhook",
        outcome="completed",
        reason="charge_success",
    )
    ReconciliationLogRepository(session).append(
        transaction_id=transaction.id,
        provider="paystack",
        provider_reference="ps-history-1",
        event="manual_reconcile",
        outcome="completed",
        reason="provider_completed",
    )
    session.commit()

    response = client.get(f"{BASE}/ops/paystack/{transaction.id}/reconciliations")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["data"][0]["event"] == "manual_reconcile"
    assert body["data"][0]["outcome"] == "completed"
    assert body["data"][1]["event"] == "webhook"
    assert body["data"][1]["reason"] == "charge_success"


def test_ops_list_pending_paystack_transactions(client, auth, session, make_user):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    _, account_id = make_user()
    pending = TransactionRepository(session).create(
        type_="deposit",
        status="pending",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=Money(5000, "XOF"),
        provider_reference="ps-pending-1",
    )
    completed = TransactionRepository(session).create(
        type_="deposit",
        status="completed",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=Money(3000, "XOF"),
        provider_reference="ps-done-1",
    )
    session.commit()

    response = client.get(f"{BASE}/ops/paystack/pending")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["data"][0]["transaction_id"] == str(pending.id)
    assert body["data"][0]["provider_reference"] == "ps-pending-1"
    assert body["data"][0]["amount"] == 5000


def test_ops_paystack_reconciliation_summary(client, auth, session, make_user):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    _, account_id = make_user()
    TransactionRepository(session).create(
        type_="deposit",
        status="pending",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=Money(5000, "XOF"),
        provider_reference="ps-summary-pending",
    )
    TransactionRepository(session).create(
        type_="deposit",
        status="completed",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=Money(3000, "XOF"),
        provider_reference="ps-summary-completed",
    )
    TransactionRepository(session).create(
        type_="deposit",
        status="failed",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=Money(1000, "XOF"),
        provider_reference="ps-summary-failed",
    )
    session.commit()

    response = client.get(f"{BASE}/ops/paystack/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["pending"] == 1
    assert body["completed"] == 1
    assert body["failed"] == 1
    assert body["missing_reference"] == 0


def test_ops_outbox_listing_and_relay(client, auth, session, make_user):
    auth.user = CurrentUser(uuid.uuid4(), "admin", "active")
    _, account_id = make_user()
    use_cases = WalletUseCases(session)
    transaction = use_cases.transactions.create(
        type_="deposit",
        status="completed",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=Money(1000, "XOF"),
    )
    use_cases._publish_completed(transaction, Money(1000, "XOF"))
    session.commit()

    bus = InMemoryEventBus()
    set_bus(bus)
    try:
        response = client.get(f"{BASE}/ops/outbox")
        assert response.status_code == 200
        assert response.json()["total"] == 1

        relay = client.post(f"{BASE}/ops/outbox/relay")
        assert relay.status_code == 200
        assert relay.json()["published"] == 1
        assert len(bus.published) == 1
    finally:
        set_bus(InMemoryEventBus())


def test_readiness_route_reports_ready(client):
    response = client.get("/payfund/v1/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
