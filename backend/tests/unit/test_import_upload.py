from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest
from starlette.requests import Request

from routeops.application.import_contract import DATASETS
from routeops.application.import_templates import csv_template, xlsx_template
from routeops.application.import_upload import UploadError, _file_identity, receive_package
from routeops.application.import_validation import ImportLimits
from routeops.application.ports.object_storage import ObjectWriter, StoredObject
from routeops.infrastructure.storage import LocalObjectStorage


def _request(files: list[tuple[str, bytes]], *, truncate: int = 0) -> Request:
    outgoing = httpx.Request(
        "POST",
        "http://localhost/upload",
        files=[("files", (name, content, "application/octet-stream")) for name, content in files],
    )
    body = outgoing.read()
    if truncate:
        body = body[:-truncate]
    chunks = [body[index : index + 17] for index in range(0, len(body), 17)]

    async def receive() -> dict[str, object]:
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [(b"content-type", outgoing.headers["content-type"].encode("ascii"))],
        },
        receive,
    )


def _csv_files() -> list[tuple[str, bytes]]:
    return [(f"{name}.csv", csv_template(name)) for name in DATASETS]


def test_streamed_csv_and_workbook_recover_through_gateway(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    for files, expected_count in [(_csv_files(), 5), ([("book.xlsx", xlsx_template())], 1)]:
        package = asyncio.run(receive_package(_request(files), storage, ImportLimits()))
        assert len(package.files) == expected_count
        for item in package.files:
            source = dict(files)[item.display_name]
            with storage.open(item.object.key) as stream:
                assert stream.read() == source
            assert item.object.sha256 == hashlib.sha256(source).hexdigest()
            assert item.display_name not in item.object.key


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ([("../orders.csv", b"x")], "FILE_NAME_INVALID"),
        ([("orders.txt", b"x")], "FORMAT_UNSUPPORTED"),
        ([("orders.csv", b"x"), ("orders.csv", b"x")], "FILE_DUPLICATE"),
        ([("orders.csv", b"x")], "PACKAGE_INCOMPLETE"),
    ],
)
def test_rejected_package_leaves_no_objects(
    tmp_path: Path, files: list[tuple[str, bytes]], code: str
) -> None:
    storage = LocalObjectStorage(tmp_path)
    with pytest.raises(UploadError) as caught:
        asyncio.run(receive_package(_request(files), storage, ImportLimits()))
    assert caught.value.code == code
    assert list(storage.object_keys()) == []
    assert list(storage.temporary_keys()) == []


def test_limits_are_applied_while_receiving(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    with pytest.raises(UploadError) as caught:
        asyncio.run(
            receive_package(
                _request([("book.xlsx", b"12345")]),
                storage,
                ImportLimits(max_file_bytes=4, max_package_bytes=100),
            )
        )
    assert caught.value.code == "FILE_LIMIT"
    with pytest.raises(UploadError) as caught:
        asyncio.run(
            receive_package(
                _request([(f"{name}.csv", b"123") for name in DATASETS]),
                storage,
                ImportLimits(max_file_bytes=10, max_package_bytes=10),
            )
        )
    assert caught.value.code == "PACKAGE_LIMIT"
    assert list(storage.object_keys()) == []
    assert list(storage.temporary_keys()) == []


def test_interrupted_stream_cleans_completed_and_partial_files(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    with pytest.raises(UploadError) as caught:
        asyncio.run(receive_package(_request(_csv_files(), truncate=100), storage, ImportLimits()))
    assert caught.value.code == "MULTIPART_INVALID"
    assert list(storage.object_keys()) == []
    assert list(storage.temporary_keys()) == []


def test_client_disconnect_cleans_partial_file(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    outgoing = httpx.Request(
        "POST",
        "http://localhost/upload",
        files=[("files", ("book.xlsx", b"some unfinished data", "application/octet-stream"))],
    )
    body = outgoing.read()
    messages = [
        {"type": "http.request", "body": body[: len(body) // 2], "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive() -> dict[str, object]:
        return messages.pop(0)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [(b"content-type", outgoing.headers["content-type"].encode("ascii"))],
        },
        receive,
    )
    with pytest.raises(UploadError) as caught:
        asyncio.run(receive_package(request, storage, ImportLimits()))
    assert caught.value.code == "UPLOAD_INTERRUPTED"
    assert list(storage.object_keys()) == []
    assert list(storage.temporary_keys()) == []


def test_storage_write_failure_cleans_completed_and_partial_files(tmp_path: Path) -> None:
    class FailingWriter:
        def __init__(self, wrapped: ObjectWriter) -> None:
            self.wrapped = wrapped

        def write(self, data: bytes) -> None:
            raise OSError("simulated write failure")

        def finish(self) -> StoredObject:
            return self.wrapped.finish()

        def abort(self) -> None:
            self.wrapped.abort()

    class FailingStorage(LocalObjectStorage):
        begun = 0

        def begin(self) -> ObjectWriter:
            self.begun += 1
            writer = super().begin()
            return FailingWriter(writer) if self.begun == 2 else writer

    storage = FailingStorage(tmp_path)
    with pytest.raises(OSError, match="simulated write failure"):
        asyncio.run(receive_package(_request(_csv_files()), storage, ImportLimits()))
    assert list(storage.object_keys()) == []
    assert list(storage.temporary_keys()) == []


def test_key_cannot_escape_private_root(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    with pytest.raises(ValueError, match="invalid object key"), storage.open("../secret"):
        pass


def test_windows_path_filename_is_rejected() -> None:
    with pytest.raises(UploadError) as caught:
        _file_identity(b"C:\\fakepath\\orders.csv", set())
    assert caught.value.code == "FILE_NAME_INVALID"
