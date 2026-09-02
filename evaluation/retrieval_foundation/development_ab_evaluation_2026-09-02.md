# Retrieval Foundation Development A/B Evaluation

## Decision

**BLOCKED**

The evaluation did not run and no metrics were fabricated. The checkout has
real P0 PyMuPDF Child/Parent artifacts and an exact existing evidence mapping
for the six Development cases, but it has no original Development PDFs and no
populated LightRAG workspace. The only `industrial_rag_index.json` is an empty
legacy marker with no `kv_store_text_chunks.json`. The builder is fail-closed
before writing any generation artifact.

## Development guard and labels

- Dataset: `evaluation/retrieval_foundation/dev_cases.jsonl`
- Provenance: `development_dataset_manifest.json`
- Question IDs: `S014, S015, S006, S003, S016, S011`
- The manifest binds every row to `split=development`; non-Development rows are
  rejected.
- Existing `evidence_mapping_p0.json` maps all six labels exactly to P0 child
  identities. No labels were edited.
- Validation/Holdout data was not accessed.

## Implemented entry points

- `scripts/build_retrieval_foundation_dev_generation.py` reads only the real P0
  child/parent artifacts, copies an explicitly supplied LightRAG workspace into
  an isolated generation directory, freezes the Child/Parent snapshot, writes
  the manifest and lexical index, and creates an isolated SQLite generation DB.
  It exits `BLOCKED` when the LightRAG workspace is absent and never reads a
  mutable `current/` alias.
- `scripts/run_retrieval_foundation_dev_ab.py` loads one validated frozen
  generation and runs A0 (LightRAG), A1 (LightRAG+BM25+RRF), and A2 (A1 plus
  fail-safe reranker), with shared generation/chunk identity checks, latency,
  fallback, and per-question output.
- `src/industrial_rag/services/retrieval_ab_evaluation.py` contains the
  Development split guard, label mapping guard, immutable generation loader,
  variant contract, and report builder.

## Verification

`ruff` passed for all new modules and contract tests. Focused pytest could not
start because the checked-in `.venv` points at the missing interpreter
`C:\Users\mming\AppData\Local\Programs\Python\Python311\python.exe`.

## Required unblock input

Provide or restore the real Development LightRAG workspace (and its source
provenance) so the builder can copy it into an isolated generation directory.
Then run the builder followed by the single A/B runner command; only then can
Recall/MRR and latency be reported or considered for downstream QA.
