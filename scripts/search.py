"""Query an index built by ``scripts/build_index.py``.

    python scripts/search.py --index ~/.codesense/netty \\
        --query "backpressure when the write buffer fills up"

Without ``--base-url``/``--model`` and an API key, the search runs the
`lexical` route -- weaker, but it works offline and costs nothing. With them it
runs `codegen`, which had the best measured recall.

``--show-script`` prints the query script the model wrote. That script is the
artifact: edit a line and re-run it to see what changed.

This file only parses arguments; the work is in `codesense.project`.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from codesense.project import Project
from codesense.search import ROUTES


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True, help="an index directory")
    parser.add_argument("--query", required=True, help="what to look for, in natural language")
    parser.add_argument("--route", default="codegen", choices=ROUTES)
    parser.add_argument("--limit", type=int, default=20, help="how many results to print")
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="run the intent operator over the finalists (costs one LLM call per batch)",
    )
    parser.add_argument("--show-script", action="store_true", help="print the generated script")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    project = Project.open(args.index, llm=_llm(args))
    result = project.search(args.query, route=args.route, limit=args.limit, judge=args.judge)

    if args.show_script:
        print(result.explain())
    else:
        print(f"{project.describe()}\n")
        print(f"{result.route} route, {len(result)} hits in {result.elapsed:.2f}s")
        for note in result.notes:
            print(f"  note: {note}")
        print()
        for hit in result:
            print(hit)
            if hit.why:
                print(f"      {hit.why}")
    return 0 if result.hits else 1


def _llm(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """Build the LLM config, or None.

    A missing key is not an error: the search degrades to the lexical route,
    which is the whole reason that route exists.
    """
    if args.route == "lexical":
        return None
    from codesense.llm import LlmConfig

    try:
        return LlmConfig.load(base_url=args.base_url, model=args.model)
    except ValueError as exc:
        print(f"no LLM configured ({exc}); using the lexical route")
        return None


if __name__ == "__main__":
    raise SystemExit(main())
