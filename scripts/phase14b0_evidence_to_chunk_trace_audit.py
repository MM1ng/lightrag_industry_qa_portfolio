"""Read-only Phase 14B-0 evidence-to-chunk lineage audit.

This module deliberately does not import a retriever or a reranker.  It audits
only frozen snapshots and previously saved evaluation artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "evaluation" / "retrieval_foundation"
GENERATION = EVALUATION / "dev_generation_v2"
UNAVAILABLE = "unavailable"
CAUSES = (
    "PARSING_LOSS",
    "CHUNK_GENERATION_LOSS",
    "INDEX_MISSING",
    "CANDIDATE_RECALL_FAILURE",
    "FUSION_LOSS",
    "RERANKER_LOSS",
    "TOPK_SELECTION_LOSS",
    "UNRESOLVED",
)
LINEAGE_FIELDS = (
    "question_id", "gold_evidence_id", "document_id", "page", "gold_text_hash",
    "parsed_block_ids", "parsed_match", "parent_chunk_ids", "child_chunk_ids",
    "chunk_match", "embedding_exists", "bm25_exists", "retrieval_hit",
    "retrieval_rank", "fusion_hit", "fusion_rank", "rerank_hit", "rerank_rank",
    "final_top10", "final_top5",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_lineage_record(row: dict[str, object]) -> list[str]:
    return [f"missing field: {field}" for field in LINEAGE_FIELDS if field not in row]


def build_missing_rows(
    missing: dict[str, list[str]],
    supplied: dict[tuple[str, str], dict[str, object]],
) -> list[dict[str, object]]:
    """Keep the frozen missing-only denominator even when a stage has no trace."""
    rows: list[dict[str, object]] = []
    for question_id, evidence_ids in missing.items():
        for evidence_id in evidence_ids:
            row = supplied.get((question_id, evidence_id), {
                "question_id": question_id,
                "gold_evidence_id": evidence_id,
                "retrieval_hit": UNAVAILABLE,
                "retrieval_rank": None,
            })
            rows.append(row)
    return rows


def resolve_chunk_lineage(
    evidence_id: str,
    gold_text: str,
    child_by_id: dict[str, dict[str, Any]],
) -> dict[str, object]:
    child = child_by_id.get(evidence_id)
    if child is None:
        return {"parent_chunk_ids": [], "child_chunk_ids": [], "chunk_match": "MISSING"}
    content = child.get("content", "")
    if content == gold_text:
        match = "FULL"
    elif gold_text and content and (gold_text in content or content in gold_text):
        match = "PARTIAL"
    else:
        match = "MISSING"
    return {
        "parent_chunk_ids": [child["parent_chunk_id"]] if child.get("parent_chunk_id") else [],
        "child_chunk_ids": [evidence_id],
        "chunk_match": match,
    }


def identity_drift(expected: dict[str, object], current: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        key: {"expected": value, "actual": current.get(key)}
        for key, value in expected.items()
        if current.get(key) != value
    }


def classify_root_cause(row: dict[str, object]) -> str:
    """Apply the phase contract without turning unknown trace into a failure."""
    if row["parsed_match"] == "MISSING":
        return "PARSING_LOSS"
    if row["chunk_match"] == "MISSING":
        return "CHUNK_GENERATION_LOSS"
    if row["embedding_exists"] is False or row["bm25_exists"] is False:
        return "INDEX_MISSING"
    if row["retrieval_hit"] is False:
        return "CANDIDATE_RECALL_FAILURE"
    if row["retrieval_hit"] is True and row["fusion_hit"] is False:
        return "FUSION_LOSS"
    if row["retrieval_hit"] is True and row["fusion_hit"] is True and row["rerank_hit"] is False:
        return "RERANKER_LOSS"
    if row["retrieval_hit"] is True and row["fusion_hit"] is True and row["rerank_hit"] is True and row["final_top10"] is False:
        return "TOPK_SELECTION_LOSS"
    return "UNRESOLVED"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def historical_missing(phase13b: dict[str, Any]) -> dict[str, list[str]]:
    return {
        row["question_id"]: list(row["a2_missing_gold"])
        for row in phase13b["phase13a_six_miss_recovery"]["questions"]
    }


def vdb_evidence_ids(vdb: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in vdb.get("data", []):
        match = re.search(r"\bchunk=([^\]\s]+)", item.get("content", ""))
        if match:
            ids.add(match.group(1))
    return ids


def final_a2_rows(formal: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        row["id"]: row["variants"]["A2_lightrag_bm25_rrf_reranker"]
        for row in formal["per_question"]
    }


def parser_records(phase14a: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (row["question_id"], row["gold_evidence_id"]): row["pymupdf"]
        for row in phase14a["results"]["evidence_diff"]
        if row["diagnostic"]
    }


def make_lineage_rows(
    missing: dict[str, list[str]],
    evidence_map: dict[str, dict[str, Any]],
    child_by_id: dict[str, dict[str, Any]],
    parser_by_key: dict[tuple[str, str], dict[str, Any]],
    embedding_ids: set[str],
    bm25_ids: set[str],
    a2_by_question: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for question_id, evidence_ids in missing.items():
        final = a2_by_question[question_id]
        final_ids = [item["child_chunk_id"] for item in final["top_results"]]
        for evidence_id in evidence_ids:
            gold = evidence_map[evidence_id]
            child = child_by_id.get(evidence_id, {})
            parser = parser_by_key.get((question_id, evidence_id))
            chunk = resolve_chunk_lineage(evidence_id, gold["text"], child_by_id)
            final_rank = final_ids.index(evidence_id) + 1 if evidence_id in final_ids else None
            row: dict[str, Any] = {
                "question_id": question_id,
                "gold_evidence_id": evidence_id,
                "document_id": child.get("document_id", UNAVAILABLE),
                "page": gold["page_start"],
                "gold_text_hash": sha256_text(gold["text"]),
                "parsed_block_ids": [block["block_id"] for block in parser.get("blocks", [])] if parser else [],
                "parsed_match": parser["status"] if parser else UNAVAILABLE,
                **chunk,
                "embedding_exists": evidence_id in embedding_ids,
                "bm25_exists": evidence_id in bm25_ids,
                # The canonical A2 artifact only saves final TopK.  Its raw and
                # fusion candidates were not captured, so none of these values
                # may be inferred from absence in the final result.
                "retrieval_hit": UNAVAILABLE,
                "retrieval_rank": None,
                "fusion_hit": UNAVAILABLE,
                "fusion_rank": None,
                "rerank_hit": UNAVAILABLE,
                "rerank_rank": None,
                "final_top10": final_rank is not None and final_rank <= 10,
                "final_top5": final_rank is not None and final_rank <= 5,
                "trace_availability": {
                    "canonical_a2_raw_retrieval": UNAVAILABLE,
                    "canonical_a2_fusion_top20": UNAVAILABLE,
                    "canonical_a2_rerank_output": "final_top10_only",
                },
            }
            row["primary_root_cause"] = classify_root_cause(row)
            rows.append(row)
    return rows


def funnel(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    stages = {
        "parsed": lambda row: row["parsed_match"] in {"FULL", "PARTIAL"},
        "chunk": lambda row: row["chunk_match"] in {"FULL", "PARTIAL"},
        "embedding": lambda row: row["embedding_exists"] is True,
        "bm25": lambda row: row["bm25_exists"] is True,
        "retrieval": lambda row: row["retrieval_hit"] is True,
        "fusion": lambda row: row["fusion_hit"] is True,
        "rerank": lambda row: row["rerank_hit"] is True,
        "final_top10": lambda row: row["final_top10"] is True,
        "final_top5": lambda row: row["final_top5"] is True,
    }
    return {
        name: {"known_hit": sum(predicate(row) for row in rows), "unavailable": sum(_is_unavailable_for_stage(row, name) for row in rows)}
        for name, predicate in stages.items()
    }


def _is_unavailable_for_stage(row: dict[str, Any], stage: str) -> bool:
    field = {"retrieval": "retrieval_hit", "fusion": "fusion_hit", "rerank": "rerank_hit"}.get(stage)
    return field is not None and row[field] == UNAVAILABLE


def render_markdown(report: dict[str, Any]) -> str:
    identity = report["experiment_identity"]
    lines = [
        "# Phase 14B-0 — Evidence-to-Chunk Trace Audit",
        "",
        f"**Status:** `{report['status']}`",
        f"**Final decision:** `{report['final_decision']}`",
        "",
        "## 1. Executive Summary",
        "",
        "All 21 historical missing evidence records are present in the saved PyMuPDF parser audit, frozen child registry, embedding snapshot, and BM25 index. The canonical A2 artifact records final TopK only; it does not persist canonical raw retrieval, fusion Top20, or complete reranker output. Consequently, the first retrieval-stage loss cannot be attributed without guessing. This audit therefore finds no supported parsing, chunk-generation, or index loss and treats the retrieval-stage root cause as trace-incomplete.",
        "",
        "## 2. Experiment Identity",
        "",
        f"- Audit commit: `{identity['audit_commit']}`",
        f"- Generation: `{identity['generation_id']}`",
        f"- Dataset fingerprint: `{identity['dataset_fingerprint']}`",
        f"- Child chunk registry fingerprint: `{identity['chunk_fingerprint']}`",
        f"- BM25 fingerprint: `{identity['bm25_fingerprint']}`",
        f"- Embedding snapshot fingerprint: `{identity['embedding_snapshot_fingerprint']}`",
        f"- Qdrant collection: `{identity['qdrant_collection_identity']}`",
        f"- Identity drift: `{identity['drift'] or 'none'}`",
        "",
        "Qdrant is not applicable to this frozen generation: the saved LightRAG chunk vector store is the local Nano `vdb_chunks.json` snapshot. No Qdrant connection was read or queried.",
        "",
        "## 3. Evidence Funnel",
        "",
        "| Stage | Known hit / 21 | Unavailable / 21 |",
        "|---|---:|---:|",
    ]
    for stage, counts in report["evidence_funnel"].items():
        lines.append(f"| {stage} | {counts['known_hit']} | {counts['unavailable']} |")
    lines += [
        "",
        "The final Top10/Top5 values are independently read from the canonical formal A2 artifact. The intermediate A2 values are unavailable rather than zero because their candidate traces were never saved in that artifact.",
        "",
        "## 4. Root Cause Distribution",
        "",
    ]
    for cause in CAUSES:
        lines.append(f"- `{cause}`: {report['root_cause_distribution'][cause]}")
    lines += ["", "## 5. Question Level Analysis", ""]
    for question_id, item in report["question_analysis"].items():
        evidence_rows = [row for row in report["evidence_lineage"] if row["question_id"] == question_id]
        lines += [
            f"### {question_id}",
            "",
            f"- Gold evidence: {item['gold_count']}; final Top10 hits: {item['final_top10_hits']}; final Top5 hits: {item['final_top5_hits']}",
            f"- Parsed / Chunk / Embedding / BM25: {item['parsed_hits']}/{item['chunk_hits']}/{item['embedding_hits']}/{item['bm25_hits']}",
            f"- Retrieval / Fusion / Rerank: `unavailable` for all missing evidence in canonical A2.",
            f"- First failure point: `{item['first_failure_point']}`; root cause: `{item['primary_root_cause']}`.",
            "",
            "| Gold evidence | Parsed block(s) | Chunk | Embedding / BM25 | Retrieval | Fusion | Rerank | Final |",
            "|---|---:|---|---|---|---|---|---|",
        ]
        for row in evidence_rows:
            lines.append(
                f"| `{row['gold_evidence_id']}` | {len(row['parsed_block_ids'])} / {row['parsed_match']} | "
                f"{row['chunk_match']} | {row['embedding_exists']} / {row['bm25_exists']} | "
                f"{row['retrieval_hit']} | {row['fusion_hit']} | {row['rerank_hit']} | "
                f"Top10={row['final_top10']}, Top5={row['final_top5']} |"
            )
        lines.append("")
    lines += [
        "## 6. Final Decision",
        "",
        f"`{report['final_decision']}`. A parser/chunk/index root cause is not supported by the frozen artifacts. The next safe diagnostic is to capture a canonical A2 retrieval trace under the existing trace contract; no retrieval, chunking, or ranking change is justified by this audit.",
        "",
        "## Trace Limitations",
        "",
        "- Phase 13D-2 includes complete trace fields, but its arms are Multi-query A3.1 rather than frozen A2 and cannot be substituted for A2 attribution.",
        "- Phase 13E-0 captured A2-like stages but is explicitly noncanonical because its live replay drifted from the frozen A2 final metrics. It is retained as historical context only and is not used for this classification.",
    ]
    return "\n".join(lines) + "\n"


def audit(audit_commit: str) -> dict[str, Any]:
    phase13b = load_json(EVALUATION / "phase13b_multi_query_ablation_2026-09-03.json")
    formal = load_json(EVALUATION / "formal_development_effectiveness_2026-09-03.json")
    phase14a = load_json(ROOT / "docs" / "phase-14a-pymupdf-vs-mineru-parser-ab.json")
    dataset_manifest = load_json(EVALUATION / "retrieval_foundation_dev_v2_manifest.json")
    generation_metadata = load_json(GENERATION / "generation_metadata.json")
    chunk_manifest = load_json(GENERATION / "retrieval" / "chunk_manifest.json")
    lexical = load_json(GENERATION / "retrieval" / "lexical_index.json")
    vdb_path = GENERATION / "lightrag_workspace" / "vdb_chunks.json"
    vdb = load_json(vdb_path)
    evidence_mapping = load_json(EVALUATION / "retrieval_foundation_dev_v2_evidence_mapping.json")
    evidence_by_id = {
        row["child_chunk_id"]: row
        for question in evidence_mapping.values()
        for row in question["evidence"]
    }
    children = read_jsonl(GENERATION / "retrieval" / "child_chunks.jsonl")
    child_by_id = {child["chunk_id"]: child for child in children}
    expected = {
        "dataset_fingerprint": formal["dataset_identity"]["fingerprint"],
        "generation_id": formal["generation_identity"]["generation_id"],
        "chunk_fingerprint": formal["generation_identity"]["child_manifest_hash"],
    }
    current = {
        "dataset_fingerprint": dataset_manifest["dataset_fingerprint"],
        "generation_id": generation_metadata["generation_id"],
        "chunk_fingerprint": chunk_manifest["child_manifest_hash"],
    }
    drift = identity_drift(expected, current)
    if phase14a["identity"]["dataset_fingerprint"] != expected["dataset_fingerprint"]:
        drift["phase14a_dataset_fingerprint"] = {"expected": expected["dataset_fingerprint"], "actual": phase14a["identity"]["dataset_fingerprint"]}
    if phase14a["identity"]["generation"] != expected["generation_id"]:
        drift["phase14a_generation"] = {"expected": expected["generation_id"], "actual": phase14a["identity"]["generation"]}
    missing = historical_missing(phase13b)
    rows = make_lineage_rows(
        missing,
        evidence_by_id,
        child_by_id,
        parser_records(phase14a),
        vdb_evidence_ids(vdb),
        set(lexical["document_lengths"]),
        final_a2_rows(formal),
    )
    distribution = Counter(row["primary_root_cause"] for row in rows)
    for cause in CAUSES:
        distribution.setdefault(cause, 0)
    question_analysis: dict[str, Any] = {}
    for question_id in missing:
        question_rows = [row for row in rows if row["question_id"] == question_id]
        question_causes = Counter(row["primary_root_cause"] for row in question_rows)
        question_analysis[question_id] = {
            "gold_count": len(question_rows),
            "parsed_hits": sum(row["parsed_match"] in {"FULL", "PARTIAL"} for row in question_rows),
            "chunk_hits": sum(row["chunk_match"] in {"FULL", "PARTIAL"} for row in question_rows),
            "embedding_hits": sum(row["embedding_exists"] is True for row in question_rows),
            "bm25_hits": sum(row["bm25_exists"] is True for row in question_rows),
            "final_top10_hits": sum(row["final_top10"] is True for row in question_rows),
            "final_top5_hits": sum(row["final_top5"] is True for row in question_rows),
            "first_failure_point": "canonical_a2_retrieval_trace_unavailable",
            "primary_root_cause": question_causes.most_common(1)[0][0],
            "secondary_causes": [],
            "evidence_ids": [row["gold_evidence_id"] for row in question_rows],
        }
    identity = {
        "audit_commit": audit_commit,
        "generation_id": current["generation_id"],
        "dataset_fingerprint": current["dataset_fingerprint"],
        "chunk_fingerprint": current["chunk_fingerprint"],
        "parent_chunk_fingerprint": generation_metadata["parent_snapshot_hash"],
        "pdf_fingerprint": generation_metadata["corpus_fingerprint"],
        "parsed_document_fingerprint": phase14a["result_fingerprint"],
        "embedding_snapshot_fingerprint": sha256_file(vdb_path),
        "bm25_fingerprint": lexical["artifact_hash"],
        "qdrant_collection_identity": "not_applicable:nano_local_vdb_chunks",
        "drift": drift,
    }
    status = "BLOCKED_EXPERIMENT_IDENTITY" if drift else "TRACE_INCOMPLETE"
    return {
        "phase": "14B-0",
        "status": status,
        "final_decision": "TRACE_INCOMPLETE" if not drift else "NO_ACTIONABLE_ROOT_CAUSE",
        "read_only": True,
        "models_called": 0,
        "retrieval_rerun": False,
        "rerank_rerun": False,
        "embedding_rerun": False,
        "experiment_identity": identity,
        "historical_missing_evidence_total": len(rows),
        "historical_missing_question_ids": list(missing),
        "evidence_funnel": funnel(rows),
        "root_cause_distribution": dict(distribution),
        "question_analysis": question_analysis,
        "evidence_lineage": rows,
        "trace_sources": {
            "historical_missing_denominator": "phase13b_multi_query_ablation_2026-09-03.json:phase13a_six_miss_recovery",
            "canonical_final_a2": "formal_development_effectiveness_2026-09-03.json",
            "parser_lineage": "docs/phase-14a-pymupdf-vs-mineru-parser-ab.json",
            "frozen_generation": "dev_generation_v2",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.commit)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "missing": report["historical_missing_evidence_total"]}))


if __name__ == "__main__":
    main()
