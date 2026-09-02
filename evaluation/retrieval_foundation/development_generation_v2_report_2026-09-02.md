# Frozen Development Generation V2

## Final status: `BLOCKED_ENVIRONMENT`

The real source corpus is available and was audited read-only. Both PDFs are
readable and correspond to historical document identities, but the repository
`.venv` is bound to a Python 3.11 installation removed by the system reinstall.
The builder therefore could not run; no generation, chunks, LightRAG workspace,
or label-audit result was fabricated.

| Source PDF | Size | SHA256 | Historical document ID |
|---|---:|---|---|
| `2196-ANSI-Manual-Chinese.pdf` | 1,561,387 | `e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700` | `doc-4ffb6df91a9a` |
| `t1739cn.pdf` | 4,532,306 | `77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae` | `doc-6a9ea3ff1f42` |

The updated builder uses the production `parse_pdf` →
`build_parent_child_chunks` → `freeze_generation_child_chunks` path and then
indexes the exact frozen chunks through `LightRAGService` in an isolated
workspace/database. The label audit runs separately and compares historical
document/page/text evidence to V2 chunks without using retrieval output.

Development IDs are fixed to `S014, S015, S006, S003, S016, S011`. No
Validation/Holdout data was accessed. A0/A1/A2 execution is not allowed until
the builder succeeds and label audit returns `READY_FOR_AB`.

Verification: `ruff check .` passed. Focused pytest and the builder were blocked
by the missing interpreter at
`C:\Users\mming\AppData\Local\Programs\Python\Python311\python.exe`.
