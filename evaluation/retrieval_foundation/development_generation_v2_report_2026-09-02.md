# Frozen Development Generation V2

## Final status: `BLOCKED_ENVIRONMENT`

The real source corpus is available and was audited read-only. Both PDFs are
readable and correspond to historical document identities. The repository
`.venv` metadata reports Python 3.11.9, but starting its underlying interpreter
is still denied by Windows. The builder therefore could not run; no generation,
chunks, LightRAG workspace, or label-audit result was fabricated.

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
with `Access is denied` when starting
`C:\Users\mming\AppData\Local\Programs\Python\Python311\python.exe`.

## Python environment audit

- Required version: `>=3.11,<3.12`; historical environment: Python `3.11.15`.
- Runtime dependencies: `pyproject.toml` / `requirements.txt`.
- Development/test dependencies: `pyproject.toml` `[dev]` extra.
- Evaluation-only dependency: `ragas==0.3.9` via the `[evaluation]` extra.
- `requirements.lock.txt` is an old pip-freeze snapshot and contains packages
  outside the declared project dependencies; it was not used to add frameworks
  or upgrade versions.
- Planned restore: `py -3.11 -m venv .venv`, then
  `.venv\Scripts\python.exe -m pip install --upgrade pip` and
  `.venv\Scripts\python.exe -m pip install -e .[dev]`.
- Actual Python executable: `C:\Users\mming\AppData\Local\Programs\Python\Python311\python.exe` exists, but process start returns `Access is denied`; Python version and pip version remain unverified.
