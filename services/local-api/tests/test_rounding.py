"""Golden tests for deterministic integer rounding."""

from __future__ import annotations

import pytest

from laura.timebase.rounding import Rounding, div_round


@pytest.mark.parametrize(
    ("num", "den", "mode", "expected"),
    [
        # exact
        (10, 2, Rounding.HALF_EVEN, 5),
        (-10, 2, Rounding.HALF_EVEN, -5),
        # floor / ceil
        (7, 2, Rounding.FLOOR, 3),
        (7, 2, Rounding.CEIL, 4),
        (-7, 2, Rounding.FLOOR, -4),
        (-7, 2, Rounding.CEIL, -3),
        # non-tie rounding (1/3, 2/3)
        (1, 3, Rounding.HALF_EVEN, 0),
        (2, 3, Rounding.HALF_EVEN, 1),
        # ties: 0.5
        (1, 2, Rounding.HALF_UP, 1),
        (1, 2, Rounding.HALF_DOWN, 0),
        (1, 2, Rounding.HALF_EVEN, 0),
        # ties: 1.5
        (3, 2, Rounding.HALF_UP, 2),
        (3, 2, Rounding.HALF_DOWN, 1),
        (3, 2, Rounding.HALF_EVEN, 2),
        # ties: 2.5 (even check)
        (5, 2, Rounding.HALF_EVEN, 2),
        # negative ties: -0.5
        (-1, 2, Rounding.HALF_UP, -1),
        (-1, 2, Rounding.HALF_DOWN, 0),
        (-1, 2, Rounding.HALF_EVEN, 0),
        # negative ties: -1.5
        (-3, 2, Rounding.HALF_UP, -2),
        (-3, 2, Rounding.HALF_DOWN, -1),
        (-3, 2, Rounding.HALF_EVEN, -2),
        # negative denominator normalises
        (3, -2, Rounding.FLOOR, -2),
        (3, -2, Rounding.CEIL, -1),
    ],
)
def test_div_round(num: int, den: int, mode: Rounding, expected: int) -> None:
    assert div_round(num, den, mode) == expected


def test_div_round_zero_denominator() -> None:
    with pytest.raises(ZeroDivisionError):
        div_round(1, 0)
