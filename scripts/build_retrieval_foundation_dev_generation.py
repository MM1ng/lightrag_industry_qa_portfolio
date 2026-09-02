"""Build Frozen Development Generation V2 from the real manual PDFs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from industrial_rag.citation_formatter import Citation, encode_chunk_header
from industrial_rag.config import Settings
from industrial_rag.document_parser import parse_pdf, scan_pdf_files
from industrial_rag.lightrag_service import LightRAGService
from industrial_rag.services.generation_artifacts import (
    GenerationArtifactError,
    freeze_generation_child_chunks,
    load_generation_manifest,
)
from industrial_rag.structured_chunker import (
    ChunkerConfig,
    build_parent_child_chunks,
    pymupdf_chunks_to_blocks,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT.parent / "lightrag_industry_qa_portfolio" / "data" / "manuals"
DEFAULT_ENV_FILE = ROOT.parent / "lightrag_industry_qa_portfolio" / ".env"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_source(source_dir: Path) -> tuple[list[tuple[object, object]], list[tuple[object, object]], list[dict[str, object]]]:
    children: list[tuple[object, object]] = []
    parents: list[tuple[object, object]] = []
    source_manifest: list[dict[str, object]] = []
    config = ChunkerConfig(strategy="pymupdf-v1", version="0.1.0")
    for pdf in scan_pdf_files(source_dir):
        parsed = parse_pdf(pdf)
        document_parents, document_children = build_parent_child_chunks(
            pymupdf_chunks_to_blocks(parsed, pdf.name), pdf.name, config=config
        )
        if not document_parents or not document_children:
            raise RuntimeError(f"BLOCKED_GENERATION: no Parent-Child chunks for {pdf.name}")
        document = SimpleNamespace(
            id=document_children[0].document_id,
            version=1,
            file_hash=_sha256(pdf),
            original_file_name=pdf.name,
        )
        children.extend((document, child) for child in document_children)
        parents.extend((document, parent) for parent in document_parents)
        source_manifest.append(
            {
                "file_name": pdf.name,
                "path": str(pdf.resolve()),
                "size_bytes": pdf.stat().st_size,
                "sha256": _sha256(pdf),
                "readable": True,
                "document_id": document.id,
                "child_count": len(document_children),
                "parent_count": len(document_parents),
            }
        )
    if len(source_manifest) != 2:
        raise RuntimeError(f"BLOCKED_GENERATION: expected 2 PDFs, found {len(source_manifest)}")
    return children, parents, source_manifest


async def _index_lightrag(output: Path, children: list[tuple[object, object]], source_manifest: list[dict[str, object]], env_file: Path | None) -> None:
    if env_file is not None:
        load_dotenv(env_file, override=False)
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise RuntimeError("BLOCKED_ENVIRONMENT: DASHSCOPE_API_KEY is unavailable")
    settings = replace(Settings.from_env(), working_dir=output / "lightrag_workspace", vector_workspace=None)
    service = LightRAGService(settings)
    await service.initialize()
    boundary = "\n\n<<<INDUSTRIAL_RAG_CHUNK_BOUNDARY>>>\n\n"
    by_document: dict[str, list[tuple[object, object]]] = {}
    for document, child in children:
        by_document.setdefault(str(document.id), []).append((document, child))
    inputs: list[str] = []
    ids: list[str] = []
    file_paths: list[str] = []
    for source in source_manifest:
        rows = by_document[str(source["document_id"])]
        inputs.append(
            boundary.join(
                f"{encode_chunk_header(Citation(document.original_file_name, child.page_start or 1, child.chunk_id))}\n"
                f"[来源：{document.original_file_name}，第{child.page_start or 1}页，章节：{child.section_title or '未识别章节'}]\n"
                f"[parent_chunk_id：{child.parent_chunk_id}]\n{child.embedding_content or child.content}"
                for document, child in rows
            )
        )
        ids.append("dev-v2-" + hashlib.sha256("\n".join(child.chunk_id for _, child in rows).encode()).hexdigest()[:20])
        file_paths.append(str(source["file_name"]))
    try:
        await service._backend.ainsert(
            input=inputs,
            ids=ids,
            file_paths=file_paths,
            split_by_character=boundary,
            split_by_character_only=True,
        )
    finally:
        await service.close()
    workspace = output / "lightrag_workspace"
    marker = workspace / "industrial_rag_index.json"
    text_store = workspace / "kv_store_text_chunks.json"
    if not marker.is_file() or not text_store.is_file() or text_store.stat().st_size <= 2:
        raise RuntimeError("BLOCKED_GENERATION: LightRAG workspace was not populated")


def build_generation(output: Path, generation_id: str, source_dir: Path, env_file: Path | None) -> dict[str, object]:
    if output.exists():
        raise RuntimeError("refusing to overwrite an existing Frozen Development Generation V2")
    children, parents, source_manifest = _parse_source(source_dir)
    output.mkdir(parents=True)
    (output / "source_manifest.json").write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = freeze_generation_child_chunks(
        output, generation_id=generation_id, document_children=children, document_parents=parents
    )
    load_generation_manifest(
        output, expected_generation_id=generation_id, expected_child_manifest_hash=manifest.child_manifest_hash
    )
    asyncio.run(_index_lightrag(output, children, source_manifest, env_file))
    corpus_fingerprint = hashlib.sha256(
        ("".join(str(item["sha256"]) for item in source_manifest) + manifest.child_manifest_hash + manifest.parent_snapshot_hash).encode("ascii")
    ).hexdigest()
    retrieval_config = {"sparse": True, "rrf_k": 60, "reranker": "runtime-configured"}
    retrieval_config_fingerprint = hashlib.sha256(
        json.dumps(retrieval_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    isolated_db = output / "generation.db"
    with sqlite3.connect(isolated_db) as connection:
        connection.execute("create table generation (generation_id text primary key, child_manifest_hash text not null, corpus_fingerprint text not null)")
        connection.execute("insert into generation values (?, ?, ?)", (generation_id, manifest.child_manifest_hash, corpus_fingerprint))
    metadata = {
        "schema_version": "retrieval-foundation-development-generation-v2",
        "generation_id": generation_id,
        "built_at": datetime.now(UTC).isoformat(),
        "build_commit": os.environ.get("GIT_COMMIT", "unknown"),
        "source_manifest": source_manifest,
        "generation_manifest": manifest.to_dict(),
        "corpus_fingerprint": corpus_fingerprint,
        "child_manifest_hash": manifest.child_manifest_hash,
        "parent_snapshot_hash": manifest.parent_snapshot_hash,
        "child_count": manifest.count,
        "parent_count": len(parents),
        "retrieval_config": retrieval_config,
        "retrieval_config_fingerprint": retrieval_config_fingerprint,
        "isolated_database": str(isolated_db.resolve()),
        "mutable_current_used": False,
    }
    (output / "generation_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()
    try:
        metadata = build_generation(args.output, args.generation_id, args.source_dir, args.env_file)
    except (RuntimeError, OSError, ValueError, GenerationArtifactError) as error:
        print(str(error))
        return 2
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
