# This module defines call graph resolvers (LSP-first, conservative fallback).

from typing import Dict, List

from .base import CallItem, SymbolItem


class BaseCallResolver:
    """Base class for resolving a call site to concrete callee targets."""

    def resolve(
        self,
        caller: str,
        callee: str,
        line: int,
        code: str,
        caller_file: str,
        symbols_lookup: Dict[str, List[SymbolItem]],
    ) -> List[CallItem]:
        raise NotImplementedError


class LSPCallResolver(BaseCallResolver):
    """LSP-first resolver placeholder.

    Current implementation is conservative and avoids global same-name matching
    to prevent false cross-file edges. It keeps callee_file empty when unresolved.
    """

    def __init__(self, project_path: str):
        self.project_path = project_path

    def resolve(
        self,
        caller: str,
        callee: str,
        line: int,
        code: str,
        caller_file: str,
        symbols_lookup: Dict[str, List[SymbolItem]],
    ) -> List[CallItem]:
        return [
            CallItem(
                caller=caller,
                callee=callee,
                caller_file=caller_file,
                callee_file="",
                call_site={"line": line, "code": code},
            )
        ]
