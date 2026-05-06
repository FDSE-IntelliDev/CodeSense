import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _load_symbols(symbols_index_path: str) -> List[Dict[str, Any]]:
    path = Path(symbols_index_path)
    if not path.exists():
        raise FileNotFoundError(f"symbols_index.json not found: {symbols_index_path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("symbols_index.json must be a JSON array")

    return [item for item in data if isinstance(item, dict)]


def _normalize_keywords(keywords: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for kw in keywords:
        k = str(kw).strip()
        if not k:
            continue
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _build_pattern_from_keywords(
    keywords: Iterable[str],
    mode: str = "or",
    use_word_boundary: bool = False,
) -> str:
    kws = _normalize_keywords(keywords)
    if not kws:
        raise ValueError("keywords must not be empty")

    escaped = [re.escape(k) for k in kws]

    if use_word_boundary:
        # 单词边界模式：\bkw\b
        wrapped = [rf"\b{e}\b" for e in escaped]
    else:
        # 子串包含模式：.*kw.*
        wrapped = [rf".*{e}.*" for e in escaped]

    if mode.lower() == "and":
        # all keywords must appear
        if use_word_boundary:
            return "".join(f"(?=.*{w})" for w in wrapped) + ".*"
        return "".join(f"(?=.*{e})" for e in escaped) + ".*"

    # any keyword appears
    return "(" + "|".join(wrapped) + ")"



def search_symbols_by_keywords(
    symbols_index_path: str,
    keywords: Iterable[str],
    mode: str = "or",
    case_sensitive: bool = False,
    use_word_boundary: bool = False,
    deduplicate: bool = True,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Search symbols by running keyword search per-keyword, then merge results.
    mode:
    - "or": union of per-keyword matches
    - "and": intersection of per-keyword matches
    """
    symbols = _load_symbols(symbols_index_path)
    norm_keywords = _normalize_keywords(keywords)
    if not norm_keywords:
        return {
            "keywords": [],
            "mode": mode,
            "patterns": [],
            "count": 0,
            "matched_symbols": [],
            "per_keyword_count": {},
        }

    # 预先缓存每个 symbol 的唯一 id（便于交并集和最终去重）
    symbol_with_uid: List[tuple[str, Dict[str, Any]]] = []
    for item in symbols:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        file_path = str(item.get("file", "")).strip()
        rng = item.get("range", {}) or {}
        start_line = rng.get("start_line", "")
        end_line = rng.get("end_line", "")
        uid=item.get("symbol_id")
        symbol_with_uid.append((uid, item))

    # 每个关键词单独搜索
    per_kw_uids: Dict[str, set[str]] = {}
    uid_to_item: Dict[str, Dict[str, Any]] = {uid: item for uid, item in symbol_with_uid}
    patterns: List[str] = []

    for kw in norm_keywords:
        pattern = _build_pattern_from_keywords(
            keywords=[kw],
            mode="or",  # 单关键词时用 or 等价且更直观
            use_word_boundary=use_word_boundary,
        )
        patterns.append(pattern)
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        matched_uids: set[str] = set()
        for uid, item in symbol_with_uid:
            name = str(item.get("name", "")).strip()
            if regex.search(name):
                matched_uids.add(uid)

        per_kw_uids[kw] = matched_uids

    # 按 mode 合并结果
    if mode.lower() == "and":
        merged_uids = set.intersection(*(s for s in per_kw_uids.values())) if per_kw_uids else set()
    else:
        merged_uids = set.union(*(s for s in per_kw_uids.values())) if per_kw_uids else set()

    # 输出前保持稳定顺序（沿用 symbols_index 原始顺序）
    matched: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for uid, item in symbol_with_uid:
        if uid not in merged_uids:
            continue
        if deduplicate:
            if uid in seen:
                continue
            seen.add(uid)
        matched.append(item)
        if limit is not None and limit > 0 and len(matched) >= limit:
            break

    return {
        "keywords": norm_keywords,
        "mode": mode,
        "patterns": patterns,  # 每个关键词对应一个 pattern
        "count": len(matched),
        "matched_symbols": matched,
        "per_keyword_count": {k: len(v) for k, v in per_kw_uids.items()},
    }

