# Performance and concurrency validation

The repository includes a bounded standard-library benchmark:

```powershell
python scripts/benchmark_http.py --base-url http://127.0.0.1:8000 --path /health --requests 50 --concurrency 10 --max-p95-ms 1000
```

It reports request count, failures, throughput, p50/p95/max latency and exits non-zero if any request fails or the p95 threshold is exceeded. `/health` is a service baseline; it does not represent end-to-end QA latency.

For a production-equivalent test, run separate controlled profiles for:

1. readiness/health checks;
2. cached or mocked query path;
3. real Provider + Qdrant query path;
4. read-only source lookup;
5. admin list/read APIs;
6. serialized lifecycle mutations, never concurrent destructive operations.

Record hardware, Python/Node versions, Qdrant version, provider/model, dataset and Generation identity, concurrency, request mix, timeout/retry settings, error rate, p50/p95/p99 latency and throughput. A production claim requires a production-like environment and agreed SLO; this local benchmark must not be presented as one.
