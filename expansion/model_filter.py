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
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_name = os.path.join(base_dir, "models", "st-codesearch-distilroberta-base")
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


if __name__ == "__main__":
    # 初次运行会自动下载模型到本地
    st_filter = AbbreviationModelFilter()
    from expansion.abbreviate import abbreviate_new
    abbrs=abbreviate_new("readahead")
    abbrs={'ra', 'reaa', 'reaahd', 'readaha', 'rdad', 'reahad', 'reahea', 'rahad', 'reahead', 'reaaa', 'reaaad', 'rdahead', 'raad', 'rad', 'rdahad', 'r', 'rahead', 'reaahed', 'rdaha', 'raha', 'rdaad', 'readahead', 'readahea', 'rahed', 'readahd', 'rea', 'read', 'reahed', 'reaha', 'readahed', 'readad', 'rdahd', 'reaad', 'reaahea', 'reaahead', 'rahd', 'rdahea', 'readaad', 'rdaa', 'reahd', 'raa', 'reaahad', 'rahea', 'rdahed', 'readaa', 'reaaha', 'readahad'}
    test_cases=[]
    for abbr in abbrs:
        test_cases.append(("read ahead", abbr))
    # test_cases = [
    #     ("configuration", "cfg"),
    #     ("database", "db"),
    #     ("message", "msg"),
    #     ("security check", "secchk"),
    #     ("readahead","prefetch"),
    #     ("configuration", "cat"),
    #     ("database", "apple"),
    # ]
    # res=st_filter.filter_candidates("readahead",list({'ra', 'reaa', 'reaahd', 'readaha', 'rdad', 'reahad', 'reahea', 'rahad', 'reahead', 'reaaa', 'reaaad', 'rdahead', 'raad', 'rad', 'rdahad', 'r', 'rahead', 'reaahed', 'rdaha', 'raha', 'rdaad', 'readahead', 'readahea', 'rahed', 'readahd', 'rea', 'read', 'reahed', 'reaha', 'readahed', 'readad', 'rdahd', 'reaad', 'reaahea', 'reaahead', 'rahd', 'rdahea', 'readaad', 'rdaa', 'reahd', 'raa', 'reaahad', 'rahea', 'rdahed', 'readaa', 'reaaha', 'readahad'}))
    print("====== 本地模型 Zero-Shot 缩写相似度测试 ======")
    for full, ab in test_cases:
        # 直接计算分数
        full_emb = st_filter.model.encode([full], convert_to_tensor=True)
        ab_emb = st_filter.model.encode([ab], convert_to_tensor=True)
        score = st_filter.cos_sim(full_emb, ab_emb)[0][0].item()

        is_match = score >= 0.4
        print(f"Full: {full:15} | Abbr: {ab:6} | Score: {score:.4f} | Pass: {is_match}")
