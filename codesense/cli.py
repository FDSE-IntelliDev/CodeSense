"""The command line: ``init`` once, then ``query`` as often as you like.

    cd ~/src/netty
    codesense init                       # builds ./.codesense/
    codesense query "写缓冲积压时的背压"    # finds it by walking up

**Index discovery works like git's.** ``init`` writes ``.codesense/`` beside
the source, and every later command walks up from the working directory until
it finds one. That is what makes ``query`` a one-argument command: there is no
global registry to keep in sync, no state outside the repository it describes,
and moving or deleting the repository takes its index with it.

Endpoint and model come from the environment (``CODESENSE_BASE_URL``,
``CODESENSE_MODEL``) as well as from flags, for the same reason the API key
does: retyping them on every query is how people end up writing a config file,
and a config file becomes a second source of truth. A flag always wins over
the environment.

This module only parses arguments and prints. The work is in
`codesense.project`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from codesense.index import Index
from codesense.indexing.grounding import STRATEGIES
from codesense.search import ROUTES

__all__ = ["find_index", "main"]

#: The directory `init` creates and every other command looks for.
INDEX_DIR = ".codesense"

#: Environment variables read when the corresponding flag is absent. The API
#: key is handled by `LlmConfig` itself and is deliberately not a flag.
ENV_BASE_URL = "CODESENSE_BASE_URL"
ENV_MODEL = "CODESENSE_MODEL"

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def find_index(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` looking for an index directory.

    Returns None rather than raising, so a caller can print something more
    useful than a traceback -- almost always "run codesense init first".
    """
    current = (start or Path.cwd()).expanduser().resolve()
    for directory in (current, *current.parents):
        candidate = directory / INDEX_DIR
        if (candidate / "meta.json").is_file():
            return candidate
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    logging.basicConfig(
        level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return {"init": _init, "query": _query, "info": _info}[args.command](args)


# -- init --------------------------------------------------------------------


def _init(args: argparse.Namespace) -> int:
    from codesense.project import Project

    source = args.path.expanduser().resolve()
    if not source.is_dir():
        print(f"{source} is not a directory", file=sys.stderr)
        return 2
    target = args.out or source / INDEX_DIR
    if (target / "meta.json").is_file() and not args.force:
        print(f"{target} already holds an index; pass --force to rebuild", file=sys.stderr)
        return 1
    if args.strategy != "lexical" and args.vectors is None:
        print(
            f"--strategy {args.strategy} needs --vectors pointing at a fastText .bin",
            file=sys.stderr,
        )
        return 2

    project = Project.build(
        source,
        index_dir=target,
        name=args.name,
        strategy=args.strategy,
        model_path=args.vectors,
        epochs=args.epochs,
    )
    print(f"\n{project.describe()}")
    print('\nnow try:  codesense query "..."')
    return 0


# -- query -------------------------------------------------------------------


def _query(args: argparse.Namespace) -> int:
    from codesense.project import Project

    index_dir = args.index or find_index()
    if index_dir is None:
        print(
            "no index found here or in any parent directory.\n"
            "run `codesense init` in the repository you want to search.",
            file=sys.stderr,
        )
        return 2

    project = Project.open(index_dir, llm=_llm(args))
    result = project.search(args.text, route=args.route, limit=args.limit, judge=args.judge)

    if args.script:
        print(result.explain())
        return 0 if result.hits else 1

    print(f"{project.describe()}")
    print(f"{result.route} route · {len(result)} hits · {result.elapsed:.2f}s\n")
    for note in result.notes:
        print(f"  note: {note}")
    if result.notes:
        print()
    for hit in result:
        print(hit)
        if hit.why and args.why:
            print(f"      {hit.why}")
    if not result.hits:
        print("  nothing matched.")
        return 1
    return 0


# -- info --------------------------------------------------------------------


def _info(args: argparse.Namespace) -> int:
    index_dir = args.index or find_index()
    if index_dir is None:
        print("no index found here or in any parent directory.", file=sys.stderr)
        return 2
    index = Index.load(index_dir)
    meta = index.meta
    print(f"index      {index_dir}")
    print(f"project    {meta.project}")
    print(f"source     {meta.root}")
    print(f"language   {meta.language}")
    print(f"built      {meta.built_at}")
    print(f"symbols    {meta.symbols:,}")
    print(f"postings   {meta.postings:,}")
    print(f"edges      {meta.edges:,}")
    print(f"vocabulary {len(index.vocabulary()):,}")
    print(f"grounded   {meta.grounded_terms:,} general words")
    if args.terms:
        print("\ncommonest terms (term:df)")
        for term, df in index.vocabulary(args.terms):
            print(f"  {term:<28}{df:>8,}")
    return 0


# -- wiring ------------------------------------------------------------------


def _llm(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """Build an LLM config, or None.

    A missing key is not an error: `search` degrades to the lexical route,
    which is the whole reason that route exists.
    """
    if args.route == "lexical":
        return None
    from codesense.llm import LlmConfig

    base_url = args.base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL
    model = args.model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
    try:
        return LlmConfig.load(base_url=base_url, model=model)
    except ValueError as exc:
        print(f"note: {exc}\n      falling back to the lexical route\n", file=sys.stderr)
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codesense",
        description="Compile a natural-language query into a query script over a codebase.",
        epilog='run `codesense init` in a repository, then `codesense query "..."` inside it.',
    )
    sub = parser.add_subparsers(dest="command", metavar="{init,query,info}")

    init = sub.add_parser("init", help="index a repository")
    init.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("."),
        help="repository root (default: the working directory)",
    )
    init.add_argument("--out", type=Path, help=f"where to write it (default: <path>/{INDEX_DIR})")
    init.add_argument("--name", default="", help="project name (default: the directory name)")
    init.add_argument(
        "--strategy",
        default="lexical",
        choices=STRATEGIES,
        help="how to ground general vocabulary (default: lexical, needs nothing)",
    )
    init.add_argument(
        "--vectors", type=Path, help="fastText .bin, required by --strategy vectors/finetune"
    )
    init.add_argument(
        "--epochs", type=int, default=5, help="training passes over the repo corpus (finetune only)"
    )
    init.add_argument("--force", action="store_true", help="rebuild over an existing index")
    init.add_argument("--verbose", action="store_true")

    query = sub.add_parser("query", help="search an indexed repository")
    query.add_argument("text", help="what to look for, in natural language")
    query.add_argument("--index", type=Path, help="index directory (default: found by walking up)")
    query.add_argument(
        "--route",
        default="codegen",
        choices=ROUTES,
        help="codegen writes a script, planned plans, lexical uses no model",
    )
    query.add_argument("-n", "--limit", type=int, default=20, help="how many results")
    query.add_argument(
        "--script", action="store_true", help="print the generated script and the full trace"
    )
    query.add_argument("--why", action="store_true", help="print the evidence under each hit")
    query.add_argument(
        "--judge",
        action="store_true",
        help="run intent over the finalists (costs one LLM call per batch)",
    )
    query.add_argument("--base-url", help=f"OpenAI-compatible endpoint (env: {ENV_BASE_URL})")
    query.add_argument("--model", help=f"model name (env: {ENV_MODEL})")
    query.add_argument("--verbose", action="store_true")

    info = sub.add_parser("info", help="describe the index in scope")
    info.add_argument("--index", type=Path, help="index directory (default: found by walking up)")
    info.add_argument(
        "--terms",
        type=int,
        default=0,
        metavar="N",
        help="also print the N commonest terms with their df",
    )
    info.add_argument("--verbose", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
