"""Capture frozen A2 stage trace for the Phase 13E-0 missing-evidence audit."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402
from industrial_rag.config import Settings  # noqa: E402
from industrial_rag.lightrag_service import QueryOptions, _extract_retrieved  # noqa: E402
from industrial_rag.services.evaluation_trace_contract import (  # noqa: E402
    build_evaluation_trace,
    validate_trace_contract,
)
from industrial_rag.services.lexical_retrieval import BM25Index, load_lexical_index  # noqa: E402
from industrial_rag.services.reranker_runtime import RerankerRuntime  # noqa: E402
from industrial_rag.services.rrf_fusion import reciprocal_rank_fusion  # noqa: E402
from industrial_rag.vector_collections import VectorBackend  # noqa: E402
from run_formal_retrieval_effectiveness import (  # noqa: E402
    EXPECTED_FINGERPRINT,
    EXPECTED_GENERATION,
    _build_dashscope_runtime_provider,
    preflight,
)


def classify_missing_evidence(row: dict[str, Any]) -> str:
    if row.get("representation_issue"):
        return "EVIDENCE_REPRESENTATION"
    if not row.get("lightrag_hit") and not row.get("bm25_hit"):
        return "CANDIDATE_RECALL"
    if row.get("fusion_rank") is None:
        return "FUSION_LOSS"
    if row.get("rerank_rank") is None:
        return "UNRESOLVED"
    if row.get("final_top10_rank") is None:
        return "TOPK_SELECTION"
    return "UNRESOLVED"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def capture(args: argparse.Namespace) -> dict[str, Any]:
    cases, generation, preflight_report = preflight(args.dataset, args.manifest, args.mapping, args.generation)
    old = _read(args.baseline)
    if old["dataset_identity"]["fingerprint"] != EXPECTED_FINGERPRINT or old["generation_identity"]["generation_id"] != EXPECTED_GENERATION:
        raise ValueError("baseline identity mismatch")
    lexical = BM25Index.from_artifact(load_lexical_index((generation.workspace / "retrieval" / "lexical_index.json").read_bytes()))
    load_dotenv(ROOT.parent / "lightrag_industry_qa_portfolio" / ".env", override=False)
    settings = Settings.from_env()
    settings = settings.__class__(**{**{field: getattr(settings, field) for field in settings.__dataclass_fields__}, "working_dir": generation.workspace / "lightrag_workspace", "vector_workspace": None, "vector_backend": VectorBackend.nano, "qdrant_generation": None, "sparse_retrieval_enabled": False, "reranker_enabled": False})
    from industrial_rag.lightrag_service import LightRAGService

    service = LightRAGService(settings)
    await service.initialize()
    reranker_adapter, provider = _build_dashscope_runtime_provider(generation=generation, model="qwen3-rerank", timeout_seconds=2.0)
    per_question: list[dict[str, Any]] = []
    try:
        for case in cases:
            started = time.perf_counter()
            dense = _extract_retrieved(await service._backend.aquery_data(case["question"], QueryOptions(mode="mix", top_k=20, chunk_top_k=20, enable_rerank=False)))
            sparse = [{"child_chunk_id": x.child_chunk_id, "score": x.score, "rank": x.rank} for x in await asyncio.to_thread(lexical.search, case["question"], limit=20)]
            dense = [dict(x, rank=i + 1) for i, x in enumerate(dense)]
            fused = reciprocal_rank_fusion({"lightrag": dense, "sparse": sparse}, k=60, limit=20)
            fusion_rows = [{"child_chunk_id": x.child_chunk_id, "score": x.rrf_score, "rrf_score": x.rrf_score, "rank": x.rrf_rank, "source": "rrf", "contributions": [{"source": c.source, "original_rank": c.original_rank, "original_score": c.original_score} for c in x.contributions]} for x in fused]
            rerank_result = await RerankerRuntime(provider=reranker_adapter, timeout_seconds=2.0, provider_name="aliyun_model_studio").rerank(case["question"], fusion_rows, limit=10)
            final = [dict(x, rank=i + 1) for i, x in enumerate(rerank_result.candidates)]
            final_by_id = {x["child_chunk_id"]: x for x in final}
            retrieval_candidates = [{"query_id": "original", "query_text": case["question"], "retriever_source": source, "evidence_id": x.get("child_chunk_id"), "local_rank": i + 1, "raw_score": x.get("score")} for source, rows in (("lightrag", dense), ("bm25", sparse)) for i, x in enumerate(rows)]
            trace = build_evaluation_trace(question_id=case["question_id"], question=case["question"], variants=[{"query_id": "original", "query_text": case["question"], "source": "original"}], retrieval_candidates=retrieval_candidates, fusion_candidates=[{"evidence_id": x["child_chunk_id"], "contributing_queries": ["original"], "contributing_retrievers": sorted({c["source"] for c in x["contributions"]}), "fusion_score": x["rrf_score"], "fusion_rank": x["rank"]} for x in fusion_rows], rerank_candidates=[{"evidence_id": x["child_chunk_id"], "rerank_input_rank": x["rank"], "rerank_score": final_by_id.get(x["child_chunk_id"], {}).get("rerank_score"), "rerank_rank": final_by_id.get(x["child_chunk_id"], {}).get("rank")} for x in fusion_rows], final_top5=[x["child_chunk_id"] for x in final[:5]], final_top10=[x["child_chunk_id"] for x in final[:10]], gold_evidence_ids=case["expected_child_chunk_ids"])
            validate_trace_contract(trace, raise_on_error=True)
            gold = []
            for evidence_id in case["expected_child_chunk_ids"]:
                light = [x for x in dense if x.get("child_chunk_id") == evidence_id]
                sparse_hit = [x for x in sparse if x.get("child_chunk_id") == evidence_id]
                fusion = next((x for x in fusion_rows if x["child_chunk_id"] == evidence_id), None)
                rerank = next((x for x in final if x["child_chunk_id"] == evidence_id), None)
                item = {"question_id": case["question_id"], "gold_evidence_id": evidence_id, "lightrag_hit": bool(light), "lightrag_local_rank": light[0].get("rank") if light else None, "lightrag_score": light[0].get("score") if light else None, "bm25_hit": bool(sparse_hit), "bm25_local_rank": sparse_hit[0].get("rank") if sparse_hit else None, "bm25_score": sparse_hit[0].get("score") if sparse_hit else None, "fusion_rank": fusion["rank"] if fusion else None, "fusion_score": fusion["rrf_score"] if fusion else None, "rerank_input_rank": fusion["rank"] if fusion else None, "rerank_score": rerank.get("rerank_score") if rerank else None, "rerank_rank": rerank.get("rank") if rerank else None, "final_top10_rank": rerank.get("rank") if rerank and rerank.get("rank", 99) <= 10 else None, "final_top5_rank": rerank.get("rank") if rerank and rerank.get("rank", 99) <= 5 else None}
                item["primary_cause"] = classify_missing_evidence(item) if evidence_id not in [x["child_chunk_id"] for x in final] else "PASS"
                gold.append(item)
            per_question.append({"question_id": case["question_id"], "expected_evidence": case["expected_child_chunk_ids"], "trace": trace, "gold_lineage": gold, "elapsed_ms": (time.perf_counter() - started) * 1000})
    finally:
        await service.close()
    old_a2 = old["metrics"]["A2_lightrag_bm25_rrf_reranker"]["overall"]
    return {"final_status": "TRACE_CAPTURE_COMPLETE", "scope": "Development_only_frozen_A2_trace_capture", "dataset_identity": {"fingerprint": EXPECTED_FINGERPRINT, "question_count": 24, "split": "Development"}, "generation_identity": {"generation_id": generation.generation_id, "corpus_fingerprint": generation.corpus_fingerprint, "child_manifest_hash": generation.child_manifest_hash}, "validation_or_holdout_accessed": False, "retrieval_config": {"candidate_top_n": 20, "final_top_k": 10, "rrf_k": 60}, "reranker_config": {"model": "qwen3-rerank", "timeout_seconds": 2.0, "calls": len(provider.calls)}, "baseline_metrics": old_a2, "preflight_checks": preflight_report["checks"], "per_question": per_question}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_generation_v2")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2.jsonl")
    parser.add_argument("--manifest", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_manifest.json")
    parser.add_argument("--mapping", type=Path, default=ROOT / "evaluation/retrieval_foundation/retrieval_foundation_dev_v2_evidence_mapping.json")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(capture(args))
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["final_status"], "questions": len(report["per_question"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
