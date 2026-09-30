"""Private local-volume implementation; keys never encode a filename or path."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from routeops.application.ports.object_storage import StoredObject

_KEY = re.compile(r"[0-9a-f]{32}\Z")


def _safe_key(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise ValueError("invalid object key")
    return key


class LocalObjectWriter:
    def __init__(self, storage: LocalObjectStorage) -> None:
        self._storage = storage
        self._key = uuid4().hex
        self._temporary = storage.temporary / self._key
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self._stream: BinaryIO | None = os.fdopen(os.open(self._temporary, flags, 0o600), "wb")
        self._hash = hashlib.sha256()
        self._size = 0
        self._finished = False

    def write(self, data: bytes) -> None:
        if self._stream is None:
            raise ValueError("object writer is closed")
        self._stream.write(data)
        self._hash.update(data)
        self._size += len(data)

    def finish(self) -> StoredObject:
        if self._stream is None:
            raise ValueError("object writer is closed")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()
        self._stream = None
        target = self._storage.objects / self._key
        # Linking rather than replacing keeps an unexpected existing key intact.
        os.link(self._temporary, target)
        self._temporary.unlink()
        self._finished = True
        return StoredObject(self._key, self._hash.hexdigest(), self._size)

    def abort(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if not self._finished:
            self._temporary.unlink(missing_ok=True)


class LocalObjectStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects = root / "objects"
        self.temporary = root / "temporary"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.objects.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.temporary.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in (self.root, self.objects, self.temporary):
            directory.chmod(0o700)

    def begin(self) -> LocalObjectWriter:
        return LocalObjectWriter(self)

    @contextmanager
    def open(self, key: str) -> Iterator[BinaryIO]:
        path = self.objects / _safe_key(key)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        with os.fdopen(os.open(path, flags), "rb") as stream:
            yield stream

    def delete(self, key: str) -> None:
        (self.objects / _safe_key(key)).unlink(missing_ok=True)

    def object_keys(self) -> Iterator[tuple[str, float]]:
        for path in self.objects.iterdir():
            if _KEY.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                yield path.name, path.stat().st_mtime

    def temporary_keys(self) -> Iterator[tuple[str, float]]:
        for path in self.temporary.iterdir():
            if _KEY.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                yield path.name, path.stat().st_mtime

    def delete_temporary(self, key: str) -> None:
        (self.temporary / _safe_key(key)).unlink(missing_ok=True)
