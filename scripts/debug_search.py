"""Run an end-to-end CodeSense search through the public Python API."""

from __future__ import annotations

import sys
from pathlib import Path

from codesense import Project
from codesense.llm import LlmConfig

# Edit this block directly before each debugging run.
PROJECT_ROOT = "/Users/huangzhuochen/IdeaProjects/youlai-boot-master"
QUERY = "find code that applies backpressure when the write buffer fills"
ROUTE = "codegen"
LIMIT = 20
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen-plus"
TIMEOUT = 60.0
GROUNDING_STRATEGY = "finetune"
FASTTEXT_MODEL = Path(__file__).parent.parent / "model" / "cc.en.300.bin"

INDEX_DIR = ".codesense"


def main() -> int:
    """Build or open an index, execute one query, and print its full trace."""
    project_root = Path(PROJECT_ROOT).resolve()
    if not project_root.is_dir():
        print(f"{project_root} is not a directory", file=sys.stderr)
        return 2

    index_dir = project_root / INDEX_DIR
    llm = _llm()

    # Reuse the local index during iterative debugging; build it on first use.
    if (index_dir / "meta.json").is_file():
        project = Project.open(index_dir, llm=llm)
    else:
        project = Project.build(
            root=project_root,
            index_dir=index_dir,
            strategy=GROUNDING_STRATEGY,
            model_path=FASTTEXT_MODEL,
            llm=llm,
        )

    result = project.search(QUERY, route=ROUTE, limit=LIMIT)
    print(result.explain())
    return 0 if result.hits else 1


def _llm() -> LlmConfig | None:
    """Create the requested LLM config, or allow search to fall back safely."""
    if ROUTE == "lexical":
        return None
    try:
        return LlmConfig.load(
            base_url=BASE_URL,
            model=MODEL,
            timeout=TIMEOUT,
        )
    except ValueError as exc:
        print(f"note: {exc}\n      falling back to the lexical route\n", file=sys.stderr)
        return None


if __name__ == "__main__":
    main()
