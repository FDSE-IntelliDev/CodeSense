"""
Exact code search for SemQL exact_code field.

当前实现范围：
- 先判断 SemQL.has_exact_code 是否为 1
- 只处理 kind == "code_element"
- code_element_type 暂支持：function / method / class / variable / file
- enum 暂不处理
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from definition import OUTPUT_DIR, PROJECT_NAME
from parsers.read_tools import read_file_lines
from search.fuzzy_matcher import FuzzyMatcher


class ExactCodeSearcher:
    def __init__(self, symbols_index_path: str):
        self.symbols_index_path = symbols_index_path
        self.symbols = self._load_symbols(symbols_index_path)
        self.fuzzy_matcher = FuzzyMatcher()
        self._file_lines_cache: Dict[str, List[str]] = {}

    @staticmethod
    def _load_symbols(path: str) -> List[Dict[str, Any]]:
        p = Path(path)
        if not p.exists():
            return []
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    @staticmethod
    def _norm(text: Any) -> str:
        return str(text or "").strip()

    def search(self, semql: Dict[str, Any], top_k: int = 50) -> List[Dict[str, Any]]:
        """Run exact code search from SemQL."""
        if not isinstance(semql, dict):
            return []
        if str(semql.get("has_exact_code", "0")).strip() != "1":
            return []

        exact_items = semql.get("exact_code", [])
        if not isinstance(exact_items, list):
            return []

        results: List[Dict[str, Any]] = []
        seen = set()

        for item in exact_items:
            if not isinstance(item, dict):
                continue
            text = self._norm(item.get("text"))
            if not text:
                continue

            kind = self._norm(item.get("kind", "unknown")) or "unknown"
            if kind == "code_element":

                element_type = self._norm(item.get("code_element_type", "unknown")).lower() or "unknown"
                match_mode = self._norm(item.get("match_mode", "fuzzy_match").lower())

                for symbol in self.symbols:
                    match_info = self._match_code_element(text, element_type, match_mode, symbol)
                    if not match_info:
                        continue

                    symbol_id = symbol.get("symbol_id")
                    key = (symbol_id, text, match_info.get("field"), element_type)
                    if key in seen:
                        continue
                    seen.add(key)

                    record = dict(symbol)
                    record["_exact_code_query"] = text
                    record["_exact_code_kind"] = kind
                    record["_exact_code_element_type"] = element_type
                    record["_exact_code_match_mode"] = match_mode
                    record["_exact_match_field"] = match_info.get("field")
                    record["_exact_match_text"] = match_info.get("matched_text")
                    record["_exact_score"] = self._score_match(element_type, match_info.get("field"), match_mode)
                    results.append(record)
            elif kind == "code_line":
                match_mode = self._norm(item.get("match_mode", "fuzzy_match").lower())
                for symbol in self.symbols:
                    match_info = self._match_code_line(text, match_mode, symbol)
                    if not match_info:
                        continue
                    symbol_id = symbol.get("symbol_id")
                    key = (symbol_id, text, match_info.get("line"), "code_line")
                    if key in seen:
                        continue
                    seen.add(key)

                    record = dict(symbol)
                    record["_exact_code_query"] = text
                    record["_exact_code_kind"] = kind
                    record["_exact_code_match_mode"] = match_mode
                    record["_exact_match_field"] = "code_line"
                    record["_exact_match_text"] = match_info.get("matched_text")
                    record["_exact_match_line"] = match_info.get("line")
                    record["_exact_match_file"] = match_info.get("file")
                    record["_exact_score"] = match_info.get("score", 1.0)
                    results.append(record)

        results.sort(key=lambda x: x.get("_exact_score", 0.0), reverse=True)
        return results[:top_k]

    def _match_code_element(
        self,
        text: str,
        element_type: str,
        match_mode: str,
        symbol: Dict[str, Any],
    ) -> Dict[str, str]:
        if element_type == "enum":
            return {}

        if element_type == "file":
            file_path = self._norm(symbol.get("file"))
            if self._match_path_suffix(text, file_path):
                return {"field": "file", "matched_text": file_path}
            return {}

        symbol_type = self._norm(symbol.get("type")).lower()
        if not self._symbol_type_matches(element_type, symbol_type):
            return {}

        name = self._norm(symbol.get("name"))
        signature = self._norm(symbol.get("signature"))

        if self._match_name(text, name, match_mode):
            return {"field": "name", "matched_text": name}
        if self._match_name(text, signature, match_mode):
            return {"field": "signature", "matched_text": signature}

        return {}

    # todo 待优化：如果对类进行了code_line的扫描和匹配 应该可以同时关联到类内的函数等symbol 防止后续的重复扫描（同时要对不同代码语言做兼容）
    def _match_code_line(self, text: str, match_mode: str, symbol: Dict[str, Any]) -> Dict[str, Any]:
        file_path = self._norm(symbol.get("file"))
        rng = symbol.get("range", {}) or {}
        start_line = int(rng.get("start_line", 0) or 0)
        end_line = int(rng.get("end_line", 0) or 0)
        if not file_path or start_line <= 0 or end_line <= 0:
            return {}

        lines = self._get_file_lines(file_path)
        if not lines:
            return {}

        best = None
        start = max(1, start_line)
        end = min(len(lines), end_line)
        for line_no in range(start, end + 1):
            line_text = lines[line_no - 1]
            if match_mode == "exact_match":
                matched = self._normalize_code_line(text) == self._normalize_code_line(line_text)
                score = 1.6 if matched else 0.0
            else:
                score, matched = self.fuzzy_matcher.score(text, line_text, kind="code_line")
                score = float(score)

            if not matched:
                continue
            if best is None or score > best["score"]:
                best = {
                    "field": "code_line",
                    "matched_text": line_text.strip(),
                    "line": line_no,
                    "file": file_path,
                    "score": round(score + 1.0, 4),
                }

        return best or {}

    def _get_file_lines(self, file_path: str) -> List[str]:
        if file_path not in self._file_lines_cache:
            self._file_lines_cache[file_path] = read_file_lines(file_path)
        return self._file_lines_cache[file_path]

    @staticmethod
    def _normalize_code_line(text: str) -> str:
        return " ".join(str(text or "").strip().split())

    @staticmethod
    def _symbol_type_matches(element_type: str, symbol_type: str) -> bool:
        if element_type == "function":
            return symbol_type in {"function", "method"}
        if element_type == "method":
            return symbol_type == "method"
        if element_type == "class":
            return symbol_type == "class"
        if element_type == "variable":
            return symbol_type in {"variable", "field", "parameter", "constant"}
        # unknown 情况下，在已解析 symbol 的 name/signature 中找
        if element_type == "unknown":
            return symbol_type in {"function", "method", "class", "variable", "field", "parameter", "constant"}
        return False

    @staticmethod
    def _normalize_name(text: str) -> str:
        return str(text or "").strip().lower().replace("_", "")

    def _match_name(self, query_text: str, target_text: str, match_mode: str) -> bool:
        if not query_text or not target_text:
            return False
        if match_mode == "exact_match":
            return self._normalize_name(query_text) == self._normalize_name(target_text)
        return self.fuzzy_matcher.match(query_text, target_text, kind="code_element")

    @staticmethod
    def _path_parts(path_text: str) -> List[str]:
        normalized = str(path_text or "").replace("\\", "/").strip("/")
        if not normalized:
            return []
        return [part for part in normalized.split("/") if part]

    def _match_path_suffix(self, query_path: str, symbol_path: str) -> bool:
        """Match by path suffix.

        Examples:
        - query c.py matches a/b/c.py and a/d/c.py
        - query b/c.py matches a/b/c.py and d/b/c.py
        - query a/b/c.py does not match a/d/c.py
        """
        query_parts = self._path_parts(query_path)
        symbol_parts = self._path_parts(symbol_path)
        if not query_parts or not symbol_parts:
            return False
        if len(query_parts) > len(symbol_parts):
            return False
        return symbol_parts[-len(query_parts):] == query_parts

    @staticmethod
    def _score_match(element_type: str, field: str, match_mode: str) -> float:
        score = 1.0
        if field == "name":
            score += 0.5
        elif field == "signature":
            score += 0.35
        elif field == "file":
            score += 0.45
        if match_mode == "exact_match":
            score += 0.3
        if element_type in {"function", "method", "class", "variable"} and field == "name":
            score += 0.2
        return round(score, 4)


def exact_code_search(symbols_index_path: str, semql_path: str, top_k: int = 50) -> List[Dict[str, Any]]:
    p = Path(semql_path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as f:
        semql = json.load(f)
    semql={
  "intent": {
    "action": {
      "term": "find",
      "synonyms": [
        "search",
        "locate"
      ]
    },
    "object": {
      "term": "login function",
      "synonyms": [
        "login method",
        "authentication function",
        "sign-in function"
      ]
    }
  },
  "has_exact_code": "1",
  "exact_code": [
    {
      "text": "login",
      "kind": "code_element",
      "code_element_type": "function",
      "match_mode": "fuzzy_match",
      "source": "The query explicitly mentions 'login function', where 'login' may be a function name or part of a function name."
    }
  ],
  "keywords": [
    {
      "term": "login function",
      "synonyms": [
        "login method",
        "authentication function",
        "sign-in function"
      ]
    },
    {
      "term": "login",
      "synonyms": [
        "sign in",
        "authenticate",
        "authentication"
      ]
    }
  ],
  "target": [
    "function",
    "method"
  ],
  "filters": [
    {
      "concept": "login",
      "relation": "core responsibility"
    },
    {
      "concept": "authentication",
      "relation": "related domain"
    }
  ],
  "exclude": [
    "registration",
    "logout",
    "password reset"
  ],
  "raw_query": "Find the login function"
}

    semql={
  "intent": {
    "action": {
      "term": "find",
      "synonyms": [
        "search",
        "locate"
      ]
    },
    "object": {
      "term": "code line returning login token success result",
      "synonyms": [
        "return token success line",
        "successful login token return",
        "result success token return"
      ]
    }
  },
  "has_exact_code": "1",
  "exact_code": [
    {
      "text": "return Result.success(token);",
      "kind": "code_line",
      "code_element_type": "unknown",
      "match_mode": "fuzzy_match",
      "source": "The query explicitly mentions a code line that returns Result.success(token)."
    }
  ],
  "keywords": [
    {
      "term": "return success token",
      "synonyms": [
        "return token",
        "success result",
        "login token response"
      ]
    },
    {
      "term": "login token",
      "synonyms": [
        "authentication token",
        "access token",
        "jwt token"
      ]
    }
  ],
  "target": [
    "function",
    "method"
  ],
  "filters": [
    {
      "concept": "login",
      "relation": "related domain"
    },
    {
      "concept": "token",
      "relation": "returned result"
    }
  ],
  "exclude": [
    "registration",
    "logout",
    "password reset"
  ],
  "raw_query": "Find the code line return Result.success(token); in the login function"
}

    return ExactCodeSearcher(symbols_index_path).search(semql, top_k=top_k)


if __name__ == "__main__":
    symbols_index_path = f"{OUTPUT_DIR}/{PROJECT_NAME}/symbols_index.json"
    semql_path = f"{OUTPUT_DIR}/{PROJECT_NAME}/semQL.json"
    print(json.dumps(exact_code_search(symbols_index_path, semql_path, top_k=50), indent=4))
