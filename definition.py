import os
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))  # This is your Project Root

OUTPUT_DIR=str(Path(ROOT_DIR) / "output")

TOKENIZER_DIR=str(Path(ROOT_DIR) / "tokenizer")
EMBEDDING_DIR=str(Path(ROOT_DIR) / "embedding")
EXPANSION_DIR=str(Path(ROOT_DIR) / "expansion")

PROJECT_PATH="/Users/bytedance/old6ma/projects/youlai-boot-master"
PROJECT_NAME="youlai-boot-master"
PROJECT_OUTPUT_DIR=str(Path(OUTPUT_DIR) / PROJECT_NAME)

CORPUS="enhanced_call_chain_corpus.json"

BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
API_KEY="sk-e2a20472cac148bdb711c663110c4d6f"
BASE_MODEL="qwen-plus"

# BASE_URL="https://openkey.cloud/v1"
# API_KEY="sk-qJN0l8K8tFDtobxlDc083e5c4d684062B49b02A5C6F3Be6a"
# BASE_MODEL="gpt-4o-mini"

JDTLS_PATH="/opt/homebrew/bin/jdtls"
QUERY_ID=1


def get_query_output_dir(
    project_output_dir: str = PROJECT_OUTPUT_DIR,
    query_id: int = QUERY_ID,
) -> str:
    """Return the per-query online pipeline output directory.

    Project-level offline artifacts stay in ``project_output_dir``. All online
    intermediate results for one query are written under
    ``project_output_dir/query_<query_id>``.
    """
    return str(Path(project_output_dir) / f"query_{query_id}")


QUERY_OUTPUT_DIR=get_query_output_dir()
