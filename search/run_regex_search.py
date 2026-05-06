import json

from definition import OUTPUT_DIR
from search.regex_search import search_symbols_by_keywords


def main() -> None:
    result = search_symbols_by_keywords(
        symbols_index_path=f"{OUTPUT_DIR}/youlai-boot-master/symbols_index.json",
        keywords=["readahead","ra"],
        mode="and",
        case_sensitive=False,
        use_word_boundary=False,
        limit=50,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
