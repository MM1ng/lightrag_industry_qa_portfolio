"""Run the Development-only A0/A1/A2 retrieval smoke evaluation."""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.services.lexical_retrieval import BM25Index, load_lexical_index
from industrial_rag.services.retrieval_ab_evaluation import (
    EvaluationBlocked,
    FrozenGeneration,
    load_development_cases,
    map_expected_evidence,
    run_ab_evaluation,
)

def _load_mapping(path: Path) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, list[str]] = {}
    for entry in raw.get("entries", []):
        if entry.get("mapped"):
            mapping[str(entry["gold_chunk_id"])] = [str(item) for item in entry.get("mapped_child_ids", [])]
    return mapping


def _load_v2_label_mapping(path: Path) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[str]] = {}
    for audit in raw.get("label_audits", []):
        if str(audit.get("status")) not in {"EXACT", "EQUIVALENT"}:
            raise EvaluationBlocked(f"label audit is not reliable for {audit.get('question_id')}")
        for item in audit.get("v2_candidate_evidence", []):
            historical_id = str(item["historical_chunk_id"])
            result.setdefault(historical_id, []).append(str(item["v2_chunk_id"]))
    return {key: list(dict.fromkeys(values)) for key, values in result.items()}


def _load_baseline(path: Path) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        result[str(row["case_id"])] = [
            {
                "child_chunk_id": item.get("chunk_id"),
                "score": item.get("score"),
                "rank": item.get("rank"),
                "source_file": item.get("file"),
                "page": item.get("page"),
            }
            for item in row.get("retrieved", [])
        ]
    return result


def _translate_baseline_ids(
    baseline: dict[str, list[dict[str, object]]], generation: FrozenGeneration
) -> dict[str, list[dict[str, object]]]:
    """Translate historical P0 IDs to the frozen V2 IDs without silent drops."""
    historical_by_key: dict[tuple[str, str, int | None], list[dict[str, object]]] = defaultdict(list)
    for path in (ROOT / "evaluation/experiments/parser_backend/P0").glob("*/child_chunks.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            historical_by_key[(str(row.get("chunk_id")), str(row.get("document_name")), row.get("page_start"))].append(row)
    frozen_records = [
        json.loads(line)
        for line in (generation.workspace / "retrieval" / "child_chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    def normalized(value: object) -> str:
        return " ".join(str(value or "").casefold().split())
    frozen_by_content: dict[tuple[str, int | None, str], list[str]] = defaultdict(list)
    for row in frozen_records:
        frozen_by_content[(str(row.get("document_name")), row.get("page_start"), normalized(row.get("content")))].append(str(row["chunk_id"]))
    translated: dict[str, list[dict[str, object]]] = {}
    for question, rows in baseline.items():
        output: list[dict[str, object]] = []
        for row in rows:
            old_id = str(row.get("child_chunk_id") or "")
            if old_id in generation.chunk_ids:
                candidates = [old_id]
            else:
                historical = historical_by_key.get((old_id, str(row.get("source_file")), row.get("page")), [])
                candidates = []
                for old in historical:
                    candidates.extend(
                        frozen_by_content.get(
                            (str(old.get("document_name")), old.get("page_start"), normalized(old.get("content"))),
                            [],
                        )
                    )
                candidates = list(dict.fromkeys(candidates))
            if len(candidates) != 1:
                raise EvaluationBlocked(
                    f"baseline candidate {old_id} for {question} maps to {len(candidates)} frozen chunks"
                )
            item = dict(row)
            item["historical_child_chunk_id"] = old_id
            item["child_chunk_id"] = candidates[0]
            output.append(item)
        translated[question] = output
    return translated


def _markdown(report: dict[str, object]) -> str:
    lines = [
        "# Retrieval Foundation Development A/B Evaluation",
        "",
        f"**Status:** `{report['status']}`  ",
        f"**Downstream QA allowed:** `{report['downstream_qa_allowed']}`  ",
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
    lines.extend(["", "## Reranker", "", json.dumps(report["reranker"], ensure_ascii=False, indent=2)])
    lines.extend(["", "## Delta classification", "", "| Classification | Count |", "|---|---:|"])
    for name, count in sorted(report["delta_summary"].items()):
        lines.append(f"| {name} | {count} |")
    lines.extend(["", "## Trace integrity", "", f"- Invalid chunk IDs: `{report['trace_integrity']['invalid_chunk_ids']}`", "", "## Question IDs", "", ", ".join(report["question_ids"])])
    lines.extend(["", "Raw per-question details are stored in the adjacent JSON report. Results from six questions are pipeline smoke evidence, not a stable effectiveness claim (Sample-size limitation: n=6).", ""])
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    generation = FrozenGeneration.load(args.generation)
    cases = load_development_cases(args.dataset, args.dataset_manifest)
    historical_mapping = _load_mapping(args.evidence_mapping)
    v2_mapping = _load_v2_label_mapping(args.label_audit)
    generation_mapping = {
        gold_id: list(
            dict.fromkeys(
                v2_id
                for historical_id in historical_ids
                for v2_id in v2_mapping.get(historical_id, ())
            )
        )
        for gold_id, historical_ids in historical_mapping.items()
    }
    cases = map_expected_evidence(cases, generation_mapping, generation.chunk_ids)
    lexical = load_lexical_index((generation.workspace / "retrieval" / "lexical_index.json").read_bytes())
    if lexical.child_manifest_hash != generation.child_manifest_hash:
        raise EvaluationBlocked("lexical index and generation chunk manifest differ")
    sparse_index = BM25Index.from_artifact(lexical)
    raw_baseline = _load_baseline(args.lightrag_results)
    baseline_by_question: dict[str, list[dict[str, object]]] = {}
    for case in cases:
        case_id = str(case["id"])
        if case_id not in raw_baseline:
            raise EvaluationBlocked(f"LightRAG baseline is missing case: {case_id}")
        baseline_by_question[str(case.get("question") or "")] = raw_baseline[case_id]
    baseline = _translate_baseline_ids(
        baseline_by_question,
        generation,
    )

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
    parser.add_argument("--label-audit", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_label_audit_v2.json")
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
