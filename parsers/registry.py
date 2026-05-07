import pathlib
from typing import Tuple, List

from .java_parser import parse_java
from .javascript_parser import parse_javascript_typescript
from .python_parser import parse_python
from .c_cpp_parser import parse_c_cpp
from .ctags_parser import parse_c_cpp_ctags


LANG_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".rs": "rust",
    ".php": "php",
    ".rb": "ruby",
}


def get_language_for_file(file_path: str) -> str:
    return LANG_EXT.get(pathlib.Path(file_path).suffix.lower(), "")


def parse_file_with_registry(file_rel: str, file_path: str, source: str):
    lang = get_language_for_file(file_path)

    if lang == "python":
        return parse_python(file_rel, source)

    if lang in ("javascript", "typescript"):
        return parse_javascript_typescript(file_rel, source, lang)

    if lang == "java":
        return parse_java(file_rel, source)

    if lang in ("c", "cpp"):
        return parse_c_cpp_ctags(file_rel, source, lang)
        # return parse_c_cpp(file_rel, source, lang)

    return [], [], []
