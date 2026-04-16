from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class RangeInfo:
    start_line: int
    end_line: int


@dataclass
class SymbolItem:
    name: str
    type: str
    file: str
    range: RangeInfo
    signature: str
    language: str
    doc: str
    container: str


@dataclass
class CallItem:
    caller: str
    callee: str
    caller_file: str
    callee_file: str
    call_site: Dict[str, Any]


@dataclass
class DependencyItem:
    source_file: str
    target_file: str
    type: str
