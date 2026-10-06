"""Tests for answer cleaning: which functions a Java file really declares."""

from __future__ import annotations

from pathlib import Path

from evaluation.answer_cleaning import (
    clean_record,
    declared_functions,
    normalize_function_name,
)

_JAVA = """
package com.amihaiemil.eoyaml;

public final class ReadPlainScalarValue {
    private final YamlLine line;

    public ReadPlainScalarValue(final YamlLine line) {
        this.line = line;
    }

    public String value() {
        return this.line.trimmed();
    }

    void helper(int x) { }

    void helper(String s) { }

    static class Inner {
        void innerMethod() { }
    }
}
"""


def test_normalize_function_name_strips_params_and_qualifiers() -> None:
    assert normalize_function_name("com.foo.Bar.value(int x)") == "value"
    assert normalize_function_name("  value() ") == "value"
    assert normalize_function_name(None) == ""


def test_declared_functions_includes_methods_and_constructors() -> None:
    names = declared_functions(_JAVA)
    assert "value" in names
    assert "helper" in names  # overloads collapse to one name
    assert "innerMethod" in names  # inner-class method via recursion
    assert "ReadPlainScalarValue" in names  # constructor counts


def test_declared_functions_excludes_fields_and_type_names() -> None:
    names = declared_functions(_JAVA)
    assert "line" not in names  # field, not callable
    assert "Inner" not in names  # nested class, not callable


def _write_repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "src/main/java/com/amihaiemil/eoyaml"
    pkg.mkdir(parents=True)
    (pkg / "ReadPlainScalarValue.java").write_text(
        "package com.amihaiemil.eoyaml;\n"
        "public final class ReadPlainScalarValue {\n"
        '    public String value() { return ""; }\n'
        "}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_clean_record_marks_function_existence_without_dropping_answers(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    record = {
        "query": "q",
        "answer": [],
        "trace_answer": [
            {
                "file": "src/main/java/com/amihaiemil/eoyaml/ReadPlainScalarValue.java",
                "functions": ["value", "unquote"],
            }
        ],
        "candidate_answers": [],
    }

    cleaned = clean_record(record, repo)

    location = cleaned["trace_answer"][0]
    assert location["functions"] == ["value", "unquote"]
    assert location["file_exist"] is True
    assert location["function_exist"] == {"value": True, "unquote": False}
    # input not mutated
    assert record["trace_answer"][0]["functions"] == ["value", "unquote"]


def test_clean_record_marks_missing_file_and_all_its_functions_absent(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    record = {
        "answer": [{"file": "src/main/java/DoesNotExist.java", "functions": ["a", "b"]}],
        "trace_answer": [],
        "candidate_answers": [],
    }

    cleaned = clean_record(record, repo)

    assert cleaned["answer"][0]["functions"] == ["a", "b"]
    assert cleaned["answer"][0]["file_exist"] is False
    assert cleaned["answer"][0]["function_exist"] == {"a": False, "b": False}


def test_clean_record_annotates_all_three_answer_fields(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    f = "src/main/java/com/amihaiemil/eoyaml/ReadPlainScalarValue.java"
    record = {
        "answer": [{"file": f, "functions": ["value", "ghost1"]}],
        "trace_answer": [{"file": f, "functions": ["ghost2"]}],
        "candidate_answers": [{"file": f, "functions": ["value"]}],
    }

    cleaned = clean_record(record, repo)

    assert cleaned["answer"][0]["function_exist"] == {"value": True, "ghost1": False}
    assert cleaned["trace_answer"][0]["function_exist"] == {"ghost2": False}
    assert cleaned["candidate_answers"][0]["function_exist"] == {"value": True}
