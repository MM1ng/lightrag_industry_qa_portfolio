# Phase 14A — PyMuPDF vs MinerU Parser-only A/B

## Decision

**KEEP_PYMUPDF** — do not start MinerU RAG A/B.

No promotion: strict source-faithfulness does not improve; independently confirmed safety-step deletion and cross-page lineage loss prevent promotion even after accounting for source-parser bias.

MinerU recovers useful table/reading-order structure, but drops three safety steps in SUMMIT p23 and merges DESMI p51 table text into p50. These are verified source-PDF defects, not inferred retrieval failures. No retrieval or downstream inference was run.

## Identity and implementation

- Branch: `dev/retrieval-foundation-qa-downstream`; input HEAD: `5903677bc66fe96d96db8c53ac7a0644fd3f0f89`.
- Generation: `dev-v2-20260902`; Development: 24 questions, 8 multi-evidence questions.
- Dataset fingerprint: `deac5832de37a95f933267aba10e40215582f1136cd6a60dfabf2d9784385060`.
- Grain: 50 question–gold associations / 45 unique chunks. Diagnostic: 21 associations / 20 unique chunks; identical repeated IDs are not deduplicated out of question-specific denominators.
- Validation / Final / Holdout not accessed. PDF, generation snapshots, dataset and mapping hashes unchanged before/after. Complete hashes and frozen diagnostic keys are in JSON.

| PDF | document_id | Pages | SHA256 |
|---|---|---:|---|
| 2196-ANSI-Manual-Chinese.pdf | doc-4ffb6df91a9a | 55 | `e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700` |
| t1739cn.pdf | doc-6a9ea3ff1f42 | 62 | `77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae` |

PyMuPDF: unchanged `document_parser.parse_pdf` → `pymupdf_chunks_to_blocks`, exactly the parse_service upstream path. `get_text(text, sort=True)`, page-local 1800-character/180-overlap slices and paragraph conversion remain unchanged. No Parent/Child construction is invoked. Section is the first nonnumeric short page line; the adapter exposes no bbox/table cells. Runtime PyMuPDF is 1.28.2; the existing upstream adapter's hardcoded 1.28.0 metadata is not substituted for the actual version.

MinerU: online v4, requested backend `pipeline`; both returned `_backend=pipeline`, `_version_name=3.4.4`. First ZIP reused, second uploaded once with the same bytes/settings. Downloaded ZIPs CRC-checked. This evaluator only reads cached ZIPs; no network entry exists. Raw content-list array order is retained, captions precede table bodies, cell text and HTML spans are retained, and headers/footers are not deleted. Section paths are a stack of supplied text_level headings, not a verified semantic hierarchy. Signed URLs/API credentials are not published.

## Metric semantics and important limitations

FULL/PARTIAL/MISSING below are **strict ordered source-text preservation proxies**, not answer correctness or semantic evidence recall. NFC + whitespace normalization only; ordered character alignment has autojunk disabled. FULL requires 100% aligned characters and all numeric expressions exact; PARTIAL requires ≥50%; otherwise MISSING. All frozen gold/page ranges (including cross-page evidence) remain unchanged. Each JSON row contains raw gold/parsed text, blocks, offsets, missing fragments, sections and boxes.

Numeric exactness retains comparator, sign, unit, range and multiplicity, using raw expression boundaries before whitespace removal (100 and 1 cannot become 1001). Matching must occur at the aligned position with four characters of left context. The denominator is numeric-bearing gold, not all gold; numbers include ordinals, model/part identifiers and page furniture, not only physical parameters. Raw LaTeX is not silently repaired. Expression totals are diagnostic, not independent trials.

Gold was generated from historical PyMuPDF and includes footer-first/column-interleaved text, private-use bullets and table fragments. Current PyMuPDF is therefore not 50/50 FULL either. MinerU's correct reordered table may score lower against that sequence. The three MISSING labels do **not** mean three entire evidences disappeared; e.g. S011 p18 procedure remains in raw blocks but moved before footer text. Section-contract 50/50 vs 0/50 is same-source coarse-path agreement, NOT true hierarchy accuracy. Classification counts below are strict proxy labels only; source-PDF structure findings are reported separately.

