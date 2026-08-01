"""The end-to-end entry point: a repository in, searchable results out.

    from codesense import Project

    project = Project.build("~/src/netty", index_dir="~/.codesense/netty")
    print(project.search("backpressure when the write buffer fills up").explain())

    project = Project.open("~/.codesense/netty")   # later, without rebuilding

`build` runs four stages, and the split between them is about cost, not tidiness:

    scan       tree-sitter over every Java file -- minutes on a large repo
    index      symbols, postings, edges                (same pass as scan)
    ground     abbreviation rules, optionally vectors  (seconds to ~an hour)
    save       one directory, reloadable in seconds

**Grounding is a build-time stage whose output is a few hundred KB.** Search
loads the index and the grounding table; it never loads a 7GB vector model.
That is what makes `open` fast enough to be worth having.

Everything is a parameter. Nothing here reads a config file, because a config
file becomes a second source of truth and "which value is actually in effect"
stops being answerable. The one exception is the API key, which cannot be a
command-line argument without landing in shell history.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codesense.index import Index, IndexMeta
from codesense.search import SearchResult, search

__all__ = ["Project"]

_log = logging.getLogger(__name__)


class _Default:
    """Marks "the caller said nothing", which differs from "the caller said no".

    ``index_dir=None`` means build in memory and save nothing; omitting it
    means save beside the source. A plain None default cannot express both.
    """

    def __repr__(self) -> str:
        return "<default>"


DEFAULT = _Default()

#: Vocabulary handed to the model. Enough for it to see the domain, small
#: enough to leave room for the operator spec in the same prompt.
DEFAULT_VOCAB = 1200


class Project:
    """An indexed repository you can query.

    Construct with `build` (from source) or `open` (from a saved index). The
    constructor takes an already-loaded `Index`, so tests can build one in
    memory without touching disk.
    """

    def __init__(self, index: Index, *, llm: Any = None, vocab_size: int = DEFAULT_VOCAB) -> None:
        self.index = index
        self.llm = llm
        self._vocab_size = vocab_size
        self._context: Any = None
        self._vocabulary: list[tuple[str, int]] | None = None

    # -- construction ----------------------------------------------------

    @classmethod
    def build(
        cls,
        root: Path | str,
        *,
        index_dir: Path | str | None | _Default = DEFAULT,
        strategy: str = "lexical",
        model_path: Path | str | None = None,
        llm: Any = None,
        name: str = "",
        verbose: bool = True,
        **grounding: Any,
    ) -> Project:
        """Scan a repository and build an index.

        ``strategy`` selects grounding: ``lexical`` needs nothing, ``vectors``
        and ``finetune`` need ``model_path`` pointing at a fastText ``.bin``.
        See `codesense.indexing.grounding` for what each buys.

        ``index_dir`` defaults to ``<root>/.codesense``. Passing None for it
        explicitly is not the same as omitting it -- None means build in memory
        and save nothing.
        """
        from codesense.indexing.grounding import GroundingConfig, ground_vocabulary
        from codesense.indexing.pipeline import build_index

        source = Path(root).expanduser().resolve()
        if not source.is_dir():
            raise NotADirectoryError(f"{source} is not a directory")
        project_name = name or source.name

        report = _reporter(verbose)
        report(f"scanning {source} ...")
        result = build_index(
            source,
            progress=lambda files, symbols: report(f"  {files} files, {symbols} symbols ..."),
        )
        stats = result.stats
        report(
            f"  {stats.files} files -> {stats.symbols:,} symbols, "
            f"{stats.postings:,} postings, {stats.edges:,} edges"
            + (f" ({stats.failed} files failed to parse)" if stats.failed else "")
        )

        index = Index(
            meta=IndexMeta(
                project=project_name,
                root=str(source),
                built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                symbols=stats.symbols,
                postings=stats.postings,
                edges=stats.edges,
                language=", ".join(stats.languages),
            ),
            payload=result.payload,
        )

        report(f"grounding vocabulary ({strategy}) ...")
        config = GroundingConfig(
            strategy=strategy,
            model_path=Path(model_path).expanduser() if model_path else None,
            **grounding,
        )
        index.expansion = ground_vocabulary(
            dict(index.vocabulary()),
            stats.symbols,
            config=config,
            sentences=result.sentences,
        )
        index.meta = _with_grounding(index.meta, len(index.expansion))
        report(f"  {len(index.expansion):,} general words mapped onto project spellings")

        target = _default_dir(source) if isinstance(index_dir, _Default) else index_dir
        if target is not None:
            saved = index.save(Path(target).expanduser())
            report(f"saved to {saved}")
        return cls(index, llm=llm)

    @classmethod
    def open(cls, index_dir: Path | str, *, llm: Any = None) -> Project:
        """Reopen a saved index. Seconds, not minutes."""
        return cls(Index.load(Path(index_dir).expanduser()), llm=llm)

    # -- querying --------------------------------------------------------

    def search(
        self, query: str, *, route: str = "codegen", limit: int = 30, **kwargs: Any
    ) -> SearchResult:
        """Run a query. See `codesense.search.search` for the routes."""
        return search(
            query,
            self.context,
            project=self.index.meta.project,
            vocabulary=self.vocabulary,
            llm=self.llm,
            route=route,
            limit=limit,
            **kwargs,
        )

    @property
    def context(self) -> Any:
        """The evaluation context, built once and reused.

        Lazy because building it materialises every symbol and posting, which
        is seconds and hundreds of megabytes on a large project -- too much to
        spend if the caller only wanted `meta`.
        """
        if self._context is None:
            self._context = self.index.to_context(**self._context_overrides())
        return self._context

    @property
    def vocabulary(self) -> list[tuple[str, int]]:
        """The project's terms with df, commonest first."""
        if self._vocabulary is None:
            self._vocabulary = self.index.vocabulary(self._vocab_size)
        return self._vocabulary

    def _context_overrides(self) -> dict[str, Any]:
        """Inject the judge only when an LLM is configured.

        Without one the context keeps its `NullJudge`, so `intent`'s fallback
        path is genuinely exercised rather than crashing.
        """
        if self.llm is None:
            return {}
        from codesense.llm import OpenAICompatibleJudge

        return {"judge": OpenAICompatibleJudge(self.llm)}

    # -- introspection ---------------------------------------------------

    def describe(self) -> str:
        return self.index.meta.describe()

    def __len__(self) -> int:
        return len(self.index)

    def __repr__(self) -> str:
        return f"Project({self.index.meta.project!r}, {len(self):,} symbols)"


def _default_dir(source: Path) -> Path:
    return source / ".codesense"


def _with_grounding(meta: IndexMeta, grounded: int) -> IndexMeta:
    from dataclasses import replace

    return replace(meta, grounded_terms=grounded)


def _reporter(verbose: bool):  # type: ignore[no-untyped-def]
    """Progress goes to stdout, not the log.

    A build takes minutes and the person who started it is watching a terminal;
    routing that through logging means they see nothing unless they configured
    a handler first.
    """
    if not verbose:
        return lambda message: None
    return lambda message: print(message, flush=True)
