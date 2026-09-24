from decimal import Decimal

import pytest

from app.core.money import (
    D,
    allocate,
    cash_round,
    line_total,
    money,
    percent_of,
    qty,
    split_evenly,
    tax_from_inclusive,
    unit_cost,
)


def test_floats_and_bools_rejected():
    for bad in (1.5, True):
        with pytest.raises(TypeError):
            D(bad)


def test_invalid_strings_and_nan_rejected():
    for bad in ("abc", "NaN", "Infinity", ""):
        with pytest.raises(ValueError):
            D(bad)


def test_no_binary_float_error():
    # 0.1 + 0.2 != 0.3 in floats; must be exact here.
    assert money("0.1") + money("0.2") == money("0.3")


def test_half_up_rounding():
    assert money("2.675") == Decimal("2.68")   # float would give 2.67
    assert money("2.665") == Decimal("2.67")
    assert money("-2.675") == Decimal("-2.68")


def test_precisions():
    assert qty("0.15") == Decimal("0.1500")
    assert unit_cost("0.0085") == Decimal("0.008500")


def test_percent_and_line_total():
    assert percent_of("199.99", "16") == Decimal("32.00")
    assert line_total("3.33", "3") == Decimal("9.99")


def test_tax_from_inclusive():
    assert tax_from_inclusive("116.00", "16") == Decimal("16.00")


def test_allocate_sums_exactly():
    parts = split_evenly("100.00", 3)
    assert parts == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum(parts) == Decimal("100.00")


def test_allocate_weighted_and_negative():
    parts = allocate("10.00", ["1", "2", "3"])
    assert sum(parts) == Decimal("10.00")
    assert parts == [Decimal("1.67"), Decimal("3.33"), Decimal("5.00")]
    neg = allocate("-10.00", [1, 1, 1])
    assert sum(neg) == Decimal("-10.00")


def test_allocate_zero_weight_line_gets_nothing():
    assert allocate("5.00", [0, 1]) == [Decimal("0.00"), Decimal("5.00")]


def test_allocate_validation():
    for weights in ([], [0, 0], [-1, 2]):
        with pytest.raises(ValueError):
            allocate("1.00", weights)


def test_allocate_property_many_cases():
    import itertools
    for total, n in itertools.product(["0.01", "0.02", "99.99", "1234.57"], range(1, 9)):
        assert sum(split_evenly(total, n)) == Decimal(total)


def test_cash_round():
    assert cash_round("10.02") == (Decimal("10.00"), Decimal("-0.02"))
    assert cash_round("10.03") == (Decimal("10.05"), Decimal("0.02"))
    assert cash_round("10.025", "0.05")[0] == Decimal("10.05")


def test_recipe_scenario_exact():
    # 20 products x 150 g = exactly 3000 g
    assert qty("150") * 20 == Decimal("3000.0000")
