import json
import numpy as np
from typing import List, Dict, Any
from sentence_transformers import SentenceTransformer
from parsers.read_tools import get_symbol_code
from sklearn.cluster import AgglomerativeClustering
from definition import QUERY_OUTPUT_DIR

from pathlib import Path

class CodeEmbedder:
    DEFAULT_SEMANTIC_MODEL = 'all-MiniLM-L6-v2'
    def __init__(self, model_name: str = None):
        # if model_name is None:
        #     model_name = str(
        #         Path(__file__).resolve().parent.parent / "models" / "st-codesearch-distilroberta-base"
        #     )
        self.model = SentenceTransformer(self.DEFAULT_SEMANTIC_MODEL)

    def encode(self, texts: List[str]) -> np.ndarray:
        """
        对传入的文本列表进行编码并返回标准化后的 numpy 向量。
        normalize_embeddings=True 确保可以直接通过向量点积计算余弦相似度。
        """
        if not texts:
            return np.array([])

        # 将句子直接编码为 numpy array (Shape: N x Embedding_Dim)
        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return embeddings

    def build_feature_text(self, symbol: Dict[str, Any]) -> str:
        """
        核心动作：将符号字典拼接为模型容易理解的特征文本。
        尽量把关键名称和业务逻辑往前放。
        """
        # 这里提取基础属性，组合成能够表征这一代码元素的字符串
        container = symbol.get("container", "")
        symbol_type = symbol.get("type", "")
        name = symbol.get("name", "")
        signature = symbol.get("signature", "")

        # 调用读取工具，传入根路径获取原始代码字符串
        code_content = get_symbol_code("", symbol)

        parts = [f"[{symbol_type}] {container}.{name}"]
        if signature:
            parts.append(signature)
        if code_content:
            # 简单清理多余的换行，合并代码内容，考虑模型截断也可保留适度前缀
            clean_code = " ".join(code_content.split())
            parts.append(clean_code)

        feature_text = " ".join(parts)
        return feature_text

class SymbolClusterer:
    """
    自适应的层次聚类器。
    使用 Agglomerative Clustering (结合余弦距离) 以避免手动指定 n_clusters。
    """
    def __init__(self, distance_threshold: float = 0.3):
        """
        :param distance_threshold: 两簇之间合并的距离阈值。
               基于 Sentence-Transformers 生成的 standardized 向量，使用 cosine 距离计算。
               0.3 意味着余弦相似度大于 0.7 左右的才会合成一个簇。
               当候选集差异大时，调低此值能切出更多细分簇。
        """
        self.distance_threshold = distance_threshold
        # 注意: metric = 'cosine'
        self.model = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=self.distance_threshold,
            metric='cosine',
            linkage='average'
        )

    def fit_predict(self, embeddings: np.ndarray) -> np.ndarray:
        """
        执行聚类，返回每个样本的簇标签。
        """
        if embeddings.shape[0] < 2:
            # 如果元素少于2个，无法/无需聚类，全部标记为簇 0
            return np.zeros(embeddings.shape[0], dtype=int)

        labels = self.model.fit_predict(embeddings)
        return labels

    def compute_centroids(self, embeddings: np.ndarray, labels: np.ndarray) -> Dict[int, np.ndarray]:
        """
        根据聚类标签，计算每个簇的平均中心向量（质心）。
        """
        unique_labels = np.unique(labels)
        centroids = {}
        for label in unique_labels:
            if label == -1: # Noise points if any algorithm outputs it (like DBSCAN)
                continue
            indices = np.where(labels == label)[0]
            centroid = np.mean(embeddings[indices], axis=0)
            # L2 归一化质心，方便后续继续做余弦计算
            centroid = centroid / np.linalg.norm(centroid)
            centroids[label] = centroid
        return centroids

