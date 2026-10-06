# Benchmark 幽灵函数清洗设计

## 2026-10-05 修订（当前权威口径）

实现不再删除 benchmark 中的文件或函数。挖掘阶段保留原始
`answer` / `trace_answer` / `candidate_answers`，并在每个 location 上增加：

```json
{
  "file": "src/Foo.java",
  "functions": ["existing", "addedLater"],
  "file_exist": true,
  "function_exist": {
    "existing": true,
    "addedLater": false
  }
}
```

- `file_exist` 表示文件是否存在于该记录的 `base_commit` 工作树。
- `function_exist` 以原始函数字符串为键，表示归一化后的 callable 是否由该文件声明。
- evaluator 完全排除 `file_exist=false` 的 location；文件存在时仍保留文件级 gold，
  但函数级 gold 只使用 `function_exist=true` 的函数。
- 缺少存在性字段的旧 benchmark 继续按旧口径评分。
- `evaluation/repo_cache.py` 仍按 `{repo}__{commit}` 隔离工作树，保证存在性标注与索引
  使用同一版本。

以下“删除幽灵函数”的文字保留为最初方案记录，已由上述标注式方案取代。

## 背景与目标

评测基准 `codesense-semantic-query.jsonl` 的答案（`answer` / `trace_answer` /
`candidate_answers`）是 `{file, functions}` 结构。`functions` 来自修复 diff
（`extract_patch_locations` → `_changed_functions`）与 Agent 最终答案，因此天然会
引用**修复新增、在被检索的 base_commit 版本里尚不存在**的符号。

实证：case#4 / case#5 的 `trace_answer` 是
`ReadPlainScalarValue.java # value, unquote`。在该记录的 base_commit
`95a4860` 检出树里，`ReadPlainScalarValue.java` **存在**，但只声明了
`public String value()`，**没有 `unquote`**（`unquote()` 是 trace 里 Agent 作为
修复新增的方法）。

后果：评测 `_location_metrics` 把每个答案的 `functions` 展平成
`(file, function)` 对计入 `function_recall` 分母（[scripts/evaluation.py:542-563](../../../scripts/evaluation.py)），
而 `unquote` 永远不可能出现在检索 hit 里 → 该 pair 恒不可命中 →
trace 轨 `function_recall` 被硬生生封顶（哪怕完美命中 `value()` 也只能到 1/2）。
这是一个 benchmark 正确性缺陷：**评分目标里混入了在被检索版本中不存在的幽灵符号。**

目标：让挖掘产出的 benchmark 中，每个答案的 `functions` 只保留**在该记录
base_commit 检出树里、对应文件中真实声明过**的函数；文件本身一律保留。清洗作为
`mine_trace_queries.py` 流程内的一步自动完成，产出单一、干净的 benchmark jsonl，
评测直接消费，无需第二个手动脚本。

## 现状与根因

- **挖掘侧无仓库检出**：`scripts/mine_trace_queries.py` 只读 trace 样本 jsonl、
  写查询 jsonl，从不 clone/checkout，因此当前无从校验函数存在性。
- **评测侧只校验文件、从不校验函数**：`_gold_problems` 仅对 `_GOLD_FIELDS =
  ("answer","trace_answer","candidate_answers")` 做**文件级**存在性检查
  （`glob` / `missing`），且只有 primary gold 文件非法才触发 skip；`functions`
  从不校验（[scripts/evaluation.py:426-465](../../../scripts/evaluation.py)）。
- **clone 目录命名会覆盖**：`_prepare_repo` 用 `repo.replace("/","__")` 命名工作
  树、不带 commit，同仓库不同 commit 共用一个目录被来回 `_checkout`；而索引目录
  却带 commit（`{repo}__{commit}`）。实证：`evaluation/projects/` 下只有一个
  `decorators-squad__eo-yaml` 工作树，`.indexes/` 里却有 `__1987c13` 与 `__95a4860`
  两个索引；benchmark 里 `decorators-squad/eo-yaml` 确以 2 个 commit 出现。
- **硬编码绝对路径**：`INPUT` / `OUTPUT` / `PROJECTS_DIR` / `BENCHMARK` 写死
  `/Users/huangzhuochen/...`，违反 AGENTS.md 规则 5。

## 方案比较

### 判定"函数已声明"的解析器