## Overall and fixed historical diagnostic subset

| Set / Parser | FULL | PARTIAL | MISSING | Numeric exact | Exact expressions | Mean aligned coverage | Blocks/gold |
|---|---:|---:|---:|---:|---:|---:|---:|
| overall/pymupdf | 28/50 | 22/50 | 0/50 | 11/32 | 502/684 | 0.956 | 5.26 |
| overall/mineru | 22/50 | 25/50 | 3/50 | 7/32 | 449/684 | 0.893 | 5.24 |
| historical21/pymupdf | 21/21 | 0/21 | 0/21 | 7/7 | 16/16 | 1.000 | 1.00 |
| historical21/mineru | 17/21 | 4/21 | 0/21 | 6/7 | 15/16 | 0.987 | 1.00 |

The historical21 set is diagnostic only. Its parser preservation result does not validate the invalidated Phase13E runtime or reassert any historical candidate-recall root cause.

## Evidence-type breakdown (overlapping labels)

| Group | N | PyMuPDF FULL/PARTIAL/MISSING | MinerU FULL/PARTIAL/MISSING | Numeric exact P/M |
|---|---:|---|---|---|
| adjacent | 2 | 0/2/0 | 0/2/0 | 0/2 ; 0/2 |
| multi | 34 | 24/10/0 | 19/12/3 | 8/18 ; 6/18 |
| numeric | 32 | 10/22/0 | 7/22/3 | 11/32 ; 7/32 |
| pattern:adjacent_chunk_evidence | 2 | 0/2/0 | 0/2/0 | 0/2 ; 0/2 |
| pattern:multi_evidence | 32 | 24/8/0 | 19/10/3 | 8/16 ; 6/16 |
| pattern:single_evidence | 13 | 4/9/0 | 3/10/0 | 3/11 ; 1/11 |
| pattern:table_structured | 3 | 0/3/0 | 0/3/0 | 0/3 ; 0/3 |
| single | 16 | 4/12/0 | 3/13/0 | 3/14 ; 1/14 |
| source:doc-4ffb6df91a9a | 40 | 27/13/0 | 22/15/3 | 9/22 ; 7/22 |
| source:doc-6a9ea3ff1f42 | 10 | 1/9/0 | 0/10/0 | 2/10 ; 0/10 |
| table | 3 | 0/3/0 | 0/3/0 | 0/3 ; 0/3 |
| type:component_structure | 2 | 0/2/0 | 0/2/0 | 0/2 ; 0/2 |
| type:condition_prerequisite | 2 | 0/2/0 | 0/2/0 | 0/2 ; 0/2 |
| type:fault_handling | 11 | 9/2/0 | 5/4/2 | 1/3 ; 0/3 |
| type:installation_debugging | 6 | 6/0/0 | 6/0/0 | 2/2 ; 2/2 |
| type:maintenance | 5 | 1/4/0 | 1/4/0 | 2/5 ; 1/5 |
| type:parameter | 9 | 5/4/0 | 3/6/0 | 3/7 ; 1/7 |
| type:procedure | 11 | 6/5/0 | 6/4/1 | 3/8 ; 3/8 |
| type:safety_warning_limit | 4 | 1/3/0 | 1/3/0 | 0/3 ; 0/3 |

## Source-PDF table, reading order, section and lineage audit

Manual review covered 13 source pages; machine checks replay the saved anchors/cells. No sample score is generalized to the whole corpus. The table denominator is the three explicitly table-labelled gold pages (D-V2-002/003/007); supplementary historical tables were visually reviewed but not silently added to that denominator. A table is intact only if source headers, cell associations and spans match, not merely because HTML exists. Two table-labelled questions are actually answered by nearby prose; the frozen labels are left intact.

