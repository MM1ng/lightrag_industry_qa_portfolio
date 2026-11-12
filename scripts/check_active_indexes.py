"""Read-only startup check of the persisted Active pointers against Qdrant."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from urllib.request import Request, urlopen


def check(db: Path, endpoint: str) -> None:
    connection = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT k.id, k.chunk_count, g.collections FROM knowledge_bases k "
            "JOIN vector_index_generations g ON g.id=k.active_vector_generation_id "
            "WHERE k.status != 'deleted' AND g.backend='qdrant'"
        ).fetchall()
    finally:
        connection.close()
    for kb_id, expected, collections in rows:
        name = json.loads(collections)["chunks"]
        headers = {}
        if os.environ.get("QDRANT_API_KEY"):
            headers["api-key"] = os.environ["QDRANT_API_KEY"]
        request = Request(f"{endpoint.rstrip('/')}/collections/{name}", headers=headers)
        with urlopen(request, timeout=10) as response:
            actual = json.load(response)["result"]["points_count"]
        if actual != expected:
            raise RuntimeError(f"Active index mismatch for {kb_id}: expected {expected}, actual {actual}")
        print(f"ACTIVE_INDEX_OK kb={kb_id} points={actual}")


if __name__ == "__main__":
    # Use the same database resolver as the API, including DATABASE_URL.
    from industrial_rag.db.session import _database_url
    from sqlalchemy.engine import make_url

    url = make_url(_database_url())
    if url.get_backend_name() != "sqlite":
        raise SystemExit("Active index startup check currently requires SQLite")
    check(Path(url.database), os.environ["QDRANT_URL"])
