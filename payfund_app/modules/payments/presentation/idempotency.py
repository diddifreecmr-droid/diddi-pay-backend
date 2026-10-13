"""Shared Idempotency-Key header validation for the PaymentIntent/Payout/Refund routers.

Regression: a too-long header reached the DB unvalidated and crashed with an unhandled 500
(psycopg.errors.StringDataRightTruncation) instead of a clean 422 -- found via a real
DiddiSend courier top-up request in staging. 128 matches every idempotency_key column
(payment_intents, payouts, refunds all use String(128); see infra/models.py).
"""

from __future__ import annotations

from payfund_app.core.errors import UnprocessableEntity

MAX_IDEMPOTENCY_KEY_LENGTH = 128


def require_idempotency_key(raw: str | None) -> str:
    key = (raw or "").strip()
    if not key:
        raise UnprocessableEntity(
            "L'en-tete Idempotency-Key est obligatoire.", code="IDEMPOTENCY_KEY_REQUIRED"
        )
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise UnprocessableEntity(
            f"L'en-tete Idempotency-Key ne doit pas depasser "
            f"{MAX_IDEMPOTENCY_KEY_LENGTH} caracteres.",
            code="IDEMPOTENCY_KEY_TOO_LONG",
        )
    return key
