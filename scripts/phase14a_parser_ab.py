"""Offline parser-only audit. No networking, retrieval, chunk construction or QA.

Acquisition ZIPs/checkpoints are inputs, never instructions to submit new tasks.
`--replay` re-evaluates saved normalized records without running either parser.
"""
# ruff: noqa: RUF001
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
import zipfile
from collections import Counter
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean
from typing import Any

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from industrial_rag.document_parser import parse_pdf  # noqa: E402
from industrial_rag.services.expanded_development_dataset import (  # noqa: E402
    canonical_dataset_fingerprint,
    load_generation_snapshot,
    validate_dataset,
)
from industrial_rag.structured_chunker import pymupdf_chunks_to_blocks  # noqa: E402

BASE = ROOT / "evaluation/retrieval_foundation"
GENERATION = BASE / "dev_generation_v2"
DATASET = BASE / "retrieval_foundation_dev_v2.jsonl"
OUT = ROOT / "evaluation/experiments/phase14a_parser_ab"
ARTIFACT = ROOT / "docs/phase-14a-pymupdf-vs-mineru-parser-ab.json"
FINGERPRINT = "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060"
PAGE_COUNTS = {"doc-4ffb6df91a9a": 55, "doc-6a9ea3ff1f42": 62}
NUMBER = r"[+−-]?(?:\d+\s*/\s*\d+|\d+(?:\.\d+)?|\.\d+)"
UNIT = r"(?:\s*(?:°\s*[CF]|℃|℉|MPa|kPa|Pa|mm/s|mm|cm|m/s|m³/h|m3/h|min|m|SSU|rpm|r/min|Hz|kW|W|V|A|s|h|%|\"|″|英寸|毫米|厘米|小时|分钟|秒|滴))?"
NUMERIC = re.compile(r"(?:(?:≤|≥|±|<=|>=|<|>)\s*)?" + NUMBER + UNIT
                     + r"(?:\s*[至~～—–]\s*" + NUMBER + UNIT + r")?")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def compact(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text))


def canonical_parser_record(**values: Any) -> dict[str, Any]:
    keys = ("document_id", "page_no", "block_id", "block_type", "text", "bbox",
            "reading_order", "section_path", "table_content", "parser_name", "parser_version")
    return {key: values.get(key) for key in keys}


def parser_artifact_fingerprint(records: Any) -> str:
    return hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def validate_bundle_fingerprints(artifact: dict) -> None:
    observed = {name: parser_artifact_fingerprint(rows)
                for name, rows in artifact["normalized_records"].items()}
    if set(observed) != {"pymupdf", "mineru"} or observed != artifact["normalized_fingerprints"]:
        raise ValueError("BLOCKED_EXPERIMENT_IDENTITY: normalized bundle fingerprint")
    if parser_artifact_fingerprint(artifact["results"]) != artifact["result_fingerprint"]:
        raise ValueError("Result fingerprint mismatch")


def verify_pdf(path: Path, expected_hash: str, pages: int) -> None:
    if sha(path) != expected_hash:
        raise ValueError("BLOCKED_EXPERIMENT_IDENTITY: PDF bytes")
    with pymupdf.open(path) as doc:
        if len(doc) != pages:
            raise ValueError("BLOCKED_EXPERIMENT_IDENTITY: PDF page count")


def validate_records(records: list[dict], pages: dict[str, int]) -> None:
    seen = set()
    for r in records:
        if r["document_id"] not in pages or not 1 <= r["page_no"] <= pages[r["document_id"]]:
            raise ValueError("Invalid document/page identity")
        key = (r["document_id"], r["block_id"])
        if key in seen:
            raise ValueError("Duplicate block identity")
        seen.add(key)


def exact_numeric_tokens(text: str) -> list[str]:
    return [compact(m.group()) for m in NUMERIC.finditer(normalize_text(text))]


def numeric_spans(text: str) -> list[tuple[int, int, str]]:
    text = normalize_text(text)
    return [(len(compact(text[:m.start()])), len(compact(text[:m.end()])), compact(m.group()))
            for m in NUMERIC.finditer(text)]


