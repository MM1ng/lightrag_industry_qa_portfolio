"""Bounded HTTP latency/concurrency benchmark using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def one_request(url: str, timeout: float) -> tuple[bool, float, int | None, str | None]:
    started = time.perf_counter()
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
            response.read()
            return True, (time.perf_counter() - started) * 1000, response.status, None
    except (HTTPError, URLError, TimeoutError) as error:
        status = getattr(error, "code", None)
        return False, (time.perf_counter() - started) * 1000, status, type(error).__name__


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", default="/health")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-p95-ms", type=float, default=1000.0)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive")
    url = args.base_url.rstrip("/") + "/" + args.path.lstrip("/")
    results = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(one_request, url, args.timeout) for _ in range(args.requests)]
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - started
    latencies = [item[1] for item in results]
    successes = sum(item[0] for item in results)
    report = {
        "url": url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "successes": successes,
        "failures": args.requests - successes,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(args.requests / elapsed, 2),
        "latency_ms": {"min": round(min(latencies), 2), "p50": round(percentile(latencies, .50), 2), "p95": round(percentile(latencies, .95), 2), "max": round(max(latencies), 2), "mean": round(statistics.mean(latencies), 2)},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if successes == args.requests and report["latency_ms"]["p95"] <= args.max_p95_ms else 1


if __name__ == "__main__":
    raise SystemExit(main())