| Check | PyMuPDF | MinerU |
|---|---:|---:|
| Structured table intact | 0/3 | 1/3 |
| Ordered source-anchor checks | 5/5 | 3/5 |
| Correct leaf section (sample) | 0/3 | 2/3 |
| Gold associations with bbox available | 0/50 | 50/50 |
| Full hierarchy accuracy | unavailable | unavailable |
| Global bbox semantic correctness | unavailable | unavailable |

PyMuPDF's 0 structured tables does not mean values are unreadable. MinerU table1 has correct merged cells; table5 loses its two-column header span; the parts list merges quantity/description headers and sets a five-column title over six columns. Answer-bearing 101→叶轮 and 122→轴 remain readable.

Ordinary MinerU bbox coordinates are raw 0–1000 and visually correspond to source regions; layout.json supplies point-space equivalents. This proves availability, not universal correctness. The DESMI merged table contains rows physically located on other pages, while its bbox still belongs to the first page. Do not use that box for every row's citation.

| Order check | PyMuPDF | MinerU |
|---|---|---|
| alignment_steps | True | True |
| warning_and_startup | True | True |
| repair_safety_steps | True | False |
| heading_conditions_warning | True | True |
| cross_page_steps_page51 | True | False |

Both current parsers pass the source order anchors on p17/p28; these examples do not establish a MinerU reading-order improvement over current PyMuPDF. MinerU separates more explicit semantic blocks, while historical gold may still encode older extraction order. The S015 p23 safety sequence has only steps1/2 in MinerU; steps3/4/5 are absent from both content_list and that page's layout JSON. DESMI p51 steps3–6 occur in a p50 block; p51 has an empty table with `lines_deleted=true`. Numeric/step labels still present elsewhere are not accepted as proof of correct page lineage.

Leaf sections improve on grease/commissioning examples, but warnings become headings and overwrite `6.2` in MinerU's stack. Its ancestry can retain unrelated earlier headings (e.g. 保证); neither parser has a verified deep hierarchy. More blocks are not automatically worse: MinerU separates steps/warnings, while table text can collapse many pages into one block. JSON includes exact block counts per gold; whole-run averages conceal both patterns.

## Per-evidence comparison

Counts: MINERU_BETTER=0; PYMUPDF_BETTER=10; EQUIVALENT=22; BOTH_BAD=18.

