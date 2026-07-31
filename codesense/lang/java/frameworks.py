"""元注解：框架自己声明的同义关系。

这是注解相对其它信号的**独有优势**。Spring 里 `@RestController` 就是
`@Controller` + `@ResponseBody`，`@GetMapping` 就是 `@RequestMapping(GET)`——
**这层关系是框架在源码里声明的事实，不是估计**，所以进扩展表时分数是 1.0，
与 `prefix` / `ctx` 那些估计值并列但更硬。

三层来源，按成本从低到高（``docs/design/09-grounding.md`` 第八节）：

1. 本模块这张硬编码表——常用框架，几十条覆盖绝大多数
2. 解析项目自己的 ``@interface`` 声明——自定义注解的元注解在源码里
3. 落回名字切分——不认识的注解走这条

本模块只做第 1 层，纯数据加一点推导，**不做 IO**。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

__all__ = ["META_ANNOTATIONS", "expansions_for", "meta_expansion_table"]

#: 注解 → 它由哪些注解组合而成（``@RestController`` 含 ``@Controller``）。
#: 方向是「具体 → 它蕴含的更一般者」。
META_ANNOTATIONS: Mapping[str, tuple[str, ...]] = {
    # Spring MVC
    "@RestController": ("@Controller", "@ResponseBody", "@Component"),
    "@Controller": ("@Component",),
    "@Service": ("@Component",),
    "@Repository": ("@Component",),
    "@Configuration": ("@Component",),
    "@GetMapping": ("@RequestMapping",),
    "@PostMapping": ("@RequestMapping",),
    "@PutMapping": ("@RequestMapping",),
    "@DeleteMapping": ("@RequestMapping",),
    "@PatchMapping": ("@RequestMapping",),
    # Spring Boot
    "@SpringBootApplication": ("@Configuration", "@ComponentScan", "@EnableAutoConfiguration"),
    "@SpringBootTest": ("@ExtendWith",),
    # 缓存 / 事务 / 调度
    "@Cacheable": ("@Caching",),
    "@CacheEvict": ("@Caching",),
    "@CachePut": ("@Caching",),
    # 校验
    "@NotNull": ("@Constraint",),
    "@NotBlank": ("@Constraint",),
    "@NotEmpty": ("@Constraint",),
    "@Size": ("@Constraint",),
    "@Valid": ("@Constraint",),
    "@Validated": ("@Constraint",),
    # JPA
    "@Entity": ("@Table",),
    "@Id": ("@Column",),
    # 测试
    "@Test": ("@Testable",),
    "@ParameterizedTest": ("@Testable",),
}

#: 直接声明关系的分数。它是事实，不是估计。
DIRECT_SCORE = 1.0

#: 传递关系每多一跳的衰减。`@GetMapping` → `@RequestMapping` 是直接的，
#: `@RestController` → `@Component` 经 `@Controller` 是传递的，稍弱。
TRANSITIVE_DECAY = 0.8


def expansions_for(name: str, *, max_depth: int = 3) -> dict[str, float]:
    """一个注解蕴含的全部注解及其分数（含传递闭包）。

    ``max_depth`` 防环也防过度传播——超过三跳的蕴含关系实践中没有意义。
    """
    found: dict[str, float] = {}
    frontier = [(name, DIRECT_SCORE, 0)]
    while frontier:
        current, score, depth = frontier.pop()
        if depth >= max_depth:
            continue
        for parent in META_ANNOTATIONS.get(current, ()):
            child_score = score * (DIRECT_SCORE if depth == 0 else TRANSITIVE_DECAY)
            if found.get(parent, 0.0) >= child_score:
                continue
            found[parent] = child_score
            frontier.append((parent, child_score, depth + 1))
    found.pop(name, None)
    return found


def meta_expansion_table() -> dict[str, list[tuple[str, float, str]]]:
    """建成扩展表要的形状：**一般 → 具体**。

    方向要反过来：查询问的是「所有 HTTP 入口」（一般），
    要展开成 `@GetMapping` / `@PostMapping`（具体）。
    而 `META_ANNOTATIONS` 记的是具体 → 一般，所以这里做一次反转。
    """
    table: dict[str, list[tuple[str, float, str]]] = {}
    for specific in _all_names():
        for general, score in expansions_for(specific).items():
            table.setdefault(general, []).append((specific, score, "meta"))
    for entries in table.values():
        entries.sort(key=lambda item: (-item[1], item[0]))
    return table


def _all_names() -> Iterator[str]:
    seen: set[str] = set()
    for name, parents in META_ANNOTATIONS.items():
        for candidate in (name, *parents):
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
