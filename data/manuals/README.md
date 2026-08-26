# Local manuals

This directory is the local input area for industrial manuals. Original business and
vendor documents are not distributed with the repository because their copyright and
redistribution terms may differ.

## Supported input

- PDF documents
- Text-based PDFs are parsed with PyMuPDF by default.
- MinerU can be enabled as an optional parser for more complex layouts; see
  `.env.example` for the related settings.

## Recommended workflow

1. Start the FastAPI service and Vue workbench as described in the root `README.md`.
2. Sign in to the administrator workspace.
3. Create a knowledge base under **Knowledge Bases**.
4. Upload a PDF under **Documents**. Parsing, Parent-Child chunking and indexing are
   executed by the document lifecycle task.
5. Wait for the document and generation status to become ready, then query it from the
   user workbench.

The corresponding API endpoints are:

```text
POST /v1/knowledge-bases
POST /v1/knowledge-bases/{kb_id}/documents
GET  /v1/tasks/{task_id}
```

Management endpoints require the administrator Bearer token configured locally in
`.env`; never commit that value.

## Legacy two-manual benchmark

`scripts/parse_manuals.py` and several frozen evaluation artifacts refer to two
historical pump manuals by filename. Those source PDFs are intentionally not included.
To reproduce that exact historical benchmark, obtain the same documents through a
lawful source and place them in this directory using the expected filenames. For normal
product use, upload your own PDFs through the workbench instead.

Generated parse, index and runtime files under `data/processed/`, `runtime/` and
LightRAG/Qdrant storage directories are local artifacts and are not source-controlled.
