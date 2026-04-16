"""Minimal runner for manual keyword extraction verification."""

from query_processing import KeyBertExtractor

def main() -> None:
    extractor = KeyBertExtractor(top_n=6)
    query = "function that performs readahead in disk"
    result = extractor.extract_keywords(query)
    print(f"query: {query}")
    print("keywords:")
    for item in result:
        print(f"- {item['keyword']} -> {item['score']}")


if __name__ == "__main__":
    main()
