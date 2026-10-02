from __future__ import annotations

import csv
import io
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from uuid import uuid4

import httpx
import pytest

from routeops.application.import_contract import DATASETS, SCHEMA
from routeops.application.import_templates import csv_template, xlsx_template

pytestmark = pytest.mark.integration


def test_real_stack_routes_and_persists_demo() -> None:
    base_url = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not base_url:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL to run against Compose")

    with httpx.Client(base_url=base_url, timeout=60) as client:
        dependencies = client.get("/health/dependencies")
        dependencies.raise_for_status()
        assert dependencies.json()["status"] == "ready"

        response = client.post("/api/v1/demo/runs", json={"solution_quality": "BALANCED"})
        response.raise_for_status()
        run = response.json()

        persisted = client.get(f"/api/v1/runs/{run['run_id']}")
        persisted.raise_for_status()
        cost_response = client.get(f"/api/v1/runs/{run['run_id']}/estimated-operating-cost")
        cost_response.raise_for_status()
        cost = cost_response.json()
        assert cost["calculation_version"] == "operating-cost-v1"
        assert cost["provenance"]["rate_source"] == "persisted_demo_rate_snapshot"
        assert len(cost["routes"]) == len(run["result"]["routes"])
        assert cost["solver_objective"]["units"] == run["result"]["summary"]["objective_cost_units"]
        assert client.get(f"/api/v1/runs/{run['run_id']}/estimated-operating-cost").json() == cost
        metrics_response = client.get(f"/api/v1/runs/{run['run_id']}/metrics")
        metrics_response.raise_for_status()
        report = metrics_response.json()
        metrics = report["plan"]["metrics"]
        assert metrics["valid_input_orders"]["value"] == 5
        assert metrics["allocated_orders"]["value"] == 4
        assert metrics["routed_orders"]["value"] == 4
        assert metrics["unrouted_orders"]["value"] == 1
        assert metrics["coverage"]["value"] == "0.8"
        assert metrics["vehicles_used"]["value"] == 2
        assert report["plan"]["operating_cost"] == cost
        assert report["processing"]["initial_queue"]["value"] == "0"
        assert report["processing"]["active_all_attempts"]["value"] is not None
        assert client.get(f"/api/v1/runs/{run['run_id']}/metrics").json() == report

    assert run["status"] in {"SUCCEEDED", "PARTIAL"}
    assert run["kpis"]["routes"] >= 1
    assert run["kpis"]["assigned_orders"] >= 1
    assert run["kpis"]["unassigned_orders"] >= 1
    assert sum(len(route["geometry"]) for route in run["result"]["routes"]) >= 2
    summary = run["result"]["summary"]
    kpis = run["kpis"]
    assert kpis["routes"] == summary["route_count"]
    assert kpis["vehicles_used"] == len({route["vehicle_id"] for route in run["result"]["routes"]})
    assert kpis["distance_km"] == round(summary["distance_meters"] / 1000, 3)
    assert kpis["driving_hours"] == round(summary["driving_seconds"] / 3600, 3)
    assert kpis["service_hours"] == round(summary["service_seconds"] / 3600, 3)
    assert kpis["waiting_hours"] == round(summary["waiting_seconds"] / 3600, 3)
    assert kpis["total_hours"] == round(summary["total_duration_seconds"] / 3600, 3)
    assert kpis["solver_time_ms"] == run["result"]["solver"]["solve_duration_ms"]
    assert persisted.json() == run


