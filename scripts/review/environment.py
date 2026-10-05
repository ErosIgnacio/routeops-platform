"""Own a synthetic review project; never operate on the default RouteOps project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRITERS = (
    "frontend",
    "backend",
    "import-validation-worker",
    "planning-worker",
    "comparison-worker",
    "import-maintenance",
)
PROJECT = re.compile(r"routeops-review-[a-z0-9][a-z0-9-]{0,48}\Z")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            value.update(chunk)
    return value.hexdigest()


def command(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
    # Failure messages omit subprocess output, which may include configuration.
    result = subprocess.run(args, cwd=ROOT, stderr=subprocess.PIPE, check=False, **kwargs)
    if result.returncode:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}); inspect this review project privately"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "prepare",
            "up",
            "status",
            "stop",
            "restart",
            "backup",
            "restore",
            "reset",
        ),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--confirm-project")
    parser.add_argument("--api-port", type=int, default=18000)
    parser.add_argument("--ui-port", type=int, default=15173)
    args = parser.parse_args()
    if not PROJECT.fullmatch(args.project):
        parser.error("Only explicit routeops-review-* projects are allowed")
    if (
        not all(1024 <= port <= 65535 for port in (args.api_port, args.ui_port))
        or args.api_port == args.ui_port
    ):
        parser.error("Ports must be distinct and between 1024 and 65535")
    state = ROOT / ".ci-work" / args.project
    marker = state / "owner.json"
    env = state / "review.env"
    if args.action == "prepare":
        if state.exists():
            parser.error("Review state already exists; refusing to overwrite it")
        # Reject accidental adoption of existing Docker resources before claiming ownership.
        existing = command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={args.project}",
            ],
            stdout=subprocess.PIPE,
        )
        volumes = command(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={args.project}",
            ],
            stdout=subprocess.PIPE,
        )
        if existing.stdout.strip() or volumes.stdout.strip():
            parser.error("Project already has Docker resources; choose a new project identity")
        state.mkdir(parents=True, mode=0o700)
        password = secrets.token_hex(24)
        env.write_text(
            "POSTGRES_DB=routeops_review\nPOSTGRES_USER=routeops_review\n"
            f"POSTGRES_PASSWORD={password}\n"
            f"ROUTEOPS_DATABASE_URL=postgresql+psycopg://routeops_review:{password}@database:5432/routeops_review\n"
            f"REVIEW_API_PORT={args.api_port}\nREVIEW_UI_PORT={args.ui_port}\n",
            encoding="utf-8",
        )
        os.chmod(env, 0o600)
        marker.write_text(
            json.dumps({"project": args.project, "purpose": "synthetic review only"}),
            encoding="utf-8",
        )
        sys.path.insert(0, str(ROOT / "scripts" / "ci"))
        from prepare_map import restore

        restore(
            ROOT / "infrastructure/ci/santiago-demo.osm.gz",
            ROOT / "data/osrm/source-lock.json",
            ROOT / "infrastructure/ci/osm-archive.json",
            ROOT / ".ci-work/osrm/santiago-demo.osm",
        )
        print("Private review configuration created; exact OSM archive and source verified")
        return
    if (
        not marker.is_file()
        or json.loads(marker.read_text())["project"] != args.project
        or not env.is_file()
    ):
        parser.error("Owned review state and private environment file are required")
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(env),
        "-p",
        args.project,
        "-f",
        "docker-compose.yml",
        "-f",
        "infrastructure/review/compose.yml",
    ]
    # Shell environment takes precedence over --env-file: reject inherited overrides.
    if any(key.startswith(("ROUTEOPS_", "POSTGRES_", "REVIEW_")) for key in os.environ):
        parser.error("Unset inherited ROUTEOPS_*, POSTGRES_* and REVIEW_* variables")
    if args.action == "up":
        command([*compose, "config", "--quiet"])
        command([*compose, "--profile", "tools", "run", "--rm", "osrm-prepare"])
        command([*compose, "up", "-d", "--build"])
        port_line = next(
            line for line in env.read_text().splitlines() if line.startswith("REVIEW_API_PORT=")
        )
        port = int(port_line.split("=", 1)[1])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health/ready", timeout=3
                ) as response:
                    if json.load(response)["status"] == "ready":
                        print(
                            "Review API ready; migrations and real routing dependencies available"
                        )
                        break
            except OSError, urllib.error.URLError:
                pass  # Expected only during startup; exhaustion fails explicitly below.
            time.sleep(0.5)
        else:
            raise RuntimeError("Review API readiness failed within the 120s startup check")
    elif args.action == "status":
        command([*compose, "ps", "-a"])
    elif args.action == "stop":
        command([*compose, "stop"])
    elif args.action == "restart":
        command([*compose, "restart"])
    elif args.action == "reset":
        if args.confirm_project != args.project:
            parser.error("Synthetic reset requires --confirm-project with the exact owned project")
        command([*compose, "down", "--volumes"])
        print("Only the explicit owned review project containers/network/two volumes were removed")
    elif args.action in ("backup", "restore"):
        if args.backup_dir is None:
            parser.error(
                "--backup-dir is required; keep this PRIVATE and outside Git/public evidence"
            )
        directory = args.backup_dir.resolve()
        if directory.is_relative_to(ROOT):
            parser.error("Backup must be outside the checkout")
        if args.action == "backup":
            if directory.exists():
                parser.error("Backup destination must not exist")
            directory.mkdir(parents=True, mode=0o700)
            running = (
                command(
                    [*compose, "ps", "--status", "running", "--services"],
                    stdout=subprocess.PIPE,
                )
                .stdout.decode()
                .splitlines()
            )
            resume = [name for name in WRITERS if name in running]
            try:
                command([*compose, "stop", *WRITERS])
                with (directory / "database.dump").open("xb") as output:
                    command(
                        [
                            *compose,
                            "exec",
                            "-T",
                            "database",
                            "sh",
                            "-ec",
                            'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" "$POSTGRES_DB"',
                        ],
                        stdout=output,
                    )
                # Backend is stopped: one-off process reads the same private named volume.
                code = (
                    "import sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|'); "
                    "t.add('/app/private-imports',arcname='objects'); t.close()"
                )
                with (directory / "originals.tar").open("xb") as output:
                    command(
                        [
                            *compose,
                            "run",
                            "--rm",
                            "--no-deps",
                            "-T",
                            "backend",
                            "python",
                            "-c",
                            code,
                        ],
                        stdout=output,
                    )
                manifest = {
                    "source_project": args.project,
                    "quiesced_writers": list(WRITERS),
                    "files": {
                        name: {
                            "sha256": digest(directory / name),
                            "bytes": (directory / name).stat().st_size,
                        }
                        for name in ("database.dump", "originals.tar")
                    },
                }
                (directory / "manifest.json").write_text(
                    json.dumps(manifest, indent=2), encoding="utf-8"
                )
                print("Coherent private PostgreSQL + original-object backup completed and hashed")
            finally:
                if resume:
                    command([*compose, "start", *resume])
        else:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if manifest["source_project"] == args.project or args.confirm_project != args.project:
                parser.error(
                    "Restore requires a different owned EMPTY project and exact confirmation"
                )
            for name in ("database.dump", "originals.tar"):
                if (
                    digest(directory / name) != manifest["files"][name]["sha256"]
                    or (directory / name).stat().st_size != manifest["files"][name]["bytes"]
                ):
                    parser.error(
                        "Backup hash/size mismatch; restore refused before database writes"
                    )
            command([*compose, "up", "-d", "--wait", "database"])
            check = command(
                [
                    *compose,
                    "exec",
                    "-T",
                    "database",
                    "sh",
                    "-ec",
                    'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '
                    '"SELECT count(*) FROM information_schema.tables '
                    "WHERE table_schema='public' AND table_type='BASE TABLE' "
                    "AND table_name<>'spatial_ref_sys'\"",
                ],
                stdout=subprocess.PIPE,
            )
            if check.stdout.strip() != b"0":
                parser.error("Restore target is not empty; existing history preserved")
            # Private target volume must also be empty. Restore members cannot escape its root.
            code = (
                "import sys,tarfile,pathlib; p=pathlib.Path('/app/private-imports'); "
                "assert not any(p.iterdir()), 'target objects not empty'; "
                "t=tarfile.open(fileobj=sys.stdin.buffer,mode='r|');\n"
                "for m in t:\n"
                " assert m.name=='objects' or m.name.startswith('objects/'); "
                "assert not m.issym() and not m.islnk() "
                "and '..' not in pathlib.PurePosixPath(m.name).parts; "
                "m.name=m.name.removeprefix('objects/'); "
                "m.name='.' if m.name=='objects' else m.name; t.extract(m,p,filter='data')"
            )
            with (directory / "originals.tar").open("rb") as input_file:
                command(
                    [*compose, "run", "--rm", "--no-deps", "-T", "backend", "python", "-c", code],
                    stdin=input_file,
                    stdout=subprocess.PIPE,
                )
            with (directory / "database.dump").open("rb") as input_file:
                command(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "database",
                        "sh",
                        "-ec",
                        "pg_restore --clean --if-exists --no-owner --no-acl --exit-on-error "
                        '-U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                    ],
                    stdin=input_file,
                )
            print("Restore completed; start the empty target project and verify history/provenance")


if __name__ == "__main__":
    main()
