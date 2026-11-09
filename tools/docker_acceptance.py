"""Run the approved acceptance suite against a deployed stack.

Reads the approved suite from evaluation/bootstrap_suites, queries the backend
defined by BACKEND_BASE_URL, and compares the returned citations against each
question's frozen expectation. Exit code is non-zero when the suite fails, so it
can gate CI or a release.

Usage (inside the acceptance container):
    python /app/tools/docker_acceptance.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("APP_ROOT", "/app"))
SUITE_ID = os.environ.get("SUITE_ID", "iteration1-pump-manuals-v1")
SUITE_DIR = ROOT / "evaluation" / "bootstrap_suites" / SUITE_ID
REGISTRY = ROOT / "evaluation" / "bootstrap_suites" / "approved_suites.json"
BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://backend:8000").rstrip("/")
KB_ID = os.environ.get("ACCEPTANCE_KB_ID") or os.environ.get("QDRANT_KB_ID", "")
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")
MIN_POSITIVE_MATCHES = 15


def query(question: str) -> dict:
    payload = json.dumps({"query": question}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}/v1/knowledge-bases/{KB_ID}/query",
        data=payload,
        headers={
            "Authorization": f"Bearer {ADMIN_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    if not KB_ID or not ADMIN_KEY:
        print("FAIL: ACCEPTANCE_KB_ID / ADMIN_API_KEY are not configured")
        raise SystemExit(2)

    questions = [
        json.loads(line)
        for line in (SUITE_DIR / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    approved = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if not any(entry.get("suite_id") == SUITE_ID for entry in approved.get("suites", [])):
        print(f"FAIL: suite {SUITE_ID} is not registered as approved")
        raise SystemExit(2)

    positives = [q for q in questions if q["expects_evidence"]]
    negatives = [q for q in questions if not q["expects_evidence"]]

    matched = 0
    print(f"suite: {SUITE_ID}  questions: {len(positives)} positive / {len(negatives)} negative\n")
    for question in positives:
        try:
            data = query(question["question"])
        except (urllib.error.URLError, TimeoutError) as error:
            print(f"  ERROR {question['id']}: {error}")
            continue
        expected = question["expected_citations"][0]
        got = {(c.get("document_name"), c.get("page")) for c in (data.get("citations") or [])}
        hit = (expected["source_file"], expected["page_number"]) in got
        matched += int(hit)
        print(f"  {'HIT ' if hit else 'MISS'} {data.get('status'):<22} {question['id']} {question['question'][:34]}")

    refused = 0
    print()
    for question in negatives:
        try:
            data = query(question["question"])
        except (urllib.error.URLError, TimeoutError) as error:
            print(f"  ERROR {question['id']}: {error}")
            continue
        ok = data.get("status") == "insufficient_evidence" and not (data.get("citations") or [])
        refused += int(ok)
        print(f"  {'OK  ' if ok else 'LEAK'} {str(data.get('status')):<22} {question['id']}")

    print(f"\npositive matches: {matched}/{len(positives)} (threshold >= {MIN_POSITIVE_MATCHES})")
    print(f"negative refusals: {refused}/{len(negatives)} (threshold = all)")

    ok = matched >= MIN_POSITIVE_MATCHES and refused == len(negatives)
    print("ACCEPTANCE: " + ("PASS" if ok else "FAIL"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
