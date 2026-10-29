"""Contract tests for the bounded upload reader.

The previous implementation read the entire request body before checking the
size, so one request could pull an unbounded amount of data into memory.
"""

from __future__ import annotations

import io

import pytest
from industrial_rag.errors import AppError
from industrial_rag.routers.documents import _read_upload_limited
from starlette.datastructures import UploadFile


def _upload(payload: bytes, *, size: int | None = None) -> UploadFile:
    file = UploadFile(filename="manual.pdf", file=io.BytesIO(payload))
    if size is not None:
        file.size = size
    return file


@pytest.mark.asyncio
async def test_reader_returns_payload_within_limit() -> None:
    payload = b"%PDF-1.4 payload"

    assert await _read_upload_limited(_upload(payload), max_bytes=1024) == payload


@pytest.mark.asyncio
async def test_reader_rejects_payload_larger_than_limit() -> None:
    payload = b"x" * 64

    with pytest.raises(AppError) as excinfo:
        await _read_upload_limited(_upload(payload), max_bytes=16)

    assert excinfo.value.code == "file_too_large"
    assert excinfo.value.status_code == 413


@pytest.mark.asyncio
async def test_reader_rejects_declared_size_before_reading() -> None:
    upload = _upload(b"x" * 64, size=4096)

    with pytest.raises(AppError) as excinfo:
        await _read_upload_limited(upload, max_bytes=1024)

    assert excinfo.value.code == "file_too_large"


@pytest.mark.asyncio
async def test_reader_accepts_empty_payload() -> None:
    assert await _read_upload_limited(_upload(b""), max_bytes=16) == b""
