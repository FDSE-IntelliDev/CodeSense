"""Build a searchable index from a Java repository.

    python scripts/build_index.py --source ~/src/netty --out ~/.codesense/netty

Grounding defaults to `lexical`, which needs nothing beyond the repository.
For the vector strategies, point `--model` at a fastText ``.bin``::

    python scripts/build_index.py --source ~/src/netty --out ~/.codesense/netty \\
        --strategy vectors --model ~/models/cc.en.300.bin

This file only parses arguments; the work is in `codesense.project`.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from codesense.indexing.grounding import STRATEGIES
from codesense.project import Project


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="root of the Java codebase")
    parser.add_argument(
        "--out", type=Path, help="where to write the index (default: <source>/.codesense)"
    )
    parser.add_argument("--name", default="", help="project name (default: the directory name)")
    parser.add_argument(
        "--strategy",
        default="lexical",
        choices=STRATEGIES,
        help="how to ground general vocabulary in the project's spellings",
    )
    parser.add_argument("--model", type=Path, help="fastText .bin, for vectors/finetune")
    parser.add_argument(
        "--epochs", type=int, default=5, help="training passes over the repo corpus (finetune)"
    )
    parser.add_argument("--verbose", action="store_true", help="log the vector work in detail")
    args = parser.parse_args(argv)

    if args.strategy != "lexical" and args.model is None:
        parser.error(f"--strategy {args.strategy} needs --model")
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    project = Project.build(
        args.source,
        index_dir=args.out if args.out is not None else args.source / ".codesense",
        name=args.name,
        strategy=args.strategy,
        model_path=args.model,
        epochs=args.epochs,
    )
    print(project.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
