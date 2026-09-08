"""Deciding how many units a set of terms forms, from statistics -- **not by
asking the LLM**.

This follows a query optimiser's division of labour: it does not ask the
user how to join, it consults statistics. "How many units should these terms
form" is likewise a statistical question rather than a semantic one, and the
criterion -- **do these terms land on the same symbols** -- is answered by
the inverted index.

Measured (``docs/design/06-script-and-execution.md``): having the LLM split
netty's zero-copy query semantically into zero-copy, user-space memory and
performance took R@100 from 65% down to 21%. The statistics said the mean
pairwise Jaccard was 0.011 -- these terms describe facets of one thing and
belonged in a single unit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from codesense.ql.context import EvalContext

__all__ = ["OVERLAP_FLOOR", "Cluster", "partition"]

#: Jaccard floor for two terms to count as belonging to one unit.
#:
#: Deliberately high: **splitting wrongly costs far more than not splitting**.
#: Not splitting yields at worst one broad unit, which still recalls and only
#: ranks less well; splitting wrongly scatters the answer across units where
#: weighting then dilutes it.
OVERLAP_FLOOR = 0.08

#: A term above this share of postings is excluded from clustering: it
#: overlaps with everything and would glue separate clusters into one.
HUB_RATIO = 0.25

#: Maximum number of units. More than this means the clustering found no
#: structure, and not splitting is better.
MAX_UNITS = 3


@dataclass(frozen=True, slots=True)
class Cluster:
    """Terms that belong in one unit."""

    terms: tuple[str, ...]
    reason: str = ""


def partition(terms: Sequence[str], ctx: EvalContext) -> list[Cluster]:
    """Group terms by posting overlap.

    **Defaults to not splitting**: it splits only when the clustering found
    real structure -- more than one cluster, none of them a lone term --
    and otherwise returns a single cluster.
    """
    usable = [term for term in terms if ctx.postings.term_info(term) is not None]
    if len(usable) < 4:
        return [Cluster(tuple(usable), "too few terms to split")]

    total = max(ctx.population, 1)
    postings = {term: {p.symbol_id for p in ctx.postings.lookup(term)} for term in usable}
    hubs = {term for term, ids in postings.items() if len(ids) > total * HUB_RATIO}

    groups = _connected([term for term in usable if term not in hubs], postings, OVERLAP_FLOOR)
    solid = [group for group in groups if len(group) >= 2]
    if len(solid) < 2 or len(solid) > MAX_UNITS:
        return [
            Cluster(
                tuple(usable),
                f"clustering found no structure ({len(solid)} formed clusters); "
                "not splitting, since splitting wrongly costs more",
            )
        ]

    loose = [term for group in groups if len(group) < 2 for term in group] + sorted(hubs)
    clusters = [
        Cluster(tuple(sorted(group)), f"{len(group)} mutually overlapping terms") for group in solid
    ]
    if loose:
        # Fold stray terms into the largest cluster: a unit of their own would
        # only be diluted by weighting
        biggest = max(range(len(clusters)), key=lambda i: len(clusters[i].terms))
        merged = tuple(sorted({*clusters[biggest].terms, *loose}))
        clusters[biggest] = Cluster(merged, clusters[biggest].reason + ", plus stray terms")
    return clusters


def _connected(
    terms: Sequence[str], postings: dict[str, set[int]], floor: float
) -> list[list[str]]:
    """Connect terms whose overlap clears the floor, then take components.

    Components rather than something like k-means: the **number** of clusters
    is itself what has to be inferred, not supplied as a parameter.
    """
    parent = {term: term for term in terms}

    def find(term: str) -> str:
        while parent[term] != term:
            parent[term] = parent[parent[term]]
            term = parent[term]
        return term

    for index, left in enumerate(terms):
        for right in terms[index + 1 :]:
            if _jaccard(postings[left], postings[right]) >= floor:
                parent[find(left)] = find(right)

    groups: dict[str, list[str]] = {}
    for term in terms:
        groups.setdefault(find(term), []).append(term)
    return list(groups.values())


def _jaccard(left: set[int], right: set[int]) -> float:
    union = len(left | right)
    return len(left & right) / union if union else 0.0
