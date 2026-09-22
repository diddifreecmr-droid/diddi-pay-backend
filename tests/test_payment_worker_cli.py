from contextlib import nullcontext
from importlib import import_module
from types import SimpleNamespace

from payfund_app.ops import __main__ as ops


def test_maintenance_command_reports_dead_letters(monkeypatch, capsys):
    assert import_module("payfund_app.ops.__main__") is ops
    configured = []
    monkeypatch.setattr(ops, "configure_logging", lambda: configured.append(True))
    monkeypatch.setattr(ops, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(
        ops, "reconcile_pending_payment_intents",
        lambda _: SimpleNamespace(scanned=2, succeeded=1, mismatched=0),
    )
    monkeypatch.setattr(
        ops, "deliver_payment_events",
        lambda _: SimpleNamespace(delivered=1, retried=0),
    )
    monkeypatch.setattr(
        ops, "reconcile_pending_payouts",
        lambda _: SimpleNamespace(scanned=0, succeeded=0, failed=0, pending=0),
    )
    monkeypatch.setattr(
        ops, "payment_event_delivery_status",
        lambda _: {"pending": 0, "delivering": 0, "delivered": 1, "dead_letter": 1},
    )
    assert ops.main(["maintain-payment-intents"]) == 2
    assert configured == [True]
    assert "dead_letter=1" in capsys.readouterr().out


def test_worker_cycle_log_uses_distinct_delivery_fields(monkeypatch):
    emitted = []
    monkeypatch.setattr(ops, "emit", lambda level, message, **fields: emitted.append(fields))
    reconciliation = SimpleNamespace(scanned=2)
    payout_reconciliation = SimpleNamespace(scanned=3)
    delivery = SimpleNamespace(delivered=1)
    queue = {"pending": 0, "delivering": 0, "delivered": 9, "dead_letter": 0}
    ops._emit_worker_cycle(reconciliation, payout_reconciliation, delivery, queue)
    assert emitted[0]["delivered_this_cycle"] == 1
    assert emitted[0]["delivered"] == 9
    assert emitted[0]["payouts_reconciled"] == 3
