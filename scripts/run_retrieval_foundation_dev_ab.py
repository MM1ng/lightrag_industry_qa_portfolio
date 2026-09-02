"""Run the Development-only A0/A1/A2 retrieval smoke evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from industrial_rag.services.lexical_retrieval import BM25Index, load_lexical_index
from industrial_rag.services.retrieval_ab_evaluation import (
    EvaluationBlocked,
    FrozenGeneration,
    load_development_cases,
    map_expected_evidence,
    run_ab_evaluation,
)

ROOT = Path(__file__).resolve().parents[1]


def _load_mapping(path: Path) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, list[str]] = {}
    for entry in raw.get("entries", []):
        if entry.get("mapped"):
            mapping[str(entry["gold_chunk_id"])] = [str(item) for item in entry.get("mapped_child_ids", [])]
    return mapping


def _load_baseline(path: Path) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        result[str(row["question"])] = [
            {"child_chunk_id": item.get("chunk_id"), "score": item.get("score"), "rank": item.get("rank")}
            for item in row.get("retrieved", [])
        ]
    return result


def _markdown(report: dict[str, object]) -> str:
    lines = [
        "# Retrieval Foundation Development A/B Evaluation",
        "",
        f"**Status:** `{report['status']}`  ",
        f"**Scope:** `{report['scope']}`  ",
        f"**Question count:** `{report['sample_size']}` (sample-size limitation: `{report['sample_size_limitation']}`)",
        "",
        "## Aggregate metrics",
        "",
        "| Variant | Recall@5 | Recall@10 | MRR@5 | MRR@10 | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    metrics = report["metrics"]
    latency = report["latency"]
    for variant in ("A0_lightrag", "A1_lightrag_bm25_rrf", "A2_lightrag_bm25_rrf_reranker"):
        overall = metrics[variant]["overall"]
        lines.append(
            f"| {variant} | {overall['recall@5']:.3f} | {overall['recall@10']:.3f} | {overall['mrr@5']:.3f} | {overall['mrr@10']:.3f} | {latency[variant]['p50_ms']:.1f} | {latency[variant]['p95_ms']:.1f} |"
        )
    lines.extend(["", "## Reranker", "", json.dumps(report["reranker"], ensure_ascii=False, indent=2), "", "## Question IDs", "", ", ".join(report["question_ids"])])
    lines.extend(["", "Raw per-question details are stored in the adjacent JSON report. Results from six questions are pipeline smoke evidence, not a stable effectiveness claim.", ""])
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    generation = FrozenGeneration.load(args.generation)
    cases = load_development_cases(args.dataset, args.dataset_manifest)
    mapping = _load_mapping(args.evidence_mapping)
    cases = map_expected_evidence(cases, mapping, generation.chunk_ids)
    lexical = load_lexical_index((generation.workspace / "retrieval" / "lexical_index.json").read_bytes())
    if lexical.child_manifest_hash != generation.child_manifest_hash:
        raise EvaluationBlocked("lexical index and generation chunk manifest differ")
    sparse_index = BM25Index.from_artifact(lexical)
    baseline = _load_baseline(args.lightrag_results)

    async def retriever(question: str, _top_k: int):
        if question not in baseline:
            raise EvaluationBlocked(f"LightRAG baseline is missing question: {question}")
        return baseline[question]

    return await run_ab_evaluation(
        cases=cases,
        generation=generation,
        sparse_index=sparse_index,
        lightrag_retriever=retriever,
        reranker_provider=None,
        reranker_provider_name=args.reranker_provider,
        reranker_model=args.reranker_model,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_cases.jsonl")
    parser.add_argument("--dataset-manifest", type=Path, default=ROOT / "evaluation/retrieval_foundation/development_dataset_manifest.json")
    parser.add_argument("--evidence-mapping", type=Path, default=ROOT / "evaluation/experiments/parser_backend/fixed_model/comparison/evidence_mapping_p0.json")
    parser.add_argument("--lightrag-results", type=Path, default=ROOT / "evaluation/experiments/parser_backend/retrieval/pymupdf_qdrant/results.jsonl")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--reranker-provider", default="external")
    parser.add_argument("--reranker-model")
    args = parser.parse_args()
    try:
        report = asyncio.run(_run(args))
    except (EvaluationBlocked, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "question_ids": report["question_ids"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
