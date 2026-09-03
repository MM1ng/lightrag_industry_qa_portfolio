"""Build the frozen Development-only retrieval dataset without invoking retrieval."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.services.expanded_development_dataset import (  # noqa: E402
    audit_dataset,
    build_manifest,
    load_generation_snapshot,
    validate_dataset,
)

LEGACY_IDS = {"S014", "S015", "S006", "S003", "S016", "S011"}
OLD_META = {
    "S014": ("parameter", "EASY"),
    "S015": ("fault_handling", "MEDIUM"),
    "S006": ("parameter", "EASY"),
    "S003": ("installation_debugging", "MEDIUM"),
    "S016": ("fault_handling", "MEDIUM"),
    "S011": ("procedure", "HARD"),
}

# These are selected from the frozen snapshot before any A/B command is run.
NEW_SPECS = [
    ("D-V2-001", "2196-R 型泵用塞尺法设置叶轮间隙时，应先把叶轮推到什么状态再测量？", "procedure", "HARD", "adjacent_chunk_evidence", ["cchunk-pymupdf-v1-17f4771c4c817a77-000", "cchunk-pymupdf-v1-c1a49660ca4fa082-001"]),
    ("D-V2-002", "2196 系列泵的填料函在启动时应调节到每分钟多少滴漏？", "parameter", "EASY", "table_structured", ["cchunk-pymupdf-v1-3b58bc1e45428865-000"]),
    ("D-V2-003", "SUMMIT 泵泵送温度超过 350°F 时，轴承应采用什么润滑方案？", "maintenance", "MEDIUM", "table_structured", ["cchunk-pymupdf-v1-e538966686c111ea-000"]),
    ("D-V2-004", "安装 2196 系列泵的入口管时，偏心异径管接的平面应朝哪个方向？", "installation_debugging", "MEDIUM", "single_evidence", ["cchunk-pymupdf-v1-8ac7c8e5aafefa93-000"]),
    ("D-V2-005", "联轴器罩拆装前需要采取哪些断电和挂牌措施？", "safety_warning_limit", "MEDIUM", "single_evidence", ["cchunk-pymupdf-v1-285811f368cf8a64-000"]),
    ("D-V2-006", "2196 泵的轴承架加润滑油时，油位应加到观察窗的什么位置？", "maintenance", "EASY", "single_evidence", ["cchunk-pymupdf-v1-6c84270736d64bea-000"]),
    ("D-V2-007", "2196 泵的零件清单中，零件号 101 和 122 分别对应什么部件？", "component_structure", "EASY", "table_structured", ["cchunk-pymupdf-v1-0993bee22239dfa9-000"]),
    ("D-V2-008", "高温工况下安装 2196 泵时，联轴器对正检查有什么额外要求？", "condition_prerequisite", "MEDIUM", "single_evidence", ["cchunk-pymupdf-v1-3b58bc1e45428865-000"]),
    ("D-V2-009", "叶轮间隙为什么会影响泵的性能，手册将它定义为哪两个表面之间的距离？", "component_structure", "HARD", "single_evidence", ["cchunk-pymupdf-v1-7e3e80ba8c62809f-000"]),
    ("D-V2-010", "DESMI 泵组启动前必须确认哪些条件已经满足？", "condition_prerequisite", "MEDIUM", "single_evidence", ["cchunk-pymupdf-v1-4e14a3b265877fb1-000"]),
    ("D-V2-011", "DESMI 水泵注液和排气时，吸入管路上的截断装置应处于什么状态？", "procedure", "MEDIUM", "single_evidence", ["cchunk-pymupdf-v1-3c48ca477586c617-000"]),
    ("D-V2-012", "DESMI 泵在排出阀关闭时能否长时间运行？如果不能，应如何保证最小液体流量？", "safety_warning_limit", "HARD", "multi_evidence", ["cchunk-pymupdf-v1-5178c456afbf1e5a-000", "cchunk-pymupdf-v1-fc16e77b6450e35c-000"]),
    ("D-V2-013", "DESMI 手册给出的最小进水压力由哪些压力或水头项共同决定？", "parameter", "HARD", "single_evidence", ["cchunk-pymupdf-v1-7ed93679bb25416b-000"]),
    ("D-V2-014", "吸入管路过滤器堵塞时，怎样监测污染程度并安排维护？", "maintenance", "MEDIUM", "single_evidence", ["cchunk-pymupdf-v1-6ebe88f96ee7a0d4-000"]),
    ("D-V2-015", "备用 DESMI 泵为保持可用，手册建议多久启动一次？还应监测哪些状态？", "maintenance", "MEDIUM", "single_evidence", ["cchunk-pymupdf-v1-7733ed3c6f455ef6-000"]),
    ("D-V2-016", "泵组长期停用期间，应以什么频率运行多长时间来降低沉积或卡滞风险？", "maintenance", "HARD", "single_evidence", ["cchunk-pymupdf-v1-e1d1a306d81ddb56-000"]),
    ("D-V2-017", "DESMI 泵发生机械密封短时间后泄漏且此前长期存放时，手册列出的处理方向是什么？", "fault_handling", "HARD", "single_evidence", ["cchunk-pymupdf-v1-cb4c3258ac73b8a0-000"]),
    ("D-V2-018", "拆卸 DESMI DNS 标准结构的后拉单元时，如何支撑泵端以避免倾倒伤害？", "safety_warning_limit", "HARD", "single_evidence", ["cchunk-pymupdf-v1-b0fdd25338e01728-000"]),
 ]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _evidence(snapshot, child_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "child_chunk_id": child_id,
            "parent_chunk_id": snapshot.children[child_id].parent_chunk_id,
            "text": snapshot.children[child_id].content,
            "page_start": snapshot.children[child_id].page_start,
            "page_end": snapshot.children[child_id].page_end,
            "section_path": list(snapshot.children[child_id].section_path),
            "location": f"第 {snapshot.children[child_id].page_start} 页 / {snapshot.children[child_id].section_title}",
            "necessary": True,
        }
        for child_id in child_ids
    ]


def _legacy_cases(snapshot, root: Path) -> list[dict[str, Any]]:
    old_rows = _read_jsonl(root / "evaluation/retrieval_foundation/dev_cases.jsonl")
    old = dict(zip(["S014", "S015", "S006", "S003", "S016", "S011"], old_rows, strict=True))
    audit = json.loads((root / "evaluation/retrieval_foundation/dev_label_audit_v2.json").read_text(encoding="utf-8"))
    mapping = {str(item["question_id"]): [str(x["v2_chunk_id"]) for x in item["v2_candidate_evidence"]] for item in audit["label_audits"]}
    result = []
    for question_id in ["S014", "S015", "S006", "S003", "S016", "S011"]:
        child_ids = mapping[question_id]
        first = snapshot.children[child_ids[0]]
        question_type, difficulty = OLD_META[question_id]
        result.append({
            "question_id": question_id,
            "question": old[question_id]["question"],
            "split": "development",
            "source_document_id": first.document_id,
            "question_type": question_type,
            "difficulty": difficulty,
            "evidence_pattern": "multi_evidence" if len(child_ids) > 1 else "single_evidence",
            "expected_child_chunk_ids": child_ids,
            "expected_parent_chunk_ids": list(dict.fromkeys(snapshot.children[x].parent_chunk_id for x in child_ids)),
            "evidence": _evidence(snapshot, child_ids),
            "legacy_source": "dev_cases.jsonl + dev_label_audit_v2.json (EQUIVALENT)",
        })
    return result


def build(root: Path, generation_path: Path, output: Path) -> dict[str, Any]:
    snapshot = load_generation_snapshot(generation_path)
    cases = _legacy_cases(snapshot, root)
    for question_id, question, question_type, difficulty, pattern, child_ids in NEW_SPECS:
        cases.append({
            "question_id": question_id,
            "question": question,
            "split": "development",
            "source_document_id": snapshot.children[child_ids[0]].document_id,
            "question_type": question_type,
            "difficulty": difficulty,
            "evidence_pattern": pattern,
            "expected_child_chunk_ids": child_ids,
            "expected_parent_chunk_ids": list(dict.fromkeys(snapshot.children[x].parent_chunk_id for x in child_ids)),
            "evidence": _evidence(snapshot, child_ids),
        })
    errors = validate_dataset(cases, snapshot)
    if errors:
        raise ValueError("; ".join(errors))
    audit = audit_dataset(cases, snapshot, LEGACY_IDS)
    guards = {
        "a0_a1_a2_not_run": True,
        "validation_holdout_not_accessed": True,
        "generation_not_modified": True,
        "retrieval_parameters_not_modified": True,
    }
    manifest = build_manifest(cases, snapshot, audit, source_dataset="retrieval_foundation_dev_v2.jsonl", guards=guards)
    output.mkdir(parents=True, exist_ok=True)
    dataset_path = output / "retrieval_foundation_dev_v2.jsonl"
    dataset_path.write_text("".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases), encoding="utf-8")
    mapping = {case["question_id"]: {"source_document_id": case["source_document_id"], "child_parent": [{"child_chunk_id": item["child_chunk_id"], "parent_chunk_id": item["parent_chunk_id"]} for item in case["evidence"]], "evidence": case["evidence"]} for case in cases}
    (output / "retrieval_foundation_dev_v2_evidence_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "retrieval_foundation_dev_v2_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_reports(output, manifest, audit)
    return manifest


def _write_reports(output: Path, manifest: dict[str, Any], audit: dict[str, Any]) -> None:
    coverage = audit["coverage"]
    lines = ["# Expanded Development Dataset Coverage", "", f"- Status: `{manifest['final_status']}`", f"- Total: {manifest['counts']['total_questions']}", f"- New: {manifest['counts']['new_questions']}", f"- Legacy retained: {manifest['counts']['legacy_questions_retained']}", "", "## Coverage", ""]
    for name, values in coverage.items():
        lines += [f"### {name}", "", *[f"- {key}: {value}" for key, value in sorted(values.items())], ""]
    lines += ["### Evidence patterns", "", f"- single evidence: {audit['counts']['single_evidence']}", f"- multi evidence: {audit['counts']['multi_evidence']}", f"- table/structured: {audit['counts']['table_or_structured']}", f"- adjacent chunk: {audit['counts']['adjacent_chunk']}", ""]
    (output / "retrieval_foundation_dev_v2_coverage.md").write_text("\n".join(lines), encoding="utf-8")
    duplicate = audit["duplicate_audit"]
    audit_lines = ["# Expanded Development Dataset Quality Audit", "", f"- Status: `{manifest['final_status']}`", f"- Dataset fingerprint: `{manifest['dataset_fingerprint']}`", f"- Evidence mapping complete: `{manifest['evidence_mapping_complete']}`", f"- Duplicate audit passed: `{manifest['duplicate_audit_passed']}`", f"- Max evidence reuse: `{duplicate['max_evidence_reuse']}`", "", "## Guard status", ""]
    audit_lines += [f"- {key}: `{value}`" for key, value in manifest["guards"].items()]
    failures = [f"- {failure}" for failure in manifest["gate_failures"]] or ["- none"]
    audit_lines += ["", "## Duplicate findings", "", json.dumps(duplicate["question_duplicate_pairs"], ensure_ascii=False, indent=2), "", "## Gate failures", "", *failures]
    (output / "retrieval_foundation_dev_v2_audit.md").write_text("\n".join(audit_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, default=ROOT / "evaluation/retrieval_foundation/dev_generation_v2")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/retrieval_foundation")
    args = parser.parse_args()
    build(ROOT, args.generation, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
