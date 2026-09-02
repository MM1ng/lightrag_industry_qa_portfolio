"""Build an isolated frozen Development generation from existing P0 artifacts.

The command is deliberately fail-closed: parsed chunks alone are not claimed
to be a LightRAG generation.  A real LightRAG workspace marker must be passed
explicitly, otherwise the command exits with ``BLOCKED``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from industrial_rag.parser_models import ChildChunk
from industrial_rag.services.generation_artifacts import (
    GenerationArtifactError,
    freeze_generation_child_chunks,
    load_generation_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
P0_ROOT = ROOT / "evaluation" / "experiments" / "parser_backend" / "P0"
P0_FILES = (
    P0_ROOT / "2196-ANSI-Manual-Chinese.pdf",
    P0_ROOT / "t1739cn.pdf",
)


def _sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_generation(output: Path, generation_id: str, lightrag_workspace: Path | None) -> dict[str, object]:
    if (
        lightrag_workspace is None
        or not lightrag_workspace.is_dir()
        or not list(lightrag_workspace.rglob("industrial_rag_index.json"))
        or not any(
            path.is_file() and path.stat().st_size > 2
            for path in lightrag_workspace.rglob("kv_store_text_chunks.json")
        )
    ):
        raise RuntimeError("BLOCKED: a populated LightRAG workspace is missing; no generation was built")
    if not P0_FILES or any(not (path / "child_chunks.jsonl").is_file() for path in P0_FILES):
        raise RuntimeError("BLOCKED: real P0 child artifacts are incomplete")
    if output.exists():
        raise RuntimeError("refusing to overwrite an existing frozen Development generation")

    children: list[tuple[object, ChildChunk]] = []
    parents: list[tuple[object, dict[str, object]]] = []
    source_files: list[Path] = []
    for document_dir in P0_FILES:
        child_path = document_dir / "child_chunks.jsonl"
        parent_path = document_dir / "parent_chunks.jsonl"
        source_files.extend(path for path in (child_path, parent_path) if path.is_file())
        child_rows = [json.loads(line) for line in child_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        document_id = str(child_rows[0]["document_id"])
        document_name = str(child_rows[0].get("document_name") or document_dir.name)
        document = SimpleNamespace(
            id=document_id,
            version=1,
            file_hash=_sha256([child_path, parent_path]) if parent_path.is_file() else _sha256([child_path]),
            original_file_name=document_name,
        )
        children.extend((document, ChildChunk.from_dict(row)) for row in child_rows)
        if parent_path.is_file():
            parents.extend((document, json.loads(line)) for line in parent_path.read_text(encoding="utf-8").splitlines() if line.strip())

    output.mkdir(parents=True)
    frozen_lightrag_workspace = output / "lightrag_workspace"
    shutil.copytree(lightrag_workspace, frozen_lightrag_workspace)
    isolated_db = output / "generation.db"
    with sqlite3.connect(isolated_db) as connection:
        connection.execute(
            "create table generation (generation_id text primary key, child_manifest_hash text not null, corpus_fingerprint text not null)"
        )
    manifest = freeze_generation_child_chunks(
        output,
        generation_id=generation_id,
        document_children=children,
        document_parents=parents,
    )
    load_generation_manifest(
        output,
        expected_generation_id=generation_id,
        expected_child_manifest_hash=manifest.child_manifest_hash,
    )
    metadata = {
        "schema_version": "retrieval-foundation-development-generation-v1",
        "generation_id": generation_id,
        "workspace": str(output.resolve()),
        "lightrag_workspace": str(frozen_lightrag_workspace.resolve()),
        "source_artifacts": [
            {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in source_files
        ],
        "generation_manifest": manifest.to_dict(),
        "corpus_fingerprint": hashlib.sha256(
            (manifest.child_manifest_hash + manifest.parent_snapshot_hash).encode("ascii")
        ).hexdigest(),
        "isolated_database_path": str(isolated_db.resolve()),
        "mutable_current_used": False,
        "isolated_database": True,
    }
    with sqlite3.connect(isolated_db) as connection:
        connection.execute(
            "insert into generation values (?, ?, ?)",
            (generation_id, manifest.child_manifest_hash, metadata["corpus_fingerprint"]),
        )
    (output / "development_generation_manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--lightrag-workspace", type=Path)
    args = parser.parse_args()
    try:
        metadata = build_generation(args.output, args.generation_id, args.lightrag_workspace)
    except (RuntimeError, OSError, ValueError, GenerationArtifactError) as error:
        print(str(error))
        return 2
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
