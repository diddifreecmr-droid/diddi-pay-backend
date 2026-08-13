"""Command-line entrypoint for internal Payfund ops tasks."""

from __future__ import annotations

import argparse
import sys
import uuid

from payfund_app.core.database import SessionLocal
from payfund_app.core.security import CurrentUser
from payfund_app.ops.maintenance import (
    backfill_wallet,
    reconcile_pending_paystack_deposits,
    reconcile_paystack_deposit,
    require_admin,
)


def _parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def main(argv: list[str] | None = None) -> int:
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

    args = parser.parse_args(argv)
    admin_user = CurrentUser(uuid.uuid4(), args.admin_role, "active")
    require_admin(admin_user)

    with SessionLocal() as session:
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
            return 0
        if args.command == "reconcile-paystack":
            result = reconcile_paystack_deposit(session, transaction_id=args.transaction_id)
            print(f"transaction_id={result.transaction_id} status={result.status}")
            return 0
        if args.command == "reconcile-paystack-pending":
            result = reconcile_pending_paystack_deposits(session)
            print(
                f"scanned={result.scanned} completed={result.completed} "
                f"failed={result.failed} pending={result.pending}"
            )
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