- **方案一：复用 `codesense.lang` 的 Java 扫描器（采用）**
  `JavaDeclarationScanner.for_java().scan(source) -> ScanResult`，取
  `ScanResult.declarations` 中 `kind ∈ {"method","constructor"}`（scanner 的
  `_CALLABLE_KINDS`）的 `Declaration.name`。与索引器同源、tree-sitter robust、
  已是依赖；内部类方法经 `_walk` 递归天然纳入。代价：在 `evaluation/` 里加一层薄封装。
- **方案二：独立正则提取方法签名（不采用）**
  不耦合 codesense，但 Java 签名形态多样、脆弱（适配器现有 `_declared_function`
  正则本就不准），易误判。
- **方案三：复用已建索引 `index.elements()`（不采用）**
  与用户选定的"源码 ground-truth"口径不符：索引未收录某个真实方法属于**检索系统
  质量**问题，不应靠缩小 benchmark 掩盖，否则混淆两个关注点。

### 清洗落点

- **并入挖掘流程（采用）**：清洗逻辑（解析/过滤）放 `evaluation/answer_cleaning.py`，
  由 `scripts/mine_trace_queries.py` 编排调用，一趟产出干净 benchmark。符合用户
  "不再手动跑第二个脚本"的要求，也符合 AGENTS.md 规则 6（scripts 不写业务逻辑）。
- **独立清洗脚本（不采用）**：需手动二次运行，且与挖掘各自维护仓库检出。

## 详细设计

### 数据流

```text
mine_trace_queries.py
  iter_jsonl(样本) → 每条 case: mine_queries() 生成 rows（每行含 repo/base_commit/answers）
      → resolve_repo(repo, base_commit)  检出该版本仓库（共享、复用、按 (repo,commit) 缓存）
      → clean_record(row, repo_root)     剔除文件内未声明的函数（文件不动）
      → 写 codesense-semantic-query.jsonl（单一、已干净）
                          │
   evaluation.py 直接消费（BENCHMARK 路径不变）
```

### ① 共享仓库解析器 `evaluation/repo_cache.py`（从 evaluation.py 抽出）

- `resolve_repo(repo, base_commit, projects_dir, *, manual_paths=None,
  run=subprocess.run) -> Path`
  - 目录名：`repo.replace("/","__")`；**有 base_commit 则追加 `__{base_commit}`**
    （根治同仓库多 commit 覆盖，与 `.indexes/` 命名对齐）。
  - 目录已存在 → `_checkout(run, target, base_commit)` 后复用；不存在 →
    有 commit 时全量 `clone` 再 `checkout`，无 commit 时 `--depth 1` 浅 clone。
  - `manual_paths` 命中 → 原样返回、绝不 checkout（沿用现有语义）。
  - `_REPOSITORY` 校验正则一并迁入。
- `projects_dir` 默认 `Path(__file__).resolve().parents[1] / "evaluation/projects"`。
- `scripts/evaluation.py` 删除自带 `_prepare_repo` / `_checkout`，改 import 本模块。

### ② 函数存在性校验 + 清洗 `evaluation/answer_cleaning.py`

- `_CALLABLE_KINDS = frozenset({"method", "constructor"})`（与 scanner 一致）。
- `normalize_function_name(value) -> str`：本模块自带，规则与 `scripts/evaluation.py`
  的 `_function_name()` **完全一致**（`strip → split("(",1)[0] → rsplit(".",1)[-1]`）。
  放在 `evaluation/` 而非从脚本 import（scripts 不应被 import）；后续可让
  `evaluation.py` 反过来复用它以消除重复，但非本次必须。
- `declared_functions(source_text: str) -> set[str]`
  - `JavaDeclarationScanner.for_java().scan(source_text)`，收集
    `kind ∈ _CALLABLE_KINDS` 的 `Declaration.name`，逐个用 `normalize_function_name`
    归一化。
  - 重载（同名多签名）归一化后同名 → 只要文件里有任一同名 callable 即视为存在。
- `clean_record(record: dict, repo_root: Path) -> dict`
  - 遍历 `("answer","trace_answer","candidate_answers")` 每个 location：
    - **文件在 `repo_root` 下存在** → 读文件文本、`declared_functions` 解析，
      `functions` 只保留归一化名 ∈ 声明集合的项；**文件条目本身不删不改**。
    - **文件不存在** → 该 location 的 `functions` **原样保留**（文件层不在本次范围，
      交评测现有 `gold_problems` / skip 处理）。
  - 返回**新** record（不改入参），保持其余字段不变。
- `base_commit` 为 None/空的记录 → 整条透传、不清洗（无从定位版本树）。

### ③ 清洗并入挖掘 `scripts/mine_trace_queries.py`（仅编排）

