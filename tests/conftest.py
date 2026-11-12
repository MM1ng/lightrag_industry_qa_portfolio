"""Shared test defaults.

Most tests exercise local development behaviour, where the uncredentialed admin
path is explicitly enabled. Tests that assert fail-closed behaviour remove or
override this variable themselves.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _insecure_local_dev_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRA_DEV_INSECURE", "1")
    monkeypatch.setenv("IRA_DEPLOYMENT_ENVIRONMENT", "local_dev")
