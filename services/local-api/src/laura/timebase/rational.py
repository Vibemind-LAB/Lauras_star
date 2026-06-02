"""RationalTime — an integer value measured at a rational rate.

Canonical time state is always integer (a frame index or sample index) at a rate
expressed as ``rate_num / rate_den`` units per second. No float is ever stored
(ADR-0005). Wall-clock seconds == ``value * rate_den / rate_num``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rounding import Rounding, div_round


@dataclass(frozen=True, order=False)
class RationalTime:
    """A time value of ``value`` units at ``rate_num/rate_den`` units per second."""

    value: int
    rate_num: int
    rate_den: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool):
            raise TypeError("RationalTime.value must be an int")
        if self.rate_num <= 0 or self.rate_den <= 0:
            raise ValueError("rate_num and rate_den must be positive")

    # --- conversions -------------------------------------------------------
    def rescale_to(
        self, rate_num: int, rate_den: int, rounding: Rounding = Rounding.HALF_EVEN
    ) -> RationalTime:
        """Return this instant expressed at a new rate, rounded with ``rounding``."""
        if rate_num <= 0 or rate_den <= 0:
            raise ValueError("target rate must be positive")
        # new = value * (rate_num/rate_den) / (self.rate_num/self.rate_den)
        num = self.value * self.rate_den * rate_num
        den = self.rate_num * rate_den
        return RationalTime(div_round(num, den, rounding), rate_num, rate_den)

    def to_seconds(self) -> float:
        """Float seconds — for DISPLAY/diagnostics only, never as stored state."""
        return self.value * self.rate_den / self.rate_num

    # --- arithmetic (same-rate only; rescale explicitly otherwise) ---------
    def _require_same_rate(self, other: RationalTime) -> None:
        if (self.rate_num, self.rate_den) != (other.rate_num, other.rate_den):
            raise ValueError("RationalTime arithmetic requires equal rates; rescale first")

    def __add__(self, other: RationalTime) -> RationalTime:
        self._require_same_rate(other)
        return RationalTime(self.value + other.value, self.rate_num, self.rate_den)

    def __sub__(self, other: RationalTime) -> RationalTime:
        self._require_same_rate(other)
        return RationalTime(self.value - other.value, self.rate_num, self.rate_den)

    # --- ordering across rates (exact, via cross multiplication) -----------
    def _key(self, other: RationalTime) -> tuple[int, int]:
        # seconds_self = value*den/num ; compare a<b via a.val*a.den*b.num ? b.val*b.den*a.num
        return (
            self.value * self.rate_den * other.rate_num,
            other.value * other.rate_den * self.rate_num,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RationalTime):
            return NotImplemented
        a, b = self._key(other)
        return a == b

    def __lt__(self, other: RationalTime) -> bool:
        a, b = self._key(other)
        return a < b

    def __le__(self, other: RationalTime) -> bool:
        a, b = self._key(other)
        return a <= b

    def __gt__(self, other: RationalTime) -> bool:
        a, b = self._key(other)
        return a > b

    def __ge__(self, other: RationalTime) -> bool:
        a, b = self._key(other)
        return a >= b

    def __hash__(self) -> int:
        # Hash by reduced rational seconds so equal instants at different rates collide.
        from math import gcd

        n = self.value * self.rate_den
        d = self.rate_num
        g = gcd(n, d) or 1
        return hash((n // g, d // g))
