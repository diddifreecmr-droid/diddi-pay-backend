"""Whole-franc money rule (ticket 144).

Every amount handled by DiddiPay and DiddiFund is a whole number of francs, rounded to the
nearest, .5 going up (ROUND_HALF_UP): 2.4 -> 2, 2.5 -> 3, 2.6 -> 3. Use these helpers at every
boundary where a non-integer could enter: API input, provider answers, stored Numeric values.
The database columns are unchanged; rounding happens in the application.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator

_ONE = Decimal("1")


def round_money(value: Any) -> int:
    """Nearest whole franc, .5 up. Accepts int, Decimal, numeric str, float."""
    if isinstance(value, bool):  # bool is an int subclass; never a money amount
        raise ValueError("a boolean is not an amount")
    if isinstance(value, int):
        return value
    try:
        # str() first: Decimal(0.1) would carry the float's binary error.
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not an amount: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"not an amount: {value!r}")
    return int(amount.quantize(_ONE, rounding=ROUND_HALF_UP))


def round_money_from_subunits(value: Any, factor: int) -> int:
    """Provider subunits (e.g. Paystack XOF x100) to whole francs, same rounding."""
    return round_money(Decimal(int(value)) / Decimal(factor))


def _round_input(value: Any) -> Any:
    if value is None:
        return None
    try:
        return round_money(value)
    except ValueError:
        return value  # let pydantic report its usual "valid integer" error


# API field type: accepts 5000, "5000", 5000.4 (-> 5000) or 4999.5 (-> 5000), then validates
# as an int, so Field(gt=0) and the published `integer` schema still apply.
MoneyAmount = Annotated[int, BeforeValidator(_round_input)]
