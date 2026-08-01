"""SemQL 字段抽取的测试。

这个 helper 同时要吃新旧两种 payload 结构，是最容易在改 schema 时悄悄
坏掉的地方，所以两种形态都要覆盖。
"""

from __future__ import annotations

from codesense.query.semql_utils import extract_semql_terms, extract_semql_text_terms


def test_从分组结构里取_include_关键词():
    payload = {
        "conditions": {
            "surface": {
                "include": [{"keywords": ["login", "auth"]}],
                "exclude": [{"keywords": ["logout"]}],
            }
        }
    }

    assert extract_semql_terms(payload) == ["login", "auth"]


def test_按_property_选_include_或_exclude():
    payload = {
        "conditions": {
            "surface": {
                "include": [{"keywords": ["login"]}],
                "exclude": [{"keywords": ["logout"]}],
            }
        }
    }

    assert extract_semql_terms(payload, properties=("exclude",)) == ["logout"]
    assert set(extract_semql_terms(payload, properties=("include", "exclude"))) == {
        "login",
        "logout",
    }


def test_按_condition_type_过滤():
    payload = {
        "conditions": {
            "surface": {"include": [{"keywords": ["login"]}]},
            "intention": {"include": [{"keywords": ["authenticate"]}]},
        }
    }

    assert extract_semql_terms(payload, condition_type="intention") == ["authenticate"]


def test_结果去重且保持顺序():
    payload = {
        "conditions": {
            "surface": {
                "include": [
                    {"keywords": ["login", "auth"]},
                    {"keywords": ["auth", "user"]},
                ]
            }
        }
    }

    assert extract_semql_terms(payload) == ["login", "auth", "user"]


def test_兼容旧的顶层_keywords_结构():
    assert extract_semql_terms({"keywords": ["login", "auth"]}) == ["login", "auth"]


def test_intent_字段会被递归展平():
    """intent 是唯一会被递归展开的字段——其它字段原样返回。"""
    payload = {
        "conditions": {"intention": {"include": [{"intent": {"action": "find", "object": "user"}}]}}
    }

    assert extract_semql_terms(payload, term_name="intent") == ["find", "user"]


def test_非_intent_字段不展平只原样返回():
    payload = {"conditions": {"surface": {"include": [{"term": {"a": 1}}]}}}

    assert extract_semql_terms(payload, term_name="term") == [{"a": 1}]


def test_非法输入返回空列表而不是抛异常():
    assert extract_semql_terms(None) == []
    assert extract_semql_terms("not a dict") == []
    assert extract_semql_terms({}) == []


def test_text_terms_去掉空白并转成字符串():
    payload = {"keywords": ["  login  ", "", "   ", 42, None]}

    assert extract_semql_text_terms(payload) == ["login", "42"]
