"""Repeated HTTP samples on an explicitly disposable Compose project, never routeops.

Reuses existing planning/import fixtures. Raw JSON includes warmups and failures.
No limit, worker count or timeout is changed by this tool.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from benchmark_http_flow import CONTEXT, _planning_files
from benchmark_import_flow import _docker_bytes, _rss_bytes, _write_csv, _write_xlsx

from routeops.application.import_contract import DATASETS, SCHEMA
from routeops.application.import_templates import _column, xlsx_template

PROFILES = {"small": (2, 1, 1), "medium": (10, 2, 3), "bounded": (20, 4, 6)}
SERVICES = (
    "backend",
    "import-validation-worker",
    "planning-worker",
    "comparison-worker",
    "database",
    "osrm",
    "vroom",
)


def checked(result: httpx.Response) -> dict[str, Any]:
    result.raise_for_status()
    return result.json()


def wait(client: httpx.Client, path: str, statuses: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        value = checked(client.get(path))
        if value["status"] in statuses:
            return value
        time.sleep(0.2)
    raise TimeoutError("benchmark observation deadline exceeded; server limits unchanged")


def payloads(
    folder: Path, model: str, profile: str, kind: str, seed: int
) -> tuple[dict[str, bytes], dict[str, list[dict[str, str]]]]:
    if profile.startswith("inventory-"):
        amount = int(profile.removeprefix("inventory-"))
        paths = {name: folder / f"{name}.csv" for name in DATASETS}
        for name, path in paths.items():
            _write_csv(path, name, amount, 6)
        rows = {}
        result = {}
        for name, path in paths.items():
            with path.open(encoding="utf-8") as source:
                rows[name] = list(csv.DictReader(source))
            result[path.name] = path.read_bytes()
        if kind == "xlsx":
            book = folder / "package.xlsx"
            _write_xlsx(book, amount, 6)
            result = {book.name: book.read_bytes()}
        return result, rows
    count, centers, vehicles = PROFILES[profile]
    paths = _planning_files(folder, orders=count, centers=centers, vehicles=vehicles)
    rows = {}
    for name, path in paths.items():
        with path.open(encoding="utf-8") as source:
            rows[name] = list(csv.DictReader(source))
    generator = random.Random(seed)
    for order in rows["orders"]:
        order["customer_reference"] = f"Synthetic {model} {order['order_id']}"
        order["service_minutes"] = "5" if model == "B2B" else "1"
        order["priority"] = str(generator.randrange(1, 101))
    for vehicle in rows["vehicles"]:
        vehicle["vehicle_type"] = "truck" if model == "B2B" else "van"
    result: dict[str, bytes] = {}
    for dataset in DATASETS:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=[s.name for s in SCHEMA[dataset]])
        writer.writeheader()
        writer.writerows(rows[dataset])
        result[f"{dataset}.csv"] = stream.getvalue().encode()
    if kind == "xlsx":
        target = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(xlsx_template())) as template,
            zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as book,
        ):
            for entry in template.infolist():
                data = template.read(entry.filename)
                for index, dataset in enumerate(DATASETS, 1):
                    if entry.filename == f"xl/worksheets/sheet{index}.xml":
                        root = ET.fromstring(data)
                        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                        sheet = root.find(f"{ns}sheetData")
                        assert sheet is not None
                        for number, values in enumerate(rows[dataset], 2):
                            row = ET.SubElement(sheet, f"{ns}row", r=str(number))
                            for col, spec in enumerate(SCHEMA[dataset], 1):
                                cell = ET.SubElement(
                                    row, f"{ns}c", r=f"{_column(col)}{number}", t="inlineStr"
                                )
                                ET.SubElement(
                                    ET.SubElement(cell, f"{ns}is"), f"{ns}t"
                                ).text = values.get(spec.name, "")
                        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                book.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED)
        result = {"package.xlsx": target.getvalue()}
    return result, rows


class MemorySampler:
    """Client RSS every 50ms; docker working set and Python-process RSS sampled separately."""

    def __init__(self, project: str) -> None:
        self.project = project
        self.stop = threading.Event()
        self.samples: list[dict[str, Any]] = []
        self.client_peak = 0
        self.errors: list[str] = []
        self.threads = [
            threading.Thread(target=fn, daemon=True) for fn in (self.client, self.services)
        ]

    def client(self) -> None:
        try:
            while not self.stop.wait(0.05):
                self.client_peak = max(self.client_peak, _rss_bytes())
        except OSError:
            self.errors.append("client RSS unavailable")

    def services(self) -> None:
        names = [f"{self.project}-{service}-1" for service in SERVICES]
        rss_code = """from pathlib import Path
