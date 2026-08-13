from __future__ import annotations

import uuid

from payfund_app.core.security import CurrentUser
from payfund_app.modules.wallet.infra.gateways import GatewayOperation, GatewayStatus
from payfund_app.modules.wallet.infra.models import Transaction
from payfund_app.ops.maintenance import (
    backfill_wallet,
    reconcile_pending_paystack_deposits,
    reconcile_paystack_deposit,
    require_admin,
)


def test_backfill_wallet_creates_account(session):
    user_id = uuid.uuid4()

    result = backfill_wallet(session, user_id=user_id, phone="+2250700000000")

    assert result.user_id == user_id
    assert result.phone == "+2250700000000"


def test_reconcile_paystack_deposit_completes_success(monkeypatch, session, make_user):
    _, account_id = make_user()
    transaction = session.query(Transaction).first()
    if transaction is None:
        from payfund_app.modules.wallet.infra.repositories import TransactionRepository

        transaction = TransactionRepository(session).create(
            type_="deposit",
            status="pending",
            origin_module="wallet",
            idempotency_key=str(uuid.uuid4()),
            account_id=account_id,
            money=None,
            provider_reference="ps-ref-2",
        )
        transaction.amount = 5000
        transaction.currency = "XOF"
        session.commit()

    class FakeGateway:
        def verifier_depot(self, reference):
            return GatewayOperation(provider_reference=reference, status=GatewayStatus.COMPLETED)

    monkeypatch.setattr("payfund_app.ops.maintenance.PaystackGateway", FakeGateway)

    result = reconcile_paystack_deposit(session, transaction_id=transaction.id)

    assert result.status == "completed"
    session.expire_all()
    updated = session.get(Transaction, transaction.id)
    assert updated.status == "completed"


def test_require_admin_rejects_non_admin():
    try:
        require_admin(CurrentUser(uuid.uuid4(), "user", "active"))
    except PermissionError:
        return
    raise AssertionError("Expected PermissionError")


def test_reconcile_pending_paystack_deposits_sweep(monkeypatch, session, make_user):
    _, account_id = make_user()
    from payfund_app.modules.wallet.infra.repositories import TransactionRepository

    t1 = TransactionRepository(session).create(
        type_="deposit",
        status="pending",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=account_id,
        money=None,
        provider_reference="ps-ref-3",
    )
    t1.amount = 5000
    t1.currency = "XOF"
    session.commit()

    class FakeGateway:
        def verifier_depot(self, reference):
            return GatewayOperation(provider_reference=reference, status=GatewayStatus.COMPLETED)

    monkeypatch.setattr("payfund_app.ops.maintenance.PaystackGateway", FakeGateway)

    result = reconcile_pending_paystack_deposits(session)

    assert result.scanned == 1
    assert result.completed == 1
    session.expire_all()
    updated = session.get(Transaction, t1.id)
    assert updated.status == "completed"
