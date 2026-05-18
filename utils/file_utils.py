import json

def save_res(save_path, data):
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_res(load_path):
    with open(load_path, "r", encoding="utf-8") as f:
        return json.load(f)

