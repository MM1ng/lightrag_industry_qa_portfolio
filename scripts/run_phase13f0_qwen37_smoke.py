"""Run a small explicit qwen3.7-text-rerank adapter smoke test."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evaluation.experiments.phase4.rerank.dashscope_reranker import DashScopeQwen3Reranker  # noqa: E402
GENERATION = ROOT / "evaluation/retrieval_foundation/dev_generation_v2"
INPUT = ROOT / "evaluation/retrieval_foundation/phase13c1_weighted_rrf_ablation_2026-09-03.json"
OUTPUT = ROOT / "evaluation/retrieval_foundation/phase13f0_qwen37_smoke_2026-09-03.json"
CACHE = ROOT / "evaluation/retrieval_foundation/qwen37_rerank_cache.jsonl"
QUESTION_IDS = ("S014", "S003", "S015", "S011")


def _records() -> dict[str, dict]:
    path = GENERATION / "retrieval" / "child_chunks.jsonl"
    return {
        str(row["chunk_id"]): row
        for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    }


async def _run() -> dict:
    load_dotenv(ROOT.parent / "lightrag_industry_qa_portfolio" / ".env", override=False)
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is missing")
    source = json.loads(INPUT.read_text(encoding="utf-8"))
    dataset_rows = [
        json.loads(line)
        for line in (ROOT / "evaluation/retrieval_foundation/dev_cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest = json.loads((ROOT / "evaluation/retrieval_foundation/development_dataset_manifest.json").read_text(encoding="utf-8"))
    questions = {
        str(binding["id"]): str(row["question"])
        for row, binding in zip(dataset_rows, manifest["question_bindings"], strict=True)
    }
    records = _records()
    provider = DashScopeQwen3Reranker(
        api_key=api_key,
        model="qwen3.7-text-rerank",
        timeout=60.0,
        cache_path=CACHE,
        config_hash="phase13f0-smoke",
        commit=os.popen("git rev-parse HEAD").read().strip(),
    )
    results = []
    for question_id in QUESTION_IDS:
        row = next(item for item in source["arms"]["A3.1_original_1_5"]["per_question"] if item["question_id"] == question_id)
        candidates = []
        for item in row["fusion_top20"]:
            record = records[item["child_chunk_id"]]
            candidates.append(
                {
                    "chunk_id": item["child_chunk_id"],
                    "text": record.get("content", ""),
                    "child_text_hash": record.get("text_hash") or hashlib.sha256(str(record.get("content", "")).encode("utf-8")).hexdigest(),
                    "document_id": record.get("document_id"),
                    "page": record.get("page_start"),
                    "original_rank": item["rank"],
                    "original_score": item["fusion_score"],
                }
            )
        ranked = await provider.rerank(questions[question_id], candidates, top_n=len(candidates))
        results.append(
            {
                "question_id": question_id,
                "candidate_count": len(candidates),
                "output_count": len(ranked),
                "output_ids": [item.chunk_id for item in ranked],
                "model": provider.model,
                "candidate_fingerprint": provider.candidate_fingerprint(questions[question_id], candidates),
                "status": "ok",
            }
        )
    return {
        "status": "SMOKE_PASS",
        "model": provider.model,
        "provider": "aliyun_model_studio",
        "endpoint": provider.endpoint,
        "endpoint_mode": provider.endpoint_mode,
        "questions": results,
        "calls": provider.calls,
        "cache_path": str(CACHE),
        "no_silent_fallback": all(call.get("status") == "ok" for call in provider.calls),
    }


def main() -> int:
    report = asyncio.run(_run())
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "questions": len(report["questions"]), "calls": len(report["calls"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