def classify_evidence_match(gold: str, parsed: str, *, page_overlap: bool) -> dict:
    g, p = compact(gold), compact(parsed)
    matcher = SequenceMatcher(None, g, p, autojunk=False)
    spans = [m for m in matcher.get_matching_blocks() if m.size]
    coverage = sum(m.size for m in spans) / len(g) if g and page_overlap else 0
    numbers = numeric_spans(gold)
    parsed_numbers = set(numeric_spans(parsed))
    details = []
    for number_start, number_end, expression in numbers:
        # Use positional alignment + a four-character left context. A number from
        # a different statement on the page cannot repair a missing expression.
        exact = False
        for match in spans:
            if match.a <= max(0, number_start - 4) and number_end <= match.a + match.size:
                start = match.b + number_start - match.a
                exact = (start, start + len(expression), expression) in parsed_numbers
                if exact:
                    break
        details.append({"expression": expression, "gold_offset": number_start, "exact": exact})
    numeric_exact = all(d["exact"] for d in details) if numbers else None
    status = ("FULL" if coverage == 1 and numeric_exact is not False else
              "PARTIAL" if coverage >= .5 else "MISSING")
    missing = [{"gold": g[a:b], "parsed": p[c:d], "operation": op}
               for op, a, b, c, d in matcher.get_opcodes() if op in {"replace", "delete"}]
    return {"status": status, "ordered_coverage": coverage, "numeric_exact": numeric_exact,
            "numeric_expressions": details, "missing_fragments": missing,
            "alignment_spans": [{"gold_start": m.a, "parsed_start": m.b, "length": m.size}
                                for m in spans]}


class TableReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self.cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        if tag in {"td", "th"}:
            a = dict(attrs)
            self.cell = {"text": "", "rowspan": int(a.get("rowspan", 1)),
                         "colspan": int(a.get("colspan", 1))}
            if not self.rows:
                self.rows.append([])
            self.rows[-1].append(self.cell)
        if tag == "br" and self.cell is not None:
            self.cell["text"] += "\n"

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            self.cell = None


def table_cells(html: str) -> list[list[dict]]:
    reader = TableReader()
    reader.feed(html)
    return reader.rows


def pymupdf_blocks(path: Path, doc_id: str) -> list[dict]:
    # Exactly the existing parse_service entry/upstream adapter; never construct
    # new Parent/Child chunks or persist production records.
    blocks = pymupdf_chunks_to_blocks(parse_pdf(path), path.name)
    return [canonical_parser_record(
        document_id=doc_id, page_no=b.page_number, block_id=b.block_id,
        block_type=b.block_type.value, text=normalize_text(b.text), bbox=b.bbox,
        reading_order=i, section_path=list(b.section_path) or None, table_content=None,
        parser_name="pymupdf", parser_version=pymupdf.VersionBind)
        for i, b in enumerate(blocks, 1)]


