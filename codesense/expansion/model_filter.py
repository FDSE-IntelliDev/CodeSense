import os
from typing import List, Set, Iterable

class AbbreviationModelFilter:
    """
    基于轻量化本地预训练模型的缩写过滤器（无需微调）。
    推荐模型：
    - "flax-sentence-embeddings/st-codesearch-distilroberta-base" (对代码含义理解好)
    - "all-MiniLM-L6-v2" (极度轻量, 约 80MB)
    """
    def __init__(self, model_name: str = None):
        try:
            from sentence_transformers import SentenceTransformer
            from sentence_transformers.util import cos_sim
        except ImportError:
            raise ImportError(
                "请先安装 sentence-transformers: pip install sentence-transformers"
            )

        self.cos_sim = cos_sim

        if model_name is None:
            # 默认使用本地 models 目录下的模型
            # 仓库根从 config 取；原来用 __file__ 上溯两层，搬进包后指到了
            # codesense/models/，于是永远找不到本地模型、静默 fallback 去下载。
            from codesense.config import REPO_ROOT

            model_name = str(REPO_ROOT / "models" / "st-codesearch-distilroberta-base")
            if not os.path.exists(model_name):
                # fallback
                model_name = "flax-sentence-embeddings/st-codesearch-distilroberta-base"

        # 模型会自动加载（如果是本地路径）或下载并缓存在本地
        self.model = SentenceTransformer(model_name)

    def filter_candidates(self, full_name: str, candidates: Iterable[str], threshold: float = 0.4) -> List[str]:
        """
        批量过滤全称的候选缩写
        """
        cands = list(candidates)
        if not cands:
            return []

        # 转换为向量表示
        full_emb = self.model.encode([full_name], convert_to_tensor=True)
        cand_embs = self.model.encode(cands, convert_to_tensor=True)

        # 计算余弦相似度
        scores = self.cos_sim(full_emb, cand_embs)[0]

        filtered = []
        for i, score in enumerate(scores):
            # 你可以将这里的 score 打印出来，用于观察并微调 threshold 参数
            # print(f"[{full_name}] vs [{cands[i]}] score: {score.item():.4f}")
            if score.item() >= threshold:
                filtered.append(cands[i])

        return filtered

    def check_pair(self, full_name: str, abbr: str, threshold: float = 0.4) -> bool:
        """
        快捷检查单对 (全称, 缩写) 是否匹配
        """
        return len(self.filter_candidates(full_name, [abbr], threshold)) > 0


# 原来这里有一个 __main__ 调试块，参数全部硬编码（一大坨字面量集合），
# 换个输入只能改代码。已删除；要跑对比实验请自己写脚本放 scripts/。
