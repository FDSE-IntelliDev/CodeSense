"""元注解表的单元测试。纯数据推导，不需要任何第三方依赖。"""

from __future__ import annotations

from codesense.indexing import META_ANNOTATIONS, expansions_for, meta_expansion_table


class TestExpansionsFor:
    def test_直接关系(self) -> None:
        assert "@RequestMapping" in expansions_for("@GetMapping")

    def test_直接关系分数是1_它是事实不是估计(self) -> None:
        assert expansions_for("@GetMapping")["@RequestMapping"] == 1.0

    def test_传递关系(self) -> None:
        """`@Service` → `@Component`，`@RestController` → `@Component`。"""
        assert "@Component" in expansions_for("@Service")

    def test_未知注解返回空(self) -> None:
        assert expansions_for("@ProjectSpecific") == {}

    def test_不含自己(self) -> None:
        assert "@GetMapping" not in expansions_for("@GetMapping")

    def test_深度有上限_防环也防过度传播(self) -> None:
        """`@SpringBootApplication` → `@Configuration` → `@Component` 是两跳。"""
        shallow = expansions_for("@SpringBootApplication", max_depth=1)
        deep = expansions_for("@SpringBootApplication", max_depth=3)
        assert "@Component" not in shallow
        assert "@Component" in deep

    def test_传递关系比直接关系弱(self) -> None:
        deep = expansions_for("@SpringBootApplication", max_depth=3)
        assert deep["@Component"] < deep["@Configuration"]


class TestMetaExpansionTable:
    def test_方向是一般到具体(self) -> None:
        """查询问「所有 HTTP 入口」，要展开成 GetMapping / PostMapping。"""
        table = meta_expansion_table()
        targets = {name for name, _, _ in table["@RequestMapping"]}
        assert {"@GetMapping", "@PostMapping", "@DeleteMapping"} <= targets

    def test_component_能展开到所有构造型(self) -> None:
        targets = {name for name, _, _ in meta_expansion_table()["@Component"]}
        assert {"@Service", "@Repository", "@Controller", "@RestController"} <= targets

    def test_理由标成_meta(self) -> None:
        assert all(
            reason == "meta"
            for entries in meta_expansion_table().values()
            for _, _, reason in entries
        )

    def test_按分数降序(self) -> None:
        for entries in meta_expansion_table().values():
            assert [s for _, s, _ in entries] == sorted((s for _, s, _ in entries), reverse=True)

    def test_表里没有自环(self) -> None:
        for key, entries in meta_expansion_table().items():
            assert key not in {name for name, _, _ in entries}

    def test_声明表本身没有自环(self) -> None:
        assert all(name not in parents for name, parents in META_ANNOTATIONS.items())
