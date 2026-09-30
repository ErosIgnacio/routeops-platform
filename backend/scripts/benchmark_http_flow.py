"""Measure local HTTP import and optional planning with identifiable test scenarios.

Run from backend with PYTHONPATH=src. It leaves historical test scenarios in the
local database; it never changes unrelated scenarios or reservations.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from benchmark_import_flow import _docker_bytes, _rss_bytes, _write_csv, _write_xlsx

from routeops.application.import_contract import DATASETS, SCHEMA

CONTEXT = {
    "planning_date": "2026-10-15",
    "horizon_start_at": "2026-10-15T08:00:00-03:00",
    "horizon_end_at": "2026-10-15T20:00:00-03:00",
    "timezone_iana": "America/Santiago",
    "currency": "CLP",
}
SERVICES = (
    "routeops-backend-1",
    "routeops-planning-worker-1",
    "routeops-import-validation-worker-1",
    "routeops-database-1",
    "routeops-vroom-1",
    "routeops-osrm-1",
)


def _planning_files(folder: Path) -> dict[str, Path]:
    """Create the accepted 20/60/4/6 synthetic planning workload as five CSVs."""
    paths = {name: folder / f"{name}.csv" for name in DATASETS}
    rows: dict[str, list[dict[str, str]]] = {name: [] for name in DATASETS}
    for center_index in range(4):
        center = f"CD-{center_index}"
        rows["distribution_centers"].append(
            {
                "distribution_center_id": center,
                "name": center,
                "latitude": f"{-33.445 + center_index * 0.003:.5f}",
                "longitude": f"{-70.66 + center_index * 0.004:.5f}",
                "operating_start": "08:00",
                "operating_end": "18:00",
            }
        )
        for sku_index in range(3):
            rows["inventory"].append(
                {
                    "snapshot_at": "2026-10-15T08:00:00-03:00",
                    "distribution_center_id": center,
                    "sku": f"SKU-{sku_index:03d}",
                    "on_hand_quantity": "100",
                    "externally_reserved_quantity": "0",
                    "safety_stock_quantity": "0",
                }
            )
    for vehicle_index in range(6):
        rows["vehicles"].append(
            {
                "vehicle_id": f"VEH-{vehicle_index:02d}",
                "distribution_center_id": f"CD-{vehicle_index % 4}",
                "vehicle_type": "van",
                "capacity_units": "200",
                "capacity_weight_kg": "1000",
                "capacity_volume_m3": "100",
                "shift_start": "08:00",
                "shift_end": "18:00",
                "skills": "",
                "fixed_cost": "100",
                "cost_per_hour": "10",
                "cost_per_km": "1",
            }
        )
    for order_index in range(20):
        order = f"BENCH-{order_index:03d}"
        rows["orders"].append(
            {
                "order_id": order,
                "customer_reference": order,
                "latitude": f"{-33.448 + (order_index % 10) * 0.002:.5f}",
                "longitude": f"{-70.657 + (order_index // 10) * 0.003:.5f}",
                "priority": "50",
                "time_window_start": "2026-10-15T09:00:00-03:00",
                "time_window_end": "2026-10-15T17:00:00-03:00",
                "service_minutes": "5",
                "required_skills": "",
            }
        )
        for sku_index in range(3):
            rows["order_lines"].append(
                {
                    "order_id": order,
                    "sku": f"SKU-{sku_index:03d}",
                    "quantity": "1",
                    "unit_weight_kg": "1",
                    "unit_volume_m3": "0.001",
                }
            )
    for name, path in paths.items():
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=[field.name for field in SCHEMA[name]])
            writer.writeheader()
            writer.writerows(rows[name])
    return paths


def _check(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


def _wait(client: httpx.Client, path: str, terminal: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        result = _check(client.get(path))
        if result["status"] in terminal:
            return result
        time.sleep(0.2)
    raise TimeoutError(path)


def _one(base_url: str, paths: dict[str, Path], planning: bool) -> dict[str, Any]:
    with httpx.Client(base_url=base_url, timeout=180) as client:
        scenario = _check(
            client.post(
                "/api/v1/scenarios",
                json={"name": f"M26 HTTP benchmark {uuid4().hex}"},
            )
        )["id"]
        prefix = f"/api/v1/scenarios/{scenario}/imports"
        started = time.perf_counter()
        # Keep all file handles open until the multipart request finishes.
        handles = [path.open("rb") for path in paths.values()]
        try:
            files = [
                ("files", (path.name, handle, "application/octet-stream"))
                for path, handle in zip(paths.values(), handles, strict=True)
            ]
            batch = _check(
                client.post(prefix, files=files, headers={"Idempotency-Key": uuid4().hex})
            )["id"]
        finally:
            for handle in handles:
                handle.close()
        upload_s = time.perf_counter() - started
        validation = f"{prefix}/{batch}/validation"
        started = time.perf_counter()
        _check(client.post(validation, json=CONTEXT))
        report = _wait(client, validation, {"VALID", "INVALID", "FAILED"})
        validation_s = time.perf_counter() - started
        if report["status"] != "VALID":
            raise RuntimeError(f"validation ended {report['status']}")
        started = time.perf_counter()
        revision = _check(client.post(f"{prefix}/{batch}/publish"))
        publication_s = time.perf_counter() - started
        result: dict[str, Any] = {
            "scenario_id": scenario,
            "batch_id": batch,
            "revision_id": revision["id"],
            "upload_s": round(upload_s, 3),
            "validation_s": round(validation_s, 3),
            "publication_including_replay_s": round(publication_s, 3),
            "errors": 0,
        }
        if planning:
            started = time.perf_counter()
            queued = _check(
                client.post(
                    f"/api/v1/scenarios/{scenario}/revisions/1/runs",
                    json={},
                    headers={"Idempotency-Key": uuid4().hex},
                )
            )
            ready = _wait(client, f"/api/v1/revision-runs/{queued['run_id']}", {"READY", "FAILED"})
            result["planning_s"] = round(time.perf_counter() - started, 3)
            result["run_id"] = queued["run_id"]
            result["run_status"] = ready["status"]
            if ready["status"] != "READY":
                raise RuntimeError("planning failed")
            _check(client.post(f"/api/v1/revision-runs/{queued['run_id']}/cancel"))
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--format", choices=("csv", "xlsx"), required=True)
    parser.add_argument("--inventory-rows", type=int, required=True)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--planning", action="store_true")
    parser.add_argument("--planning-workload", action="store_true")
    args = parser.parse_args()
    if args.inventory_rows < 1 or args.parallel < 1 or args.parallel > 4:
        parser.error("inventory rows must be positive and parallel must be 1-4")
    if args.planning and args.inventory_rows > 10000:
        parser.error("planning samples should use bounded import data")
    if args.planning_workload and args.format != "csv":
        parser.error("planning workload uses five CSV files")
    root = Path(__file__).resolve().parents[1] / ".pytest_cache"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        folder = Path(directory)
        if args.planning_workload:
            paths = _planning_files(folder)
        elif args.format == "csv":
            paths = {name: folder / f"{name}.csv" for name in DATASETS}
            for name, path in paths.items():
                _write_csv(path, name, args.inventory_rows, 6)
        else:
            paths = {"workbook": folder / "package.xlsx"}
            _write_xlsx(paths["workbook"], args.inventory_rows, 6)
        stop = threading.Event()
        rss_peak = [0]
        docker_peak: dict[str, int] = {}

        def sample_process() -> None:
            while not stop.wait(0.05):
                rss_peak[0] = max(rss_peak[0], _rss_bytes())

        def sample_services() -> None:
            while not stop.is_set():
                completed = subprocess.run(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.Name}}|{{.MemUsage}}",
                        *SERVICES,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode == 0:
                    for line in completed.stdout.splitlines():
                        name, usage = line.split("|", 1)
                        docker_peak[name] = max(docker_peak.get(name, 0), _docker_bytes(usage))
                stop.wait(0.5)

        monitors = [
            threading.Thread(target=sample_process, daemon=True),
            threading.Thread(target=sample_services, daemon=True),
        ]
        for monitor in monitors:
            monitor.start()
        try:
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                futures = [
                    pool.submit(_one, args.base_url, paths, args.planning or args.planning_workload)
                    for _ in range(args.parallel)
                ]
                samples = [future.result() for future in futures]
        finally:
            stop.set()
            for monitor in monitors:
                monitor.join(timeout=3)
        print(
            json.dumps(
                {
                    "machine": platform.platform(),
                    "logical_cpus": os.cpu_count(),
                    "format": args.format,
                    "inventory_rows": 12 if args.planning_workload else args.inventory_rows,
                    "other_rows": (
                        {"orders": 20, "order_lines": 60, "distribution_centers": 4, "vehicles": 6}
                        if args.planning_workload
                        else {name: 1 for name in DATASETS if name != "inventory"}
                    ),
                    "sizes_bytes": {name: path.stat().st_size for name, path in paths.items()},
                    "concurrent_clients": args.parallel,
                    "samples": samples,
                    "client_rss_peak_bytes": rss_peak[0],
                    "docker_stats_memory_peak_bytes": docker_peak,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
