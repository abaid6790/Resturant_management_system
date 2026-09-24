"""
Exact decimal arithmetic for money, quantities and costs.

Rules (see docs/DECISIONS.md ADR-003):
  * Floats are rejected everywhere. Inputs must be Decimal, int or str.
  * Rounding is ROUND_HALF_UP (what customers expect on a receipt).
  * Each monetary line amount is rounded to MONEY_DP; documents total the ROUNDED lines,
    so a printed receipt always adds up.
  * Splits/allocations use largest-remainder so parts always sum to the total exactly.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import (
    ROUND_FLOOR,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
)

MONEY_DP = 2  # prices, payments, taxes, discounts, totals
QTY_DP = 4  # stock quantities (base units, e.g. grams -> 4dp is ample)
COST_DP = 6  # unit costs, e.g. 0.008500 per gram
RATE_DP = 4  # tax / discount percentages, e.g. 16.0000

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def D(value) -> Decimal:
    """Convert to Decimal safely. Floats and bools are refused on purpose."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(
            f"{type(value).__name__} is not allowed for exact values; use str/int/Decimal"
        )
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal value: {value!r}") from exc
    else:
        raise TypeError(f"Unsupported type for Decimal conversion: {type(value).__name__}")
    if not d.is_finite():
        raise ValueError("NaN/Infinity are not valid amounts")
    return d


def quantize(value, dp: int, rounding=ROUND_HALF_UP) -> Decimal:
    return D(value).quantize(Decimal(1).scaleb(-dp), rounding=rounding)


def money(value) -> Decimal:
    return quantize(value, MONEY_DP)


def qty(value) -> Decimal:
    return quantize(value, QTY_DP)


def unit_cost(value) -> Decimal:
    return quantize(value, COST_DP)


def rate(value) -> Decimal:
    return quantize(value, RATE_DP)


def percent_of(amount, percent) -> Decimal:
    """`percent`% of `amount`, rounded to money."""
    return money(D(amount) * D(percent) / HUNDRED)


def line_total(unit_price, quantity) -> Decimal:
    """Price x quantity, rounded once at line level."""
    return money(D(unit_price) * D(quantity))


def tax_exclusive_to_inclusive(net, tax_rate) -> Decimal:
    return money(D(net) + percent_of(net, tax_rate))


def tax_from_inclusive(gross, tax_rate) -> Decimal:
    """Tax portion contained in a tax-inclusive price."""
    g, r = D(gross), D(tax_rate)
    return money(g - g / (Decimal(1) + r / HUNDRED))


def allocate(total, weights: Sequence, dp: int = MONEY_DP) -> list[Decimal]:
    """
    Split `total` across `weights` proportionally so the parts sum to `total` EXACTLY
    (largest-remainder method). Used for split bills, order-level discount spread over lines,
    and proportional tax/service-charge distribution.
    """
    if not weights:
        raise ValueError("weights must not be empty")
    w = [D(x) for x in weights]
    if any(x < 0 for x in w):
        raise ValueError("weights must be non-negative")
    w_sum = sum(w, ZERO)
    if w_sum == 0:
        raise ValueError("weights must not all be zero")

    unit = Decimal(1).scaleb(-dp)
    t = quantize(total, dp)
    sign = -1 if t < 0 else 1
    total_units = abs(t / unit).to_integral_value()

    raw = [total_units * x / w_sum for x in w]
    floors = [r.to_integral_value(rounding=ROUND_FLOOR) for r in raw]
    leftover = int(total_units - sum(floors, ZERO))
    order = sorted(range(len(w)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[:leftover]:
        floors[i] += 1
    return [(f * unit * sign).quantize(unit) for f in floors]


def split_evenly(total, parts: int, dp: int = MONEY_DP) -> list[Decimal]:
    if parts < 1:
        raise ValueError("parts must be >= 1")
    return allocate(total, [1] * parts, dp)


def cash_round(amount, increment="0.05") -> tuple[Decimal, Decimal]:
    """
    Round a CASH total to the nearest `increment` (half up). Returns (rounded, adjustment)
    where adjustment = rounded - amount, so it can be recorded as its own ledger line.
    """
    a, inc = money(amount), D(increment)
    if inc <= 0:
        raise ValueError("increment must be positive")
    rounded = ((a / inc).quantize(Decimal(1), rounding=ROUND_HALF_UP) * inc).quantize(
        Decimal(1).scaleb(-MONEY_DP)
    )
    return rounded, rounded - a