def mineru_blocks(source: dict) -> tuple[list[dict], dict]:
    folder = OUT / "mineru_raw" / source["file_name"]
    checkpoint = load(folder / "download_checkpoint.json")
    path = Path(checkpoint["zip_path"])
    if checkpoint["pdf_sha256"] != source["sha256"] or sha(path) != checkpoint["zip_sha256"]:
        raise ValueError("BLOCKED_EXPERIMENT_IDENTITY: MinerU checkpoint")
    with zipfile.ZipFile(path) as z:
        if z.testzip():
            raise ValueError("MinerU ZIP CRC failed")
        name, = [n for n in z.namelist() if n.endswith("_content_list.json")]
        raw = z.read(name)
        items = json.loads(raw)
        layout = json.loads(z.read("layout.json"))
        pages = layout["pdf_info"]
        if len(pages) != PAGE_COUNTS[source["document_id"]]:
            raise ValueError("BLOCKED_EXPERIMENT_IDENTITY: MinerU page count")
    version = layout.get("_version_name") or layout.get("version")
    records = []
    section: list[str] = []
    # Raw array order is the parser's output order. No x/y sorting or furniture
    # deletion. text_level is retained as given, not guessed from text.
    for i, item in enumerate(items, 1):
        kind = item["type"]
        table = item.get("table_body") if kind == "table" else None
        text = item.get("text", "")
        if table:
            text = "\n".join("\t".join(c["text"] for c in row) for row in table_cells(table))
        for key in ("table_caption", "image_caption"):
            if item.get(key):
                text = "\n".join(item[key]) + "\n" + text
        for key in ("table_footnote", "image_footnote"):
            if item.get(key):
                text += "\n" + "\n".join(item[key])
        if item.get("text_level") is not None:
            level = int(item["text_level"])
            section = [*section[:max(0, level - 1)], text]
            kind = "heading"
        r = canonical_parser_record(
            document_id=source["document_id"], page_no=item["page_idx"] + 1,
            block_id=f"mineru-{source['document_id']}-{i:04d}", block_type=kind,
            text=normalize_text(text), bbox=item.get("bbox"), reading_order=i,
            section_path=section.copy() or None, table_content=table,
            parser_name="mineru", parser_version=version)
        records.append(r)
    # No signed URLs, key, or task bearer data leave the local checkpoint.
    manifest = {"pdf_sha256": source["sha256"], "zip_sha256": sha(path),
                "content_list_sha256": hashlib.sha256(raw).hexdigest(),
                "model_requested": "pipeline", "api_version": "v4",
                "actual_parser_version": version, "page_count": len(pages),
                "raw_items": len(items), "bbox_coordinate_space": "raw content_list 0..1000",
                "bbox_note": "Normalized scale verified against layout.json coordinates and PDF page sizes; raw boxes preserved.",
                "layout_metadata": {k: v for k, v in layout.items() if k != "pdf_info"}}
    return records, manifest


def groups(case: dict, evidence: dict) -> list[str]:
    result = ["multi" if len(case["expected_child_chunk_ids"]) > 1 else "single",
              "type:" + case["question_type"], "pattern:" + case["evidence_pattern"],
              "source:" + case["source_document_id"]]
    if exact_numeric_tokens(evidence["text"]):
        result.append("numeric")
    if case["evidence_pattern"] == "table_structured":
        result.append("table")
    if case["evidence_pattern"] == "adjacent_chunk_evidence":
        result.append("adjacent")
    return result


def evaluate(cases: list[dict], records: list[dict], parser: str) -> list[dict]:
    results = []
    for case in cases:
        for e in case["evidence"]:
            relevant = [r for r in records if r["document_id"] == case["source_document_id"]
                        and e["page_start"] <= r["page_no"] <= e["page_end"]]
            text = "\n".join(r["text"] for r in relevant)
            match = classify_evidence_match(e["text"], text, page_overlap=bool(text))
            # A block contributes only through an aligned run >=4 characters
            # (or the entire gold if shorter). Not arbitrary same-page presence.
            selected, offset = [], 0
            for r in relevant:
                end = offset + len(compact(r["text"]))
                runs = match["alignment_spans"]
                if any(min(end, m["parsed_start"] + m["length"]) - max(offset, m["parsed_start"])
                       >= min(4, len(compact(e["text"]))) for m in runs):
                    selected.append(r)
                offset = end
            paths = [r["section_path"] for r in selected if r["section_path"]]
            expected = [compact(s) for s in e["section_path"]]
            section_match = (any([compact(s) for s in p] == expected for p in paths)
                             if expected and paths else None)
            results.append({"question_id": case["question_id"], "gold_evidence_id": e["child_chunk_id"],
                            "document_id": case["source_document_id"], "page_start": e["page_start"],
                            "page_end": e["page_end"], "gold_text": e["text"],
                            "gold_section_path": e["section_path"], "groups": groups(case, e),
                            "parser": parser, **match, "blocks": selected,
                            "parsed_text": "\n".join(r["text"] for r in selected),
                            "block_count": len(selected), "matched_pages": sorted({r["page_no"] for r in selected}),
                            "section_contract_match": section_match,
                            "bbox_available": bool(selected) and all(r["bbox"] is not None for r in selected)})
    return results


