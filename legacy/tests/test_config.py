"""配置层的测试。

重点不是「能读出值」，而是**读错了会不会当场报错**——配置静默用默认值
是研究项目里最难查的一类 bug（跑完才发现参数没生效）。
"""

from __future__ import annotations

import dataclasses

import pytest

from codesense.config import (
    API_KEY_ENV,
    REPO_ROOT,
    Config,
    ConfigError,
    load_config,
)
from tests.conftest import VALID_CONFIG


def test_读取合法配置(config_yaml):
    cfg = Config.from_file(config_yaml(VALID_CONFIG))

    assert cfg.target.project_name == "some-project"
    assert cfg.llm.model == "test-model"
    assert cfg.query.default_id == 7


def test_相对输出路径按仓库根解析(config_yaml):
    cfg = Config.from_file(config_yaml(VALID_CONFIG))

    assert cfg.output_dir == REPO_ROOT / "output"
    assert cfg.project_output_dir == REPO_ROOT / "output" / "some-project"


def test_绝对输出路径原样保留(config_yaml):
    absolute = VALID_CONFIG.replace('dir: "output"', 'dir: "/var/tmp/out"')
    cfg = Config.from_file(config_yaml(absolute))

    assert str(cfg.output_dir) == "/var/tmp/out"


def test_查询目录按_query_id_隔离(config_yaml):
    cfg = Config.from_file(config_yaml(VALID_CONFIG))

    assert cfg.query_output_dir().name == "query_7"  # 缺省用配置里的 default_id
    assert cfg.query_output_dir(3).name == "query_3"
    assert cfg.query_output_dir(3).parent == cfg.project_output_dir


def test_字段拼错立刻报错(config_yaml):
    broken = VALID_CONFIG.replace("project_name:", "project_nme:")

    with pytest.raises(ConfigError) as exc:
        Config.from_file(config_yaml(broken))

    assert "project_nme" in str(exc.value)


def test_缺字段立刻报错(config_yaml):
    broken = VALID_CONFIG.replace('  model: "test-model"\n', "")

    with pytest.raises(ConfigError) as exc:
        Config.from_file(config_yaml(broken))

    assert "model" in str(exc.value)


def test_多写一个配置段也报错(config_yaml):
    with pytest.raises(ConfigError):
        Config.from_file(config_yaml(VALID_CONFIG + "extra_section:\n  foo: 1\n"))


def test_配置文件不存在时报清晰错误(tmp_path):
    with pytest.raises(ConfigError) as exc:
        Config.from_file(tmp_path / "nope.yaml")

    assert "不存在" in str(exc.value)


def test_密钥不在配置文件里而在环境变量(config_yaml, monkeypatch):
    cfg = Config.from_file(config_yaml(VALID_CONFIG))

    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(ConfigError) as exc:
        _ = cfg.llm.api_key
    assert API_KEY_ENV in str(exc.value)

    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    assert cfg.llm.api_key == "sk-test"


def test_仓库自带的默认配置是合法的():
    """configs/default.yaml 必须始终能被解析——它是新人跑通的第一步。"""
    cfg = load_config()

    assert cfg.target.project_name
    assert cfg.llm.base_url.startswith("http")


def test_配置对象不可变(config_yaml):
    """frozen dataclass——配置在跑到一半时被改掉是最难查的一类问题。"""
    cfg = Config.from_file(config_yaml(VALID_CONFIG))

    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.target.project_name = "changed"  # type: ignore[misc]