| Question | Gold child | Pages | PyMuPDF | MinerU | Classification | Diagnostic |
|---|---|---|---|---|---|---|
| S014 | `cchunk-pymupdf-v1-17f4771c4c817a77-000` | 20–20 | PARTIAL | PARTIAL | BOTH_BAD | False |
| S014 | `cchunk-pymupdf-v1-93807a18f7b7345f-000` | 54–54 | FULL | FULL | EQUIVALENT | True |
| S015 | `cchunk-pymupdf-v1-34cc49bd2766d02e-000` | 23–23 | FULL | FULL | EQUIVALENT | True |
| S015 | `cchunk-pymupdf-v1-5ea5c41790ab805a-000` | 23–23 | PARTIAL | MISSING | PYMUPDF_BETTER | False |
| S015 | `cchunk-pymupdf-v1-5989850607a8046c-000` | 23–23 | FULL | FULL | EQUIVALENT | True |
| S015 | `cchunk-pymupdf-v1-78a156ed97cebd53-000` | 23–23 | FULL | PARTIAL | PYMUPDF_BETTER | True |
| S015 | `cchunk-pymupdf-v1-f997c995a333b4ae-000` | 24–24 | FULL | FULL | EQUIVALENT | False |
| S006 | `cchunk-pymupdf-v1-5388c52812f37351-000` | 14–14 | FULL | FULL | EQUIVALENT | True |
| S006 | `cchunk-pymupdf-v1-e2d71181df615e5b-000` | 14–14 | FULL | PARTIAL | PYMUPDF_BETTER | False |
| S006 | `cchunk-pymupdf-v1-a03be0b31badfb6b-000` | 14–14 | FULL | PARTIAL | PYMUPDF_BETTER | True |
| S006 | `cchunk-pymupdf-v1-fcc2e6681bc7509c-000` | 14–14 | PARTIAL | PARTIAL | BOTH_BAD | False |
| S006 | `cchunk-pymupdf-v1-d8638f275d20c6d6-000` | 14–14 | FULL | FULL | EQUIVALENT | True |
| S003 | `cchunk-pymupdf-v1-6590f00e21e280d0-000` | 10–10 | FULL | FULL | EQUIVALENT | True |
| S003 | `cchunk-pymupdf-v1-c97eb4631d5d2c9c-000` | 10–10 | FULL | FULL | EQUIVALENT | True |
| S003 | `cchunk-pymupdf-v1-8ac7c8e5aafefa93-000` | 10–10 | FULL | FULL | EQUIVALENT | False |
| S003 | `cchunk-pymupdf-v1-87557f88f4709fcc-000` | 10–10 | FULL | FULL | EQUIVALENT | True |
| S003 | `cchunk-pymupdf-v1-663e640852497df6-000` | 10–10 | FULL | FULL | EQUIVALENT | True |
| S016 | `cchunk-pymupdf-v1-58f11d1e9bdc292c-000` | 24–24 | PARTIAL | MISSING | PYMUPDF_BETTER | False |
| S016 | `cchunk-pymupdf-v1-99121c418e138c64-000` | 24–24 | FULL | PARTIAL | PYMUPDF_BETTER | True |
| S016 | `cchunk-pymupdf-v1-317e33cc54ca5b18-000` | 24–24 | FULL | PARTIAL | PYMUPDF_BETTER | True |
| S016 | `cchunk-pymupdf-v1-f997c995a333b4ae-000` | 24–24 | FULL | FULL | EQUIVALENT | True |
| S016 | `cchunk-pymupdf-v1-16686d3e3ddcc21b-000` | 24–24 | FULL | FULL | EQUIVALENT | True |
| S011 | `cchunk-pymupdf-v1-acca8dbfb1b95f8f-000` | 17–17 | FULL | FULL | EQUIVALENT | True |
| S011 | `cchunk-pymupdf-v1-a94c7167b5a3f6ed-000` | 17–17 | PARTIAL | PARTIAL | BOTH_BAD | False |
| S011 | `cchunk-pymupdf-v1-bf2be6315d2f187b-000` | 17–17 | FULL | FULL | EQUIVALENT | True |
| S011 | `cchunk-pymupdf-v1-ac2c48838803419d-000` | 17–17 | FULL | FULL | EQUIVALENT | True |
| S011 | `cchunk-pymupdf-v1-cc1f6fd20cdb46f6-000` | 17–17 | FULL | FULL | EQUIVALENT | True |
| S011 | `cchunk-pymupdf-v1-91e5666cf6078fb9-000` | 18–18 | FULL | FULL | EQUIVALENT | True |
| S011 | `cchunk-pymupdf-v1-2db1a56a7e45ceb2-000` | 18–18 | PARTIAL | MISSING | PYMUPDF_BETTER | False |
| S011 | `cchunk-pymupdf-v1-93807a18f7b7345f-000` | 54–54 | FULL | FULL | EQUIVALENT | True |
| D-V2-001 | `cchunk-pymupdf-v1-17f4771c4c817a77-000` | 20–20 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-001 | `cchunk-pymupdf-v1-c1a49660ca4fa082-001` | 19–19 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-002 | `cchunk-pymupdf-v1-3b58bc1e45428865-000` | 11–11 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-003 | `cchunk-pymupdf-v1-e538966686c111ea-000` | 16–16 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-004 | `cchunk-pymupdf-v1-8ac7c8e5aafefa93-000` | 10–10 | FULL | FULL | EQUIVALENT | False |
| D-V2-005 | `cchunk-pymupdf-v1-285811f368cf8a64-000` | 49–49 | FULL | FULL | EQUIVALENT | False |
| D-V2-006 | `cchunk-pymupdf-v1-6c84270736d64bea-000` | 15–15 | FULL | FULL | EQUIVALENT | False |
| D-V2-007 | `cchunk-pymupdf-v1-0993bee22239dfa9-000` | 35–35 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-008 | `cchunk-pymupdf-v1-3b58bc1e45428865-000` | 11–11 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-009 | `cchunk-pymupdf-v1-7e3e80ba8c62809f-000` | 19–19 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-010 | `cchunk-pymupdf-v1-4e14a3b265877fb1-000` | 28–28 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-011 | `cchunk-pymupdf-v1-3c48ca477586c617-000` | 29–29 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-012 | `cchunk-pymupdf-v1-5178c456afbf1e5a-000` | 30–30 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-012 | `cchunk-pymupdf-v1-fc16e77b6450e35c-000` | 24–24 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-013 | `cchunk-pymupdf-v1-7ed93679bb25416b-000` | 18–18 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-014 | `cchunk-pymupdf-v1-6ebe88f96ee7a0d4-000` | 47–47 | PARTIAL | PARTIAL | PYMUPDF_BETTER | False |
| D-V2-015 | `cchunk-pymupdf-v1-7733ed3c6f455ef6-000` | 45–45 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-016 | `cchunk-pymupdf-v1-e1d1a306d81ddb56-000` | 35–35 | PARTIAL | PARTIAL | BOTH_BAD | False |
| D-V2-017 | `cchunk-pymupdf-v1-cb4c3258ac73b8a0-000` | 38–38 | FULL | PARTIAL | PYMUPDF_BETTER | False |
| D-V2-018 | `cchunk-pymupdf-v1-b0fdd25338e01728-000` | 50–51 | PARTIAL | PARTIAL | BOTH_BAD | False |

