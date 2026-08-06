"""Small deterministic vocabulary plan constrained by a memory budget."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VocabularyPlan:
    general: tuple[str, ...]
    project: tuple[str, ...]
    estimated_bytes: int

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.general + self.project))


def plan_vocabulary(
    *,
    general: Sequence[str],
    project_df: Mapping[str, int],
    targets: Collection[str],
    vector_size: int,
    memory_budget_mb: int,
    min_df: int = 2,
) -> VocabularyPlan:
    """Keep general/target terms and prune low-frequency project context."""
    general_terms = tuple(sorted(set(general)))
    target_terms = {term for term in targets if term in project_df}
    optional = {
        term for term, df in project_df.items() if df >= min_df and term not in target_terms
    }
    budget = memory_budget_mb * 1024 * 1024

    def estimate(project: Collection[str]) -> int:
        rows = len(set(general_terms) | set(project))
        return int(rows * vector_size * 4 * 3 * 1.35)

    if estimate(target_terms) > budget:
        raise MemoryError("protected vocabulary exceeds memory budget")
    for term in sorted(optional, key=lambda value: (project_df[value], value)):
        if estimate(target_terms | optional) <= budget:
            break
        optional.remove(term)
    project_terms = tuple(sorted(target_terms | optional))
    return VocabularyPlan(general_terms, project_terms, estimate(project_terms))
