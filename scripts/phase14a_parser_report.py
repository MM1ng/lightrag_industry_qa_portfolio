"""Render Phase 14A report from saved scores + source-reviewed structural anchors."""
# ruff: noqa: RUF001
from __future__ import annotations

from scripts.phase14a_parser_ab import (
    ARTIFACT,
    ROOT,
    compact,
    load,
    parser_artifact_fingerprint,
    sha,
    table_cells,
    write,
)

REFERENCE = ROOT / "docs/phase-14a-parser-structure-review.json"
REPORT = ROOT / "docs/phase-14a-pymupdf-vs-mineru-parser-ab.md"


def page_records(rows, item):
    return [r for r in rows if r["document_id"] == item["document_id"]
            and r["page_no"] == item["page"]]


def ordered_check(text, anchors):
    text = compact(text)
    position, offsets = 0, []
    for anchor in anchors:
        found = text.find(compact(anchor), position)
        offsets.append(found if found >= 0 else None)
        if found >= 0:
            position = found + len(compact(anchor))
    return {"passed": all(x is not None for x in offsets), "anchor_offsets": offsets}


def normalized_cells(rows):
    return [[c | {"text": compact(c["text"])} for c in row] for row in rows]


def structure_checks(normalized, reference):
    result = {}
    for name, records in normalized.items():
        tables = []
        for table in reference["tables"]:
            rows = page_records(records, table)
            # Baseline exports no table schema: values can remain readable, but
            # a machine-readable row/column/span contract is not present.
            candidate = next((r for r in rows if r["block_id"] == table["mineru_block_id"]), None)
            cells = table_cells(candidate["table_content"]) if candidate and candidate["table_content"] else []
            expected = table.get("expected_rows", table.get("expected_header_rows"))
            compare = cells if "expected_rows" in table else cells[:len(expected)]
            passed = normalized_cells(compare) == normalized_cells(expected)
            tables.append({"question_id": table["question_id"], "page": table["page"],
                           "table_intact": passed, "structured_table_available": bool(cells),
                           "parsed_cells": cells, "reason": table["reason"] if name == "mineru" else
                           "Current production adapter supplies flat text, no explicit row/column/span semantics; not a claim that table values vanished."})
        order = [{"id": c["id"], "page": c["page"], **ordered_check(
            "\n".join(r["text"] for r in page_records(records, c)), c["anchors"])}
            for c in reference["ordered_checks"]]
        sections = []
        for c in reference["section_checks"]:
            matches = [r for r in page_records(records, c) if compact(c["anchor"]) in compact(r["text"])]
            paths = [r["section_path"] for r in matches]
            sections.append({"id": c["id"], "paths": paths,
                             "passed": any(p and compact(p[-1]) == compact(c["expected_leaf"]) for p in paths)})
        lineage = []
        for c in reference["lineage_checks"]:
            matches = [r for r in records if r["document_id"] == c["document_id"]
                       and compact(c["anchor"]) in compact(r["text"])]
            pages = sorted({r["page_no"] for r in matches})
            lineage.append({"id": c["id"], "expected_page": c["expected_page"], "observed_pages": pages,
                            "passed": c["expected_page"] in pages, "block_ids": [r["block_id"] for r in matches]})
        boxes = [r["bbox"] for r in records if r["bbox"] is not None]
        result[name] = {"table_intact": sum(t["table_intact"] for t in tables), "table_total": len(tables),
                        "tables": tables, "order_pass": sum(c["passed"] for c in order),
                        "order_total": len(order), "order_checks": order,
                        "section_leaf_pass": sum(c["passed"] for c in sections),
                        "section_leaf_total": len(sections), "section_checks": sections,
                        "full_hierarchy_accuracy": None, "full_hierarchy_note": "No independently annotated deep hierarchy; raw MinerU text_level stack often treats warnings as sections.",
                        "lineage_checks": lineage, "bbox_available_blocks": len(boxes),
                        "block_total": len(records), "bbox_geometry_valid": sum(
                            len(b) == 4 and 0 <= b[0] < b[2] <= 1000 and 0 <= b[1] < b[3] <= 1000 for b in boxes),
                        "bbox_semantic_accuracy": None,
                        "bbox_note": "Availability/range is not semantic correctness. Visual examples support ordinary boxes, but merged cross-page table text invalidates a single-page bbox for all cells."}
    return result


