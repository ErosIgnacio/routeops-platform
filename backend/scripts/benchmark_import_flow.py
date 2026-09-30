"""Disposable PostgreSQL/PostGIS benchmark for the complete private import path.

Run with ROUTEOPS_TEST_DATABASE_URL set to a local administrative test database.
Every invocation creates and drops a separate database and private object tree.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from routeops.application.import_context import ValidationContext
from routeops.application.import_contract import DATASETS, SCHEMA
from routeops.application.import_templates import xlsx_template
from routeops.application.import_upload import ReceivedFile, ReceivedPackage
from routeops.application.import_validation import ImportLimits
from routeops.infrastructure.persistence.import_publication import ImportPublicationService
from routeops.infrastructure.persistence.import_upload_repository import UploadService
from routeops.infrastructure.persistence.import_validation_jobs import ValidationJobService
from routeops.infrastructure.persistence.planning_data_repository import ScenarioRepository
from routeops.infrastructure.persistence.session import create_session_factory
from routeops.infrastructure.storage import LocalObjectStorage

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _rows(name: str, sku_width: int) -> list[dict[str, str]] | None:
    fixed = {
        "orders": [
            {
                "order_id": "ORD-1",
                "customer_reference": "Benchmark",
                "latitude": "-33.45",
                "longitude": "-70.65",
                "priority": "1",
                "time_window_start": "2026-10-15T10:00:00-03:00",
                "time_window_end": "2026-10-15T11:00:00-03:00",
                "service_minutes": "10",
                "required_skills": "",
            }
        ],
        "order_lines": [
            {
                "order_id": "ORD-1",
                "sku": f"SKU-{0:0{sku_width}d}",
                "quantity": "1",
                "unit_weight_kg": "1",
                "unit_volume_m3": "0.001",
            }
        ],
        "distribution_centers": [
            {
                "distribution_center_id": "CD-1",
                "name": "Benchmark",
                "latitude": "-33.44",
                "longitude": "-70.64",
                "operating_start": "08:00",
                "operating_end": "18:00",
            }
        ],
        "vehicles": [
            {
                "vehicle_id": "VEH-1",
                "distribution_center_id": "CD-1",
                "vehicle_type": "van",
                "capacity_units": "100",
                "capacity_weight_kg": "1000",
                "capacity_volume_m3": "100",
                "shift_start": "08:00",
                "shift_end": "18:00",
                "skills": "",
                "fixed_cost": "100",
                "cost_per_hour": "10",
                "cost_per_km": "1",
            }
        ],
    }
    return None if name == "inventory" else fixed[name]


def _inventory_row(index: int, sku_width: int) -> dict[str, str]:
    return {
        "snapshot_at": "2026-10-15T08:00:00-03:00",
        "distribution_center_id": "CD-1",
        "sku": f"SKU-{index:0{sku_width}d}",
        "on_hand_quantity": "10",
        "externally_reserved_quantity": "1",
        "safety_stock_quantity": "1",
    }


def _write_csv(path: Path, name: str, inventory_rows: int, sku_width: int) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[field.name for field in SCHEMA[name]])
        writer.writeheader()
        if name == "inventory":
            for index in range(inventory_rows):
                writer.writerow(_inventory_row(index, sku_width))
        else:
            writer.writerows(_rows(name, sku_width) or [])


def _xml_row(name: str, row_number: int, values: dict[str, str]) -> bytes:
    cells = []
    for index, field in enumerate(SCHEMA[name]):
        column = chr(ord("A") + index)
        value = values.get(field.name, "")
        cells.append(f'<c r="{column}{row_number}" t="inlineStr"><is><t>{value}</t></is></c>')
    return f'<row r="{row_number}">{"".join(cells)}</row>'.encode()


def _write_xlsx(path: Path, inventory_rows: int, sku_width: int) -> None:
    source = io.BytesIO(xlsx_template())
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(path, "w") as target:
        for entry in original.infolist():
            dataset = next(
                (
                    name
                    for index, name in enumerate(DATASETS, start=1)
                    if entry.filename == f"xl/worksheets/sheet{index}.xml"
                ),
                None,
            )
            if dataset is None:
                target.writestr(entry, original.read(entry.filename))
                continue
            with target.open(entry, "w") as stream:
                stream.write(
                    (
                        '<?xml version="1.0" encoding="utf-8"?>'
                        f'<worksheet xmlns="{_NS}"><sheetData>'
                    ).encode()
                )
                stream.write(
                    _xml_row(dataset, 1, {field.name: field.name for field in SCHEMA[dataset]})
                )
                if dataset == "inventory":
                    for index in range(inventory_rows):
                        stream.write(_xml_row(dataset, index + 2, _inventory_row(index, sku_width)))
                else:
                    for index, row in enumerate(_rows(dataset, sku_width) or [], start=2):
                        stream.write(_xml_row(dataset, index, row))
                stream.write(b"</sheetData></worksheet>")


def _rss_bytes() -> int:
    if os.name == "nt":
        import ctypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        value = Counters()
        value.cb = ctypes.sizeof(value)
        current_process = ctypes.windll.kernel32.GetCurrentProcess
        current_process.restype = ctypes.c_void_p
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        get_memory.restype = ctypes.c_int
        if not get_memory(current_process(), ctypes.byref(value), value.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return int(value.WorkingSetSize)
    status = Path("/proc/self/status").read_text()
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


def _docker_bytes(raw: str) -> int:
    number, unit = raw.split(" / ", 1)[0].strip().split(" ")[0], ""
    for suffix in ("GiB", "MiB", "KiB", "GB", "MB", "kB", "B"):
        if number.endswith(suffix):
            number, unit = number[: -len(suffix)], suffix
            break
    multipliers = {
        "B": 1,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
    }
    return int(float(number) * multipliers[unit]) if unit else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("csv", "xlsx"), required=True)
    parser.add_argument("--inventory-rows", type=int, required=True)
    parser.add_argument("--sku-width", type=int, default=6)
    parser.add_argument("--sample-docker", action="store_true")
    args = parser.parse_args()
    if args.inventory_rows < 1 or args.inventory_rows > 250_000:
        parser.error("inventory rows must be between 1 and 250000")
    if args.sku_width < 6 or args.sku_width > 96:
        parser.error("SKU width must be between 6 and 96")
    raw_url = os.environ["ROUTEOPS_TEST_DATABASE_URL"]
    base = make_url(raw_url)
    name = f"routeops_bench_{uuid4().hex}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    root = Path(__file__).resolve().parents[2] / ".benchmark_tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        files_dir = Path(directory)
        if args.format == "csv":
            paths = {name: files_dir / f"{name}.csv" for name in DATASETS}
            for dataset, path in paths.items():
                _write_csv(path, dataset, args.inventory_rows, args.sku_width)
        else:
            paths = {"workbook": files_dir / "package.xlsx"}
            _write_xlsx(paths["workbook"], args.inventory_rows, args.sku_width)
        sizes = {name: path.stat().st_size for name, path in paths.items()}
        with admin.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {name}")
        engine = None
        try:
            url = base.set(database=name)
            old_url = os.environ.get("ROUTEOPS_DATABASE_URL")
            os.environ["ROUTEOPS_DATABASE_URL"] = url.render_as_string(hide_password=False)
            try:
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                command.upgrade(config, "head")
            finally:
                if old_url is None:
                    del os.environ["ROUTEOPS_DATABASE_URL"]
                else:
                    os.environ["ROUTEOPS_DATABASE_URL"] = old_url
            engine = create_engine(url)
            sessions = create_session_factory(engine)
            storage = LocalObjectStorage(files_dir / "private")
            uploader = UploadService(sessions, storage)
            validator = ValidationJobService(
                sessions,
                storage,
                ImportLimits(),
                retention_days=30,
                lease_seconds=120,
                max_attempts=3,
            )
            publisher = ImportPublicationService(
                sessions, validator, files_dir / "spool", retention_days=30
            )
            scenario = ScenarioRepository(sessions).create("Benchmark")
            stop = threading.Event()
            peak = [0]
            database_peak = [0]

            def sample() -> None:
                while not stop.wait(0.05):
                    peak[0] = max(peak[0], _rss_bytes())

            monitor = threading.Thread(target=sample, daemon=True)
            monitor.start()

            def sample_database() -> None:
                while not stop.is_set():
                    result = subprocess.run(
                        [
                            "docker",
                            "stats",
                            "--no-stream",
                            "--format",
                            "{{.MemUsage}}",
                            "routeops-database-1",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        database_peak[0] = max(
                            database_peak[0], _docker_bytes(result.stdout.strip())
                        )
                    stop.wait(0.5)

            database_monitor = (
                threading.Thread(target=sample_database, daemon=True)
                if args.sample_docker
                else None
            )
            if database_monitor is not None:
                database_monitor.start()
            try:
                started = time.perf_counter()
                received: list[ReceivedFile] = []
                for dataset, path in paths.items():
                    writer = storage.begin()
                    with path.open("rb") as stream:
                        while chunk := stream.read(64 * 1024):
                            writer.write(chunk)
                    received.append(ReceivedFile(dataset, path.name, writer.finish()))
                import hashlib

                manifest = hashlib.sha256()
                for item in sorted(received, key=lambda file: file.dataset):
                    manifest.update(
                        f"{item.dataset}\0{item.object.sha256}\0{item.object.size_bytes}\n".encode()
                    )
                batch, _ = uploader.create(
                    scenario, uuid4().hex, ReceivedPackage(tuple(received), manifest.hexdigest())
                )
                upload_seconds = time.perf_counter() - started
                context = ValidationContext(
                    planning_date=date(2026, 10, 15),
                    horizon_start_at=datetime.fromisoformat("2026-10-15T08:00:00-03:00"),
                    horizon_end_at=datetime.fromisoformat("2026-10-15T20:00:00-03:00"),
                    timezone_iana="America/Santiago",
                    currency="CLP",
                )
                validator.request(scenario, batch, context)
                started = time.perf_counter()
                assert validator.process_once()
                validation_seconds = time.perf_counter() - started
                state = validator.get(scenario, batch)
                if state["status"] != "VALID":
                    raise RuntimeError(f"validation status: {state['status']}")
                replay_seconds = [0.0]
                original = validator.replay_verified_for_publication

                def timed_replay(
                    scenario_id: UUID,
                    batch_id: UUID,
                    *,
                    row_sink: Callable[[str, str, int, dict[str, Any]], None] | None = None,
                ) -> tuple[dict[str, bytes], ValidationContext]:
                    replay_started = time.perf_counter()
                    result = original(scenario_id, batch_id, row_sink=row_sink)
                    replay_seconds[0] += time.perf_counter() - replay_started
                    return result

                validator.replay_verified_for_publication = timed_replay  # type: ignore[method-assign]
                started = time.perf_counter()
                revision, created = publisher.publish(scenario, batch)
                publish_seconds = time.perf_counter() - started
                assert created and revision["revision_no"] == 1
                stop.set()
                monitor.join(timeout=2)
                if database_monitor is not None:
                    database_monitor.join(timeout=2)
                print(
                    json.dumps(
                        {
                            "format": args.format,
                            "inventory_rows": args.inventory_rows,
                            "sku_width": args.sku_width,
                            "sizes_bytes": sizes,
                            "package_bytes": sum(sizes.values()),
                            "upload_seconds": round(upload_seconds, 3),
                            "validation_seconds": round(validation_seconds, 3),
                            "replay_seconds": round(replay_seconds[0], 3),
                            "publication_insert_seconds": round(
                                publish_seconds - replay_seconds[0], 3
                            ),
                            "publication_total_seconds": round(publish_seconds, 3),
                            "process_peak_sampled_rss_mib": round(peak[0] / 1024 / 1024, 1),
                            "database_peak_sampled_mib": (
                                round(database_peak[0] / 1024 / 1024, 1)
                                if args.sample_docker
                                else None
                            ),
                        }
                    ),
                    flush=True,
                )
            finally:
                stop.set()
                monitor.join(timeout=2)
                if database_monitor is not None:
                    database_monitor.join(timeout=2)
        finally:
            if engine is not None:
                engine.dispose()
            with admin.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
            admin.dispose()


if __name__ == "__main__":
    main()
