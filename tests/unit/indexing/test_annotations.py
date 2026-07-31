"""注解抽取的单元测试。

`arg_tokens` / `posting_terms` 是纯函数，无条件跑；
抽取本身要 tree-sitter，没装就跳过（标 slow，默认不跑）。
"""

from __future__ import annotations

import pytest

from codesense.indexing import AnnotationUse, arg_tokens
from codesense.indexing.annotations import posting_terms

SOURCE = """
@RestController
@RequestMapping("/api/v1/users")
public class UserController {
    @Autowired private UserService svc;

    @Log(module = "user")
    @RepeatSubmit
    @GetMapping("/{id}")
    @PreAuthorize("@ss.hasPerm('sys:user:query')")
    public Result<UserVO> getUser(@PathVariable Long id) {
        Runnable r = new Runnable() {
            @Override public void run() {}
        };
        return null;
    }
}
"""


def naive_split(name: str) -> list[str]:
    import re

    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return [t.lower() for t in text.split() if t]


class TestArgTokens:
    def test_切出权限串的各段(self) -> None:
        assert arg_tokens("\"@ss.hasPerm('sys:user:query')\"") == (
            "ss",
            "hasperm",
            "sys",
            "user",
            "query",
        )

    def test_切出_url_路径段(self) -> None:
        assert arg_tokens('("/api/v1/users")') == ("api", "v1", "users")

    def test_保序去重(self) -> None:
        assert arg_tokens("(a, b, a)") == ("a", "b")

    def test_丢掉纯数字(self) -> None:
        assert "1" not in arg_tokens("(maxAge = 3600)")

    def test_空参数(self) -> None:
        assert arg_tokens("") == ()


class TestPostingTerms:
    def _use(self, name: str = "CacheEvict", args: str = "") -> AnnotationUse:
        return AnnotationUse(name=name, args=args, target_kind="method", target_name="f", line=1)

    def test_整体名字进_annotation_域(self) -> None:
        assert ("@CacheEvict", "annotation") in posting_terms(self._use(), naive_split)

    def test_切分单元也进_annotation_域(self) -> None:
        """这样项目自定义的 `@AppCache` 也能命中 `cache` 单元。"""
        terms = posting_terms(self._use(), naive_split)
        assert ("cache", "annotation") in terms
        assert ("evict", "annotation") in terms

    def test_参数进_annotation_arg_域(self) -> None:
        terms = posting_terms(self._use(args='(value = "userCache")'), naive_split)
        assert ("annotation_arg") in {field for _, field in terms}
        assert ("usercache", "annotation_arg") in terms

    def test_去重(self) -> None:
        terms = posting_terms(self._use(name="Cache", args="(cache)"), naive_split)
        assert len(terms) == len(set(terms))


@pytest.mark.slow
class TestJavaAnnotationExtractor:
    """需要 tree-sitter。"""

    @pytest.fixture(scope="class")
    def uses(self) -> list[AnnotationUse]:
        pytest.importorskip("tree_sitter_languages")
        from codesense.indexing import JavaAnnotationExtractor

        return JavaAnnotationExtractor.for_java().extract(SOURCE)

    def test_抽到类上的注解(self, uses: list[AnnotationUse]) -> None:
        found = {(u.name, u.target_name) for u in uses if u.target_kind == "class"}
        assert ("RestController", "UserController") in found

    def test_抽到方法上的注解(self, uses: list[AnnotationUse]) -> None:
        found = {u.name for u in uses if u.target_name == "getUser"}
        assert {"Log", "RepeatSubmit", "GetMapping", "PreAuthorize"} <= found

    def test_抽到字段上的注解(self, uses: list[AnnotationUse]) -> None:
        assert ("Autowired", "field", "svc") in {
            (u.name, u.target_kind, u.target_name) for u in uses
        }

    def test_抽到参数上的注解(self, uses: list[AnnotationUse]) -> None:
        assert ("PathVariable", "parameter") in {(u.name, u.target_kind) for u in uses}

    def test_保留参数原文(self, uses: list[AnnotationUse]) -> None:
        pre = next(u for u in uses if u.name == "PreAuthorize")
        assert "sys:user:query" in pre.args

    def test_匿名类里的注解不算到外层方法头上(self, uses: list[AnnotationUse]) -> None:
        """方法体里可能有匿名类，整棵子树扫就会张冠李戴。"""
        assert "Override" not in {u.name for u in uses if u.target_name == "getUser"}
        assert ("Override", "run") in {(u.name, u.target_name) for u in uses}

    def test_记录行号(self, uses: list[AnnotationUse]) -> None:
        assert all(u.line > 0 for u in uses)
