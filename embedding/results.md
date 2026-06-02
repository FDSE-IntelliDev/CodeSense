输入keyword为security
# 单通道——semantic
```json
[
  {
    "term": "authentication",
    "sem_score": 0.631101,
    "final_score": 0.631101,
    "rank_source": "semantic"
  },
  {
    "term": "authenticate",
    "sem_score": 0.520519,
    "final_score": 0.520519,
    "rank_source": "semantic"
  },
  {
    "term": "lock",
    "sem_score": 0.516062,
    "final_score": 0.516062,
    "rank_source": "semantic"
  },
  {
    "term": "access",
    "sem_score": 0.501576,
    "final_score": 0.501576,
    "rank_source": "semantic"
  },
  {
    "term": "integrity",
    "sem_score": 0.496484,
    "final_score": 0.496484,
    "rank_source": "semantic"
  },
  {
    "term": "auth",
    "sem_score": 0.495341,
    "final_score": 0.495341,
    "rank_source": "semantic"
  },
  {
    "term": "password",
    "sem_score": 0.48655,
    "final_score": 0.48655,
    "rank_source": "semantic"
  },
  {
    "term": "permission",
    "sem_score": 0.481482,
    "final_score": 0.481482,
    "rank_source": "semantic"
  },
  {
    "term": "authenticated",
    "sem_score": 0.458837,
    "final_score": 0.458837,
    "rank_source": "semantic"
  },
  {
    "term": "credentials",
    "sem_score": 0.457073,
    "final_score": 0.457073,
    "rank_source": "semantic"
  }
]
```
# 单通道——ICF
```json
[
  {
    "term": "chain",
    "co_score": 0.802241,
    "final_score": 0.802241,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 7.076316
  },
  {
    "term": "registration",
    "co_score": 0.748756,
    "final_score": 0.748756,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "interceptor",
    "co_score": 0.733968,
    "final_score": 0.733968,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "integrity",
    "co_score": 0.701726,
    "final_score": 0.701726,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 7.076316
  },
  {
    "term": "annotation",
    "co_score": 0.68868,
    "final_score": 0.68868,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "global",
    "co_score": 0.678817,
    "final_score": 0.678817,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 7.992607
  },
  {
    "term": "alias",
    "co_score": 0.673489,
    "final_score": 0.673489,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 7.992607
  },
  {
    "term": "region",
    "co_score": 0.650414,
    "final_score": 0.650414,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "limiter",
    "co_score": 0.647717,
    "final_score": 0.647717,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "internal",
    "co_score": 0.61676,
    "final_score": 0.61676,
    "rank_source": "co",
    "is_high_freq": false,
    "icf": 4.090634
  }
]

```

# 双通道策略
```json
[
  {
    "term": "authentication",
    "co_score": 0.488428,
    "sem_score": 0.631101,
    "final_score": 0.602566,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 5.977704
  },
  {
    "term": "integrity",
    "co_score": 0.553012,
    "sem_score": 0.496484,
    "final_score": 0.50779,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 7.076316
  },
  {
    "term": "permission",
    "co_score": 0.490993,
    "sem_score": 0.481482,
    "final_score": 0.483384,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "authenticate",
    "co_score": 0.201861,
    "sem_score": 0.520519,
    "final_score": 0.456787,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 4.142459
  },
  {
    "term": "authenticated",
    "co_score": 0.273949,
    "sem_score": 0.458837,
    "final_score": 0.421859,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 7.992607
  },
  {
    "term": "lock",
    "co_score": 0.0,
    "sem_score": 0.516062,
    "final_score": 0.41285,
    "rank_source": "semantic",
    "is_high_freq": false,
    "icf": null
  },
  {
    "term": "access",
    "co_score": 0.0,
    "sem_score": 0.501576,
    "final_score": 0.401261,
    "rank_source": "semantic",
    "is_high_freq": false,
    "icf": null
  },
  {
    "term": "auth",
    "co_score": 0.0,
    "sem_score": 0.495341,
    "final_score": 0.396273,
    "rank_source": "semantic",
    "is_high_freq": false,
    "icf": null
  },
  {
    "term": "password",
    "co_score": 0.0,
    "sem_score": 0.48655,
    "final_score": 0.38924,
    "rank_source": "semantic",
    "is_high_freq": false,
    "icf": null
  },
  {
    "term": "job",
    "co_score": 0.473842,
    "sem_score": 0.364125,
    "final_score": 0.386068,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 7.992607
  }
]

```

# 双通道+pairwise rerank策略
```json
[
  {
    "term": "roles",
    "co_score": 0.0,
    "sem_score": 0.387975,
    "final_score": 0.271872,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": null,
    "hybrid_score": 0.31038,
    "pair_score": 0.271872
  },
  {
    "term": "ip",
    "co_score": 0.0,
    "sem_score": 0.386057,
    "final_score": 0.257952,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": null,
    "hybrid_score": 0.308846,
    "pair_score": 0.257952
  },
  {
    "term": "credentials",
    "co_score": 0.0,
    "sem_score": 0.457073,
    "final_score": 0.240833,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": null,
    "hybrid_score": 0.365658,
    "pair_score": 0.240833
  },
  {
    "term": "authenticated",
    "co_score": 0.273949,
    "sem_score": 0.458837,
    "final_score": 0.234743,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 7.992607,
    "hybrid_score": 0.421859,
    "pair_score": 0.234743
  },
  {
    "term": "tokens",
    "co_score": 0.0,
    "sem_score": 0.39507,
    "final_score": 0.227245,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": null,
    "hybrid_score": 0.316056,
    "pair_score": 0.227245
  },
  {
    "term": "system",
    "co_score": 0.247531,
    "sem_score": 0.372581,
    "final_score": 0.224092,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 5.85254,
    "hybrid_score": 0.347571,
    "pair_score": 0.224092
  },
  {
    "term": "authenticate",
    "co_score": 0.201861,
    "sem_score": 0.520519,
    "final_score": 0.223697,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 4.142459,
    "hybrid_score": 0.456787,
    "pair_score": 0.223697
  },
  {
    "term": "internal",
    "co_score": 0.500938,
    "sem_score": 0.284012,
    "final_score": 0.222177,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 4.090634,
    "hybrid_score": 0.327397,
    "pair_score": 0.222177
  },
  {
    "term": "access",
    "co_score": 0.0,
    "sem_score": 0.501576,
    "final_score": 0.21224,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": null,
    "hybrid_score": 0.401261,
    "pair_score": 0.21224
  },
  {
    "term": "password",
    "co_score": 0.0,
    "sem_score": 0.48655,
    "final_score": 0.205619,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": null,
    "hybrid_score": 0.38924,
    "pair_score": 0.205619
  }
]
```


应用场景：关键词扩展（测试个别query效果还不错）、全称缩写过滤筛除（目前还未测试）、搜索结果过滤

输入单词效果较好 输入短语效果下降明显