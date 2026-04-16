import os
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))  # This is your Project Root

OUTPUT_DIR=str(Path(ROOT_DIR) / "output")

TOKENIZER_DIR=str(Path(ROOT_DIR) / "tokenizer")
EMBEDDING_DIR=str(Path(ROOT_DIR) / "embedding")
EXPANSION_DIR=str(Path(ROOT_DIR) / "expansion")