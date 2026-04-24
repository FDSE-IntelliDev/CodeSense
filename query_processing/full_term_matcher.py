"""Match query keywords to code symbols' sub-tokens via abbreviation."""
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from expansion.abbreviate import abbreviate
from definition import OUTPUT_DIR


class FullTermMatcher:
    """Resolve query keywords into candidate code symbols using index files."""

    def __init__(
        self,
        invert_index_path: str = "output/youlai-boot/invert_index.json",
        ngramed_symbol_path: str = "output/youlai-boot/ngramed_symbol.json",
    ) -> None:
        self.invert_index_path = invert_index_path
        self.ngramed_symbol_path = ngramed_symbol_path
        self.invert_index: Dict[str, Any] = self._load_json(invert_index_path)
        self.ngramed_symbol: Dict[str, Any] = self._load_json(ngramed_symbol_path)

    @staticmethod
    def _load_json(path: str) -> Dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(str(text).strip().lower().split())

    @staticmethod
    def _to_keyword_list(keyword_payload: Any) -> List[str]:
        """
        Accept flexible inputs:
        - list[str]
        - list[{"keyword": ...}] from keyword_extractor
        - dict with keys "keywords"/"expanded_keywords" from expansion output
        """
        if keyword_payload is None:
            return []

        out: List[str] = []

        if isinstance(keyword_payload, list):
            for item in keyword_payload:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    if "keyword" in item:
                        out.append(str(item.get("keyword", "")))
                    elif "text" in item:
                        out.append(str(item.get("text", "")))
            return [x for x in out if str(x).strip()]

        if isinstance(keyword_payload, dict):
            for key in ("keywords", "expanded_keywords"):
                value = keyword_payload.get(key, [])
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            out.append(item)
                        elif isinstance(item, dict):
                            if "keyword" in item:
                                out.append(str(item.get("keyword", "")))
                            elif "text" in item:
                                out.append(str(item.get("text", "")))
            return [x for x in out if str(x).strip()]

        return []

    def _keyword_subsequences(self, keyword: str) -> Set[str]:
        """Generate abbreviation subsequences for one keyword."""
        key = self._normalize(keyword)
        if not key:
            return set()

        subs: Set[str] = {key}
        try:
            abbr_result = abbreviate(key)
        except Exception:
            abbr_result = None

        if isinstance(abbr_result, str):
            if abbr_result.strip():
                subs.add(self._normalize(abbr_result))
        elif isinstance(abbr_result, (list, tuple, set)):
            for item in abbr_result:
                item_s = self._normalize(item)
                if item_s:
                    subs.add(item_s)

        # Add compact form to improve match chances (e.g., "read ahead" -> "readahead").
        compact = key.replace(" ", "")
        if compact:
            subs.add(compact)
        return subs

    def _resolve_subtoken_candidates(self, subseqs: Iterable[str]) -> Set[str]:
        """
        Map subsequences to sub-tokens via invert_index.
        invert_index assumed shape: subtoken -> [abbreviation...]
        """
        subtokens: Set[str] = set()
        normalized_subseqs = {self._normalize(s) for s in subseqs if self._normalize(s)}

        for subtoken, abbr_list in self.invert_index.items():
            if not isinstance(abbr_list, list):
                continue
            normalized_abbrs = {self._normalize(item) for item in abbr_list if self._normalize(item)}
            intersection=normalized_subseqs & normalized_abbrs
            if intersection:
                st = self._normalize(subtoken)
                if st:
                    subtokens.add(st)

        return subtokens

    def _resolve_symbols(self, subtokens: Iterable[str]) -> List[Dict[str, Any]]:
        """
        Map subtokens to final symbols via ngramed_symbol.
        ngramed_symbol shape: subtoken -> [symbol_dict, ...]
        """
        symbols: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        for st in subtokens:
            mapped = self.ngramed_symbol.get(st)
            if not isinstance(mapped, list):
                continue

            for symbol in mapped:
                if not isinstance(symbol, dict):
                    continue

                name = str(symbol.get("name", "")).strip()
                file_path = str(symbol.get("file", "")).strip()
                rng = symbol.get("range", {}) or {}
                start_line = rng.get("start_line", "")
                end_line = rng.get("end_line", "")
                unique_id = f"{name}|{file_path}|{start_line}|{end_line}"

                if unique_id in seen:
                    continue
                seen.add(unique_id)
                symbols.append(symbol)

        return symbols

    def match_keywords(self, keyword_payload: Any) -> Dict[str, Any]:
        """
        End-to-end matching:
        keyword -> subsequences -> invert_index subtokens -> ngramed_symbol symbols
        """
        keywords = self._to_keyword_list(keyword_payload)

        all_symbols: Set[str] = set()
        detail: List[Dict[str, Any]] = []

        for kw in keywords:
            normalized_kw = self._normalize(kw)
            if not normalized_kw:
                continue

            subseqs = self._keyword_subsequences(normalized_kw)
            subtokens = self._resolve_subtoken_candidates(subseqs)
            symbols = self._resolve_symbols(subtokens)  # 匹配到子词之后通过该函数使用倒排索引直接定位到相关的代码元素

            for sym in symbols:
                name = str(sym.get("name", "")).strip()
                file_path = str(sym.get("file", "")).strip()
                rng = sym.get("range", {}) or {}
                start_line = rng.get("start_line", "")
                end_line = rng.get("end_line", "")
                unique_id = f"{name}|{file_path}|{start_line}|{end_line}"
                if unique_id in all_symbols:
                    continue
                all_symbols.add(unique_id)

            detail.append(
                {
                    "keyword": normalized_kw,
                    "subsequences": sorted(subseqs),
                    "matched_subtokens": sorted(subtokens),
                    "matched_symbols": symbols,
                }
            )

        return {
            "matched_symbols": [d["matched_symbols"] for d in detail if d.get("matched_symbols")],
            "detail": detail,
        }



if __name__ == "__main__":
    matcher = FullTermMatcher(
        invert_index_path=f"{OUTPUT_DIR}/youlai-boot-master/invert_index.json",
        ngramed_symbol_path=f"{OUTPUT_DIR}/youlai-boot-master/ngramed_symbol.json",
    )

    payload = {
        "keywords": [{"text": "read ahead", "score": 0.9}],
    }

    result = matcher.match_keywords(payload)
    print(result["matched_symbols"])
    print(result["detail"])