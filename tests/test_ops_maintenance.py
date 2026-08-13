from __future__ import annotations

import uuid

from payfund_app.core.security import CurrentUser
from payfund_app.modules.wallet.infra.gateways import GatewayOperation, GatewayStatus
from payfund_app.modules.wallet.infra.models import Transaction
from payfund_app.shared_kernel.events.bus import InMemoryEventBus
from payfund_app.ops.maintenance import (
    backfill_wallet,
    reconcile_pending_paystack_deposits,
    reconcile_paystack_deposit,
    relay_outbox_events,
    require_admin,
)


def test_backfill_wallet_creates_account(session):
    user_id = uuid.uuid4()

    result = backfill_wallet(session, user_id=user_id, phone="+2250700000000")

    assert result.user_id == user_id
    assert result.phone == "+2250700000000"
    assert result.account_type == "user"


def test_backfill_wallet_can_create_merchant_account(session):
    user_id = uuid.uuid4()

    result = backfill_wallet(
        session,
        user_id=user_id,
        phone=None,
        account_type="merchant",
    )

    assert result.user_id == user_id
    assert result.account_type == "merchant"


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


def test_relay_outbox_events_publishes_and_marks_done(session, make_user):
    user_id, compte = make_user()
    from payfund_app.modules.wallet.application.use_cases import WalletUseCases
    from payfund_app.modules.wallet.domain.money import Money
    from payfund_app.modules.wallet.infra.repositories import OutboxRepository

    use_cases = WalletUseCases(session)
    transaction = use_cases.transactions.create(
        type_="deposit",
        status="completed",
        origin_module="wallet",
        idempotency_key=str(uuid.uuid4()),
        account_id=compte,
        money=Money(1000, "XOF"),
    )
    use_cases._publish_completed(transaction, Money(1000, "XOF"))
    session.commit()

    bus = InMemoryEventBus()
    result = relay_outbox_events(session, bus)

    assert result.scanned == 1
    assert result.published == 1
    assert bus.published[0].event == "payment.completed"
    assert OutboxRepository(session).pending() == []