The four diagnostic strict regressions are S015's bullet-marked short row, S006's lubrication paragraph/table-caption boundary, and two S016 rows with removed bullets/changed wraps. These are not four established semantic losses. Real improvement: p19/p23/p24 explicit structured value associations and p28 leaf-section metadata; real regression: p23 missing safety instructions and p50/51 page lineage. The p35 header spans are a MinerU structural defect, not a regression against an existing PyMuPDF span API. The safety deletion is in the all-gold set, not one of the 21 diagnostic missing associations.

## Promotion gate

| Requirement | Pass |
|---|---|
| overall_preservation_not_worse | False |
| numeric_fidelity_not_worse | False |
| structure_gain | True |
| diagnostic_material_gain | False |
| no_major_regression | False |
| page_citation_lineage_preserved | False |

Result: `KEEP_PYMUPDF`. Structure gains alone are insufficient. Retain production PyMuPDF. No MinerU index/RAG A/B or parser replacement has been started.

## Reproduction and verification

```powershell
.venv\Scripts\python.exe scripts/phase14a_parser_ab.py --replay
.venv\Scripts\python.exe -m pytest tests/test_phase14a_parser_ab.py
.venv\Scripts\python.exe -m ruff check .
```

JSON embeds normalized records and all per-evidence diffs; `--replay` validates immutable identities and independently recomputes both scores and structural checks. Raw ZIPs/checkpoints remain local (large payloads and signed URLs); SHA256s are published. Re-capture is not needed for replay. No dependencies were installed.

Result fingerprint: `7139ac3f67f5c1d502a47c247866fd449cf8d782a593f791f21f9cc59284fc2d`. Structural reference SHA256: `c64166bebfa5b6b0990b9bcc835729edfcb4983f8d2b0f701f3c3ff7c6337b9b`.

Validation: 36 passed; project Ruff=passed; explicit script Ruff=passed; offline replay=MATCH.
Code review identified and regression-tested two evaluation defects: unscored block tampering bypassing replay hashes, and fractional inch units being ignored. Both were repaired before the final offline computation; compound velocity units are also protected. No parser or production algorithm changed.
Source code and report belong to the same phase commit; input HEAD above is deliberately the pre-change commit, avoiding a self-referential commit hash.
