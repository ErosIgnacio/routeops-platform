"""Bounded multipart reception without buffering complete files in the API process."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect

from routeops.application.import_contract import DATASETS
from routeops.application.import_validation import ImportLimits
from routeops.application.ports.object_storage import (
    ObjectStorageGateway,
    ObjectWriter,
    StoredObject,
)

if TYPE_CHECKING:
    from fastapi import Request

_NAME = re.compile(r"[^/\\\x00-\x1f\x7f]{1,200}\Z")
_CSV_NAMES = {f"{name}.csv": name for name in DATASETS}
_MULTIPART_OVERHEAD = 64 * 1024
_PART_HEADER_BYTES = 2 * 1024
_PART_HEADER_COUNT = 8


class UploadError(Exception):
    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ReceivedFile:
    dataset: str
    display_name: str
    object: StoredObject


@dataclass(frozen=True, slots=True)
class ReceivedPackage:
    files: tuple[ReceivedFile, ...]
    sha256: str


def _file_identity(raw: bytes, seen: set[str]) -> tuple[str, str]:
    try:
        name = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadError("FILE_NAME_INVALID", 400) from exc
    if not _NAME.fullmatch(name) or name in {".", ".."} or name != name.strip():
        raise UploadError("FILE_NAME_INVALID", 400)
    if name in _CSV_NAMES:
        dataset = _CSV_NAMES[name]
    elif name.endswith(".xlsx"):
        dataset = "workbook"
    else:
        raise UploadError("FORMAT_UNSUPPORTED", 422)
    if dataset in seen:
        raise UploadError("FILE_DUPLICATE", 422)
    return name, dataset


class _Receiver:
    def __init__(self, storage: ObjectStorageGateway, limits: ImportLimits) -> None:
        self.storage = storage
        self.limits = limits
        self.files: list[ReceivedFile] = []
        self.seen: set[str] = set()
        self.writer: ObjectWriter | None = None
        self.current_name = ""
        self.current_dataset = ""
        self.current_size = 0
        self.total_size = 0
        self.header_field = bytearray()
        self.header_value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.complete = False
        self.header_bytes = 0

    def part_begin(self) -> None:
        if len(self.files) >= len(DATASETS):
            raise UploadError("PACKAGE_INCOMPLETE", 422)
        self.headers = {}
        self.header_bytes = 0
        self.current_size = 0

    def header_field_data(self, data: bytes, start: int, end: int) -> None:
        self._bound_header(end - start)
        self.header_field.extend(data[start:end])

    def header_value_data(self, data: bytes, start: int, end: int) -> None:
        self._bound_header(end - start)
        self.header_value.extend(data[start:end])

    def _bound_header(self, size: int) -> None:
        self.header_bytes += size
        if self.header_bytes > _PART_HEADER_BYTES:
            raise UploadError("MULTIPART_HEADER_LIMIT", 413)

    def header_end(self) -> None:
        if len(self.headers) >= _PART_HEADER_COUNT:
            raise UploadError("MULTIPART_HEADER_LIMIT", 413)
        field = bytes(self.header_field).lower()
        if field in self.headers:
            raise UploadError("MULTIPART_INVALID", 400)
        self.headers[field] = bytes(self.header_value)
        self.header_field.clear()
        self.header_value.clear()

    def headers_finished(self) -> None:
        disposition, options = parse_options_header(self.headers.get(b"content-disposition"))
        if disposition != b"form-data" or options.get(b"name") != b"files":
            raise UploadError("MULTIPART_INVALID", 400)
        raw_name = options.get(b"filename")
        if raw_name is None:
            raise UploadError("FILE_NAME_INVALID", 400)
        self.current_name, self.current_dataset = _file_identity(raw_name, self.seen)
        if self.current_dataset == "workbook" and self.files:
            raise UploadError("FORMAT_UNSUPPORTED", 422)
        if self.current_dataset != "workbook" and "workbook" in self.seen:
            raise UploadError("FORMAT_UNSUPPORTED", 422)
        self.seen.add(self.current_dataset)
        self.writer = self.storage.begin()

    def part_data(self, data: bytes, start: int, end: int) -> None:
        size = end - start
        self.current_size += size
        self.total_size += size
        if self.current_size > self.limits.max_file_bytes:
            raise UploadError("FILE_LIMIT", 413)
        if self.total_size > self.limits.max_package_bytes:
            raise UploadError("PACKAGE_LIMIT", 413)
        if self.writer is None:
            raise UploadError("MULTIPART_INVALID", 400)
        self.writer.write(data[start:end])

    def part_end(self) -> None:
        if self.writer is None:
            raise UploadError("MULTIPART_INVALID", 400)
        stored = self.writer.finish()
        self.writer = None
        self.files.append(ReceivedFile(self.current_dataset, self.current_name, stored))

    def end(self) -> None:
        self.complete = True

    def abort(self) -> None:
        if self.writer is not None:
            self.writer.abort()
        for item in self.files:
            self.storage.delete(item.object.key)

    def result(self) -> ReceivedPackage:
        if not self.complete:
            raise UploadError("MULTIPART_INVALID", 400)
        if self.seen != {"workbook"} and self.seen != set(DATASETS):
            raise UploadError("PACKAGE_INCOMPLETE", 422)
        manifest = hashlib.sha256()
        for item in sorted(self.files, key=lambda file: file.dataset):
            manifest.update(
                f"{item.dataset}\0{item.object.sha256}\0{item.object.size_bytes}\n".encode("ascii")
            )
        return ReceivedPackage(tuple(self.files), manifest.hexdigest())


async def receive_package(
    request: Request, storage: ObjectStorageGateway, limits: ImportLimits
) -> ReceivedPackage:
    content_type, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise UploadError("MULTIPART_INVALID", 400)
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal() or len(length) > 12:
            raise UploadError("MULTIPART_INVALID", 400)
        if int(length) > limits.max_package_bytes + _MULTIPART_OVERHEAD:
            raise UploadError("PACKAGE_LIMIT", 413)
    receiver = _Receiver(storage, limits)
    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": receiver.part_begin,
            "on_header_field": receiver.header_field_data,
            "on_header_value": receiver.header_value_data,
            "on_header_end": receiver.header_end,
            "on_headers_finished": receiver.headers_finished,
            "on_part_data": receiver.part_data,
            "on_part_end": receiver.part_end,
            "on_end": receiver.end,
        },
    )
    received_bytes = 0
    try:
        async for chunk in request.stream():
            received_bytes += len(chunk)
            if received_bytes > limits.max_package_bytes + _MULTIPART_OVERHEAD:
                raise UploadError("PACKAGE_LIMIT", 413)
            parser.write(chunk)
        return receiver.result()
    except MultipartParseError as exc:
        raise UploadError("MULTIPART_INVALID", 400) from exc
    except ClientDisconnect as exc:
        raise UploadError("UPLOAD_INTERRUPTED", 400) from exc
    finally:
        if not receiver.complete or receiver.seen not in ({"workbook"}, set(DATASETS)):
            receiver.abort()