def gate(results, structure):
    p, m = (results["overall"][k] for k in ("pymupdf", "mineru"))
    d = results["historical21"]
    safety = next(c for c in structure["mineru"]["order_checks"] if c["id"] == "repair_safety_steps")
    checks = {
        "overall_preservation_not_worse": m["full"] >= p["full"] and m["missing"] <= p["missing"],
        "numeric_fidelity_not_worse": m["numeric_exact"] >= p["numeric_exact"],
        "structure_gain": structure["mineru"]["table_intact"] > structure["pymupdf"]["table_intact"],
        "diagnostic_material_gain": d["mineru"]["full"] > d["pymupdf"]["full"],
        "no_major_regression": safety["passed"] and results["classifications"]["PYMUPDF_BETTER"] == 0,
        "page_citation_lineage_preserved": all(c["passed"] for c in structure["mineru"]["lineage_checks"]),
    }
    return {"checks": checks, "allow_rag_ab": all(checks.values()),
            "status": "MINERU_RAG_AB_RECOMMENDED" if all(checks.values()) else "KEEP_PYMUPDF",
            "reason": "No promotion: strict source-faithfulness does not improve; independently confirmed safety-step deletion and cross-page lineage loss prevent promotion even after accounting for source-parser bias."}


def render(artifact):
    result, structure = artifact["results"], artifact["structure_results"]
    info = artifact["identity"]
    lines = ["# Phase 14A — PyMuPDF vs MinerU Parser-only A/B", "",
             "## Decision", "", f"**{artifact['status']}** — do not start MinerU RAG A/B.", "",
             artifact["promotion_gate"]["reason"], "",
             "MinerU recovers useful table/reading-order structure, but drops three safety steps in SUMMIT p23 and merges DESMI p51 table text into p50. These are verified source-PDF defects, not inferred retrieval failures. No retrieval or downstream inference was run.", "",
             "## Identity and implementation", "",
             f"- Branch: `{artifact['branch']}`; input HEAD: `{artifact['capture_commit']}`.",
             f"- Generation: `{info['generation']}`; Development: 24 questions, 8 multi-evidence questions.",
             f"- Dataset fingerprint: `{info['dataset_fingerprint']}`.",
             "- Grain: 50 question–gold associations / 45 unique chunks. Diagnostic: 21 associations / 20 unique chunks; identical repeated IDs are not deduplicated out of question-specific denominators.",
             "- Validation / Final / Holdout not accessed. PDF, generation snapshots, dataset and mapping hashes unchanged before/after. Complete hashes and frozen diagnostic keys are in JSON.",
             "", "| PDF | document_id | Pages | SHA256 |", "|---|---|---:|---|"]
    for d in info["documents"]:
        lines.append(f"| {d['file_name']} | {d['document_id']} | {d['page_count']} | `{d['sha256']}` |")
    lines += ["", "PyMuPDF: unchanged `document_parser.parse_pdf` → `pymupdf_chunks_to_blocks`, exactly the parse_service upstream path. `get_text(text, sort=True)`, page-local 1800-character/180-overlap slices and paragraph conversion remain unchanged. No Parent/Child construction is invoked. Section is the first nonnumeric short page line; the adapter exposes no bbox/table cells. Runtime PyMuPDF is " + artifact["normalized_records"]["pymupdf"][0]["parser_version"] + "; the existing upstream adapter's hardcoded 1.28.0 metadata is not substituted for the actual version.", "",
              "MinerU: online v4, requested backend `pipeline`; both returned `_backend=pipeline`, `_version_name=3.4.4`. First ZIP reused, second uploaded once with the same bytes/settings. Downloaded ZIPs CRC-checked. This evaluator only reads cached ZIPs; no network entry exists. Raw content-list array order is retained, captions precede table bodies, cell text and HTML spans are retained, and headers/footers are not deleted. Section paths are a stack of supplied text_level headings, not a verified semantic hierarchy. Signed URLs/API credentials are not published.", "",
              "## Metric semantics and important limitations", "",
              "FULL/PARTIAL/MISSING below are **strict ordered source-text preservation proxies**, not answer correctness or semantic evidence recall. NFC + whitespace normalization only; ordered character alignment has autojunk disabled. FULL requires 100% aligned characters and all numeric expressions exact; PARTIAL requires ≥50%; otherwise MISSING. All frozen gold/page ranges (including cross-page evidence) remain unchanged. Each JSON row contains raw gold/parsed text, blocks, offsets, missing fragments, sections and boxes.", "",
              "Numeric exactness retains comparator, sign, unit, range and multiplicity, using raw expression boundaries before whitespace removal (100 and 1 cannot become 1001). Matching must occur at the aligned position with four characters of left context. The denominator is numeric-bearing gold, not all gold; numbers include ordinals, model/part identifiers and page furniture, not only physical parameters. Raw LaTeX is not silently repaired. Expression totals are diagnostic, not independent trials.", "",
              "Gold was generated from historical PyMuPDF and includes footer-first/column-interleaved text, private-use bullets and table fragments. Current PyMuPDF is therefore not 50/50 FULL either. MinerU's correct reordered table may score lower against that sequence. The three MISSING labels do **not** mean three entire evidences disappeared; e.g. S011 p18 procedure remains in raw blocks but moved before footer text. Section-contract 50/50 vs 0/50 is same-source coarse-path agreement, NOT true hierarchy accuracy. Classification counts below are strict proxy labels only; source-PDF structure findings are reported separately.", "",
              "## Overall and fixed historical diagnostic subset", "",
              "| Set / Parser | FULL | PARTIAL | MISSING | Numeric exact | Exact expressions | Mean aligned coverage | Blocks/gold |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for group in ("overall", "historical21"):
        for name, s in result[group].items():
            lines.append(f"| {group}/{name} | {s['full']}/{s['total']} | {s['partial']}/{s['total']} | {s['missing']}/{s['total']} | {s['numeric_exact']}/{s['numeric_total']} | {s['numeric_expression_exact']}/{s['numeric_expression_total']} | {s['mean_ordered_coverage']:.3f} | {s['mean_blocks_per_gold']:.2f} |")
    lines += ["", "The historical21 set is diagnostic only. Its parser preservation result does not validate the invalidated Phase13E runtime or reassert any historical candidate-recall root cause.", "",
              "## Evidence-type breakdown (overlapping labels)", "",
              "| Group | N | PyMuPDF FULL/PARTIAL/MISSING | MinerU FULL/PARTIAL/MISSING | Numeric exact P/M |",
              "|---|---:|---|---|---|"]
    for group, p in result["type_breakdown"]["pymupdf"].items():
        m = result["type_breakdown"]["mineru"][group]
        lines.append(f"| {group} | {p['total']} | {p['full']}/{p['partial']}/{p['missing']} | {m['full']}/{m['partial']}/{m['missing']} | {p['numeric_exact']}/{p['numeric_total']} ; {m['numeric_exact']}/{m['numeric_total']} |")
    lines += ["", "## Source-PDF table, reading order, section and lineage audit", "",
              "Manual review covered 13 source pages; machine checks replay the saved anchors/cells. No sample score is generalized to the whole corpus. The table denominator is the three explicitly table-labelled gold pages (D-V2-002/003/007); supplementary historical tables were visually reviewed but not silently added to that denominator. A table is intact only if source headers, cell associations and spans match, not merely because HTML exists. Two table-labelled questions are actually answered by nearby prose; the frozen labels are left intact.", "",
              "| Check | PyMuPDF | MinerU |", "|---|---:|---:|"]
    for label, key, total in [("Structured table intact", "table_intact", "table_total"),
                              ("Ordered source-anchor checks", "order_pass", "order_total"),
                              ("Correct leaf section (sample)", "section_leaf_pass", "section_leaf_total")]:
        lines.append(f"| {label} | {structure['pymupdf'][key]}/{structure['pymupdf'][total]} | {structure['mineru'][key]}/{structure['mineru'][total]} |")
    lines += [f"| Gold associations with bbox available | {result['overall']['pymupdf']['bbox_available_associations']}/50 | {result['overall']['mineru']['bbox_available_associations']}/50 |",
              "| Full hierarchy accuracy | unavailable | unavailable |",
              "| Global bbox semantic correctness | unavailable | unavailable |", "",
              "PyMuPDF's 0 structured tables does not mean values are unreadable. MinerU table1 has correct merged cells; table5 loses its two-column header span; the parts list merges quantity/description headers and sets a five-column title over six columns. Answer-bearing 101→叶轮 and 122→轴 remain readable.", "",
              "Ordinary MinerU bbox coordinates are raw 0–1000 and visually correspond to source regions; layout.json supplies point-space equivalents. This proves availability, not universal correctness. The DESMI merged table contains rows physically located on other pages, while its bbox still belongs to the first page. Do not use that box for every row's citation.", "",
              "| Order check | PyMuPDF | MinerU |", "|---|---|---|"]
    for p, m in zip(structure["pymupdf"]["order_checks"], structure["mineru"]["order_checks"], strict=True):
        lines.append(f"| {p['id']} | {p['passed']} | {m['passed']} |")
    lines += ["", "Both current parsers pass the source order anchors on p17/p28; these examples do not establish a MinerU reading-order improvement over current PyMuPDF. MinerU separates more explicit semantic blocks, while historical gold may still encode older extraction order. The S015 p23 safety sequence has only steps1/2 in MinerU; steps3/4/5 are absent from both content_list and that page's layout JSON. DESMI p51 steps3–6 occur in a p50 block; p51 has an empty table with `lines_deleted=true`. Numeric/step labels still present elsewhere are not accepted as proof of correct page lineage.", "",
              "Leaf sections improve on grease/commissioning examples, but warnings become headings and overwrite `6.2` in MinerU's stack. Its ancestry can retain unrelated earlier headings (e.g. 保证); neither parser has a verified deep hierarchy. More blocks are not automatically worse: MinerU separates steps/warnings, while table text can collapse many pages into one block. JSON includes exact block counts per gold; whole-run averages conceal both patterns.", "",
              "## Per-evidence comparison", "",
              "Counts: " + "; ".join(f"{k}={v}" for k, v in result["classifications"].items()) + ".", "",
              "| Question | Gold child | Pages | PyMuPDF | MinerU | Classification | Diagnostic |",
              "|---|---|---|---|---|---|---|"]
    for d in result["evidence_diff"]:
        p, m = d["pymupdf"], d["mineru"]
        lines.append(f"| {d['question_id']} | `{d['gold_evidence_id']}` | {p['page_start']}–{p['page_end']} | {p['status']} | {m['status']} | {d['classification']} | {d['diagnostic']} |")
    lines += ["", "The four diagnostic strict regressions are S015's bullet-marked short row, S006's lubrication paragraph/table-caption boundary, and two S016 rows with removed bullets/changed wraps. These are not four established semantic losses. Real improvement: p19/p23/p24 explicit structured value associations and p28 leaf-section metadata; real regression: p23 missing safety instructions and p50/51 page lineage. The p35 header spans are a MinerU structural defect, not a regression against an existing PyMuPDF span API. The safety deletion is in the all-gold set, not one of the 21 diagnostic missing associations.", "",
              "## Promotion gate", "", "| Requirement | Pass |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in artifact["promotion_gate"]["checks"].items()]
    lines += ["", "Result: `" + artifact["status"] + "`. Structure gains alone are insufficient. Retain production PyMuPDF. No MinerU index/RAG A/B or parser replacement has been started.", "",
              "## Reproduction and verification", "",
              "```powershell", ".venv\\Scripts\\python.exe scripts/phase14a_parser_ab.py --replay",
              ".venv\\Scripts\\python.exe -m pytest tests/test_phase14a_parser_ab.py",
              ".venv\\Scripts\\python.exe -m ruff check .", "```", "",
              "JSON embeds normalized records and all per-evidence diffs; `--replay` validates immutable identities and independently recomputes both scores and structural checks. Raw ZIPs/checkpoints remain local (large payloads and signed URLs); SHA256s are published. Re-capture is not needed for replay. No dependencies were installed.", "",
              f"Result fingerprint: `{artifact['result_fingerprint']}`. Structural reference SHA256: `{artifact['structure_reference_sha256']}`.", "",
              "Validation: " + str(artifact.get("verification", {}).get("focused_tests", "not recorded"))
              + "; project Ruff=" + str(artifact.get("verification", {}).get("ruff_check_dot", "not recorded"))
              + "; explicit script Ruff=" + str(artifact.get("verification", {}).get("explicit_script_ruff", "not recorded"))
              + "; offline replay=" + str(artifact.get("verification", {}).get("offline_replay", "not recorded")) + ".",
              "Code review identified and regression-tested two evaluation defects: unscored block tampering bypassing replay hashes, and fractional inch units being ignored. Both were repaired before the final offline computation; compound velocity units are also protected. No parser or production algorithm changed.",
              "Source code and report belong to the same phase commit; input HEAD above is deliberately the pre-change commit, avoiding a self-referential commit hash."]
    return "\n".join(lines) + "\n"


def finalize(artifact):
    reference = load(REFERENCE)
    structure = structure_checks(artifact["normalized_records"], reference)
    artifact["structure_reference"] = reference
    artifact["structure_reference_sha256"] = sha(REFERENCE)
    artifact["structure_results"] = structure
    artifact["structure_fingerprint"] = parser_artifact_fingerprint(structure)
    artifact["promotion_gate"] = gate(artifact["results"], structure)
    artifact["status"] = artifact["promotion_gate"]["status"]
    artifact.pop("status_note", None)
    write(ARTIFACT, artifact)
    REPORT.write_text(render(artifact), encoding="utf-8")
    return artifact
