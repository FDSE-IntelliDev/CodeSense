"""Match query keywords to code symbols' sub-tokens via abbreviation."""
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from expansion.abbreviate import abbreviate,normalize_entity
from definition import OUTPUT_DIR
from embedding.embedding_main import score_pair
from query_processing.semql_utils import extract_semql_text_terms

import itertools


def generate_ordered_subterms(keyword: str) -> List[str]:
    """
    Split the keyword by space and generate all possible combinations of the parts
    while preserving their original order.
    """
    parts = keyword.strip().split()
    if not parts:
        return []

    subterms: Set[str] = set()

    # Generate combinations from length 1 to len(parts)
    for length in range(1, len(parts) + 1):
        for combo in itertools.combinations(parts, length):
            subterms.add(" ".join(combo))

    return sorted(list(subterms))

# res=generate_ordered_subterms("user login authentication")
# print(res)


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
        self.abbr_to_subtokens: Dict[str, Set[str]] = self._build_abbr_to_subtokens()

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
    def _to_keyword_list(keyword_payload: Any, properties: tuple = ("include",)) -> List[str]:
        """
        Parse query DSL/SemQL payload and extract searchable terms.
        Returns a deduplicated list while preserving order.

        properties: which property groups to extract from (default include only;
                    pass ("exclude",) to extract exclude terms).
        """
        terms: List[str] = []
        for condition_type, term_name in (
            ("surface", "keywords"),
            ("surface", "synonyms"),
            # ("intention", "keywords"),
            # ("intention", "intent"),
        ):
            terms.extend(
                extract_semql_text_terms(
                    keyword_payload,
                    properties=properties,
                    condition_type=condition_type,
                    term_name=term_name,
                )
            )
        seen: Set[str] = set()
        out: List[str] = []
        for term in terms:
            if term in seen:
                continue
            seen.add(term)
            out.append(term)
        return out

    def _keyword_subsequences(self, keyword: str) -> Set[str]:
        """Generate abbreviation subsequences for one keyword."""
        key =keyword
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
        Map subsequences to sub-tokens via precomputed abbreviation -> subtoken index.
        """
        subtokens: Set[str] = set()
        for subseq in subseqs:
            normalized = self._normalize(subseq)
            if not normalized:
                continue
            subtokens.update(self.abbr_to_subtokens.get(normalized, set()))
        return subtokens

    def _build_abbr_to_subtokens(self) -> Dict[str, Set[str]]:
        """Build reverse index: normalized abbreviation -> subtokens."""
        out: Dict[str, Set[str]] = {}
        for subtoken, abbr_list in self.invert_index.items():
            if not isinstance(abbr_list, list):
                continue
            st = self._normalize(subtoken)
            if not st:
                continue
            for item in abbr_list:
                abbr = self._normalize(item)
                if not abbr:
                    continue
                out.setdefault(abbr, set()).add(st)
        return out

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

                unique_id= symbol.get("symbol_id")

                if unique_id in seen:
                    continue
                seen.add(unique_id)
                symbols.append(symbol)

        return symbols

    def match_keywords(self, keyword_payload: Any, properties: tuple = ("include",)) -> Dict[str, Any]:
        """
        End-to-end matching:
        keyword -> subsequences -> invert_index subtokens -> ngramed_symbol symbols
        """
        keywords = self._to_keyword_list(keyword_payload, properties)

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

    def match_ngram(self, keyword_payload: Any, properties: tuple = ("include",)) -> Dict[str, Any]:
        """
        Aggregate subtokens across all keywords:
        keyword -> subsequences -> invert_index subtokens
        """
        initial_keywords = self._to_keyword_list(keyword_payload, properties)

        # keywords按照空格拆分有序子词进行扩展
        keywords: List[str] = []
        seen_kw: Set[str] = set()
        for kw in initial_keywords:
            for subkw in generate_ordered_subterms(kw):
                if subkw not in seen_kw:
                    seen_kw.add(subkw)
                    keywords.append(subkw)

        detail: List[Dict[str, Any]] = []
        all_subtokens: Set[str] = set()

        for kw in keywords:
            normalized_kw_list = normalize_entity(kw)
            if not normalized_kw_list:
                continue

            subseqs = set()
            for normalized_kw in normalized_kw_list:
                if not normalized_kw:
                    continue
                sub_result = self._keyword_subsequences(normalized_kw)

                # 使用 embedding model 对生成的子序列进行过滤。
                # 只保留得分 >= 0.4 的子序列，避免一些由算法生成但语义不相关的子序列进入后续匹配。
                # 如果所有候选都低于 0.4，则 fallback 到得分最高的 top3（候选不足 3 个则全保留），避免过度过滤导致召回为空。
                scored_sub_results = []
                for sub in sub_result:
                    if sub == normalized_kw:
                        # scored_sub_results.append((sub, 1.0))
                        continue
                    try:
                        score_result = score_pair(normalized_kw, sub)
                        score = float(score_result.get('final_score', 0.0))
                    except Exception:
                        score = 0.0
                    scored_sub_results.append((sub, score))

                filtered_sub_result = {sub for sub, score in scored_sub_results if score >= 0.4}
                if not filtered_sub_result and scored_sub_results:
                    top_scored = sorted(scored_sub_results, key=lambda x: x[1], reverse=True)[:3]
                    filtered_sub_result = {sub for sub, _ in top_scored}

                # 始终保留原始 normalized keyword，避免模型过滤导致原词丢失。
                filtered_sub_result.add(normalized_kw)
                subseqs.update(filtered_sub_result)

            subtokens = self._resolve_subtoken_candidates(subseqs)

            all_subtokens.update(subtokens)

            detail.append(
                {
                    "keyword": kw,
                    "normalized_kws": normalized_kw_list,
                    "subsequences": sorted(subseqs),
                    "matched_subtokens": sorted(subtokens),
                }
            )

        return {
            "matched_subtokens": sorted(all_subtokens),
            "detail": detail,
        }


if __name__ == "__main__":
    matcher = FullTermMatcher(
        invert_index_path=f"{OUTPUT_DIR}/youlai-boot-master/invert_index.json",
        ngramed_symbol_path=f"{OUTPUT_DIR}/youlai-boot-master/ngramed_symbol.json",
    )


    with open(f'/Users/huangzhuochen/PycharmProjects/CodeSearch/DSL/extracted_results.json', 'r', encoding='utf-8') as f:
        payload = json.load(f)[0]  # 取最后一次提取的结果作为输入

    # result = matcher.match_keywords(payload)
    result=matcher.match_ngram(payload)
    with open(f'{OUTPUT_DIR}/full_term_match_result.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    # print(result["matched_symbols"])
    # print(result["detail"])
