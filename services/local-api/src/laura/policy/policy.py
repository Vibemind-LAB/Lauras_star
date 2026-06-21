"""Policy model, parser, serialiser, and precedence resolver.

Grammar
-------
  raw       ::= "auto" | "human" | "threshold:" float
  float     ::= 0..1 inclusive

All comparisons are whitespace-stripped and case-insensitive on the mode word.
Invalid input raises ``ValueError`` with a descriptive message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "Policy",
    "ResolvedPolicy",
    "parse_policy",
    "policy_to_str",
    "resolve_policy",
]

_VALID_MODES = frozenset({"auto", "human", "threshold"})


@dataclass(frozen=True)
class Policy:
    """Immutable policy value.

    - ``mode="auto"``      — system decides automatically.
    - ``mode="human"``     — always requires human review.
    - ``mode="threshold"`` — auto when confidence >= threshold, else human.
      ``threshold`` must be set (0.0 ≤ value ≤ 1.0) for this mode.
    """

    mode: Literal["auto", "human", "threshold"]
    threshold: float | None = None


@dataclass(frozen=True)
class ResolvedPolicy:
    """A ``Policy`` together with the name of the precedence tier that won."""

    policy: Policy
    source: Literal["row", "pattern", "env", "default"]


# ---------------------------------------------------------------------------
# parse_policy
# ---------------------------------------------------------------------------


def parse_policy(raw: str) -> Policy:
    """Parse a policy string into a :class:`Policy`.

    Valid forms (case-insensitive, outer whitespace stripped):

    * ``"auto"``
    * ``"human"``
    * ``"threshold:0.8"`` — float in [0, 1].

    Raises
    ------
    ValueError
        On any invalid input: unknown mode, ``threshold`` without a value,
        threshold value out of range, or unparseable float.
    """
    text = raw.strip()
    if not text:
        raise ValueError(f"Policy string must not be empty; got {raw!r}")

    if ":" in text:
        mode_part, _, value_part = text.partition(":")
        mode = mode_part.strip().lower()
        if mode != "threshold":
            raise ValueError(
                f"Only 'threshold' mode accepts a ':' separator; got mode {mode_part!r}"
            )
        value_part = value_part.strip()
        if not value_part:
            raise ValueError(
                "threshold mode requires a float value after the colon, e.g. 'threshold:0.8'"
            )
        try:
            value = float(value_part)
        except ValueError:
            raise ValueError(
                f"threshold value must be a float; got {value_part!r}"
            ) from None
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                f"threshold value must be in [0, 1]; got {value}"
            )
        return Policy(mode="threshold", threshold=value)

    mode = text.lower()
    if mode == "auto":
        return Policy(mode="auto")
    if mode == "human":
        return Policy(mode="human")
    if mode == "threshold":
        raise ValueError(
            "threshold mode requires a float value, e.g. 'threshold:0.8'"
        )
    raise ValueError(
        f"Unknown policy mode {text!r}; valid modes are: auto, human, threshold:<0..1>"
    )


# ---------------------------------------------------------------------------
# policy_to_str
# ---------------------------------------------------------------------------


def policy_to_str(p: Policy) -> str:
    """Serialise a :class:`Policy` to its canonical string form.

    Guaranteed to round-trip through :func:`parse_policy`.
    """
    if p.mode == "auto":
        return "auto"
    if p.mode == "human":
        return "human"
    # threshold
    if p.threshold is None:
        raise ValueError("Policy with mode='threshold' must have a threshold value set")
    return f"threshold:{p.threshold:.2f}"


# ---------------------------------------------------------------------------
# resolve_policy
# ---------------------------------------------------------------------------


def resolve_policy(
    *,
    row: str | None = None,
    pattern: str | None = None,
    env: str | None = None,
    default: str = "auto",
) -> ResolvedPolicy:
    """Resolve a policy from the precedence chain: row > pattern > env > default.

    Each tier is skipped if the value is ``None`` or an empty string.
    A present-but-unparseable value raises :class:`ValueError` immediately
    (no silent fall-through).

    Parameters
    ----------
    row:
        Policy string from a per-asset CSV row (highest precedence).
    pattern:
        Policy string from a source-path glob/pattern rule.
    env:
        Policy string from an environment variable.
    default:
        Fallback policy string (lowest precedence; defaults to ``"auto"``).

    Returns
    -------
    ResolvedPolicy
        The winning policy and the name of the tier that provided it.
    """
    tiers: list[tuple[str | None, Literal["row", "pattern", "env", "default"]]] = [
        (row, "row"),
        (pattern, "pattern"),
        (env, "env"),
        (default, "default"),
    ]
    for value, source in tiers:
        if value is None or value == "":
            continue
        return ResolvedPolicy(policy=parse_policy(value), source=source)

    # Should be unreachable because `default` always has a value, but be safe.
    return ResolvedPolicy(policy=Policy(mode="auto"), source="default")
