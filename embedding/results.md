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
    "term": "integrity",
    "co_score": 0.701726,
    "sem_score": 0.496484,
    "final_score": 0.578581,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 7.076316
  },
  {
    "term": "authentication",
    "co_score": 0.457845,
    "sem_score": 0.631101,
    "final_score": 0.561799,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 5.977704
  },
  {
    "term": "registration",
    "co_score": 0.748756,
    "sem_score": 0.282833,
    "final_score": 0.469202,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "job",
    "co_score": 0.53592,
    "sem_score": 0.364125,
    "final_score": 0.432843,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 7.992607
  },
  {
    "term": "region",
    "co_score": 0.650414,
    "sem_score": 0.286115,
    "final_score": 0.431835,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "policy",
    "co_score": 0.462365,
    "sem_score": 0.403981,
    "final_score": 0.427335,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "internal",
    "co_score": 0.61676,
    "sem_score": 0.284012,
    "final_score": 0.417111,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 4.090634
  },
  {
    "term": "entry",
    "co_score": 0.532758,
    "sem_score": 0.335758,
    "final_score": 0.414558,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "input",
    "co_score": 0.539256,
    "sem_score": 0.322028,
    "final_score": 0.408919,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 8.685754
  },
  {
    "term": "sql",
    "co_score": 0.578269,
    "sem_score": 0.28895,
    "final_score": 0.404678,
    "rank_source": "semantic_and_co",
    "is_high_freq": false,
    "icf": 5.048168
  }
]
```

# 双通道+pairwise rerank策略
```json
[
  {
    "term": "authentication",
    "co_score": 0.457845,
    "sem_score": 0.631101,
    "final_score": 0.340761,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 5.977704,
    "hybrid_score": 0.561799,
    "pair_score": 0.193402
  },
  {
    "term": "chain",
    "co_score": 0.802241,
    "sem_score": 0.0,
    "final_score": 0.32154,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 7.076316,
    "hybrid_score": 0.320896,
    "pair_score": 0.32197
  },
  {
    "term": "integrity",
    "co_score": 0.701726,
    "sem_score": 0.496484,
    "final_score": 0.316533,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 7.076316,
    "hybrid_score": 0.578581,
    "pair_score": 0.141834
  },
  {
    "term": "ip",
    "co_score": 0.401037,
    "sem_score": 0.386057,
    "final_score": 0.311591,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 5.427657,
    "hybrid_score": 0.392049,
    "pair_score": 0.257952
  },
  {
    "term": "internal",
    "co_score": 0.61676,
    "sem_score": 0.284012,
    "final_score": 0.300151,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 4.090634,
    "hybrid_score": 0.417111,
    "pair_score": 0.222177
  },
  {
    "term": "interceptor",
    "co_score": 0.733968,
    "sem_score": 0.0,
    "final_score": 0.266181,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 8.685754,
    "hybrid_score": 0.293587,
    "pair_score": 0.247911
  },
  {
    "term": "entry",
    "co_score": 0.532758,
    "sem_score": 0.335758,
    "final_score": 0.264735,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 8.685754,
    "hybrid_score": 0.414558,
    "pair_score": 0.164853
  },
  {
    "term": "web",
    "co_score": 0.420104,
    "sem_score": 0.330862,
    "final_score": 0.262569,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 8.685754,
    "hybrid_score": 0.366559,
    "pair_score": 0.193243
  },
  {
    "term": "service",
    "co_score": 0.358795,
    "sem_score": 0.401402,
    "final_score": 0.262246,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 8.685754,
    "hybrid_score": 0.384359,
    "pair_score": 0.180837
  },
  {
    "term": "input",
    "co_score": 0.539256,
    "sem_score": 0.322028,
    "final_score": 0.260044,
    "rank_source": "hybrid_and_pairwise",
    "is_high_freq": false,
    "icf": 8.685754,
    "hybrid_score": 0.408919,
    "pair_score": 0.160795
  }
]
```