import os
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))  # This is your Project Root

OUTPUT_DIR=str(Path(ROOT_DIR) / "output")

TOKENIZER_DIR=str(Path(ROOT_DIR) / "tokenizer")
EMBEDDING_DIR=str(Path(ROOT_DIR) / "embedding")
EXPANSION_DIR=str(Path(ROOT_DIR) / "expansion")

BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
API_KEY="sk-e2a20472cac148bdb711c663110c4d6f"
BASE_MODEL="qwen-plus"

JDTLS_PATH="/opt/homebrew/bin/jdtls"