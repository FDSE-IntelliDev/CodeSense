import os
from sentence_transformers import SentenceTransformer

def download_model():
    model_name = "flax-sentence-embeddings/st-codesearch-distilroberta-base"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    save_path = os.path.join(base_dir, "models", "st-codesearch-distilroberta-base")

    os.makedirs(save_path, exist_ok=True)
    print(f"Downloading {model_name}...")
    model = SentenceTransformer(model_name)
    model.save(save_path)
    print(f"Model saved to {save_path}")

if __name__ == "__main__":
    download_model()
