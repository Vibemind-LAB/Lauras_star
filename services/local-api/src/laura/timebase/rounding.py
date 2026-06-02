"""Deterministic integer rounding for rational time math.

Every time conversion in Laura goes through integer arithmetic — never float — so
results are reproducible and platform-independent (docs/03-time-model.md, ADR-0005).
This module centralises the rounding policy so each conversion picks an explicit,
tested mode instead of relying on language/CPU float behaviour.
"""

from __future__ import annotations

from enum import StrEnum


class Rounding(StrEnum):
    """Rounding policy for exact integer division `num/den -> int`."""

    FLOOR = "floor"          # toward -inf
    CEIL = "ceil"            # toward +inf
    HALF_UP = "half_up"      # ties away from zero
    HALF_DOWN = "half_down"  # ties toward zero
    HALF_EVEN = "half_even"  # ties to even (banker's) — the default


def div_round(num: int, den: int, mode: Rounding = Rounding.HALF_EVEN) -> int:
    """Return ``num / den`` rounded to an int using ``mode``. Exact, integer-only.

    Works for negative operands. ``den`` may be negative; the sign is normalised.
    """
    if den == 0:
        raise ZeroDivisionError("division by zero in div_round")
    if den < 0:
        num, den = -num, -den
    # den > 0  =>  divmod gives 0 <= r < den and q == floor(num/den)
    q, r = divmod(num, den)
    if r == 0:
        return q
    if mode is Rounding.FLOOR:
        return q
    if mode is Rounding.CEIL:
        return q + 1

    twice = 2 * r
    if twice > den:
        return q + 1
    if twice < den:
        return q

    # Exactly halfway between q and q+1.
    if mode is Rounding.HALF_UP:
        return q + 1 if num > 0 else q
    if mode is Rounding.HALF_DOWN:
        return q if num > 0 else q + 1
    # HALF_EVEN
    return q if q % 2 == 0 else q + 1
