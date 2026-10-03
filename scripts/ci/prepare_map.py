"""Restore the exact reviewed OSM source, never a changing Overpass response."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def restore(archive: Path, lock: Path, archive_lock: Path, destination: Path) -> None:
    source = json.loads(lock.read_text(encoding="utf-8"))
    packed = json.loads(archive_lock.read_text(encoding="utf-8"))
    if archive.stat().st_size != packed["bytes"]:
        raise ValueError("CI archive size mismatch")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != packed["sha256"]:
        raise ValueError("CI archive checksum mismatch")
    expected_bytes = source["bytes"]
    if not 0 < expected_bytes <= 50 * 1024 * 1024:
        raise ValueError("OSM source exceeds the documented limit")
    if destination.exists():
        if (
            destination.stat().st_size != expected_bytes
            or hashlib.sha256(destination.read_bytes()).hexdigest() != source["sha256"]
        ):
            raise ValueError("Existing source differs; refusing to overwrite it")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    descriptor, name = tempfile.mkstemp(dir=destination.parent, suffix=".restore")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output, gzip.open(archive, "rb") as input_file:
            while chunk := input_file.read(65536):
                size += len(chunk)
                if size > expected_bytes:
                    raise ValueError("Expanded source exceeds locked size")
                digest.update(chunk)
                output.write(chunk)
        if size != expected_bytes or digest.hexdigest() != source["sha256"]:
            raise ValueError("OSM source checksum or size mismatch")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=ROOT / ".ci-work/osrm/santiago-demo.osm"
    )
    args = parser.parse_args()
    restore(
        ROOT / "infrastructure/ci/santiago-demo.osm.gz",
        ROOT / "data/osrm/source-lock.json",
        ROOT / "infrastructure/ci/osm-archive.json",
        args.destination,
    )
    print("Approved OSM checksum and size verified; source ready for car/MLD preparation.")


if __name__ == "__main__":
    main()
