[过渡] 首先看第一步：DSL-Driven query parsing。
我们把自然语言 query 解析为结构化字段，包括关键词、目标类型和过滤约束。这个统一表示使后续模块之间有一致接口，避免“检索、过滤、重排”各自理解 query 的偏差。
要点：① structured query 字段 ② DSL 的接口作用 ③ 对下游模块的支撑
时长：1.5分钟