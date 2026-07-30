"""配置加载。

约定（见 ARCHITECTURE.md）：

- 阈值、路径、模型名一律进 ``configs/*.yaml``，不写死在代码里；
- YAML 里多写、写错一个字段立刻报错，不静默忽略；
- 密钥只从环境变量读，**任何情况下都不进版本库**。

新代码请这样用::

    from codesense.config import load_config

    cfg = load_config()
    index_path = cfg.project_output_dir / "symbols_index.json"

模块底部还有一层过渡期的大写常量（``PROJECT_OUTPUT_DIR`` 等），
是为搬包前的旧调用点留的，新代码不要再用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from functools import cache
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------- 目录锚点

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "default.yaml"

# 随包一起分发的资源目录。这些是包的一部分，不是可配置项，所以不进 YAML。
TOKENIZER_DIR = str(PACKAGE_DIR / "tokenizer")
EMBEDDING_DIR = str(PACKAGE_DIR / "embedding")
EXPANSION_DIR = str(PACKAGE_DIR / "expansion")

#: LLM key 的环境变量名。
API_KEY_ENV = "CODESENSE_API_KEY"


class ConfigError(RuntimeError):
    """配置文件写错、或必需的环境变量没设。"""


# ---------------------------------------------------------------- 各配置段


@dataclass(frozen=True)
class TargetConfig:
    """被搜索的目标代码库。"""

    project_path: str
    project_name: str


@dataclass(frozen=True)
class OutputConfig:
    """产物目录。相对路径按仓库根目录解析。"""

    dir: str
    corpus: str
    abbr_result_dir: str


@dataclass(frozen=True)
class LLMConfig:
    """LLM 接入。注意这里**没有** api_key 字段——key 只从环境变量取。"""

    base_url: str
    model: str

    @property
    def api_key(self) -> str:
        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise ConfigError(
                f"环境变量 {API_KEY_ENV} 没设置。参考仓库根目录的 .env.example，"
                f"用 `export {API_KEY_ENV}=sk-...` 设好再跑。"
            )
        return key


@dataclass(frozen=True)
class ToolsConfig:
    """外部可执行工具。"""

    jdtls_path: str


@dataclass(frozen=True)
class QueryConfig:
    """在线查询。"""

    default_id: int


#: YAML 顶层段名 -> 对应的 dataclass。
_SECTION_TYPES: dict[str, type] = {
    "target": TargetConfig,
    "output": OutputConfig,
    "llm": LLMConfig,
    "tools": ToolsConfig,
    "query": QueryConfig,
}


def _check_keys(section: str, got: dict[str, Any], cls: type) -> None:
    """YAML 段里的 key 必须和 dataclass 字段完全对上。

    拼错字段名是配置类 bug 里最难查的一种——静默用了默认值，跑完才发现
    参数根本没生效。这里让它当场报错。
    """
    expected = {f.name for f in fields(cls)}
    unknown = set(got) - expected
    missing = expected - set(got)
    if unknown:
        raise ConfigError(
            f"配置段 [{section}] 有无法识别的字段：{sorted(unknown)}；可用字段：{sorted(expected)}"
        )
    if missing:
        raise ConfigError(f"配置段 [{section}] 缺少字段：{sorted(missing)}")


# ---------------------------------------------------------------- 总配置


@dataclass(frozen=True)
class Config:
    """一整份配置。用 :func:`load_config` 拿，不要自己 new。"""

    target: TargetConfig
    output: OutputConfig
    llm: LLMConfig
    tools: ToolsConfig
    query: QueryConfig

    # -------------------------------------------------------- 构造

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
        path = Path(path).expanduser()
        if not path.is_file():
            raise ConfigError(f"配置文件不存在：{path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        _check_keys("<root>", raw, cls)
        sections: dict[str, Any] = {}
        for name, section_cls in _SECTION_TYPES.items():
            section = raw[name] or {}
            if not isinstance(section, dict):
                raise ConfigError(
                    f"配置段 [{name}] 应该是一个映射，实际是 {type(section).__name__}"
                )
            _check_keys(name, section, section_cls)
            sections[name] = section_cls(**section)
        return cls(**sections)

    # -------------------------------------------------------- 派生路径

    @property
    def project_path(self) -> Path:
        """目标代码库根目录（展开 ``~``）。"""
        return Path(self.target.project_path).expanduser()

    @property
    def output_dir(self) -> Path:
        """所有产物的根目录。相对路径按仓库根解析。"""
        return _resolve(self.output.dir)

    @property
    def project_output_dir(self) -> Path:
        """当前项目的离线索引产物目录：``output/<project_name>/``。"""
        return self.output_dir / self.target.project_name

    @property
    def abbr_result_dir(self) -> Path:
        """缩写检测的中间产物目录。"""
        return _resolve(self.output.abbr_result_dir)

    def query_output_dir(self, query_id: int | None = None) -> Path:
        """单次在线查询的产物目录：``output/<project_name>/query_<id>/``。

        离线产物留在 :attr:`project_output_dir`，一次查询的所有中间结果
        隔离在这个子目录里，方便对比多次查询。
        """
        qid = self.query.default_id if query_id is None else query_id
        return self.project_output_dir / f"query_{qid}"


def _resolve(raw: str) -> Path:
    """相对路径按仓库根目录解析，绝对路径原样返回。"""
    p = Path(raw).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


@cache
def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    """读一份配置并缓存。同一个 path 重复调用不会重复读盘。"""
    return Config.from_file(path)


def get_query_output_dir(
    project_output_dir: str | Path | None = None,
    query_id: int | None = None,
) -> str:
    """查询产物目录（兼容旧签名）。

    新代码请用 ``load_config().query_output_dir(query_id)``。
    """
    cfg = load_config()
    if project_output_dir is None:
        return str(cfg.query_output_dir(query_id))
    qid = cfg.query.default_id if query_id is None else query_id
    return str(Path(project_output_dir) / f"query_{qid}")


# ------------------------------------------------------------ 过渡期兼容层
#
# 搬进 codesense/ 包之前，有 40 处代码写的是
# ``from definition import PROJECT_OUTPUT_DIR`` 这种模块级常量。
# 一次性把它们全改成「Config 逐层传参」等于改所有函数签名，风险太大，
# 所以这里用 PEP 562 的模块级 __getattr__ 把它们做成惰性属性：
#
#   - import 本模块时不读 YAML，满足「模块顶层不执行逻辑」；
#   - 旧调用点 ``from codesense.config import X`` 照常可用。
#
# 这是过渡措施。新代码一律用 load_config()，逐步把这些常量的调用点迁走，
# 迁完就可以整块删掉。

_LEGACY_NAMES = {
    "PROJECT_PATH": lambda c: str(c.project_path),
    "PROJECT_NAME": lambda c: c.target.project_name,
    "OUTPUT_DIR": lambda c: str(c.output_dir),
    "PROJECT_OUTPUT_DIR": lambda c: str(c.project_output_dir),
    "QUERY_OUTPUT_DIR": lambda c: str(c.query_output_dir()),
    "ABBR_RESULT_DIR": lambda c: str(c.abbr_result_dir),
    "CORPUS": lambda c: c.output.corpus,
    "BASE_URL": lambda c: c.llm.base_url,
    "BASE_MODEL": lambda c: c.llm.model,
    "API_KEY": lambda c: c.llm.api_key,
    "JDTLS_PATH": lambda c: c.tools.jdtls_path,
    "QUERY_ID": lambda c: c.query.default_id,
    "ROOT_DIR": lambda c: str(REPO_ROOT),
}


def __getattr__(name: str) -> Any:
    if name in _LEGACY_NAMES:
        return _LEGACY_NAMES[name](load_config())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), *_LEGACY_NAMES])
