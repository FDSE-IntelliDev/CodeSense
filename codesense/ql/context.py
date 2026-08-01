"""Evaluation context: every external dependency an operator needs.

Everything is injected through the constructor. Nothing here reads config,
opens a database, or touches global state, which keeps operators testable
against in-memory implementations and lets storage change without touching
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codesense.ql.fields import DEFAULT_FIELD_WEIGHTS, FieldWeights
from codesense.ql.judge import Judge, NullJudge
from codesense.ql.store.base import EdgeStore, ExpansionTable, PostingIndex, SymbolStore

__all__ = ["EvalContext"]


@dataclass(frozen=True, slots=True)
class EvalContext:
    """Bundles index access and scoring parameters for the operators."""

    symbols: SymbolStore
    postings: PostingIndex
    expansion: ExpansionTable
    edges: EdgeStore
    field_weights: FieldWeights = field(default=DEFAULT_FIELD_WEIGHTS)

    #: Intent judge. Defaults to a null implementation that decides nothing,
    #: rather than `None`, so `intent`'s degradation path is genuinely
    #: exercised in environments without an LLM -- a bug there surfaces in
    #: tests instead of in production.
    judge: Judge = field(default_factory=NullJudge)

    #: Floor on normalised ICF. Below it a term contributes nothing --
    #: `get`, on 207 of 1718 symbols, is "similar" to everything and
    #: expanding it only adds noise. 0.34 corresponds to a raw ICF of about
    #: 2.5 on the sample project, where log(1718) is about 7.45.
    icf_floor: float = 0.34

    #: Minimum score for a single piece of evidence. Anything below is
    #: dropped so the evidence trail does not drown in noise.
    min_hit_score: float = 1e-6
