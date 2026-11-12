"""Bounded HTTP latency/concurrency benchmark using only the Python standard library.

Two modes:
  * GET  endpoints (default ``/health``): no body, no auth.
  * POST ``/query``: supply ``--query`` (and ``--kb-id`` / ``--admin-key``), or
    set ``IRA_BENCH_KB_ID`` / ``IRA_BENCH_ADMIN_KEY`` in the environment. The
    report records both HTTP-level success and the business-level ``status`` in
    the response body, since a 200 with ``insufficient_evidence`` is not a
    failure but must be visible.

Examples:
  python scripts/benchmark_http.py --requests 50 --concurrency 10
  python scripts/benchmark_http.py --path /v1/knowledge-bases/<kb>/query \\
      --query "泵的轴承温度上限是多少？" --requests 20 --concurrency 5 \\
      --timeout 60 --max-p95-ms 12000 --json-out .run/bench_query.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def one_request(
    url: str,
    timeout: float,
    *,
    payload: bytes | None,
    headers: dict[str, str],
) -> tuple[bool, float, int | None, str | None, dict[str, Any] | None]:
    """Return (ok, latency_ms, http_status, error, parsed_body)."""
    started = time.perf_counter()
    request = Request(url, data=payload, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
        body: dict[str, Any] | None = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, dict):
                    body = parsed
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = None
        return True, (time.perf_counter() - started) * 1000, response.status, None, body
    except HTTPError as error:
        # Still consume the body so connection reuse stays clean and we can surface
        # the server's error code (e.g. 429 under rate limiting).
        try:
            error.read()
        except Exception:
            pass
        return False, (time.perf_counter() - started) * 1000, error.code, type(error).__name__, None
    except (URLError, TimeoutError, OSError) as error:
        return False, (time.perf_counter() - started) * 1000, None, type(error).__name__, None


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", default="/health")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-p95-ms", type=float, default=1000.0)
    parser.add_argument("--query", default=None, help="Question text; switches to POST /query mode")
    parser.add_argument("--kb-id", default=os.environ.get("IRA_BENCH_KB_ID", ""))
    parser.add_argument("--admin-key", default=os.environ.get("IRA_BENCH_ADMIN_KEY", ""))
    parser.add_argument("--json-out", default=None, help="Write the full report to this JSON file")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive")

    is_query_mode = args.query is not None
    if is_query_mode and (not args.kb_id or not args.admin_key):
        parser.error("--query mode requires --kb-id and --admin-key (or env vars)")

    if is_query_mode:
        url = args.base_url.rstrip("/") + f"/v1/knowledge-bases/{args.kb_id}/query"
        payload = json.dumps({"query": args.query}, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {args.admin_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    else:
        url = args.base_url.rstrip("/") + "/" + args.path.lstrip("/")
        payload = None
        headers = {"Accept": "application/json"}

    results: list[tuple[bool, float, int | None, str | None, dict[str, Any] | None]] = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(one_request, url, args.timeout, payload=payload, headers=headers)
            for _ in range(args.requests)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - started

    latencies = [item[1] for item in results]
    successes = sum(item[0] for item in results)
    http_codes: Counter[int | None] = Counter(item[2] for item in results)
    errors: Counter[str] = Counter(item[3] for item in results if item[3])
    report: dict[str, Any] = {
        "url": url,
        "mode": "query" if is_query_mode else "get",
        "requests": args.requests,
        "concurrency": args.concurrency,
        "successes": successes,
        "failures": args.requests - successes,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(args.requests / elapsed, 2),
        "latency_ms": {
            "min": round(min(latencies), 2),
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2),
            "mean": round(statistics.mean(latencies), 2),
        },
        "http_status_counts": {str(k): v for k, v in sorted(http_codes.items(), key=lambda x: str(x[0]))},
        "error_counts": dict(errors),
    }

    if is_query_mode:
        # A 200 with insufficient_evidence is a legitimate business outcome, not
        # an HTTP failure. Report it so the benchmark cannot mistake an empty
        # answer for a healthy response.
        statuses: Counter[str] = Counter()
        citation_counts: list[int] = []
        for item in results:
            body = item[4]
            if body is None:
                statuses["no_json_body"] += 1
                continue
            statuses[str(body.get("status", "missing_status"))] += 1
            citations = body.get("citations")
            if isinstance(citations, list):
                citation_counts.append(len(citations))
        report["business_status_counts"] = dict(statuses)
        if citation_counts:
            report["citations_per_answer"] = {
                "min": min(citation_counts),
                "mean": round(statistics.mean(citation_counts), 2),
                "max": max(citation_counts),
            }
        report["question"] = args.query

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        target = Path(args.json_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"report written to {target}", file=sys.stderr)

    return 0 if successes == args.requests and report["latency_ms"]["p95"] <= args.max_p95_ms else 1


if __name__ == "__main__":
    raise SystemExit(main())
