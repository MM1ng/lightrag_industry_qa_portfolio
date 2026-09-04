# Current System Status

## Completed

- **Document lifecycle and frozen Generations:** document, knowledge-base,
  update-job, parent/child chunk, index, and generation identity services are
  present in the runtime path.
- **Hybrid retrieval foundation:** generation-scoped LightRAG and BM25
  candidates, RRF fusion, source/rank provenance, and a provider reranker
  runtime are implemented.
- **Grounded answers and citations:** evidence policy, context hydration,
  claim/evidence validation, citation projection, and insufficient-evidence
  handling are integrated into the API answer path.
- **Observability:** request/trace IDs and persisted retrieval diagnostics are
  available through protected runtime services.
- **Development evaluation:** frozen Development-set contracts, ranking
  metrics, trace contracts, downstream QA evaluation, and canonical artifact
  v2 validation/replay are implemented.

## Current issues and technical debt

- **Historical A2 replay limitation:** the historical canonical A2 v1 artifact
  lacks raw candidate pools, full fusion pools, rerank input fingerprints, and
  complete rerank scores. It remains immutable historical authority but cannot
  localize a later live-rerun divergence. Schema v2 fixes this only for future
  controlled captures.
- **External reranker nondeterminism:** provider timeout/fallback behavior and
  external score variability mean formal experiments must use strict runtime
  contracts plus replay artifacts; live reruns cannot replace frozen results.
- **Multi-evidence completeness:** Development diagnostics found that a
  question-level hit does not guarantee every required evidence item is
  retrieved. This remains a retrieval/evidence-chain investigation item, not a
  resolved performance claim.
- **Knowledge-base operations:** lifecycle primitives exist, but operational
  workflows, observability surfaces, and long-term maintenance need product
  hardening before a managed deployment claim.

## Not completed / not productized

- Incremental update and generation promotion have implementation support but
  require fuller operations runbooks and production acceptance.
- Validation/Holdout governance must remain separate from Development-only
  experiments; no package artifact is a production-quality assertion.
- Automated recovery for multi-evidence completeness, retriever tuning, and
  reranker selection are intentionally outside this review package.
- Production deployment, secrets management, capacity planning, and managed
  Qdrant operations are not represented by this repository snapshot.

## Review questions worth prioritizing

1. Is the Generation identity/freeze boundary sufficient for reproducible
   evaluation and safe lifecycle management?
2. Are evidence selection and citation grounding separated clearly enough from
   retrieval ranking?
3. Does the artifact v2 contract provide enough lineage for future rerank
   nondeterminism investigations?
4. Which lifecycle and operational gaps must close before treating this as a
   product rather than an evaluated local/staging system?
