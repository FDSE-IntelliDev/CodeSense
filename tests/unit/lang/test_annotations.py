"""Unit tests for annotation extraction.

`arg_tokens` and `posting_terms` are pure functions and run unconditionally;
extraction itself needs tree-sitter and is skipped when it is missing (marked
slow, so it does not run by default).
"""

from __future__ import annotations

import pytest

from codesense.lang import AnnotationUse
from codesense.lang.java import arg_tokens
from codesense.lang.java.annotations import posting_terms

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
    def test_splits_the_pieces_of_a_permission_string(self) -> None:
        assert arg_tokens("\"@ss.hasPerm('sys:user:query')\"") == (
            "ss",
            "hasperm",
            "sys",
            "user",
            "query",
        )

    def test_splits_url_path_segments(self) -> None:
        assert arg_tokens('("/api/v1/users")') == ("api", "v1", "users")

    def test_deduplicates_in_order(self) -> None:
        assert arg_tokens("(a, b, a)") == ("a", "b")

    def test_drops_bare_numbers(self) -> None:
        assert "1" not in arg_tokens("(maxAge = 3600)")

    def test_empty_arguments(self) -> None:
        assert arg_tokens("") == ()


class TestPostingTerms:
    def _use(self, name: str = "CacheEvict", args: str = "") -> AnnotationUse:
        return AnnotationUse(name=name, args=args, target_kind="method", target_name="f", line=1)

    def test_the_whole_name_goes_to_the_annotation_field(self) -> None:
        assert ("@CacheEvict", "annotation") in posting_terms(self._use(), naive_split)

    def test_split_units_go_to_the_annotation_field_too(self) -> None:
        """So a project's own `@AppCache` can match a `cache` unit too."""
        terms = posting_terms(self._use(), naive_split)
        assert ("cache", "annotation") in terms
        assert ("evict", "annotation") in terms

    def test_arguments_go_to_the_annotation_arg_field(self) -> None:
        terms = posting_terms(self._use(args='(value = "userCache")'), naive_split)
        assert ("annotation_arg") in {field for _, field in terms}
        assert ("usercache", "annotation_arg") in terms

    def test_deduplicates(self) -> None:
        terms = posting_terms(self._use(name="Cache", args="(cache)"), naive_split)
        assert len(terms) == len(set(terms))


@pytest.mark.slow
class TestJavaAnnotationExtraction:
    """Needs tree-sitter."""

    @pytest.fixture(scope="class")
    def uses(self) -> list[AnnotationUse]:
        pytest.importorskip("tree_sitter_languages")
        from codesense.lang.java import JavaDeclarationScanner

        return JavaDeclarationScanner.for_java().annotations(SOURCE)

    def test_extracts_annotations_on_a_class(self, uses: list[AnnotationUse]) -> None:
        found = {(u.name, u.target_name) for u in uses if u.target_kind == "class"}
        assert ("RestController", "UserController") in found

    def test_extracts_annotations_on_a_method(self, uses: list[AnnotationUse]) -> None:
        found = {u.name for u in uses if u.target_name == "getUser"}
        assert {"Log", "RepeatSubmit", "GetMapping", "PreAuthorize"} <= found

    def test_extracts_annotations_on_a_field(self, uses: list[AnnotationUse]) -> None:
        assert ("Autowired", "field", "svc") in {
            (u.name, u.target_kind, u.target_name) for u in uses
        }

    def test_extracts_annotations_on_a_parameter(self, uses: list[AnnotationUse]) -> None:
        assert ("PathVariable", "parameter") in {(u.name, u.target_kind) for u in uses}

    def test_keeps_the_raw_argument_text(self, uses: list[AnnotationUse]) -> None:
        pre = next(u for u in uses if u.name == "PreAuthorize")
        assert "sys:user:query" in pre.args

    def test_annotations_in_an_anonymous_class_not_attributed_to_the_outer_method(
        self, uses: list[AnnotationUse]
    ) -> None:
        """A method body may hold an anonymous class, and scanning the whole
        subtree would attribute its annotations to the wrong declaration."""
        assert "Override" not in {u.name for u in uses if u.target_name == "getUser"}
        assert ("Override", "run") in {(u.name, u.target_name) for u in uses}

    def test_records_the_line_number(self, uses: list[AnnotationUse]) -> None:
        assert all(u.line > 0 for u in uses)
