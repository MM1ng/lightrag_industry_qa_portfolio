"""Safely backfill the lexical artifact for one schema-v1 frozen generation.

The default is a read-only dry run. ``--apply`` reads only the generation's
existing frozen ``retrieval/child_chunks.jsonl`` and upgrades its manifest to
schema v2. It never loads parsed ``current`` data, calls an LLM, reparses a
document, or overwrites an existing lexical artifact.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from industrial_rag.services.generation_artifacts import LegacyLexicalBackfillResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


def backfill_legacy_lexical_artifact(
    workspace: Path, *, apply: bool = False
) -> "LegacyLexicalBackfillResult":  # noqa: UP037
    """Expose the snapshot-only migration for tests and operational callers."""
    from industrial_rag.services.generation_artifacts import migrate_legacy_lexical_artifact

    return migrate_legacy_lexical_artifact(workspace, apply=apply)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="write only a verified schema-v2 upgrade"
    )
    args = parser.parse_args()
    result = backfill_legacy_lexical_artifact(args.workspace, apply=args.apply)
    print(f"{result.status}: {result.detail}")
    return 0 if result.status in {"would_migrate", "migrated", "already_current"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
