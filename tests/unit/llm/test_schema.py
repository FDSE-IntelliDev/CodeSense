"""Strict contracts for the semantic query-understanding boundary."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from codesense.llm.schema import (
    EndpointKind,
    QueryUnderstandingResult,
    TargetKind,
    query_understanding_response_format,
)


def payload() -> dict[str, object]:
    return {
        "units": [
            {
                "name": "page_request",
                "concept": "the PageRequest type",
                "query_terms": ["PageRequest"],
                "terms": [
                    {
                        "value": "PageRequest",
                        "source": "literal",
                        "weight": 1.0,
                        "related_query_terms": ["PageRequest"],
                        "reason": "named by the query",
                    }
                ],
            }
        ],
        "relations": [
            {
                "source": {"kind": "result", "unit": None},
                "target": {"kind": "unit", "unit": "page_request"},
                "edges": ["references"],
            }
        ],
        "targets": ["file"],
        "annotations": [],
        "criterion": "A file whose code references PageRequest",
    }


def test_valid_payload_parses_to_typed_model() -> None:
    understood = QueryUnderstandingResult.model_validate(payload())

    assert understood.targets == [TargetKind.FILE]
    assert understood.relations[0].source.kind is EndpointKind.RESULT


def test_response_format_is_strict_json_schema() -> None:
    response_format = query_understanding_response_format()

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True  # type: ignore[index]
    schema = response_format["json_schema"]["schema"]  # type: ignore[index]
    assert schema["additionalProperties"] is False  # type: ignore[index]


def test_duplicate_targets_and_terms_keep_the_first_occurrence() -> None:
    raw = payload()
    raw["targets"] = ["file", "method", "file"]
    second_unit = deepcopy(raw["units"][0])  # type: ignore[index]
    second_unit["name"] = "duplicate"  # type: ignore[index]
    second_unit["terms"][0]["value"] = "pagerequest"  # type: ignore[index]
    raw["units"].append(second_unit)  # type: ignore[union-attr]

    with pytest.raises(ValidationError, match="no unique terms"):
        QueryUnderstandingResult.model_validate(raw)

    raw["units"] = raw["units"][:1]  # type: ignore[index]
    understood = QueryUnderstandingResult.model_validate(raw)
    assert understood.targets == [TargetKind.FILE, TargetKind.METHOD]


def test_duplicate_term_is_removed_when_the_unit_keeps_another_term() -> None:
    raw = payload()
    second_unit = deepcopy(raw["units"][0])  # type: ignore[index]
    second_unit["name"] = "pagination"  # type: ignore[index]
    second_unit["terms"][0]["value"] = "pagerequest"  # type: ignore[index]
    second_unit["terms"].append(  # type: ignore[index]
        {
            "value": "pagination",
            "source": "synonym",
            "weight": 0.8,
            "related_query_terms": ["PageRequest"],
            "reason": "semantic equivalent",
        }
    )
    raw["units"].append(second_unit)  # type: ignore[union-attr]

    understood = QueryUnderstandingResult.model_validate(raw)

    assert [term.value for term in understood.units[1].terms] == ["pagination"]


def _invalid(mutator: object) -> dict[str, object]:
    raw = payload()
    mutator(raw)  # type: ignore[operator]
    return raw


@pytest.mark.parametrize(
    "raw",
    [
        _invalid(lambda value: value["units"][0].update({"extra": True})),
        _invalid(lambda value: value["units"][0].pop("concept")),
        _invalid(lambda value: value["units"][0]["terms"][0].update({"source": "guess"})),
        _invalid(lambda value: value.update({"targets": ["package"]})),
        _invalid(lambda value: value["relations"][0].update({"edges": ["inherits"]})),
        _invalid(lambda value: value["units"][0]["terms"][0].update({"weight": 1.1})),
        _invalid(lambda value: value["relations"][0]["target"].update({"unit": "missing"})),
        _invalid(
            lambda value: value["relations"][0].update({"target": {"kind": "result", "unit": None}})
        ),
        _invalid(
            lambda value: value["relations"][0].update(
                {"source": {"kind": "unit", "unit": "page_request"}}
            )
        ),
        _invalid(
            lambda value: value.update(
                {
                    "relations": [
                        value["relations"][0],
                        {
                            "source": {"kind": "unit", "unit": "page_request"},
                            "target": {"kind": "result", "unit": None},
                            "edges": ["imports"],
                        },
                    ]
                }
            )
        ),
    ],
)
def test_contract_ambiguities_are_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        QueryUnderstandingResult.model_validate(raw)
