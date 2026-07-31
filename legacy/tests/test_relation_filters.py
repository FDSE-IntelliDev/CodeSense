"""关系过滤器的契约测试。

它遍历注册表里的**所有**实现——你新加一个 RelationFilter，什么配置都不用做，
下面这些检查立刻覆盖到它，接口写歪了当场红灯。

约定本身写在 `codesense/filters/base.py` 的 RelationFilter docstring 里。
**以后想加一条组内约定，在这里加一条测试**，别只写进文档：
写在文档里的约定要靠记忆维持，写进测试的约定会自己提醒你。

这些测试不碰磁盘、不连数据库——所有实现都能在 graph_store=None 下构造，
这正是「依赖从构造函数传进来」换来的好处。
"""

from __future__ import annotations

import copy

import pytest

from codesense.filters import RELATION_FILTERS, RelationFilter

FILTER_NAMES = RELATION_FILTERS.names()


@pytest.fixture
def candidates() -> list[dict]:
    """几个形状真实的候选，覆盖可调用与不可调用两类。"""
    return [
        {"symbol_id": 1, "name": "login", "type": "method", "file": "A.java"},
        {"symbol_id": 2, "name": "AuthService", "type": "class", "file": "A.java"},
        {"symbol_id": 3, "name": "verify", "type": "function", "file": "B.java"},
        {"symbol_id": 4, "name": "TOKEN", "type": "field", "file": "B.java"},
    ]


def test_注册表非空():
    assert FILTER_NAMES, "一个实现都没注册——多半是 filters/__init__.py 里触发注册的 import 被删了"


@pytest.mark.parametrize("name", FILTER_NAMES)
class TestRelationFilterContract:
    def test_能无参构造(self, name: str) -> None:
        """构造函数的参数必须都有默认值，否则配置里不写 options 就炸。"""
        RELATION_FILTERS.create(name)

    def test_是_RelationFilter_的实现(self, name: str) -> None:
        assert isinstance(RELATION_FILTERS.create(name), RelationFilter)

    def test_name_与注册名一致(self, name: str) -> None:
        assert RELATION_FILTERS.create(name).name == name

    def test_空输入返回空列表(self, name: str) -> None:
        out = RELATION_FILTERS.create(name).apply([])
        assert out == [], "空输入要返回空列表，不要返回 None"

    def test_返回值是_list(self, name: str, candidates: list[dict]) -> None:
        assert isinstance(RELATION_FILTERS.create(name).apply(candidates), list)

    def test_返回输入的子集(self, name: str, candidates: list[dict]) -> None:
        """过滤器只能减，不能增，也不能凭空造出新符号。"""
        out = RELATION_FILTERS.create(name).apply(candidates)
        got = {c["symbol_id"] for c in out}
        assert got <= {c["symbol_id"] for c in candidates}

    def test_确定性(self, name: str, candidates: list[dict]) -> None:
        f = RELATION_FILTERS.create(name)
        assert f.apply(candidates) == f.apply(candidates)

    def test_不修改传入的_candidates(self, name: str, candidates: list[dict]) -> None:
        """原地改会让上游拿到被篡改的数据，而且极难查。"""
        before = copy.deepcopy(candidates)
        RELATION_FILTERS.create(name).apply(candidates)
        assert candidates == before

    def test_没有图库时不炸(self, name: str, candidates: list[dict]) -> None:
        """codegraph.sqlite 可能不存在。这时应当降级，而不是抛异常。"""
        RELATION_FILTERS.create(name, graph_store=None).apply(candidates)


def test_未知名字报清晰错误():
    with pytest.raises(KeyError) as exc:
        RELATION_FILTERS.create("not_a_filter")
    assert "not_a_filter" in str(exc.value)


def test_三类关系约束都在注册表里():
    """relation plan 支持的约束类型，都得有对应实现。"""
    assert {"graph_role", "caller", "callee"} <= set(FILTER_NAMES)
