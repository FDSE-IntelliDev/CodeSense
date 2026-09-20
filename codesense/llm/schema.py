"""Strict semantic query IR exchanged with an LLM provider."""

from __future__ import annotations

from enum import Enum
from functools import cache

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "EndpointKind",
    "QueryRelation",
    "QueryUnderstandingResult",
    "RelationEndpoint",
    "RelationKind",
    "SemanticTerm",
    "SemanticUnit",
    "TargetKind",
    "TermSource",
    "query_understanding_response_format",
]


class _StrictModel(BaseModel):
    """Reject provider fields the query compiler does not understand."""

    model_config = ConfigDict(extra="forbid")


class TermSource(str, Enum):
    LITERAL = "literal"
    SYNONYM = "synonym"
    DERIVED = "derived"


class TargetKind(str, Enum):
    FILE = "file"
    TYPE = "type"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    RECORD = "record"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    FIELD = "field"
    ANNOTATION = "annotation"


class RelationKind(str, Enum):
    CALLS = "calls"
    CONTAINS = "contains"
    REFERENCES = "references"
    IMPORTS = "imports"
    IN_FILE = "in_file"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    OVERRIDES = "overrides"


class EndpointKind(str, Enum):
    UNIT = "unit"
    RESULT = "result"


class SemanticTerm(_StrictModel):
    value: str = Field(min_length=1)
    source: TermSource
    weight: float = Field(ge=0.0, le=1.0)
    related_query_terms: list[str]
    reason: str


class SemanticUnit(_StrictModel):
    name: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    query_terms: list[str]
    terms: list[SemanticTerm] = Field(min_length=1)


class RelationEndpoint(_StrictModel):
    kind: EndpointKind
    unit: str | None

    @model_validator(mode="after")
    def validate_role(self) -> RelationEndpoint:
        """A unit name belongs only to a concrete unit endpoint."""
        if self.kind is EndpointKind.RESULT and self.unit is not None:
            raise ValueError("result endpoint unit must be null")
        if self.kind is EndpointKind.UNIT and not self.unit:
            raise ValueError("unit endpoint must name a unit")
        return self


class QueryRelation(_StrictModel):
    source: RelationEndpoint
    target: RelationEndpoint
    edges: list[RelationKind] = Field(min_length=1)


class QueryUnderstandingResult(_StrictModel):
    units: list[SemanticUnit] = Field(min_length=1)
    relations: list[QueryRelation]
    targets: list[TargetKind]
    annotations: list[str]
    criterion: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_contract(self) -> QueryUnderstandingResult:
        """Validate references and normalize harmless provider duplicates."""
        unit_names = [unit.name for unit in self.units]
        if len(unit_names) != len(set(unit_names)):
            raise ValueError("unit names must be unique")
        known_units = set(unit_names)

        result_relations = 0
        for relation in self.relations:
            source, target = relation.source, relation.target
            for endpoint in (source, target):
                if endpoint.kind is EndpointKind.UNIT and endpoint.unit not in known_units:
                    raise ValueError("unit endpoint must name an existing unit")
            if source.kind is EndpointKind.RESULT or target.kind is EndpointKind.RESULT:
                result_relations += 1
            if source.kind is EndpointKind.RESULT and target.kind is EndpointKind.RESULT:
                raise ValueError("a relation cannot have two result endpoints")
            if (
                source.kind is EndpointKind.UNIT
                and target.kind is EndpointKind.UNIT
                and source.unit == target.unit
            ):
                raise ValueError("a relation cannot connect a unit to itself")
        if result_relations > 1:
            raise ValueError("at most one result-bound relation is supported")

        seen_terms: set[str] = set()
        normalized_units: list[SemanticUnit] = []
        for unit in self.units:
            unique_terms: list[SemanticTerm] = []
            for term in unit.terms:
                key = term.value.casefold()
                if key in seen_terms:
                    continue
                seen_terms.add(key)
                unique_terms.append(term)
            if not unique_terms:
                raise ValueError(f"unit {unit.name!r} has no unique terms")
            normalized_units.append(unit.model_copy(update={"terms": unique_terms}))

        self.units = normalized_units
        self.targets = list(dict.fromkeys(self.targets))
        return self


@cache
def query_understanding_response_format() -> dict[str, object]:
    """Return the provider's strict Structured Outputs declaration."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "query_understanding",
            "strict": True,
            "schema": QueryUnderstandingResult.model_json_schema(),
        },
    }
