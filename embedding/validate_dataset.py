"""Validate generated embedding dataset files and print basic statistics."""

import argparse
import json
from pathlib import Path


def count_jsonl(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as f:
        for _ in f:
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate generated embedding dataset files")
    parser.add_argument("--data-dir", type=str, default="embedding/data")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    docs_file = data_dir / "documents.jsonl"
    pairs_file = data_dir / "train_pairs.jsonl"
    manifest_file = data_dir / "manifest.json"

    if not docs_file.exists() or not pairs_file.exists():
        raise FileNotFoundError(f"Missing dataset files under {data_dir}")

    num_docs = count_jsonl(docs_file)
    num_pairs = count_jsonl(pairs_file)
    print(f"documents: {num_docs}")
    print(f"train_pairs: {num_pairs}")

    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        print(f"manifest: {json.dumps(manifest, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
