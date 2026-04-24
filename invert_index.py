import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union

from tokenizer.tokenizer_core import tokenizer
from expansion.abbreviate import abbreviate

import math

class InvertedIndexBuilder:
    def __init__(self, symbols_index_path: str | None = None, output_path: str | None = None):
        self.symbols_index_path = symbols_index_path
        self.invert_index_path = output_path

    def _extract_names(self, data_source: Union[str, Path, Dict[Any, Any], List[Any], None]) -> List[str]:
        """
        统一抽取待处理 name 列表：
        - str / Path: 视为文件路径，读取 JSON
        - dict: 使用 dict 的 keys 作为待处理 name
        - list: 列表本身为待处理数据（支持元素是 str 或 {"name": "..."}）
        - None: 回退到 self.symbols_index_path
        """
        if data_source is None:
            if not self.symbols_index_path:
                raise ValueError("data_source 为 None 时，必须提供 symbols_index_path")
            data_source = self.symbols_index_path

        # 1) 文件路径
        if isinstance(data_source, (str, Path)):
            path = Path(data_source)
            if not path.exists():
                raise FileNotFoundError(f"输入文件不存在: {path}")
            with path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            return self._extract_names(loaded)

        # 2) 字典 -> keys
        if isinstance(data_source, dict):
            return [str(k).strip() for k in data_source.keys() if str(k).strip()]

        # 3) 列表
        if isinstance(data_source, list):
            names: List[str] = []
            for item in data_source:
                if isinstance(item, str):
                    name = item.strip()
                    if name:
                        names.append(name)
                elif isinstance(item, dict):
                    # 兼容 symbols_index.json 的结构
                    name = item.get("name", "")
                    if isinstance(name, str) and name.strip():
                        names.append(name.strip())
            return names

        raise TypeError(
            "data_source 类型不支持。仅支持: str/Path(文件路径), dict(keys), list(字符串或含name字典)"
        )

    def build_invert_index(
        self, data_source: Union[str, Path, Dict[Any, Any], List[Any], None] = None
    ) -> Dict[str, List[str]]:
        """
        从多种输入源构建倒排索引:
          sub_token -> [identifier1, identifier2, ...]
        """
        names = self._extract_names(data_source)
        invert_index = defaultdict(set)

        for original_name in names:
            max_abbr_len = math.ceil(len(original_name.replace(' ', '')) * 0.8)
            sub_seq=abbreviate(original_name, max_part_len=8 if 8 <= max_abbr_len else int(max_abbr_len * 0.8),
                           max_abbr_len=max_abbr_len)
            invert_index[original_name].update(sub_seq)

        result = {k: sorted(list(v)) for k, v in invert_index.items()}

        if self.invert_index_path:
            out = Path(self.invert_index_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=4)

        return result


if __name__ == "__main__":
    builder = InvertedIndexBuilder(
        symbols_index_path="./output/youlai-boot-master/ngramed_symbol.json",
        output_path="./output/youlai-boot-master/invert_index.json"
    )
    invert_index = builder.build_invert_index()
    print(f"构建完成，索引项数: {len(invert_index)}")