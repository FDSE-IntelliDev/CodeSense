# ngramed_symbol.json 使用点与 Schema 维护文档

> 目标：当 `ngramed_symbol.json` 的数据结构发生变更时，可以根据本文件快速定位所有需要同步修改的函数。

## 1) 文件定位

- 默认产物路径：`output/youlai-boot-master/ngramed_symbol.json`
- 生成入口（脚本级）：`ngram_split.py`

## 2) 当前数据 Schema（基于代码读写行为）

`ngramed_symbol.json` 当前被当作一个 JSON 对象（`dict`）使用：

- 顶层：`object`
  - key: `subtoken`（`string`，通常为小写分词 token）
  - value: `array<object>`，每个元素为一个 symbol 记录（来源于 `symbols_index.json` 的条目）

symbol 记录在当前消费逻辑中至少依赖这些字段：

- `name`: `string`
- `file`: `string`
- `range`: `object`
  - `start_line`: `int | string`（当前代码未强校验类型）
  - `end_line`: `int | string`（当前代码未强校验类型）

可用伪 Schema 表示如下：

```json
{
  "<subtoken>": [
    {
      "name": "<symbol_name>",
      "file": "<relative_or_absolute_path>",
      "range": {
        "start_line": 123,
        "end_line": 130
      }
      // ...可能包含其他字段，当前逻辑会透传
    }
  ]
}
```

## 3) 函数级使用清单（按影响优先级）

## A. 直接读取/依赖 `ngramed_symbol.json` Schema 的函数

1. `query_processing/full_term_matcher.py` -> `FullTermMatcher.__init__`
   - 用法：加载 `self.ngramed_symbol = self._load_json(ngramed_symbol_path)`
   - 影响：
     - 若顶层不再是 `dict`，会被 `_load_json` 置为空 `{}`。

2. `query_processing/full_term_matcher.py` -> `FullTermMatcher._resolve_symbols`
   - 用法：
     - 按 `subtoken` 读取 `self.ngramed_symbol.get(st)`。
     - 假设 value 是 `list[dict]`。
     - 从每个 symbol 中读取 `name`、`file`、`range.start_line`、`range.end_line` 做去重与返回。
   - 影响：
     - 顶层 key/value 结构变化、symbol 字段重命名、`range` 结构变化都会直接影响匹配结果。

3. `query_processing/full_term_matcher.py` -> `FullTermMatcher.match_keywords`
   - 用法：调用 `_resolve_symbols`，间接受 `ngramed_symbol` Schema 影响。
   - 影响：
     - `_resolve_symbols` 解析失败会导致 `matched_symbols` 为空或质量下降。

## B. 直接写入/定义 `ngramed_symbol.json` 结构的函数

4. `ngram_split.py` -> `SymbolNgramer.build_ngramed_symbol`
   - 用法：
     - 从 `symbols_index.json` 读取条目。
     - 按 subtoken 构建 `dict[subtoken] -> list[item]`。
     - 将原始 `item` 直接 append 后写入 JSON。
   - 影响：
     - 这是 `ngramed_symbol.json` 的“生产者”。如果想改 Schema，应优先修改此函数。

5. `ngram_split.py` -> `get_ngramed_symbol`
   - 用法：整文件读取。
   - 影响：
     - 对新 Schema 无解析约束，但调用方可能有约束。

## C. 间接/配置级引用（非核心解析函数）

6. `query_processing/run_keyword_expansion.py` -> `main`
   - 用法：通过 `ExpansionConfig(ngram_index_path=...)` 传入文件路径。
   - 影响：
     - 该函数本身不解析 schema，但下游模块会消费该文件。

7. `ngram_split.py` -> 模块级脚本入口（文件末尾）
   - 用法：实例化 `SymbolNgramer(..., output_path=.../ngramed_symbol.json)` 并执行构建。
   - 影响：
     - 仅路径与构建触发点；Schema 由 `build_ngramed_symbol` 决定。

8. `query_processing/full_term_matcher.py` -> 模块级脚本入口（`__main__`）
   - 用法：传入 `ngramed_symbol_path` 到 `FullTermMatcher`。
   - 影响：
     - 仅演示入口，实际影响同 `__init__` + `_resolve_symbols`。

9. `invert_index.py` -> 模块级脚本入口（`__main__`）
   - 用法：当前把 `ngramed_symbol.json` 当作 `symbols_index_path` 传给 `InvertedIndexBuilder`。
   - 影响：
     - 该处属于“文件级引用”，不属于正常 `ngramed_symbol` 解析链路。
     - 且当前 `build_invert_index` 预期输入结构与 `ngramed_symbol` 并不一致（会用 dict keys 作为 name）。

## 4) 变更 Schema 时的同步修改清单（Checklist）

若你计划修改 `ngramed_symbol.json`，至少检查以下函数：

- [ ] `ngram_split.py` -> `SymbolNgramer.build_ngramed_symbol`（生产端）
- [ ] `query_processing/full_term_matcher.py` -> `FullTermMatcher.__init__`（加载策略）
- [ ] `query_processing/full_term_matcher.py` -> `FullTermMatcher._resolve_symbols`（核心消费端）
- [ ] `query_processing/full_term_matcher.py` -> `FullTermMatcher.match_keywords`（输出结构联动）
- [ ] 所有传递 `ngramed_symbol_path` / `ngram_index_path` 的 runner 与配置

## 5) 建议的兼容策略（可选）

为减少后续 schema 演进成本，建议在 `FullTermMatcher` 中增加一个单独的适配层函数（如 `_normalize_ngramed_symbol_record`），统一处理：

- 字段重命名（如 `file` -> `path`）
- `range` 平铺（如 `start_line`/`end_line` 顶层化）
- list/dict 混合输入兼容

这样未来只需改 1 个函数即可兼容新旧 Schema。
