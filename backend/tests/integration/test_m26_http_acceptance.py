"""HTTP acceptance against the local Compose stack with identifiable scenarios."""

from __future__ import annotations

import csv
import io
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from xml.sax.saxutils import escape

import httpx
import pytest

from routeops.application.import_contract import DATASETS, SCHEMA
from routeops.application.import_templates import xlsx_template
from routeops.infrastructure.data.allocation_demos import allocation_demos
from routeops.infrastructure.data.demo_catalog import demo_rows

pytestmark = pytest.mark.integration
CONTEXT = {
    "planning_date": "2026-10-15",
    "horizon_start_at": "2026-10-15T08:00:00-03:00",
    "horizon_end_at": "2026-10-15T18:00:00-03:00",
    "timezone_iana": "America/Santiago",
    "currency": "CLP",
}


def _base_url() -> str:
    result = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not result:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL for HTTP acceptance")
    return result


def _client() -> httpx.Client:
    return httpx.Client(base_url=_base_url(), timeout=90)


def _scenario(client: httpx.Client, label: str) -> str:
    response = client.post(
        "/api/v1/scenarios", json={"name": f"M26 acceptance {label} {uuid4().hex[:8]}"}
    )
    response.raise_for_status()
    return str(response.json()["id"])


def _csv_files(
    rows: dict[str, list[dict[str, str]]], *, missing_order_header: bool = False
) -> list[tuple[str, tuple[str, bytes, str]]]:
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for name in DATASETS:
        stream = io.StringIO(newline="")
        headers = [field.name for field in SCHEMA[name]]
        if missing_order_header and name == "orders":
            headers.remove("order_id")
        writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows[name])
        files.append(("files", (f"{name}.csv", stream.getvalue().encode(), "text/csv")))
    return files


