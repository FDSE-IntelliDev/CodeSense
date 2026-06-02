import os
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))  # This is your Project Root

OUTPUT_DIR=str(Path(ROOT_DIR) / "output")

TOKENIZER_DIR=str(Path(ROOT_DIR) / "tokenizer")
EMBEDDING_DIR=str(Path(ROOT_DIR) / "embedding")
EXPANSION_DIR=str(Path(ROOT_DIR) / "expansion")

PROJECT_PATH="/Users/bytedance/old6ma/projects/youlai-boot-master"

CORPUS="enhanced_call_chain_corpus.json"

BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
API_KEY="sk-e2a20472cac148bdb711c663110c4d6f"
BASE_MODEL="qwen-plus"

# BASE_URL="https://openkey.cloud/v1"
# API_KEY="sk-qJN0l8K8tFDtobxlDc083e5c4d684062B49b02A5C6F3Be6a"
# BASE_MODEL="gpt-4o-mini"

JDTLS_PATH="/opt/homebrew/bin/jdtls"