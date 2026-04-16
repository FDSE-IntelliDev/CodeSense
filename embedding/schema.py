"""Shared dataclasses and ID helpers for embedding dataset schema."""

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class SymbolDocument:
    symbol_id: str
    name: str
    type: str
    language: str
    file: str
    container: str
    signature: str
    doc: str
    start_line: int
    end_line: int
    neighbors: Dict[str, List[str]] = field(default_factory=dict)
    deps: List[str] = field(default_factory=list)
    text: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TrainPair:
    query_id: str
    query_text: str
    positive_symbol_id: str
    negative_symbol_ids: List[str]
    meta: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


def build_symbol_id(file_path: str, name: str, start_line: Optional[int], end_line: Optional[int]) -> str:
    s = start_line if isinstance(start_line, int) else -1
    e = end_line if isinstance(end_line, int) else -1
    return f"{file_path}::{name}::{s}-{e}"
