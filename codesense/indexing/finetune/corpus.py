"""Small disk-backed corpus that can be iterated for every training epoch."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path


class CorpusStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def append(self, file: str, sentences: Sequence[Sequence[str]]) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            for sentence in sentences:
                stream.write(json.dumps({"file": file, "tokens": list(sentence)}))
                stream.write("\n")

    def sentences(self) -> Iterator[list[str]]:
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                yield list(json.loads(line)["tokens"])

    def __iter__(self) -> Iterator[list[str]]:
        return self.sentences()

    def __len__(self) -> int:
        with self.path.open(encoding="utf-8") as stream:
            return sum(1 for _ in stream)
