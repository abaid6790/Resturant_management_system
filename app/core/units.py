"""
Unit conversion. Two layers:
  * Global units (g, kg, ml, l, piece, dozen...) convert within their kind via a fixed factor to
    that kind's base unit (gram / millilitre / piece).
  * Item-specific packaging units (e.g. "Box" = 24 pieces for THIS ingredient) live on the item
    itself (ItemPackagingUnit) because the same word means different quantities for different items.
Stock is always held, and every ledger entry always recorded, in the item's base stock unit.
"""
from __future__ import annotations

from app.core.errors import BusinessRuleError

WEIGHT, VOLUME, COUNT = "weight", "volume", "count"
BASE_UNIT_CODE = {WEIGHT: "g", VOLUME: "ml", COUNT: "piece"}

STANDARD_UNITS = [
    # code, name, kind, factor to that kind's base unit
    ("g", "Gram", WEIGHT, "1"),
    ("kg", "Kilogram", WEIGHT, "1000"),
    ("mg", "Milligram", WEIGHT, "0.001"),
    ("ml", "Millilitre", VOLUME, "1"),
    ("l", "Litre", VOLUME, "1000"),
    ("piece", "Piece", COUNT, "1"),
    ("dozen", "Dozen", COUNT, "12"),
]


def convert(qty, from_unit, to_unit):
    """Convert between two GLOBAL units of the same kind. Raises BusinessRuleError otherwise."""
    from app.core.money import D
    from app.core.money import qty as qround

    if from_unit.kind != to_unit.kind:
        raise BusinessRuleError(
            f"Cannot convert {from_unit.code} to {to_unit.code}: different kinds of measurement."
        )
    base = D(qty) * D(from_unit.factor_to_base)
    return qround(base / D(to_unit.factor_to_base))