def _xlsx_file(rows: dict[str, list[dict[str, str]]]) -> list[tuple[str, tuple[str, bytes, str]]]:
    source = io.BytesIO(xlsx_template())
    result = io.BytesIO()
    with zipfile.ZipFile(source) as template, zipfile.ZipFile(result, "w") as workbook:
        for entry in template.infolist():
            dataset = next(
                (
                    name
                    for index, name in enumerate(DATASETS, start=1)
                    if entry.filename == f"xl/worksheets/sheet{index}.xml"
                ),
                None,
            )
            if dataset is None:
                workbook.writestr(entry, template.read(entry.filename))
                continue
            all_rows = [
                {field.name: field.name for field in SCHEMA[dataset]},
                *rows[dataset],
            ]
            xml_rows: list[str] = []
            for row_number, values in enumerate(all_rows, start=1):
                cells = "".join(
                    f'<c r="{chr(65 + index)}{row_number}" t="inlineStr">'
                    f"<is><t>{escape(values.get(field.name, ''))}</t></is></c>"
                    for index, field in enumerate(SCHEMA[dataset])
                )
                xml_rows.append(f'<row r="{row_number}">{cells}</row>')
            workbook.writestr(
                entry,
                '<?xml version="1.0" encoding="utf-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{''.join(xml_rows)}</sheetData></worksheet>",
            )
    return [
        (
            "files",
            (
                "acceptance.xlsx",
                result.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        )
    ]


def _wait(client: httpx.Client, path: str, terminal: set[str]) -> dict[str, object]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = client.get(path)
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in terminal:
            return payload
        time.sleep(0.25)
    pytest.fail(f"timed out waiting for {path}")


def _publish(
    client: httpx.Client,
    scenario: str,
    files: list[tuple[str, tuple[str, bytes, str]]],
    key: str,
) -> tuple[str, dict[str, object]]:
    path = f"/api/v1/scenarios/{scenario}/imports"
    uploaded = client.post(path, files=files, headers={"Idempotency-Key": key})
    assert uploaded.status_code == 201, uploaded.text
    batch = str(uploaded.json()["id"])
    validation = f"{path}/{batch}/validation"
    assert client.post(validation, json=CONTEXT).status_code in (200, 202)
    report = _wait(client, validation, {"VALID", "INVALID", "FAILED"})
    assert report["status"] == "VALID", report
    assert client.get(f"{validation}/issues?limit=1").json()["items"] == []
    published = client.post(f"{path}/{batch}/publish")
    assert published.status_code == 201, published.text
    revision = published.json()
    retry = client.post(f"{path}/{batch}/publish")
    assert retry.status_code == 200 and retry.json() == revision
    return batch, revision


def _submit(client: httpx.Client, scenario: str, key: str) -> dict[str, object]:
    response = client.post(
        f"/api/v1/scenarios/{scenario}/revisions/1/runs",
        json={},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.parametrize("format_name", ["csv", "xlsx"])
def test_http_import_to_planning_accept_or_cancel(format_name: str) -> None:
    rows = demo_rows(allocation_demos()["choice_between_centers"])
    files = _csv_files(rows) if format_name == "csv" else _xlsx_file(rows)
    with _client() as client:
        scenario = _scenario(client, f"{format_name}-flow")
        batch, revision = _publish(client, scenario, files, uuid4().hex)
        assert revision["revision_no"] == 1
        assert (
            client.get(f"/api/v1/scenarios/{scenario}/imports/{batch}/publication").json()
            == revision
        )
        run_key = uuid4().hex
        queued = _submit(client, scenario, run_key)
        run_id = str(queued["run_id"])
        ready = _wait(client, f"/api/v1/revision-runs/{run_id}", {"READY", "FAILED"})
        assert ready["status"] == "READY", ready
        assert ready["result"]["routes"]  # type: ignore[index]
        assert ready["decisions"][0]["center_id"] in {"CD-A", "CD-B"}  # type: ignore[index]
        assert ready["decisions"][0]["reservation_status"] == "HELD"  # type: ignore[index]
        assert ready["inventory_snapshot_id"]
        action = "accept" if format_name == "csv" else "cancel"
        changed = client.post(f"/api/v1/revision-runs/{run_id}/{action}")
        assert changed.status_code == 200
        target = "CONFIRMED" if action == "accept" else "RELEASED"
        assert changed.json()["decisions"][0]["reservation_status"] == target
        assert client.post(f"/api/v1/revision-runs/{run_id}/{action}").status_code == 200
        lookup = client.get(
            f"/api/v1/scenarios/{scenario}/revisions/1/runs/lookup",
            params={"key": run_key},
        )
        assert lookup.status_code == 200
        assert lookup.json()["run_id"] == run_id
        history = client.get(f"/api/v1/scenarios/{scenario}/revision-runs")
        assert history.status_code == 200
        assert history.json()["items"][0]["run_id"] == run_id


def test_http_invalid_report_and_concurrent_idempotency() -> None:
    rows = demo_rows(allocation_demos()["choice_between_centers"])
    files = _csv_files(rows)
    with _client() as client:
        scenario = _scenario(client, "concurrent-upload")
    path = f"/api/v1/scenarios/{scenario}/imports"
    key = uuid4().hex
    barrier = Barrier(2)

    def concurrent_upload() -> httpx.Response:
        with _client() as client:
            barrier.wait(timeout=10)
            return client.post(path, files=files, headers={"Idempotency-Key": key})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(concurrent_upload) for _ in range(2)]
        results = [future.result(timeout=40) for future in futures]
    assert sorted(item.status_code for item in results) == [200, 201]
    batch = results[0].json()["id"]
    assert results[1].json()["id"] == batch
    changed = _csv_files(
        {**rows, "orders": [{**rows["orders"][0], "customer_reference": "changed"}]}
    )
    with _client() as client:
        conflict = client.post(path, files=changed, headers={"Idempotency-Key": key})
        assert conflict.status_code == 409
        assert len(client.get(path).json()["items"]) == 1
        invalid = client.post(
            path,
            files=_csv_files(rows, missing_order_header=True),
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert invalid.status_code == 201
        validation = f"{path}/{invalid.json()['id']}/validation"
        assert client.post(validation, json=CONTEXT).status_code in (200, 202)
        report = _wait(client, validation, {"VALID", "INVALID", "FAILED"})
        assert report["status"] == "INVALID"
        issues = client.get(f"{validation}/issues?limit=1")
        assert issues.status_code == 200
        assert issues.json()["items"][0]["code"] == "HEADER_MISSING"
        assert client.post(f"{path}/{invalid.json()['id']}/publish").status_code == 409


def test_http_concurrent_runs_and_accept_cancel_race() -> None:
    rows = demo_rows(allocation_demos()["choice_between_centers"])
    rows["inventory"] = [
        {**row, "on_hand_quantity": "2"}
        for row in rows["inventory"]
        if row["distribution_center_id"] == "CD-A"
    ]
    with _client() as client:
        scenario = _scenario(client, "stock-race")
        _publish(client, scenario, _csv_files(rows), uuid4().hex)
    run_path = f"/api/v1/scenarios/{scenario}/revisions/1/runs"
    key = uuid4().hex
    barrier = Barrier(2)

    def concurrent_submit(client_key: str) -> httpx.Response:
        with _client() as client:
            barrier.wait(timeout=10)
            return client.post(run_path, json={}, headers={"Idempotency-Key": client_key})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(concurrent_submit, key) for _ in range(2)]
        repeated = [future.result(timeout=40) for future in futures]
    assert sorted(item.status_code for item in repeated) == [200, 202]
    assert repeated[0].json()["run_id"] == repeated[1].json()["run_id"]
    first_id = repeated[0].json()["run_id"]
    with _client() as client:
        conflict = client.post(
            run_path,
            json={"allocation_policy": "greedy-v1"},
            headers={"Idempotency-Key": key},
        )
        assert conflict.status_code == 409
        second_id = _submit(client, scenario, uuid4().hex)["run_id"]
        outcomes = [
            _wait(client, f"/api/v1/revision-runs/{item}", {"READY", "FAILED"})
            for item in (first_id, second_id)
        ]
        assert all(item["status"] == "READY" for item in outcomes)
        assert sorted(len(item["result"]["routes"]) for item in outcomes) == [0, 1]  # type: ignore[index]
        winner = next(item for item in outcomes if item["result"]["routes"])  # type: ignore[index]
        loser = next(item for item in outcomes if not item["result"]["routes"])  # type: ignore[index]
        assert loser["decisions"][0]["reason_code"] == "STOCK_NO_FULL_COVERAGE"  # type: ignore[index]
    winner_id = str(winner["run_id"])
    barrier = Barrier(2)

    def transition(action: str) -> httpx.Response:
        with _client() as client:
            barrier.wait(timeout=10)
            return client.post(f"/api/v1/revision-runs/{winner_id}/{action}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(transition, action) for action in ("accept", "cancel")]
        decisions = [future.result(timeout=40) for future in futures]
    assert sorted(item.status_code for item in decisions) == [200, 409]
    with _client() as client:
        final = client.get(f"/api/v1/revision-runs/{winner_id}").json()
        expected = "CONFIRMED" if final["status"] == "ACCEPTED" else "RELEASED"
        assert final["decisions"][0]["reservation_status"] == expected


def test_http_four_independent_demos_and_original_regression() -> None:
    with _client() as client:
        catalog = client.get("/api/v1/allocation-demos")
        catalog.raise_for_status()
        assert {item["id"] for item in catalog.json()} == {
            "original",
            "exclusive_stock",
            "choice_between_centers",
            "shared_stock_restricted",
            "fleet_restrictions",
        }
        ids: set[str] = set()
        for name in (
            "exclusive_stock",
            "choice_between_centers",
            "shared_stock_restricted",
            "fleet_restrictions",
        ):
            prepared = client.post(f"/api/v1/allocation-demos/{name}/prepare")
            assert prepared.status_code == 200, prepared.text
            scenario = prepared.json()["scenario_id"]
            assert scenario not in ids
            ids.add(scenario)
            queued = _submit(client, scenario, uuid4().hex)
            result = _wait(client, f"/api/v1/revision-runs/{queued['run_id']}", {"READY", "FAILED"})
            assert result["status"] == "READY", result
            decisions = result["decisions"]
            if name == "exclusive_stock":
                assert [item["center_id"] for item in decisions] == ["CD-A", "CD-B", None]  # type: ignore[union-attr]
            elif name == "shared_stock_restricted":
                assert [item["center_id"] for item in decisions] == ["CD-B", "CD-A"]  # type: ignore[union-attr]
            elif name == "fleet_restrictions":
                assert all(item["reason_code"] == "NO_COMPATIBLE_VEHICLE" for item in decisions)  # type: ignore[union-attr]
            client.post(f"/api/v1/revision-runs/{queued['run_id']}/cancel").raise_for_status()
        original = client.post("/api/v1/demo/runs", json={})
        original.raise_for_status()
        ord003 = next(
            item
            for item in original.json()["result"]["unassigned"]
            if item["order_id"] == "ORD-003"
        )
        assert ord003["reasons"][0]["code"] == "STOCK_NO_FULL_COVERAGE"
        assert ord003["reasons"][0]["evidence"]["required"]["SKU-C"] == 20
