"""Private, opaque storage for original import files."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    sha256: str
    size_bytes: int


class ObjectWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    def finish(self) -> StoredObject: ...

    def abort(self) -> None: ...


class ObjectStorageGateway(Protocol):
    def begin(self) -> ObjectWriter: ...

    def open(self, key: str) -> AbstractContextManager[BinaryIO]: ...

    def delete(self, key: str) -> None: ...

    def object_keys(self) -> Iterator[tuple[str, float]]: ...

    def temporary_keys(self) -> Iterator[tuple[str, float]]: ...

    def delete_temporary(self, key: str) -> None: ...