def summarize(rows: list[dict]) -> dict:
    count = Counter(r["status"] for r in rows)
    numeric = [r for r in rows if r["numeric_exact"] is not None]
    return {"total": len(rows), "unique_gold_chunks": len({r["gold_evidence_id"] for r in rows}),
            "full": count["FULL"], "partial": count["PARTIAL"], "missing": count["MISSING"],
            "numeric_exact": sum(r["numeric_exact"] for r in numeric), "numeric_total": len(numeric),
            "numeric_expression_exact": sum(n["exact"] for r in rows for n in r["numeric_expressions"]),
            "numeric_expression_total": sum(len(r["numeric_expressions"]) for r in rows),
            "mean_ordered_coverage": mean(r["ordered_coverage"] for r in rows) if rows else None,
            "mean_blocks_per_gold": mean(r["block_count"] for r in rows) if rows else None,
            "matched_page_associations": sum(bool(r["matched_pages"]) for r in rows),
            "bbox_available_associations": sum(r["bbox_available"] for r in rows),
            "section_contract_match": sum(r["section_contract_match"] is True for r in rows),
            "section_contract_unavailable": sum(r["section_contract_match"] is None for r in rows)}


def compare_evidence(p: dict, m: dict) -> tuple[str, str]:
    levels = {"MISSING": 0, "PARTIAL": 1, "FULL": 2}
    pk = (levels[p["status"]], p["numeric_exact"] is True)
    mk = (levels[m["status"]], m["numeric_exact"] is True)
    classification = ("MINERU_BETTER" if mk > pk else "PYMUPDF_BETTER" if pk > mk else
                      "EQUIVALENT" if p["status"] == "FULL" else "BOTH_BAD")
    return classification, f"Strict preservation {p['status']} → {m['status']}; numeric exact {p['numeric_exact']} → {m['numeric_exact']} (not a semantic verdict)."


def compute(cases: list[dict], normalized: dict, missing: set[tuple[str, str]]) -> dict:
    arms = {name: evaluate(cases, normalized[name], name) for name in ("pymupdf", "mineru")}
    if len(missing) != 21 or any(key not in {(r["question_id"], r["gold_evidence_id"])
                                           for r in arms["pymupdf"]} for key in missing):
        raise ValueError("Diagnostic denominator mismatch")
    summary, diagnostic, breakdown = {}, {}, {}
    for name, rows in arms.items():
        summary[name] = summarize(rows)
        diagnostic[name] = summarize([r for r in rows if (r["question_id"], r["gold_evidence_id"]) in missing])
        breakdown[name] = {g: summarize([r for r in rows if g in r["groups"]])
                           for g in sorted({g for r in rows for g in r["groups"]})}
    diff = []
    for p, m in zip(arms["pymupdf"], arms["mineru"], strict=True):
        label, reason = compare_evidence(p, m)
        diff.append({"question_id": p["question_id"], "gold_evidence_id": p["gold_evidence_id"],
                     "diagnostic": (p["question_id"], p["gold_evidence_id"]) in missing,
                     "classification": label, "reason": reason, "pymupdf": p, "mineru": m})
    counts = Counter(d["classification"] for d in diff)
    return {"overall": summary, "historical21": diagnostic, "type_breakdown": breakdown,
            "classifications": {key: counts[key] for key in
                                ("MINERU_BETTER", "PYMUPDF_BETTER", "EQUIVALENT", "BOTH_BAD")},
            "evidence_diff": diff}


