"""Phase 9 staging service stopper (local_staging rehearsal)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
RUNTIME = Path(
    os.environ.get("IRA_STAGING_ROOT", str(REPO.parent / f"{REPO.name}_staging"))
) / "runtime"


def main() -> int:
    for name in ("ui", "api"):
        pid_file = RUNTIME / f"{name}.pid"
        if pid_file.is_file():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(pid, 9)
                print(f"stopped {name} pid={pid}")
            except Exception as error:
                print(f"{name} stop error: {error}")
            pid_file.unlink(missing_ok=True)
        else:
            print(f"{name} no pid file")
    print("stop done (Qdrant untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
