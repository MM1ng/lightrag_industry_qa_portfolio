"""Fail-closed checks for files that are safe to publish in a public repository."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath


FORBIDDEN_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".db-shm", ".db-wal", ".pem", ".key")
HIGH_CONFIDENCE_SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|dashscope[a-z0-9_-]*[=:][^\s#]{16,})", re.I)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line for line in result.stdout.splitlines() if line]


def forbidden_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    parts = PurePosixPath(normalized).parts
    return any(
        part in {"venv", "node_modules", "__pycache__", "artifacts", "logs"}
        or part.startswith(".venv")
        for part in parts
    ) or normalized.startswith("data/db/") or normalized.startswith("data/knowledge_bases/") or normalized.endswith(FORBIDDEN_SUFFIXES)


def main() -> int:
    failures: list[str] = []
    for filename in tracked_files():
        if forbidden_path(filename):
            failures.append(f"forbidden public path: {filename}")
            continue
        path = PurePosixPath(filename)
        if path.suffix.lower() not in {".py", ".ps1", ".md", ".toml", ".yml", ".yaml", ".json", ".ts", ".vue", ".env", ".example", ".txt"}:
            continue
        try:
            content = open(filename, "r", encoding="utf-8", errors="replace").read()
        except OSError as error:
            failures.append(f"cannot read {filename}: {error}")
            continue
        if filename.endswith((".env.example", "frontend/.env.example")):
            continue
        for match in HIGH_CONFIDENCE_SECRET.finditer(content):
            failures.append(f"possible secret in {filename} at offset {match.start()}")
    if failures:
        print("PUBLIC_REPO_CHECK=FAIL")
        print("\n".join(failures))
        return 1
    print(f"PUBLIC_REPO_CHECK=PASS files={len(tracked_files())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