import json, os
out = []
for p in Path('/proc').glob('[0-9]*/status'):
    if int(p.parent.name) == os.getpid():
        continue
    try:
        lines = p.read_text().splitlines()
        name = next(l.split(':', 1)[1].strip() for l in lines if l.startswith('Name:'))
        rss = next((int(l.split()[1])*1024 for l in lines if l.startswith('VmRSS:')), 0)
        out.append({'pid': int(p.parent.name), 'name': name, 'rss_bytes': rss})
    except (OSError, ValueError, StopIteration):
        out.append({'pid': int(p.parent.name), 'rss_bytes': None, 'unavailable': True})
print(json.dumps(out))
"""
        while not self.stop.is_set():
            observed: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "docker_bytes": {}}
            try:
                result = subprocess.run(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.Name}}|{{.MemUsage}}",
                        *names,
                    ],
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except OSError, subprocess.TimeoutExpired:
                self.errors.append("docker stats unavailable or timed out")
                self.stop.wait(1)
                continue
            if result.returncode:
                self.errors.append("docker stats unavailable")
            else:
                for line in result.stdout.splitlines():
                    name, raw = line.split("|", 1)
                    observed["docker_bytes"][name] = _docker_bytes(raw)
            observed["processes"] = {}
            for name in names[:4]:
                try:
                    value = subprocess.run(
                        ["docker", "exec", name, "python", "-c", rss_code],
                        text=True,
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                except OSError, subprocess.TimeoutExpired:
                    self.errors.append(f"process RSS unavailable or timed out: {name}")
                    continue
                if value.returncode:
                    self.errors.append(f"process RSS unavailable: {name}")
                else:
                    try:
                        observed["processes"][name] = json.loads(value.stdout)
                    except json.JSONDecodeError:
                        self.errors.append(f"process RSS invalid response: {name}")
            self.samples.append(observed)
            self.stop.wait(1)

    def __enter__(self) -> MemorySampler:
        for thread in self.threads:
            thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=60)


def one(
    base_url: str,
    files: dict[str, bytes],
    rows: dict[str, Any],
    label: str,
    *,
    full_flow: bool = True,
) -> dict[str, Any]:
    sample: dict[str, Any] = {"label": label, "http_seconds": {}, "http_status": {}, "error": None}
    with httpx.Client(base_url=base_url, timeout=180) as client:

        def request(name: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                response = client.request(method, path, **kwargs)
            finally:
                sample["http_seconds"][name] = time.perf_counter() - started
            sample["http_status"][name] = response.status_code
            return checked(response)

        run = None
        try:
            scenario = request(
                "scenario",
                "POST",
                "/api/v1/scenarios",
                json={"name": f"M42 isolated {label} {uuid4().hex}"},
            )["id"]
            sample["scenario_id"] = scenario
            prefix = f"/api/v1/scenarios/{scenario}/imports"
            multipart = [
                ("files", (name, data, "application/octet-stream")) for name, data in files.items()
            ]
            batch = request(
                "upload", "POST", prefix, files=multipart, headers={"Idempotency-Key": uuid4().hex}
            )["id"]
            sample["batch_id"] = batch
            validate = f"{prefix}/{batch}/validation"
            start = time.perf_counter()
            request("validation_submit", "POST", validate, json=CONTEXT)
            report = wait(client, validate, {"VALID", "INVALID", "FAILED"})
            sample["validation_observed_seconds"] = time.perf_counter() - start
            sample["validation_report"] = report
            if report["status"] != "VALID":
                raise RuntimeError(f"unexpected validation status {report['status']}")
            revision = request("publication_with_replay", "POST", f"{prefix}/{batch}/publish")
            sample["revision_id"] = revision["id"]
            if not full_flow:
                return sample
            start = time.perf_counter()
            run = request(
                "planning_submit",
                "POST",
                f"/api/v1/scenarios/{scenario}/revisions/1/runs",
                json={},
                headers={"Idempotency-Key": uuid4().hex},
            )["run_id"]
            ready = wait(client, f"/api/v1/revision-runs/{run}", {"READY", "FAILED"})
            sample["planning_observed_seconds"] = time.perf_counter() - start
            sample["run_id"], sample["run_status"] = run, ready["status"]
            if ready["status"] != "READY":
                raise RuntimeError("planning failed")
            sample["planning_metrics"] = checked(client.get(f"/api/v1/runs/{run}/metrics"))
            manual = []
            for route in ready["result"]["routes"]:
                ids = [step["order_id"] for step in route["steps"] if step.get("order_id")]
                manual.append(
                    {
                        "vehicle_id": route["source_vehicle_id"],
                        "center_id": route["distribution_center_id"],
                        "order_ids": ids,
                    }
                )
            # Release only this sample's reservations before freezing comparison availability.
            request("cancel", "POST", f"/api/v1/revision-runs/{run}/cancel")
            start = time.perf_counter()
            comparison = request(
                "comparison_submit",
                "POST",
                f"/api/v1/scenarios/{scenario}/revisions/1/comparisons",
                json={"manual_routes": manual},
                headers={"Idempotency-Key": uuid4().hex},
            )["comparison_id"]
            result = wait(client, f"/api/v1/comparisons/{comparison}", {"READY", "FAILED"})
            sample["comparison_observed_seconds"] = time.perf_counter() - start
            sample["comparison_id"] = comparison
            sample["comparison_status"] = result["status"]
            sample["comparison_history"] = result["history"]
            if result["status"] != "READY":
                raise RuntimeError("comparison failed")
            for resource, identity in (("runs", run), ("comparisons", comparison)):
                for kind in ("csv", "xlsx"):
                    start = time.perf_counter()
                    export = client.get(f"/api/v1/{resource}/{identity}/exports/{kind}")
                    export.raise_for_status()
                    sample["http_seconds"][f"export_{resource}_{kind}"] = (
                        time.perf_counter() - start
                    )
                    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
                        if archive.testzip() is not None:
                            raise RuntimeError("corrupt export")
                    sample[f"export_{resource}_{kind}"] = {
                        "bytes": len(export.content),
                        "sha256": hashlib.sha256(export.content).hexdigest(),
                    }
        except Exception as exc:
            sample["error"] = type(exc).__name__
        finally:
            if run is not None:
                try:
                    canceled = client.post(f"/api/v1/revision-runs/{run}/cancel")
                    if canceled.status_code not in (200, 409):
                        sample["cleanup_error"] = canceled.status_code
                except httpx.HTTPError as exc:
                    sample["cleanup_error"] = type(exc).__name__
    return sample


def variability(samples: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for key in sorted({k for sample in samples for k in sample["http_seconds"]}):
        values = [
            sample["http_seconds"][key] for sample in samples if key in sample["http_seconds"]
        ]
        output[key] = {
            "n": len(values),
            "min": min(values),
            "median": statistics.median(values),
            "max": max(values),
            "stdev": statistics.stdev(values) if len(values) > 1 else None,
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=41042)
    parser.add_argument("--imports-only", action="store_true")
    args = parser.parse_args()
    if not args.project.startswith("routeops-ci-") or not 3 <= args.repetitions <= 10:
        parser.error("requires isolated routeops-ci-* project and 3-10 repetitions")
    # Do not silently measure the operational API with a disposable project label.
    ports = subprocess.run(
        ["docker", "port", f"{args.project}-backend-1", "8000/tcp"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if args.base_url.rstrip("/") != f"http://{ports}":
        parser.error("base URL must match the isolated project's actual loopback port")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("output already exists; preserve earlier evidence")
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "command": sys.argv,
        "seed": args.seed,
        "machine": platform.platform(),
        "python": platform.python_version(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "httpx",
                "fastapi",
                "SQLAlchemy",
                "psycopg",
                "starlette",
                "python-multipart",
            )
        },
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "logical_cpus": os.cpu_count(),
        "head": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "docker": subprocess.run(
            ["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, check=True
        ).stdout,
        "groups": [],
        "memory_method": (
            "50ms client working set/RSS; docker stats working set + /proc/*/status RSS "
            "for Python services sampled about every 3s; not exact peaks"
        ),
        "tracemalloc_scope": (
            "benchmark client Python allocations only, separate from RSS; excludes services"
        ),
        "unknown_intervals": [
            "validation claim timestamp not exposed",
            "validation active duration not exposed",
            "comparison monotonic processing duration not exposed",
            "final commit acknowledgment not recorded",
        ],
    }
    # Retain only non-sensitive Docker environment summary, never credentials/config.
    info = json.loads(report.pop("docker"))
    report["docker"] = {
        key: info.get(key)
        for key in ("ServerVersion", "NCPU", "MemTotal", "OperatingSystem", "Architecture")
    }
    report["images"] = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={args.project}",
            "--format",
            "{{.Names}}|{{.Image}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    with tempfile.TemporaryDirectory(prefix="routeops-m42-") as temp:
        root = Path(temp)
        cases = (
            (
                ("B2B", "small", "csv", 1),
                ("B2C", "small", "xlsx", 1),
                ("B2B", "medium", "xlsx", 1),
                ("B2C", "medium", "csv", 2),
                ("B2B", "bounded", "csv", 2),
                ("B2C", "bounded", "xlsx", 2),
            )
            if not args.imports_only
            else (
                ("B2B", "inventory-1000", "csv", 1),
                ("B2C", "inventory-1000", "xlsx", 1),
                ("B2B", "inventory-10000", "csv", 1),
                ("B2C", "inventory-10000", "xlsx", 1),
            )
        )
        for model, profile, kind, concurrency in cases:
            folder = root / f"{model}-{profile}-{kind}"
            folder.mkdir()
            started = time.perf_counter()
            files, rows = payloads(folder, model, profile, kind, args.seed)
            group: dict[str, Any] = {
                "model": model,
                "profile": profile,
                "format": kind,
                "concurrency": concurrency,
                "preparation_seconds": time.perf_counter() - started,
                "rows": {k: len(v) for k, v in rows.items()},
                "file_bytes": {k: len(v) for k, v in files.items()},
                "file_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
                "warmup": one(
                    args.base_url, files, rows, "warmup", full_flow=not args.imports_only
                ),
                "samples": [],
            }
            report["groups"].append(group)
            tracemalloc.start()
            with MemorySampler(args.project) as memory:
                for repetition in range(args.repetitions):
                    with ThreadPoolExecutor(max_workers=concurrency) as pool:
                        futures = [
                            pool.submit(
                                one,
                                args.base_url,
                                files,
                                rows,
                                f"{model}-{profile}-{repetition}",
                                full_flow=not args.imports_only,
                            )
                            for _ in range(concurrency)
                        ]
                        group["samples"].extend(future.result() for future in futures)
                    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            group.update(
                memory_samples=memory.samples,
                memory_errors=memory.errors,
                client_sampled_rss_peak_bytes=memory.client_peak,
                client_tracemalloc_peak_bytes=peak,
                variability=variability(group["samples"]),
            )
            print(
                json.dumps(
                    {
                        "model": model,
                        "profile": profile,
                        "format": kind,
                        "samples": len(group["samples"]),
                        "errors": sum(s["error"] is not None for s in group["samples"]),
                    }
                ),
                flush=True,
            )
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["finished_at"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return int(
        any(
            g["warmup"]["error"]
            or g["memory_errors"]
            or any(s["error"] or s.get("cleanup_error") for s in g["samples"])
            for g in report["groups"]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
