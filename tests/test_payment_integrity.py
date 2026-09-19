from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from payfund_app.modules.payments.application.integrity import (
    PaymentIntegrityGap,
    PaymentIntegrityUseCases,
)
from payfund_app.modules.payments.infra.integrity import SqlPaymentIntegrityRepository


def test_audit_is_bounded_and_reports_more_rows():
    ids = [uuid4(), uuid4(), uuid4()]

    class Repository:
        def find_gaps(self, limit):
            assert limit == 3
            return [PaymentIntegrityGap(value, True, False) for value in ids]

    report = PaymentIntegrityUseCases(Repository()).audit(limit=2)
    assert [row.payment_intent_id for row in report.gaps] == ids[:2]
    assert report.has_more
    assert report.has_gaps


def test_sql_audit_only_reads_confirmed_intents_and_both_effects():
    class Session:
        def execute(self, statement):
            sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            assert "succeeded" in sql
            assert "partially_refunded" in sql
            assert "refunded" in sql
            assert "capture" in sql
            assert "payment.succeeded" in sql
            assert "EXISTS" in sql
            assert "LIMIT 2" in sql
            return [SimpleNamespace(id=uuid4(), missing_capture=True, missing_callback=False)]

    gaps = SqlPaymentIntegrityRepository(Session()).find_gaps(2)
    assert len(gaps) == 1
    assert gaps[0].missing_capture and not gaps[0].missing_callback
