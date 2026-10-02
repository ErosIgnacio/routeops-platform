"""Real HTTP import -> publication -> planning -> persisted diagnoses/cost/KPIs."""

from uuid import uuid4

import pytest
from test_m26_http_acceptance import _client, _publish, _scenario, _submit, _wait

from routeops.infrastructure.data.operation_cases import CASE_NAMES, case_payloads

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name", CASE_NAMES)
def test_http_new_operation_case_preserves_diagnostics_after_review(name: str) -> None:
    workbook = name in ("b2b-feasible", "b2c-task-pressure")
    payloads = case_payloads(name, workbook=workbook)
    mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if workbook
        else "text/csv"
    )
    files = [("files", (filename, content, mime)) for filename, content in payloads.items()]
    with _client() as client:
        assert client.get("/api/v1/operation-cases").json() == list(CASE_NAMES)
        scenario = _scenario(client, f"M31-{name}")
        batch, _ = _publish(client, scenario, files, uuid4().hex)
        validation = client.get(f"/api/v1/scenarios/{scenario}/imports/{batch}/diagnostics").json()
        assert validation["items"] == []
        assert validation["provenance"]["stage"] == "VALIDATION"
        key = uuid4().hex
        submitted = _submit(client, scenario, key)
        run_id = str(submitted["run_id"])
        path = f"/api/v1/revision-runs/{run_id}"
        ready = _wait(client, path, {"READY", "FAILED"})
        assert ready["status"] == "READY", ready
        diag_response = client.get(f"/api/v1/runs/{run_id}/diagnostics")
        diag_response.raise_for_status()
        diagnostics = diag_response.json()
        assert diagnostics["persistence"] == "immutable_document"
        metrics = client.get(f"/api/v1/runs/{run_id}/metrics").json()
        cost = client.get(f"/api/v1/runs/{run_id}/estimated-operating-cost").json()
        assert metrics["plan"]["operating_cost"] == cost
        assert metrics["processing"]["total_elapsed"]["classification"] == "PARTIAL"
        assert metrics["processing"]["durable_total_elapsed"]["value"] is None
        routed = {
            step["order_id"]
            for route in ready["result"]["routes"]
            for step in route["steps"]
            if step["kind"] == "DELIVERY"
        }
        assert metrics["plan"]["metrics"]["routed_orders"]["value"] == len(routed)
        assert (
            len(routed)
            == {
                "b2b-feasible": 2,
                "b2b-diagnostics": 1,
                "b2c-feasible": 6,
                "b2c-task-pressure": 2,
                "b2c-distance-inferred": 0,
            }[name]
        )
        for item in diagnostics["items"]:
            record = next(
                entry
                for entry in ready["result"]["unassigned"]
                if entry["order_id"] == item["order_id"]
            )
            assert any(
                item["code"] == reason["code"]
                and item["evidence"] == reason["evidence"]
                and item["certainty"] == reason["certainty"]
                for reason in record["reasons"]
            )
        if name == "b2c-task-pressure":
            assert all(
                item["certainty"] == "INFERRED"
                for item in diagnostics["items"]
                if item["role"] == "PRIMARY"
            )
        if diagnostics["items"]:
            first = client.get(f"/api/v1/runs/{run_id}/diagnostics", params={"limit": 1}).json()
            assert len(first["items"]) == 1 and first["next_offset"] == 1
        retried = client.post(
            f"/api/v1/scenarios/{scenario}/revisions/1/runs",
            json={},
            headers={"Idempotency-Key": key},
        )
        assert retried.status_code == 200 and retried.json()["run_id"] == run_id
        action = "accept" if name == "b2b-feasible" else "cancel"
        reviewed = client.post(f"{path}/{action}")
        reviewed.raise_for_status()
        target = "CONFIRMED" if action == "accept" else "RELEASED"
        assert all(
            decision["reservation_status"] == target
            for decision in reviewed.json()["decisions"]
            if decision["order_id"] in routed
        )
        after = client.get(f"/api/v1/runs/{run_id}/diagnostics").json()
        assert after["items"] == diagnostics["items"]
        assert after["document_sha256"] == diagnostics["document_sha256"]
        assert client.get(f"/api/v1/runs/{run_id}/metrics").json()["plan"] == metrics["plan"]


def test_http_prepare_operation_case_keeps_prior_demo_catalog() -> None:
    with _client() as client:
        original_catalog = client.get("/api/v1/allocation-demos").json()
        assert len(original_catalog) == 5
        results = [client.post("/api/v1/operation-cases/b2b-feasible/prepare") for _ in range(2)]
        assert all(response.status_code == 200 for response in results)
        assert results[0].json()["scenario_id"] != results[1].json()["scenario_id"]
        assert client.get("/api/v1/allocation-demos").json() == original_catalog
        assert client.post("/api/v1/operation-cases/unknown/prepare").status_code == 404