- 在 `_run_cases` 非 dry_run 分支：`rows = [q.to_dict() ...]` 之后、写盘之前，
  对每行执行 `clean_record`。
- `repo_root` 按 `(repo, base_commit)` **缓存**（同 case 多 query、同仓库多 case
  只 `resolve_repo` 一次，避免重复 `git fetch`）；`base_commit` 为空的行**跳过**
  resolve 与 clean（不做无谓 clone）。
- dry_run 分支（prompt 行、无 answer）不清洗。
- summary 增加 `removed_functions` 总数与按仓库统计，打印到控制台。
- 脚本常量改用 `_ROOT` 相对路径（`_ROOT = Path(__file__).resolve().parents[1]` 已存在），
  去掉 `/Users/...` 绝对前缀。

### ④ 评测 `scripts/evaluation.py`（改动收窄）

- 改用 `evaluation.repo_cache.resolve_repo`（clone 目录带 commit 后缀、与 `.indexes/`
  一致），删除本地 `_prepare_repo` / `_checkout`。
- `PROJECTS_DIR` / `BENCHMARK` 等常量去绝对路径写死。
- **`BENCHMARK` 指向的单一 jsonl 路径不变**；`_location_metrics` 评分逻辑**不改**
  （上游已产出干净数据，`function_recall` 分母自然不含幽灵函数）。

## 口径与边界决策

- "已声明函数"= kind ∈ {method, constructor}，**含构造器**。
- 函数名比较用 `_function_name()` 同口径的归一化简单名；重载按名匹配即算存在。
- 只清洗**函数**；**文件集合永不修改**（不删文件、不改文件路径）。
- 文件在 base_commit 不存在 → 该文件 functions 原样保留（不猜、不删）。
- 记录 base_commit 为空 → 整条不清洗。
- 单一 benchmark jsonl，挖掘就地写干净数据；**不产出** `.clean.jsonl`、**不留**
  清洗前副本、**不加**审计字段（剔除情况仅打印到控制台）。
- 清洗幂等：对已干净数据重跑无变化。

## 测试策略（TDD，先红后绿）

- `declared_functions`：方法命中；**构造器命中**；字段/类名不算；重载（同名多签名）
  算存在；内部类方法算存在。
- `clean_record`：文件在 + 函数在 → 保留；**文件在 + 函数不在（`unquote`）→ 删函数、
  留文件**；文件不存在 → functions 原样不动；base_commit 为空的记录不清洗（在编排层验证）。
- `resolve_repo`：commit 后缀命名；目录已存在不重复 clone（用假 `run` 断言未调用 clone）；
  **同 repo 两个 commit → 两个不同目录**（覆盖 bug 回归）；无 commit → 无后缀 + 浅 clone。
- 挖掘集成：桩 `mine_queries` 产出含幽灵函数的 row + 一个真实小 fixture 仓库 →
  跑 `_run_cases` → 断言写出的 jsonl 中该函数被删、文件保留、summary 含 `removed_functions`；
  base_commit 为空的行不触发 clone。
- 真实回归：`ReadPlainScalarValue.java`@`95a4860` → `value` 保留、`unquote` 剔除。

## 非目标

- 不改文件层校验 / skip 逻辑；不清洗 trace/candidate 里不存在的**文件**。
- 不加审计字段；不改评分公式；不改 query 生成逻辑（`mine_queries` 本身）。
- 不主动删除/迁移已存在的旧无后缀 clone 目录（留作孤儿，被 `.gitignore` 忽略）。

## 影响文件清单

- 新增 `evaluation/repo_cache.py`、`evaluation/answer_cleaning.py`。
- 改 `scripts/mine_trace_queries.py`（编排清洗 + 去绝对路径）。
- 改 `scripts/evaluation.py`（改用共享 resolver + 去绝对路径）。
- 新增测试 `tests/unit/evaluation/test_answer_cleaning.py`、
  `tests/unit/evaluation/test_repo_cache.py`，及挖掘集成测试。

## 验收标准

- 重跑 `mine_trace_queries.py` 后，benchmark 中 case#4 / #5 的 `trace_answer`
  仅含 `value`，`unquote` 被剔除，`ReadPlainScalarValue.java` 文件条目保留。
- 评测 `function_recall` 分母不再包含 base_commit 中不存在的函数。
- `evaluation/projects/` 下同一仓库不同 commit 各自独立目录，互不覆盖。
- `ruff check .`、`ruff format --check .`、`pytest` 三关通过。
