# golden 期望值

这里存的是「上一次确认正确时，代码的输出长什么样」。

Golden 测试回答一个别的测试回答不了的问题：**这次改动有没有悄悄改变数值结果？**
单元测试只能验证你想到的情况，而重构真正会毁掉的往往是你没想到的那些。
这套期望值就是在整个包结构重组期间兜住行为的那张网。

## 文件

| 文件 | 覆盖 | 需要什么 |
|---|---|---|
| `tokenizer_input.json` | 120 个真实符号名，是下面那份的**输入** | — |
| `tokenizer.json` | `CodeTokenizer` + `AbbreviationGenerator` 的输出 | srctoolkit、wordfreq、spacy + `en_core_web_sm`、nltk 的 wordnet |
| `relation_filter.json` | 三个 `RelationFilter` 在真实候选集上的 symbol_id 序列 | `output/<project>/codegraph.sqlite` 与 `query_1/` 产物 |
| `intention_stages.json` | cluster 与 embedding 两阶段各桶的 symbol_id | 上面那些 + SentenceTransformer 模型 + embedding 训练产物 |

`tokenizer` 那组的输入已经固化在这里，所以它**不依赖 output/**——
装齐依赖，任何人 clone 下来都能跑。另外两组要先建索引。

## 怎么跑

```bash
pytest -m slow tests/integration          # 全部 golden
pytest -m slow -k tokenizer               # 只跑可移植的那组
```

默认的 `pytest` 会跳过它们（`addopts = -m 'not slow'`），
因为依赖不轻，不该让刚 clone 的人一跑就见红。缺资源时是 **skip 不是 fail**，
跳过原因会写清楚缺什么。

## 什么时候该重录

```bash
python -m scripts.record_golden                    # 全部
python -m scripts.record_golden --only tokenizer   # 单组
```

**重录前先确认当前行为是对的。** golden 只保证「和上次一样」，不保证「对」——
在有 bug 的状态下重录，等于把 bug 固化成期望值。

正常的重构流程是：改代码 → `pytest -m slow` → 绿了说明行为没变。
**只有在有意改变行为之后**（修 bug、换算法、调阈值）才重录，
并且在 commit message 里写清楚为什么变、变成什么样。

## 为什么不存全量中间数据

`intention_stages.json` 只存各桶的 symbol_id 和整数型 stats，
不存 `tiers` 那类中间产物——后者单个 case 就有 270K，而且随实现细节变化，
拿它当回归基准只会天天误报，最后没人看。

同理，`relation_filter.json` 只比对 symbol_id 序列：那是过滤器全部的
可观察行为，候选记录里其它字段是上游带下来的，不该由这一层负责。
