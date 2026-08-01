"""产物读写 helper 的测试。

pipeline 每个阶段都用这两个函数落盘，中文和嵌套目录是最常踩的两个坑。
"""

from __future__ import annotations

import json

import pytest

from codesense.utils.file_utils import load_res, save_res


def test_存下去再读回来是同一份数据(tmp_path):
    data = {"symbols": [{"name": "login", "type": "method"}], "count": 1}
    path = tmp_path / "result.json"

    save_res(str(path), data)

    assert load_res(str(path)) == data


def test_会自动建上级目录(tmp_path):
    path = tmp_path / "youlai-boot" / "query_1" / "result.json"

    save_res(str(path), {"ok": True})

    assert path.is_file()


def test_中文不被转义成_unicode_码点(tmp_path):
    path = tmp_path / "result.json"

    save_res(str(path), {"desc": "用户登录鉴权"})

    assert "用户登录鉴权" in path.read_text(encoding="utf-8")


def test_写出来的是带缩进的合法_JSON(tmp_path):
    path = tmp_path / "result.json"

    save_res(str(path), {"a": {"b": 1}})
    text = path.read_text(encoding="utf-8")

    json.loads(text)
    assert "\n" in text  # indent=2，方便人直接看 diff


def test_重复写会覆盖而不是追加(tmp_path):
    path = tmp_path / "result.json"

    save_res(str(path), {"round": 1})
    save_res(str(path), {"round": 2})

    assert load_res(str(path)) == {"round": 2}


def test_读不存在的文件抛_FileNotFoundError(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_res(str(tmp_path / "nope.json"))
