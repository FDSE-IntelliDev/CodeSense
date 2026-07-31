"""代码元素类型注册表的测试。

这张表被 DSL schema、parser、filter 三处共用，改动影响面广，
所以约束（通用类型是各语言的子集、大小写不敏感）要钉死在测试里。
"""

from __future__ import annotations

import pytest

from codesense.parsers.code_element_types import (
    COMMON_CODE_ELEMENT_TYPES,
    LANGUAGE_CODE_ELEMENT_TYPES,
    get_all_code_element_types,
    get_common_code_element_types,
    get_language_code_element_types,
    is_valid_code_element_type,
)

SUPPORTED_LANGUAGES = sorted(LANGUAGE_CODE_ELEMENT_TYPES)


def test_通用类型非空且无重复():
    types = get_common_code_element_types()

    assert types
    assert len(types) == len(set(types))


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_每种语言都有自己的类型表(language):
    types = get_language_code_element_types(language)

    assert types, f"{language} 的类型表是空的"
    assert len(types) == len(set(types)), f"{language} 的类型表有重复项"


def test_未知语言回退到通用类型而不是抛异常():
    assert get_language_code_element_types("cobol") == get_common_code_element_types()
    assert get_language_code_element_types("") == get_common_code_element_types()


def test_语言名大小写不敏感():
    assert get_language_code_element_types("JAVA") == get_language_code_element_types("java")


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_语言类型全都收进了全量表(language):
    all_types = set(get_all_code_element_types())

    assert set(get_language_code_element_types(language)) <= all_types


def test_全量表包含通用类型():
    assert set(COMMON_CODE_ELEMENT_TYPES) <= set(get_all_code_element_types())


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_合法类型校验按语言生效(language):
    for element_type in get_language_code_element_types(language):
        assert is_valid_code_element_type(element_type, language)


def test_不传语言时按全量表校验():
    assert is_valid_code_element_type("class")
    assert not is_valid_code_element_type("definitely_not_a_type")


def test_类型校验大小写不敏感():
    assert is_valid_code_element_type("CLASS") == is_valid_code_element_type("class")
