"""Useful failure tests for the clean-checkout map and no-skip evidence gates."""

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from check_junit import verify
from prepare_map import restore


class MapContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = b'<osm version="0.6"><node id="1"/></osm>'
        self.archive = self.root / "source.gz"
        self.archive.write_bytes(gzip.compress(self.data, mtime=0))
        self.lock = self.root / "source.json"
        self.lock.write_text(
            json.dumps({"bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()})
        )
        self.packed = self.root / "archive.json"
        self.packed.write_text(
            json.dumps(
                {
                    "bytes": self.archive.stat().st_size,
                    "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                }
            )
        )
        self.output = self.root / "result.osm"

    def test_exact_source_and_idempotent_restore(self) -> None:
        restore(self.archive, self.lock, self.packed, self.output)
        restore(self.archive, self.lock, self.packed, self.output)
        self.assertEqual(self.output.read_bytes(), self.data)

    def test_archive_tampering_is_rejected_before_expansion(self) -> None:
        self.archive.write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "archive size"):
            restore(self.archive, self.lock, self.packed, self.output)
        self.assertFalse(self.output.exists())

    def test_expansion_over_limit_has_no_partial_output(self) -> None:
        self.lock.write_text(json.dumps({"bytes": 1, "sha256": "0" * 64}))
        with self.assertRaisesRegex(ValueError, "Expanded source"):
            restore(self.archive, self.lock, self.packed, self.output)
        self.assertEqual(list(self.root.glob("*.restore")), [])
        self.assertFalse(self.output.exists())

    def test_different_existing_source_is_preserved(self) -> None:
        self.output.write_bytes(b"other map")
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            restore(self.archive, self.lock, self.packed, self.output)
        self.assertEqual(self.output.read_bytes(), b"other map")


class EvidenceContracts(unittest.TestCase):
    def test_empty_skipped_or_failed_report_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "test.xml"
            for attrs in (
                'tests="0"',
                'tests="1" skipped="1"',
                'tests="1" failures="1"',
                'tests="1" errors="1"',
            ):
                report.write_text(f"<testsuites><testsuite {attrs}/></testsuites>")
                with self.assertRaises(ValueError):
                    verify(report)
            report.write_text(
                '<testsuites><testsuite tests="2" failures="0" skipped="0"/></testsuites>'
            )
            self.assertEqual(verify(report), 2)


if __name__ == "__main__":
    unittest.main()
