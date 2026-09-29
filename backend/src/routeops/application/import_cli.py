"""Local, read-only entry point for milestone 2.1 templates and validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from routeops.application.import_contract import DATASETS
from routeops.application.import_templates import csv_template, xlsx_template
from routeops.application.import_validation import ImportLimits, validate_package


class _InputLimit(ValueError):
    pass


def _read_bounded(path: Path, file_limit: int, remaining_package_bytes: int) -> bytes:
    parts: list[bytes] = []
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > file_limit:
                raise _InputLimit("FILE_LIMIT")
            if size > remaining_package_bytes:
                raise _InputLimit("PACKAGE_LIMIT")
            parts.append(chunk)
    return b"".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RouteOps import contract 2.1")
    commands = parser.add_subparsers(dest="command", required=True)
    templates = commands.add_parser("templates", help="write deterministic blank templates")
    templates.add_argument("directory", type=Path)
    validate = commands.add_parser("validate", help="validate five CSVs or one XLSX")
    validate.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.command == "templates":
        directory: Path = args.directory
        names = [f"{name}.csv" for name in DATASETS] + ["routeops-template.xlsx"]
        if any((directory / name).exists() for name in names):
            print(json.dumps({"error": "TEMPLATE_EXISTS"}))
            return 2
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for name in DATASETS:
                with (directory / f"{name}.csv").open("xb") as stream:
                    stream.write(csv_template(name))
            with (directory / "routeops-template.xlsx").open("xb") as stream:
                stream.write(xlsx_template())
        except OSError:
            print(json.dumps({"error": "TEMPLATE_WRITE_ERROR"}))
            return 2
        return 0
    paths: list[Path] = args.files
    try:
        limits = ImportLimits.from_environment()
    except ValueError:
        print(json.dumps({"error": "CONFIG_INVALID"}))
        return 2
    files: dict[str, bytes] = {}
    total_bytes = 0
    for path in paths:
        if path.name in files:
            print(json.dumps({"error": "FILE_UNKNOWN"}))
            return 2
        try:
            content = _read_bounded(
                path, limits.max_file_bytes, limits.max_package_bytes - total_bytes
            )
        except _InputLimit as exc:
            print(json.dumps({"error": str(exc)}))
            return 2
        except OSError:
            print(json.dumps({"error": "FILE_READ_ERROR"}))
            return 2
        files[path.name] = content
        total_bytes += len(content)
    report = validate_package(files, limits)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