def identity() -> tuple[dict, list[dict], list[dict], set[tuple[str, str]]]:
    cases = [json.loads(s) for s in DATASET.read_text(encoding="utf-8").splitlines() if s.strip()]
    manifest = load(BASE / "retrieval_foundation_dev_v2_manifest.json")
    snapshot = load_generation_snapshot(GENERATION)
    validate_dataset(cases, snapshot)
    if (canonical_dataset_fingerprint(cases) != FINGERPRINT or len(cases) != 24
            or [c["question_id"] for c in cases] != manifest["question_ids"]
            or snapshot.generation_id != "dev-v2-20260902"):
        raise ValueError("BLOCKED_EXPERIMENT_IDENTITY: Development")
    sources = load(GENERATION / "source_manifest.json")
    for s in sources:
        verify_pdf(Path(s["path"]), s["sha256"], PAGE_COUNTS[s["document_id"]])
    b = load(BASE / "phase13b_multi_query_ablation_2026-09-03.json")
    missing = {(r["question_id"], eid) for r in b["phase13a_six_miss_recovery"]["questions"]
               for eid in r["a2_missing_gold"]}
    files = [DATASET, BASE / "retrieval_foundation_dev_v2_manifest.json",
             BASE / "retrieval_foundation_dev_v2_evidence_mapping.json",
             GENERATION / "source_manifest.json", GENERATION / "generation_metadata.json",
             GENERATION / "retrieval/child_chunks.jsonl", GENERATION / "retrieval/parent_chunks.jsonl"]
    info = {"generation": snapshot.generation_id, "dataset_fingerprint": FINGERPRINT,
            "question_ids": manifest["question_ids"], "split": "development", "gold_associations": 50,
            "input_hashes": {str(p.relative_to(ROOT)): sha(p) for p in files},
            "documents": [{k: s[k] for k in ("file_name", "document_id", "sha256", "size_bytes")}
                          | {"page_count": PAGE_COUNTS[s["document_id"]]} for s in sources],
            "diagnostic_source": "phase13b_multi_query_ablation_2026-09-03.json:a2_missing_gold",
            "diagnostic_source_sha256": sha(BASE / "phase13b_multi_query_ablation_2026-09-03.json"),
            "diagnostic_keys": [list(key) for key in sorted(missing)]}
    return info, cases, sources, missing


def main() -> int:
    args = argparse.ArgumentParser()
    args.add_argument("--replay", action="store_true")
    options = args.parse_args()
    info, cases, sources, missing = identity()
    if options.replay:
        artifact = load(ARTIFACT)
        if artifact["identity"] != info:
            raise ValueError("BLOCKED_EXPERIMENT_IDENTITY: replay")
        validate_bundle_fingerprints(artifact)
        normalized = artifact["normalized_records"]
        for rows in normalized.values():
            validate_records(rows, PAGE_COUNTS)
        result = compute(cases, normalized, missing)
        if result != artifact["results"]:
            raise ValueError("Replay metric mismatch")
        from scripts.phase14a_parser_report import REFERENCE, gate, structure_checks

        if sha(REFERENCE) != artifact["structure_reference_sha256"] or load(REFERENCE) != artifact["structure_reference"]:
            raise ValueError("Structural reference mismatch")
        structure = structure_checks(normalized, artifact["structure_reference"])
        if (structure != artifact["structure_results"] or gate(result, structure) != artifact["promotion_gate"]
                or parser_artifact_fingerprint(structure) != artifact["structure_fingerprint"]):
            raise ValueError("Structure/gate replay mismatch")
        print(json.dumps({"replay": "MATCH", "result_fingerprint": parser_artifact_fingerprint(result)}))
        return 0
    normalized = {"pymupdf": [], "mineru": []}
    manifests = {}
    for source in sources:
        normalized["pymupdf"].extend(pymupdf_blocks(Path(source["path"]), source["document_id"]))
        rows, m = mineru_blocks(source)
        normalized["mineru"].extend(rows)
        manifests[source["file_name"]] = m
    for rows in normalized.values():
        validate_records(rows, PAGE_COUNTS)
    result = compute(cases, normalized, missing)
    after, _, _, _ = identity()
    if after != info:
        raise ValueError("BLOCKED_EXPERIMENT_IDENTITY: inputs changed")
    artifact = {"phase": "14A", "schema_version": "parser-audit-v2", "identity": info,
                "capture_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
                "mineru_manifests": manifests, "normalized_records": normalized,
                "normalized_fingerprints": {k: parser_artifact_fingerprint(v) for k, v in normalized.items()},
                "results": result, "result_fingerprint": parser_artifact_fingerprint(result),
                "input_identity_unchanged": True,
                "status": "PARSER_AB_BLOCKED", "status_note": "Awaiting source-PDF structure review and final gate."}
    from scripts.phase14a_parser_report import finalize

    finalize(artifact)
    print(json.dumps({"overall": result["overall"], "historical21": result["historical21"],
                      "classifications": result["classifications"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
