"""Destructive-operation guards fail before Docker can touch unrelated resources."""

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import environment


class OwnershipGuards(unittest.TestCase):
    def invoke(self, root, arguments):
        with (
            patch.object(environment, "ROOT", root),
            patch("sys.argv", ["environment.py", *arguments]),
            patch.object(environment, "command") as docker,
            patch.dict("os.environ", {}, clear=True),
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit),
        ):
            environment.main()
        docker.assert_not_called()

    def test_operational_project_is_never_adopted(self):
        self.invoke(
            Path("unused"),
            ["reset", "--project", "routeops", "--confirm-project", "routeops"],
        )

    def test_missing_ownership_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.invoke(Path(directory), ["stop", "--project", "routeops-review-missing"])

    def test_reset_requires_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".ci-work/routeops-review-owned"
            state.mkdir(parents=True)
            (state / "owner.json").write_text('{"project":"routeops-review-owned"}')
            (state / "review.env").write_text("private")
            self.invoke(
                root,
                [
                    "reset",
                    "--project",
                    "routeops-review-owned",
                    "--confirm-project",
                    "routeops-review-other",
                ],
            )

    def test_tampered_backup_fails_before_any_database_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            state = root / ".ci-work/routeops-review-owned"
            state.mkdir(parents=True)
            (state / "owner.json").write_text('{"project":"routeops-review-owned"}')
            (state / "review.env").write_text("private")
            backup = Path(directory) / "private-backup"
            backup.mkdir()
            (backup / "database.dump").write_bytes(b"altered")
            manifest = {
                "source_project": "routeops-review-source",
                "files": {
                    "database.dump": {
                        "bytes": 5,
                        "sha256": hashlib.sha256(b"clean").hexdigest(),
                    }
                },
            }
            (backup / "manifest.json").write_text(json.dumps(manifest))
            self.invoke(
                root,
                [
                    "restore",
                    "--project",
                    "routeops-review-owned",
                    "--backup-dir",
                    str(backup),
                    "--confirm-project",
                    "routeops-review-owned",
                ],
            )


if __name__ == "__main__":
    unittest.main()