def test_real_stack_private_provisional_upload() -> None:
    base_url = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not base_url:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL to run against Compose")
    with httpx.Client(base_url=base_url, timeout=60) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Upload integration"})
        scenario.raise_for_status()
        scenario_id = scenario.json()["id"]
        url = f"/api/v1/scenarios/{scenario_id}/imports"
        files = [("files", (f"{name}.csv", csv_template(name), "text/csv")) for name in DATASETS]
        key = uuid4().hex
        first = client.post(url, files=files, headers={"Idempotency-Key": key})
        assert first.status_code == 201
        payload = first.json()
        assert payload["status"] == "RECEIVED"
        assert len(payload["files"]) == 5
        assert all("storage_key" not in file for file in payload["files"])
        read_back = client.get(f"{url}/{payload['id']}")
        assert read_back.status_code == 200
        assert read_back.json() == payload
        retry = client.post(url, files=files, headers={"Idempotency-Key": key})
        assert retry.status_code == 200
        assert retry.json() == payload
        conflict = client.post(
            url,
            files=[("files", ("book.xlsx", xlsx_template(), "application/octet-stream"))],
            headers={"Idempotency-Key": key},
        )
        assert conflict.status_code == 409
        workbook = client.post(
            url,
            files=[("files", ("book.xlsx", xlsx_template(), "application/octet-stream"))],
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert workbook.status_code == 201
        assert [file["dataset"] for file in workbook.json()["files"]] == ["workbook"]


@pytest.mark.parametrize("workbook", [False, True])
def test_real_stack_contextual_validation(workbook: bool) -> None:
    base_url = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not base_url:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL to run against Compose")
    with httpx.Client(base_url=base_url, timeout=60) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Validation integration"})
        scenario.raise_for_status()
        scenario_id = scenario.json()["id"]
        url = f"/api/v1/scenarios/{scenario_id}/imports"
        files = (
            [("files", ("book.xlsx", xlsx_template(), "application/octet-stream"))]
            if workbook
            else [("files", (f"{name}.csv", csv_template(name), "text/csv")) for name in DATASETS]
        )
        uploaded = client.post(url, files=files, headers={"Idempotency-Key": uuid4().hex})
        assert uploaded.status_code == 201
        validation_url = f"{url}/{uploaded.json()['id']}/validation"
        context = {
            "planning_date": "2026-10-15",
            "horizon_start_at": "2026-10-15T08:00:00-03:00",
            "horizon_end_at": "2026-10-15T20:00:00-03:00",
            "timezone_iana": "America/Santiago",
            "currency": "CLP",
        }
        started = client.post(validation_url, json=context)
        assert started.status_code in (200, 202)
        assert started.json()["status"] in ("VALIDATING", "VALID")
        assert client.post(validation_url, json=context).status_code in (200, 202)
        changed = client.post(validation_url, json={**context, "currency": "USD"})
        assert changed.status_code == 409
        assert changed.json()["code"] == "VALIDATION_CONTEXT_CONFLICT"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get(validation_url)
            status.raise_for_status()
            if status.json()["status"] != "VALIDATING":
                break
            time.sleep(0.25)
        assert status.json()["status"] == "VALID"
        assert status.json()["report"]["valid"]
        assert (
            "atomic_publication_and_revision_constraints"
            in status.json()["report"]["deferred_rules"]
        )
        issues = client.get(f"{validation_url}/issues?limit=2")
        issues.raise_for_status()
        assert issues.json() == {"items": [], "next_after": None}
        assert "storage_key" not in status.text


def test_real_stack_publishes_and_reads_partial_dataset_revision() -> None:
    base_url = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not base_url:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL to run against Compose")
    values = {
        "distribution_centers": {
            "distribution_center_id": "CD-001",
            "name": "Centro",
            "latitude": "-33.44",
            "longitude": "-70.64",
            "operating_start": "08:00",
            "operating_end": "18:00",
        },
        "inventory": {
            "snapshot_at": "2026-10-15T09:00:00-03:00",
            "distribution_center_id": "CD-001",
            "sku": "SKU-1",
            "on_hand_quantity": "10",
            "externally_reserved_quantity": "2",
            "safety_stock_quantity": "1",
        },
    }
    files = []
    for name in DATASETS:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=[field.name for field in SCHEMA[name]])
        writer.writeheader()
        if name in values:
            writer.writerow(values[name])
        files.append(("files", (f"{name}.csv", stream.getvalue().encode(), "text/csv")))
    with httpx.Client(base_url=base_url, timeout=60) as client:
        scenario = client.post("/api/v1/scenarios", json={"name": "Publication integration"})
        scenario.raise_for_status()
        scenario_id = scenario.json()["id"]
        base = f"/api/v1/scenarios/{scenario_id}"
        scenarios = client.get("/api/v1/scenarios?limit=100")
        assert scenarios.status_code == 200
        assert any(item["id"] == scenario_id for item in scenarios.json()["items"])
        for name in DATASETS:
            template = client.get(f"/api/v1/import-templates/{name}")
            assert template.status_code == 200
            assert template.content == csv_template(name)
        workbook = client.get("/api/v1/import-templates/workbook")
        assert workbook.status_code == 200
        with zipfile.ZipFile(io.BytesIO(workbook.content)) as template:
            manifest = ET.fromstring(template.read("xl/workbook.xml"))
            sheet_names = [sheet.get("name") for sheet in manifest.findall(".//{*}sheet")]
            assert sheet_names == list(DATASETS)
            for index, name in enumerate(DATASETS, start=1):
                sheet = ET.fromstring(template.read(f"xl/worksheets/sheet{index}.xml"))
                expected = tuple(field.name for field in SCHEMA[name])
                assert tuple(value.text for value in sheet.findall(".//{*}t")) == expected
        assert client.get("/api/v1/import-templates/other").status_code == 404
        assert client.get("/api/v1/scenarios?limit=0").status_code == 422
        key = uuid4().hex
        uploaded = client.post(f"{base}/imports", files=files, headers={"Idempotency-Key": key})
        assert uploaded.status_code == 201
        batch_id = uploaded.json()["id"]
        assert client.get(f"{base}/imports/lookup", params={"key": key}).json()["id"] == batch_id
        assert client.get(f"{base}/imports/lookup", params={"key": uuid4().hex}).status_code == 404
        assert any(item["id"] == batch_id for item in client.get(f"{base}/imports").json()["items"])
        assert client.get(f"{base}/imports/{batch_id}/publication").status_code == 404
        validation_url = f"{base}/imports/{batch_id}/validation"
        started = client.post(
            validation_url,
            json={
                "planning_date": "2026-10-15",
                "horizon_start_at": "2026-10-15T08:00:00-03:00",
                "horizon_end_at": "2026-10-15T20:00:00-03:00",
                "timezone_iana": "America/Santiago",
                "currency": "CLP",
            },
        )
        assert started.status_code in (200, 202)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get(validation_url)
            status.raise_for_status()
            if status.json()["status"] != "VALIDATING":
                break
            time.sleep(0.25)
        assert status.json()["status"] == "VALID"
        publication = client.post(f"{base}/imports/{batch_id}/publish")
        assert publication.status_code == 201, publication.text
        revision = publication.json()
        assert revision["revision_no"] == 1
        repeated = client.post(f"{base}/imports/{batch_id}/publish")
        assert repeated.status_code == 200 and repeated.json() == revision
        assert client.get(f"{base}/imports/{batch_id}/publication").json() == revision
        assert client.get(validation_url).json()["report"]["valid"] is True
        listed = client.get(f"{base}/revisions?limit=1")
        assert listed.status_code == 200
        assert listed.json() == {"items": [revision], "next_after": None}
        detail = client.get(f"{base}/revisions/1")
        assert detail.status_code == 200
        assert detail.json()["counts"]["inventory"]["accepted"] == 1
        assert detail.json()["counts"]["orders"]["accepted"] == 0
        assert detail.json()["snapshot_at"] is not None
        assert "storage_key" not in detail.text
