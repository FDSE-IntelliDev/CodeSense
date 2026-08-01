"""Query units.

**A unit is a semantic slot, not a keyword and not a regex.** It is what
every later condition attaches to -- `hop` and `intent` both hang off units
-- so a unit's identity has to survive all the way to the end rather than
being flattened once matching finishes.

Design: ``docs/design/04-query-unit.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["QueryUnit", "Term"]


@dataclass(frozen=True, slots=True)
class Term:
    """One term of a unit, carrying where it came from and why.

    The ``source`` distinction has real consequences:

        literal   appears in the query itself; most trustworthy, but often
                  the rarest in actual code
        synonym   semantically interchangeable
        derived   association (`performance` -> `buffer`); **a hit on one of
                  these alone proves nothing**

    Derived terms lift recall a lot and hurt precision noticeably, so they
    must either combine with other signals in the same unit, be anchored by
    a graph constraint, or be reviewed by `intent`.
    """

    value: str
    source: str = "literal"
    weight: float = 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Term.value must not be empty")


@dataclass(frozen=True, slots=True)
class QueryUnit:
    """A semantic slot that several kinds of signal can satisfy.

    ``concept`` is prose for humans and for the `intent` operator; it takes
    no part in matching. ``satisfiers`` holds `codesense.ql.satisfiers`
    instances -- the type is not referenced here to avoid a circular import.
    """

    name: str
    concept: str = ""
    satisfiers: tuple[object, ...] = ()
    combine: str = "noisy_or"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("QueryUnit.name must not be empty")


@dataclass(frozen=True, slots=True)
class UnitScore:
    """A symbol's final score for one unit, and what it was made of."""

    unit: str
    score: float
    parts: tuple[float, ...] = field(default_factory=tuple)
