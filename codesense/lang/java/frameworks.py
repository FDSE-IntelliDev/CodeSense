"""Meta-annotations: synonymy the frameworks declare themselves.

This is annotations' **unique advantage** over every other signal. In Spring
`@RestController` simply *is* `@Controller` + `@ResponseBody`, and
`@GetMapping` *is* `@RequestMapping(GET)` -- **that relation is a fact the
framework declares in source, not an estimate**, so it enters the expansion
table at score 1.0, alongside estimates like `prefix` and `ctx` but harder.

Three sources, cheapest first (``docs/design/09-grounding.md``, section 8):

1. the hardcoded table in this module -- common frameworks, a few dozen
   entries covering the vast majority
2. parsing the project's own ``@interface`` declarations -- a custom
   annotation's meta-annotations are right there in the source
3. falling back to name splitting -- for annotations nobody recognises

This module does layer 1 only: pure data plus a little derivation, **no IO**.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

__all__ = ["META_ANNOTATIONS", "expansions_for", "meta_expansion_table"]

#: Annotation to the annotations it is composed of (``@RestController``
#: contains ``@Controller``). The direction is specific to the more general
#: things it implies.
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
    # caching / transactions / scheduling
    "@Cacheable": ("@Caching",),
    "@CacheEvict": ("@Caching",),
    "@CachePut": ("@Caching",),
    # validation
    "@NotNull": ("@Constraint",),
    "@NotBlank": ("@Constraint",),
    "@NotEmpty": ("@Constraint",),
    "@Size": ("@Constraint",),
    "@Valid": ("@Constraint",),
    "@Validated": ("@Constraint",),
    # JPA
    "@Entity": ("@Table",),
    "@Id": ("@Column",),
    # testing
    "@Test": ("@Testable",),
    "@ParameterizedTest": ("@Testable",),
}

#: Score for a directly declared relation. It is a fact, not an estimate.
DIRECT_SCORE = 1.0

#: Decay per extra hop for transitive relations. `@GetMapping` to
#: `@RequestMapping` is direct; `@RestController` to `@Component` via
#: `@Controller` is transitive, and slightly weaker.
TRANSITIVE_DECAY = 0.8


def expansions_for(name: str, *, max_depth: int = 3) -> dict[str, float]:
    """Every annotation an annotation implies, with scores (transitively).

    ``max_depth`` guards against both cycles and over-propagation -- an
    implication more than three hops out means nothing in practice.
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
    """Build the shape the expansion table needs: **general to specific**.

    The direction has to flip. A query asks about "all HTTP entry points"
    (general) and must expand to `@GetMapping` / `@PostMapping` (specific),
    whereas `META_ANNOTATIONS` records specific to general -- so this
    inverts it once.
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
