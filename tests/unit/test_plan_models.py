"""查询计划数据模型的测试。

这些 dataclass 是 planner 和 executor 之间的契约：planner 产出、
序列化成 *_semql.json、executor 读回来。所以要钉住两件事——
序列化结果是纯 JSON 可写的，以及计划对象不可被中途篡改。
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from codesense.query.plan_models import (
    PLAN_VERSION,
    IntentionPlan,
    QueryPlanBundle,
    RelationPlan,
    SurfacePlan,
    SurfaceTerm,
)

PLAN_CLASSES = [SurfacePlan, RelationPlan, IntentionPlan]


@pytest.mark.parametrize("plan_cls", PLAN_CLASSES)
def test_空计划可以直接构造(plan_cls):
    """所有字段都要有默认值，否则 planner 少填一项就炸。"""
    plan = plan_cls()

    assert plan.version == PLAN_VERSION


@pytest.mark.parametrize("plan_cls", PLAN_CLASSES)
def test_计划能序列化成纯_JSON(plan_cls):
    payload = plan_cls().to_dict()

    # 能被 json.dumps 吞下去，才能落成 *_semql.json
    json.dumps(payload)
    assert payload["version"] == PLAN_VERSION
    assert payload["kind"] in {"surface", "relation", "intention"}


@pytest.mark.parametrize("plan_cls", PLAN_CLASSES)
def test_三类计划的_kind_各不相同(plan_cls):
    kinds = {cls().to_dict()["kind"] for cls in PLAN_CLASSES}

    assert len(kinds) == len(PLAN_CLASSES)


def test_嵌套结构会被完整展开成_dict():
    plan = SurfacePlan(
        raw_query="find login handler",
        conditions=[],
    )
    payload = plan.to_dict()

    assert payload["raw_query"] == "find login handler"
    assert isinstance(payload["conditions"], list)
    assert isinstance(payload["condition_expression"], dict)


def test_计划对象不可变():
    """executor 拿到计划后不该能改它——要改就用 dataclasses.replace 造新的。"""
    term = SurfaceTerm(value="login", source="keyword")

    with pytest.raises(dataclasses.FrozenInstanceError):
        term.value = "logout"  # type: ignore[misc]


def test_每个实例有独立的默认列表():
    """default_factory 用错会让两个计划共享同一个 list。"""
    a, b = SurfacePlan(), SurfacePlan()

    assert a.conditions == b.conditions == []
    assert a.conditions is not b.conditions


def test_manifest_只记录子计划位置不内联内容():
    bundle = QueryPlanBundle(
        surface=SurfacePlan(),
        relation=RelationPlan(),
        intention=IntentionPlan(),
    )

    manifest = bundle.manifest(raw_query="find login handler")

    assert manifest["version"] == PLAN_VERSION
    assert manifest["raw_query"] == "find login handler"
    assert manifest["plans"] == {
        "surface": "surface_semql.json",
        "relation": "relation_semql.json",
        "intention": "intention_semql.json",
    }
    json.dumps(manifest)


def test_manifest_可以指定文件名():
    bundle = QueryPlanBundle(
        surface=SurfacePlan(), relation=RelationPlan(), intention=IntentionPlan()
    )

    manifest = bundle.manifest(raw_query=None, surface_path="s.json")

    assert manifest["plans"]["surface"] == "s.json"
    assert manifest["plans"]["relation"] == "relation_semql.json"
