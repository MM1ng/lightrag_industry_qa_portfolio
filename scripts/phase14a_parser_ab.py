"""Phase 14A: parser-only PyMuPDF vs MinerU comparison.

This module deliberately does not feed either parser into retrieval or QA. It
normalizes page blocks and evaluates preservation against the frozen V2 gold.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pymupdf
from dotenv import load_dotenv

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from industrial_rag.config import Settings
from industrial_rag.mineru_client import MinerUClient, MinerUClientConfig, MinerUValidationError
from industrial_rag.services.expanded_development_dataset import (
    canonical_dataset_fingerprint,
    load_generation_snapshot,
    validate_dataset,
)
from evaluation.experiments.parser_backend.mineru_adapter import (
    MinerUBlockPolicy,
    normalize_text,
)

ROOT = Path(__file__).resolve().parents[1]
SIBLING = ROOT.parent / "lightrag_industry_qa_portfolio"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GENERATION = ROOT / "evaluation" / "retrieval_foundation" / "dev_generation_v2"
DATASET = ROOT / "evaluation" / "retrieval_foundation" / "retrieval_foundation_dev_v2.jsonl"
OUT = ROOT / "evaluation" / "experiments" / "phase14a_parser_ab"
PDFS = {
    "2196-ANSI-Manual-Chinese.pdf": SIBLING / "data" / "manuals" / "2196-ANSI-Manual-Chinese.pdf",
    "t1739cn.pdf": SIBLING / "data" / "manuals" / "t1739cn.pdf",
}
DOC_IDS = {"2196-ANSI-Manual-Chinese.pdf": "doc-4ffb6df91a9a", "t1739cn.pdf": "doc-6a9ea3ff1f42"}
FROZEN_HASHES = {
    "2196-ANSI-Manual-Chinese.pdf": "e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700",
    "t1739cn.pdf": "77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae",
}


def canonical_parser_record(**values: Any) -> dict[str, Any]:
    keys = ("document_id", "page_no", "block_id", "block_type", "text", "bbox",
            "reading_order", "section_path", "table_content", "parser_name", "parser_version")
    return {key: values.get(key) for key in keys}


def parser_artifact_fingerprint(records: list[dict[str, Any]]) -> str:
    payload = "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in records)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def exact_numeric_tokens(text: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"(?:[<>≤≥±]=?|\d+(?:\.\d+)?|°[CFcf]|[%]|[A-Za-z]+)", text)}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text)).casefold()


def classify_evidence_match(gold: str, parsed: str, *, page_overlap: bool) -> dict[str, Any]:
    g, p = _compact(gold), _compact(parsed)
    nums = exact_numeric_tokens(gold)
    numeric_exact = nums <= exact_numeric_tokens(parsed)
    if page_overlap and g and g in p and numeric_exact:
        status = "FULL"
    elif page_overlap and g and (len(set(g) & set(p)) / max(1, len(set(g)))) >= 0.45:
        status = "PARTIAL"
    else:
        status = "MISSING"
    return {"status": status, "numeric_exact": numeric_exact, "gold_chars": len(g), "parsed_chars": len(p)}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pymupdf_blocks(pdf_path: Path, document_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with pymupdf.open(pdf_path) as doc:
        for page_no, page in enumerate(doc, start=1):
            raw = page.get_text("dict", sort=True)
            order = 0
            for block in raw.get("blocks", []):
                if block.get("type") != 0:
                    continue
                text = "".join(span.get("text", "") for line in block.get("lines", []) for span in line.get("spans", []))
                text = normalize_text(text)
                if not text:
                    continue
                order += 1
                result.append(canonical_parser_record(
                    document_id=document_id, page_no=page_no, block_id=f"pymupdf-{page_no:03d}-{order:04d}",
                    block_type="text", text=text, bbox=list(block.get("bbox", [])), reading_order=order,
                    section_path=[], table_content=None, parser_name="pymupdf", parser_version=pymupdf.VersionBind,
                ))
    return result


async def _mineru_one(pdf_name: str, settings: Settings) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pdf_path = PDFS[pdf_name]
    target = OUT / "mineru_raw" / pdf_name
    target.mkdir(parents=True, exist_ok=True)
    config = MinerUClientConfig(api_base_url=settings.mineru_api_base_url, api_key=settings.mineru_api_key,
                                api_version="v4", request_timeout=settings.mineru_request_timeout,
                                task_timeout=settings.mineru_task_timeout, poll_interval=settings.mineru_poll_interval,
                                max_retries=settings.mineru_max_retries)
    pdf_bytes = pdf_path.read_bytes()
    async with MinerUClient(config) as client:
        response = await client._request_with_retry("POST", "/api/v4/file-urls/batch",
            json={"files": [{"name": pdf_name, "data_id": hashlib.sha256(pdf_bytes).hexdigest()}], "model_version": config.model_version})
        data = response.get("data", {})
        batch_id, urls = data.get("batch_id"), data.get("file_urls")
        if not isinstance(batch_id, str) or not isinstance(urls, list) or len(urls) != 1:
            raise MinerUValidationError("invalid MinerU upload response")
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.request_timeout)) as put:
            uploaded = await put.put(urls[0], content=pdf_bytes, headers={"Content-Length": str(len(pdf_bytes))})
        uploaded.raise_for_status()
        deadline = time.monotonic() + config.task_timeout
        archive_url = None
        while time.monotonic() < deadline:
            status = await client._request_with_retry("GET", f"/api/v4/extract-results/batch/{batch_id}")
            rows = (status.get("data", {}) or {}).get("extract_result") or []
            if rows and rows[0].get("state") == "done":
                archive_url = rows[0].get("full_zip_url")
                break
            if rows and rows[0].get("state") == "failed":
                raise MinerUValidationError(str(rows[0].get("err_msg")))
            await asyncio.sleep(config.poll_interval)
        if not archive_url:
            raise MinerUValidationError("MinerU task timed out")
        zip_path = await client.download_result(archive_url, output_dir=target)
    import zipfile
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if name.endswith("_content_list.json")]
        if len(names) != 1:
            raise MinerUValidationError("content_list.json missing")
        content = archive.read(names[0])
    items = json.loads(content.decode("utf-8"))
    (target / "content_list.json").write_bytes(content)
    pages = MinerUBlockPolicy.from_items(pdf_name, items).clean_pages()
    records: list[dict[str, Any]] = []
    ordinal = 0
    for page in pages:
        for item in page["blocks"]:
            ordinal += 1
            raw = item["item"]
            text = item.get("embedding_text", item.get("raw_text", ""))
            records.append(canonical_parser_record(
                document_id=DOC_IDS[pdf_name], page_no=page["page_number"],
                block_id=f"mineru-{page['page_number']:03d}-{ordinal:04d}",
                block_type=item["block_type"].value, text=normalize_text(text),
                bbox=raw.get("bbox"), reading_order=ordinal, section_path=[],
                table_content=item.get("raw_html") if item["block_type"].value == "table" else None,
                parser_name="mineru", parser_version="online-v4",
            ))
    manifest = {"parser": "mineru", "task_id": batch_id, "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "content_list_sha256": hashlib.sha256(content).hexdigest(), "block_count": len(records)}
    return records, manifest


def evaluate(cases: list[dict[str, Any]], records: list[dict[str, Any]], parser: str) -> dict[str, Any]:
    by_page: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        by_page.setdefault(int(record["page_no"]), []).append(record)
    evidence_results: list[dict[str, Any]] = []
    for case in cases:
        for evidence in case["evidence"]:
            page = int(evidence["page_start"])
            page_text = "\n".join(item["text"] for item in by_page.get(page, []))
            match = classify_evidence_match(evidence["text"], page_text, page_overlap=bool(page_text))
            evidence_results.append({"question_id": case["question_id"], "child_chunk_id": evidence["child_chunk_id"],
                                     "page": page, "parser": parser, **match})
    full = [item for item in evidence_results if item["status"] == "FULL"]
    return {"parser": parser, "block_count": len(records), "artifact_fingerprint": parser_artifact_fingerprint(records),
            "gold_evidence_count": len(evidence_results), "full": len(full),
            "partial": sum(item["status"] == "PARTIAL" for item in evidence_results),
            "missing": sum(item["status"] == "MISSING" for item in evidence_results),
            "numeric_exact_rate": sum(item["numeric_exact"] for item in evidence_results) / max(1, len(evidence_results)),
            "evidence": evidence_results}


def main() -> int:
    # Keep process/user environment values authoritative; the sibling .env may
    # contain historical blank MinerU placeholders.
    load_dotenv(SIBLING / ".env", override=False)
    settings = Settings.from_env()
    if not settings.mineru_api_key or not settings.mineru_enabled:
        raise SystemExit("MINERU_API_KEY and MINERU_ENABLED=true are required")
    cases = _read_jsonl(DATASET)
    snapshot = load_generation_snapshot(GENERATION)
    errors = validate_dataset(cases, snapshot)
    if errors or canonical_dataset_fingerprint(cases) != "deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060":
        raise SystemExit("frozen Development dataset contract mismatch")
    for name, path in PDFS.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != FROZEN_HASHES[name]:
            raise SystemExit(f"frozen PDF hash mismatch: {name}")
    OUT.mkdir(parents=True, exist_ok=True)
    all_results: dict[str, Any] = {"phase": "14A", "generation": snapshot.generation_id,
                                    "dataset_fingerprint": canonical_dataset_fingerprint(cases), "question_count": len(cases),
                                    "pdf_hashes": FROZEN_HASHES, "status": "COMPLETED"}
    pymu: list[dict[str, Any]] = []
    mineru: list[dict[str, Any]] = []
    try:
        for name in PDFS:
            doc_id = DOC_IDS[name]
            p_records = pymupdf_blocks(PDFS[name], doc_id)
            m_records, manifest = asyncio.run(_mineru_one(name, settings))
            (OUT / f"{name}.pymupdf.json").write_text(json.dumps(p_records, ensure_ascii=False, indent=2), encoding="utf-8")
            (OUT / f"{name}.mineru.json").write_text(json.dumps(m_records, ensure_ascii=False, indent=2), encoding="utf-8")
            (OUT / f"{name}.mineru.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            relevant = [case for case in cases if case["source_document_id"] == doc_id]
            pymu.append(evaluate(relevant, p_records, "pymupdf"))
            mineru.append(evaluate(relevant, m_records, "mineru"))
    except Exception as exc:
        all_results["status"] = "PARSER_AB_BLOCKED"
        all_results["blocker"] = {"type": type(exc).__name__, "message": str(exc)}
        (OUT / "phase14a-parser-ab.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "docs" / "phase-14a-pymupdf-vs-mineru-parser-ab.md").write_text(
            "# Phase 14A — PyMuPDF vs MinerU Parser A/B\n\n"
            f"- Status: `PARSER_AB_BLOCKED`\n- Generation: `{snapshot.generation_id}`\n"
            f"- Dataset fingerprint: `{all_results['dataset_fingerprint']}`\n"
            f"- Blocker: `{type(exc).__name__}` during MinerU online result download.\n"
            "- No parser comparison metrics were published; no retrieval, index, gold, or QA data was modified.\n",
            encoding="utf-8",
        )
        print(json.dumps({"status": "PARSER_AB_BLOCKED", "error": type(exc).__name__}, ensure_ascii=False))
        return 2
    all_results["pymupdf"] = pymu
    all_results["mineru"] = mineru
    (OUT / "phase14a-parser-ab.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    report = ["# Phase 14A — PyMuPDF vs MinerU Parser A/B", "", f"- Generation: `{snapshot.generation_id}`",
              f"- Dataset fingerprint: `{all_results['dataset_fingerprint']}`", "- Scope: frozen Development V2 only", "",
              "## Preservation summary", "", "| Parser | Gold evidence | FULL | PARTIAL | MISSING | Numeric exactness |", "|---|---:|---:|---:|---:|---:|"]
    for parser, values in (("PyMuPDF", pymu), ("MinerU", mineru)):
        total = sum(x["gold_evidence_count"] for x in values)
        report.append(f"| {parser} | {total} | {sum(x['full'] for x in values)} | {sum(x['partial'] for x in values)} | {sum(x['missing'] for x in values)} | {sum(x['numeric_exact_rate']*x['gold_evidence_count'] for x in values)/max(1,total):.3f} |")
    report += ["", "## Method and limitations", "", "Both outputs were normalized without feeding them into retrieval, chunking, or QA. FULL requires page-local normalized evidence containment and exact numeric-token preservation; PARTIAL is deterministic character-overlap evidence only. The current PyMuPDF production parser does not expose table semantics, so table integrity and section association are reported as unavailable unless present in normalized parser metadata. Historical 21 missing evidence are not used as primary labels in this parser comparison.", "", "## Per-document artifacts", "", "The structured JSON and per-parser block files in `evaluation/experiments/phase14a_parser_ab/` contain hashes, normalized records, and per-evidence results."]
    (ROOT / "docs" / "phase-14a-pymupdf-vs-mineru-parser-ab.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", "output": str(OUT / "phase14a-parser-ab.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