class FiltrationDispatcher:
    """
    负责将 Query 和 簇(Clusters) 进行对比打分，
    并执行分层过滤 (Hierarchical Filtration) 的调度器。
    """
    def __init__(self, embedder: CodeEmbedder, clusterer: SymbolClusterer):
        self.embedder = embedder
        self.clusterer = clusterer

    def run_pipeline(
        self,
        search_results: List[Dict[str, Any]],
        query_text: str,
        policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute the cluster policy produced by IntentionPlanner.

        Runtime facts such as cluster count and score spread are evaluated here,
        but all thresholds and keep rules come from ``policy``.
        """
        if not search_results:
            return {"kept": [], "discarded": [], "stats": {}}

        query_embedding = self.embedder.encode([str(query_text or "")])[0]

        # 2. 构建 Code 向量
        feature_texts = [
            self.embedder.build_feature_text(sym) for sym in search_results
        ]
        code_embeddings = self.embedder.encode(feature_texts)

        # 3. 执行聚类并计算簇质心
        labels = self.clusterer.fit_predict(code_embeddings)
        centroids = self.clusterer.compute_centroids(code_embeddings, labels)

        # 4. 簇级打分：使用 Query 与每个簇的质心算余弦相似度
        cluster_scores = {
            c_id: float(np.dot(query_embedding, centroid))
            for c_id, centroid in centroids.items()
        }

        # 将簇按照相关性从高到低排序
        sorted_clusters = sorted(
            cluster_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        ranked_cluster_ids = [cid for cid, _ in sorted_clusters]
        num_clusters = len(ranked_cluster_ids)

        def _ceil_count(ratio: float) -> int:
            if num_clusters <= 0:
                return 0
            return int(np.ceil(num_clusters * ratio))

        min_cluster_count = max(
            1,
            int(policy.get("min_cluster_count_to_filter", 3)),
        )
        min_score_spread = max(0.0, float(policy.get("min_score_spread", 0.05)))
        score_values = [score for _, score in sorted_clusters]
        score_spread = (
            float(max(score_values) - min(score_values)) if score_values else 0.0
        )

        guard_reason = None
        if num_clusters < min_cluster_count:
            guard_reason = "cluster_count_below_filter_minimum"
        elif score_spread < min_score_spread:
            guard_reason = "cluster_score_spread_below_filter_minimum"

        keep_ratio = min(
            1.0,
            max(0.0, float(policy.get("keep_top_cluster_ratio", 0.6))),
        )
        if guard_reason is None:
            kept_cluster_count = max(1, int(np.ceil(num_clusters * keep_ratio)))
        else:
            kept_cluster_count = num_clusters

        priority_1_count = min(
            kept_cluster_count,
            max(1, _ceil_count(float(policy.get("priority_1_ratio", 0.1))))
            if kept_cluster_count
            else 0,
        )
        priority_2_count = min(
            max(0, kept_cluster_count - priority_1_count),
            _ceil_count(float(policy.get("priority_2_ratio", 0.2))),
        )
        priority_3_count = max(
            0,
            kept_cluster_count - priority_1_count - priority_2_count,
        )

        priority_1_clusters = set(ranked_cluster_ids[:priority_1_count])
        priority_2_start = priority_1_count
        priority_2_end = min(num_clusters, priority_2_start + priority_2_count)
        priority_2_clusters = set(
            ranked_cluster_ids[priority_2_start:priority_2_end]
        )
        priority_3_start = priority_2_end
        priority_3_end = priority_3_start + priority_3_count
        priority_3_clusters = set(
            ranked_cluster_ids[priority_3_start:priority_3_end]
        )
        priority_4_clusters = set(ranked_cluster_ids[priority_3_end:])

        tiered_symbols = {
            "priority_1": [],
            "priority_2": [],
            "priority_3": [],
            "priority_4_low_relevance": [],
        }
        kept_symbols = []
        rescue_symbols = []
        discarded_symbols = []
        forwarded_symbols = []
        defer_low_clusters = bool(
            policy.get("defer_low_cluster_discard_to_embedding", False)
        )

        individual_scores = np.dot(code_embeddings, query_embedding)
        for symbol, label, individual_score in zip(
            search_results,
            labels,
            individual_scores,
        ):
            symbol["_cluster_id"] = int(label)
            symbol["_cluster_score"] = float(cluster_scores.get(label, 0.0))
            symbol["_individual_score"] = float(individual_score)

            if label in priority_1_clusters:
                symbol["_filter_reason"] = "priority_1_cluster"
                symbol["_cluster_tier"] = "priority_1"
                tiered_symbols["priority_1"].append(symbol)
                kept_symbols.append(symbol)
                forwarded_symbols.append(symbol)
            elif label in priority_2_clusters:
                symbol["_filter_reason"] = "priority_2_cluster"
                symbol["_cluster_tier"] = "priority_2"
                tiered_symbols["priority_2"].append(symbol)
                kept_symbols.append(symbol)
                forwarded_symbols.append(symbol)
            elif label in priority_3_clusters:
                symbol["_filter_reason"] = "priority_3_cluster"
                symbol["_cluster_tier"] = "priority_3"
                tiered_symbols["priority_3"].append(symbol)
                kept_symbols.append(symbol)
                forwarded_symbols.append(symbol)
            else:
                symbol["_cluster_tier"] = "priority_4_low_relevance"
                tiered_symbols["priority_4_low_relevance"].append(symbol)
                if defer_low_clusters:
                    # Cluster is a coarse signal. Defer the irreversible decision
                    # so candidate-level Term Embedding can rescue a strong item
                    # from an otherwise weak cluster.
                    symbol["_filter_reason"] = "priority_4_cluster_rescue_candidate"
                    rescue_symbols.append(symbol)
                    forwarded_symbols.append(symbol)
                else:
                    symbol["_filter_reason"] = "priority_4_cluster_discarded"
                    discarded_symbols.append(symbol)

        for tier_name, symbols in tiered_symbols.items():
            symbols.sort(
                key=lambda item: (
                    float(item.get("_cluster_score", 0.0)),
                    float(item.get("_individual_score", 0.0)),
                ),
                reverse=True,
            )
            for rank_idx, symbol in enumerate(symbols, start=1):
                symbol["_tier_rank"] = str(rank_idx)

        unique_labels = np.unique(labels)
        stats = {
            "total_initial": len(search_results),
            "total_kept": len(kept_symbols),
            "total_rescue": len(rescue_symbols),
            "total_forwarded": len(forwarded_symbols),
            "total_discarded": len(discarded_symbols),
            "num_clusters": len(unique_labels),
            "filter_applied": guard_reason is None,
            "guard_reason": guard_reason,
            "cluster_score_spread": score_spread,
            "keep_top_cluster_ratio": keep_ratio,
            "cluster_ranking": [
                {"cluster_id": int(cid), "score": float(score)}
                for cid, score in sorted_clusters
            ],
            "tier_cluster_counts": {
                "priority_1": len(priority_1_clusters),
                "priority_2": len(priority_2_clusters),
                "priority_3": len(priority_3_clusters),
                "priority_4_low_relevance": len(priority_4_clusters),
            },
            "tier_symbol_counts": {
                tier: len(items) for tier, items in tiered_symbols.items()
            },
        }

        return {
            "kept": kept_symbols,
            "rescue_candidates": rescue_symbols,
            "forwarded": forwarded_symbols,
            "discarded": discarded_symbols,
            "tiers": tiered_symbols,
            "stats": stats,
        }


if __name__ == "__main__":
    from pathlib import Path
    import json

    # 1. 指定真实的搜索结果文件路径 (动态获取项目根目录并拼接路径)
    project_root = Path(__file__).resolve().parent.parent
    result_file_path = Path(QUERY_OUTPUT_DIR) / "filtered_by_type.json"

    print(f"正在读取真实搜索结果: {result_file_path}")
    with open(result_file_path, "r", encoding="utf-8") as f:
        real_search_results = json.load(f)
    print(f"成功加载，共计 {len(real_search_results)} 个代码元素。")

    with open(Path(QUERY_OUTPUT_DIR) / "intention_semql.json", "r", encoding="utf-8") as f:
        intention_plan = json.load(f)

    print("\n正在加载本地 Embedding 模型...")
    # 3. 初始化组件
    embedder = CodeEmbedder()
    clusterer = SymbolClusterer(distance_threshold=0.3)
    dispatcher = FiltrationDispatcher(embedder, clusterer)

    print("开始运行过滤 Pipeline...")
    # 4. 运行调度器
    result_dict = dispatcher.run_pipeline(
        real_search_results,
        intention_plan.get("query_profile", {}).get("semantic_text", ""),
        intention_plan.get("execution_plan", {}).get("cluster", {}),
    )

    # 5. 存储结果
    output_dir = Path(QUERY_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    kept_file_path = output_dir / "filtered_by_cluster.json"
    discarded_file_path = output_dir / "exclude_by_cluster.json"

    with open(kept_file_path, "w", encoding="utf-8") as f:
        json.dump(result_dict.get("kept", []), f, ensure_ascii=False, indent=4)

    with open(discarded_file_path, "w", encoding="utf-8") as f:
        json.dump(result_dict.get("discarded", []), f, ensure_ascii=False, indent=4)

    # 6. 控制台简要输出
    print("\n========== Pipeline 运行完成 ==========")
    stats = result_dict.get("stats", {})
    print(f"统计信息: 初始总数={stats.get('total_initial')}")
    print(f"聚类数={stats.get('num_clusters')}, 保留={stats.get('total_kept')}, 丢弃={stats.get('total_discarded')}")
    print(f"✅ 保留的符号已写入: {kept_file_path}")
    print(f"❌ 丢弃的符号已写入: {discarded_file_path}")
