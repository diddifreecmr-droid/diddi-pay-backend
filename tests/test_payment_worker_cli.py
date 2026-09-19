from contextlib import nullcontext
from importlib import import_module
from types import SimpleNamespace


def test_maintenance_command_reports_dead_letters(monkeypatch, capsys):
    ops = import_module("payfund_app.ops.__main__")
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
        ops, "payment_event_delivery_status",
        lambda _: {"pending": 0, "delivering": 0, "delivered": 1, "dead_letter": 1},
    )
    assert ops.main(["maintain-payment-intents"]) == 2
    assert "dead_letter=1" in capsys.readouterr().out
