# Retrieval Foundation Development A/B Evaluation

## Decision

**BLOCKED_ENVIRONMENT**

The evaluation did not run and no metrics were fabricated. The two real source
PDFs are available in the sibling project corpus and match historical document
names/IDs. The checked-in `.venv` is unusable because its base interpreter was
removed during system reinstall. The builder now uses the production parser,
structured Parent-Child chunker, frozen generation artifacts, and LightRAG
indexing, but cannot execute until Python is restored.

## Development guard and labels

- Dataset: `evaluation/retrieval_foundation/dev_cases.jsonl`
- Provenance: `development_dataset_manifest.json`
- Question IDs: `S014, S015, S006, S003, S016, S011`
- The manifest binds every row to `split=development`; non-Development rows are
  rejected.
- Existing `evidence_mapping_p0.json` maps all six labels exactly to P0 child
  identities. No labels were edited.
- Validation/Holdout data was not accessed.

## Source corpus audit

| File | Size | SHA256 | Readable | Historical document ID |
|---|---:|---|---|---|
| `2196-ANSI-Manual-Chinese.pdf` | 1,561,387 | `e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700` | yes | `doc-4ffb6df91a9a` |
| `t1739cn.pdf` | 4,532,306 | `77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae` | yes | `doc-6a9ea3ff1f42` |

## Implemented entry points

- `scripts/build_retrieval_foundation_dev_generation.py` reads only the two real
  PDFs, calls `parse_pdf` and `build_parent_child_chunks`, freezes the exact
  Parent/Child snapshot, builds the lexical index, creates an isolated SQLite
  DB, and indexes the same frozen chunks through `LightRAGService` into a
  private workspace. It never reads a mutable `current/` alias.
- `scripts/audit_retrieval_foundation_dev_labels.py` performs the required
  source-document/page/text label audit after V2 exists and blocks ambiguous or
  missing mappings before A/B execution.
- `scripts/run_retrieval_foundation_dev_ab.py` loads one validated frozen
  generation and runs A0 (LightRAG), A1 (LightRAG+BM25+RRF), and A2 (A1 plus
  fail-safe reranker), with shared generation/chunk identity checks, latency,
  fallback, and per-question output.
- `src/industrial_rag/services/retrieval_ab_evaluation.py` contains the
  Development split guard, label mapping guard, immutable generation loader,
  variant contract, and report builder.

## Verification

`ruff check .` passed. Focused pytest and the V2 builder could not start because
the checked-in `.venv` points at the missing interpreter
`C:\Users\mming\AppData\Local\Programs\Python\Python311\python.exe`.

## Required unblock input

Recreate the project Python 3.11 environment from the existing dependency
files, then run the builder against
`D:\基于Lightrag的工业手册问答系统\lightrag_industry_qa_portfolio\data\manuals`.
Run the label audit next. Only if it returns `READY_FOR_AB` may the existing
A0/A1/A2 runner execute.
