"""Command-line entrypoint for internal Payfund ops tasks."""

from __future__ import annotations

import argparse
import sys
import time
import uuid

from payfund_app.core.config import get_settings
from payfund_app.core.database import SessionLocal
from payfund_app.core.security import CurrentUser
from payfund_app.ops.maintenance import (
    audit_payment_integrity,
    backfill_wallet,
    deliver_payment_events,
    payment_event_delivery_status,
    reconcile_paystack_deposit,
    reconcile_pending_payment_intents,
    reconcile_pending_payouts,
    reconcile_pending_paystack_deposits,
    record_payment_settlement,
    relay_outbox_events,
    require_admin,
    run_housekeeping,
)
from payfund_app.ops.worker_health import record_worker_heartbeat
from payfund_app.shared_kernel.events.bus import get_bus
from payfund_app.shared_kernel.logging import configure_logging, emit


def _parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _emit_worker_cycle(reconciliation, payout_reconciliation, delivery, queue: dict[str, int]) -> None:
    emit(
        "warning" if queue["dead_letter"] else "info",
        "ops.payment_worker.cycle",
        reconciled=reconciliation.scanned,
        payouts_reconciled=payout_reconciliation.scanned,
        delivered_this_cycle=delivery.delivered,
        **queue,
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="python -m payfund_app.ops")
    sub = parser.add_subparsers(dest="command", required=True)

    backfill = sub.add_parser("backfill-wallet", help="Create a missing wallet")
    backfill.add_argument("user_id", type=_parse_uuid)
    backfill.add_argument("--phone", default=None)
    backfill.add_argument("--account-type", choices=("user", "merchant"), default="user")
    backfill.add_argument("--admin-role", default="admin")

    reconcile = sub.add_parser(
        "reconcile-paystack", help="Reconcile a pending Paystack deposit"
    )
    reconcile.add_argument("transaction_id", type=_parse_uuid)
    reconcile.add_argument("--admin-role", default="admin")

    sweep = sub.add_parser(
        "reconcile-paystack-pending",
        help="Sweep all pending Paystack deposits and reconcile them",
    )
    sweep.add_argument("--admin-role", default="admin")

    relay = sub.add_parser(
        "relay-outbox", help="Publish durable outbox events to the runtime bus"
    )
    relay.add_argument("--admin-role", default="admin")

    payment_relay = sub.add_parser(
        "relay-payment-events",
        help="Deliver signed PaymentIntent events to module callbacks",
    )
    payment_relay.add_argument("--limit", type=int, choices=range(1, 501), default=100)
    payment_relay.add_argument("--admin-role", default="admin")

    payment_worker = sub.add_parser(
        "payment-worker", help="Continuously reconcile PaymentIntents and relay module callbacks"
    )
    payment_worker.add_argument("--interval", type=int, default=30)
    payment_worker.add_argument("--admin-role", default="admin")

    payment_once = sub.add_parser(
        "maintain-payment-intents", help="Run one PaymentIntent reconciliation and callback cycle"
    )
    payment_once.add_argument("--admin-role", default="admin")

    audit = sub.add_parser(
        "audit-payment-integrity", help="Read-only check for missing captures or module events"
    )
    audit.add_argument("--limit", type=int, choices=range(1, 501), default=100)
    audit.add_argument("--admin-role", default="admin")

    settlement = sub.add_parser(
        "record-payment-settlement",
        help="Record a provider settlement against a PaymentIntent receivable",
    )
    settlement.add_argument("payment_intent_id", type=_parse_uuid)
    settlement.add_argument("amount", type=int)
    settlement.add_argument("settlement_reference")
    settlement.add_argument("--admin-role", default="admin")

    event_status = sub.add_parser(
        "payment-events-status",
        help="Show payment callback outbox counts, including dead letters",
    )
    event_status.add_argument("--admin-role", default="admin")

    housekeeping = sub.add_parser(
        "housekeeping",
        help="Run the standard maintenance cycle: Paystack reconciliation then outbox relay",
    )
    housekeeping.add_argument("--admin-role", default="admin")

    args = parser.parse_args(argv)
    admin_user = CurrentUser(uuid.uuid4(), args.admin_role, "active")
    require_admin(admin_user)

    if args.command == "payment-worker":
        if args.interval < 1:
            parser.error("--interval must be positive")
        while True:
            try:
                with SessionLocal() as session:
                    reconciliation = reconcile_pending_payment_intents(session)
                    payout_reconciliation = reconcile_pending_payouts(session)
                    delivery = deliver_payment_events(session)
                    queue = payment_event_delivery_status(session)
                    _emit_worker_cycle(
                        reconciliation, payout_reconciliation, delivery, queue
                    )
                record_worker_heartbeat(
                    get_settings().payment_worker_heartbeat_path, healthy=True
                )
            except Exception as exc:  # noqa: BLE001 - worker must retry after transient failures
                emit("error", "ops.payment_worker.failed", error=str(exc))
                record_worker_heartbeat(
                    get_settings().payment_worker_heartbeat_path,
                    healthy=False,
                    error=str(exc),
                )
            time.sleep(args.interval)

    with SessionLocal() as session:
        if args.command == "audit-payment-integrity":
            report = audit_payment_integrity(session, limit=args.limit)
            for gap in report.gaps:
                print(
                    f"payment_intent_id={gap.payment_intent_id} "
                    f"missing_capture={gap.missing_capture} "
                    f"missing_callback={gap.missing_callback}"
                )
            print(f"gaps_returned={len(report.gaps)} has_more={report.has_more}")
            return 2 if report.has_gaps else 0
        if args.command == "maintain-payment-intents":
            reconciliation = reconcile_pending_payment_intents(session)
            payout_reconciliation = reconcile_pending_payouts(session)
            delivery = deliver_payment_events(session)
            queue = payment_event_delivery_status(session)
            print(f"scanned={reconciliation.scanned} succeeded={reconciliation.succeeded} mismatched={reconciliation.mismatched} payouts_scanned={payout_reconciliation.scanned} delivered={delivery.delivered} retried={delivery.retried} pending={queue['pending']} dead_letter={queue['dead_letter']}")
            return 2 if queue["dead_letter"] else 0
        if args.command == "backfill-wallet":
            result = backfill_wallet(
                session,
                user_id=args.user_id,
                phone=args.phone,
                account_type=args.account_type,
            )
            print(
                f"wallet provisioned user_id={result.user_id} account_id={result.account_id} "
                f"account_type={result.account_type} phone={result.phone}"
            )
            emit(
                "info",
                "ops.cli.backfill.done",
                user_id=str(result.user_id),
                account_id=str(result.account_id),
                account_type=result.account_type,
                phone=result.phone,
            )
            return 0
        if args.command == "reconcile-paystack":
            result = reconcile_paystack_deposit(session, transaction_id=args.transaction_id)
            print(f"transaction_id={result.transaction_id} status={result.status}")
            emit(
                "info",
                "ops.cli.reconcile.done",
                transaction_id=str(result.transaction_id),
                status=result.status,
            )
            return 0
        if args.command == "reconcile-paystack-pending":
            result = reconcile_pending_paystack_deposits(session)
            print(
                f"scanned={result.scanned} completed={result.completed} "
                f"failed={result.failed} pending={result.pending}"
            )
            emit(
                "info",
                "ops.cli.reconcile_sweep.done",
                scanned=result.scanned,
                completed=result.completed,
                failed=result.failed,
                pending=result.pending,
            )
            return 0
        if args.command == "relay-outbox":
            result = relay_outbox_events(session, get_bus())
            print(f"scanned={result.scanned} published={result.published}")
            emit(
                "info",
                "ops.cli.relay.done",
                scanned=result.scanned,
                published=result.published,
            )
            return 0
        if args.command == "relay-payment-events":
            result = deliver_payment_events(session, limit=args.limit)
            print(
                f"scanned={result.scanned} delivered={result.delivered} "
                f"retried={result.retried} unavailable={result.unavailable}"
            )
            emit(
                "info",
                "ops.cli.payment_events.done",
                scanned=result.scanned,
                delivered=result.delivered,
                retried=result.retried,
                unavailable=result.unavailable,
            )
            return 0
        if args.command == "record-payment-settlement":
            result = record_payment_settlement(
                session,
                payment_intent_id=args.payment_intent_id,
                amount=args.amount,
                settlement_reference=args.settlement_reference,
            )
            print(
                f"net_expected={result['net_expected']} settled={result['settled']} "
                f"outstanding={result['outstanding']}"
            )
            return 0
        if args.command == "payment-events-status":
            result = payment_event_delivery_status(session)
            print(" ".join(f"{key}={value}" for key, value in result.items()))
            return 2 if result["dead_letter"] else 0
        if args.command == "housekeeping":
            result = run_housekeeping(session, get_bus())
            print(
                "reconciliation_scanned="
                f"{result.reconciliation.scanned} reconciliation_completed="
                f"{result.reconciliation.completed} reconciliation_failed="
                f"{result.reconciliation.failed} reconciliation_pending="
                f"{result.reconciliation.pending} outbox_scanned="
                f"{result.outbox.scanned} outbox_published={result.outbox.published}"
            )
            emit(
                "info",
                "ops.cli.housekeeping.done",
                reconciliation_scanned=result.reconciliation.scanned,
                reconciliation_completed=result.reconciliation.completed,
                reconciliation_failed=result.reconciliation.failed,
                reconciliation_pending=result.reconciliation.pending,
                outbox_scanned=result.outbox.scanned,
                outbox_published=result.outbox.published,
            )
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
