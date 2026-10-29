import importlib.util
import io
import json
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("index_check", Path(__file__).parents[1] / "scripts/check_active_indexes.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("points,passes", [(453, True), (0, False), (452, False)])
def test_startup_checks_actual_points(tmp_path, monkeypatch, points, passes):
    db = tmp_path / "db.sqlite"
    with sqlite3.connect(db) as connection:
        connection.executescript("""
        CREATE TABLE knowledge_bases (id, chunk_count, active_vector_generation_id, status);
        CREATE TABLE vector_index_generations (id, collections, backend);
        INSERT INTO knowledge_bases VALUES ('kb',453,'gen','ready');
        INSERT INTO vector_index_generations VALUES ('gen','{"chunks":"test_chunks"}','qdrant');
        """)
    monkeypatch.setattr(module, "urlopen", lambda *a, **k: io.BytesIO(json.dumps({"result": {"points_count": points}}).encode()))
    if passes:
        module.check(db, "http://localhost:16333")
    else:
        with pytest.raises(RuntimeError, match="Active index mismatch"):
            module.check(db, "http://localhost:16333")
