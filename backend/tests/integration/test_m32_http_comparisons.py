"""Full comparison HTTP flow on fresh scenarios; no operational runs or reservations."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from test_m26_http_acceptance import _client, _csv_files, _publish, _scenario, _wait, _xlsx_file

from routeops.infrastructure.data.allocation_demos import allocation_demos
from routeops.infrastructure.data.demo_catalog import demo_rows
from routeops.infrastructure.data.operation_cases import case_rows

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name", ["b2b-feasible", "b2c-task-pressure", "shared_stock_restricted"])
def test_http_manual_and_policy_comparison_idempotent_and_independent(name):
    rows = (
        demo_rows(allocation_demos()[name])
        if name == "shared_stock_restricted"
        else case_rows(name)
    )
    with _client() as client:
        scenario = _scenario(client, f"M32-comparison-{name}")
        files = _xlsx_file(rows) if name == "b2b-feasible" else _csv_files(rows)
        _publish(client, scenario, files, uuid4().hex)
        v = rows["vehicles"][0]
        body = {
            "manual_routes": [
                {
                    "vehicle_id": v["vehicle_id"],
                    "center_id": v["distribution_center_id"],
                    "order_ids": [o["order_id"] for o in rows["orders"]],
                }
            ]
        }
        key = uuid4().hex
        endpoint = f"/api/v1/scenarios/{scenario}/revisions/1/comparisons"
        gate = Barrier(2)

        def post():
            with _client() as other:
                gate.wait()
                response = other.post(endpoint, headers={"Idempotency-Key": key}, json=body)
                response.raise_for_status()
                return response

        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: post(), range(2)))
        assert sorted(r.status_code for r in responses) == [200, 202]
        ids = {r.json()["comparison_id"] for r in responses}
        assert len(ids) == 1
        comparison = next(iter(ids))
        path = f"/api/v1/comparisons/{comparison}"
        final = _wait(client, path, {"READY", "FAILED"})
        assert final["status"] == "READY", final["history"]
        assert "owner_token" not in client.get(path).text
        assert (
            len({p["inventory_initial_sha256"] for p in final["result"]["alternatives"].values()})
            == 1
        )
        if name == "shared_stock_restricted":
            plans = final["result"]["alternatives"]
            assert plans["greedy-v1"]["routed_order_ids"] == ["ORD-FLEX"]
            assert plans["alternatives-v2"]["routed_order_ids"] == ["ORD-FLEX", "ORD-RESTRICTED"]
        else:
            counts = [
                len(final["result"]["alternatives"][p]["routed_order_ids"])
                for p in ("greedy-v1", "alternatives-v2")
            ]
            assert counts == [2, 2]
        # Recover a deliberately forgotten first response by resubmitting its same key.
        duplicate = client.post(endpoint, headers={"Idempotency-Key": key}, json=body)
        assert duplicate.status_code == 200 and duplicate.json() == final
        conflict = client.post(
            endpoint, headers={"Idempotency-Key": key}, json={"manual_routes": []}
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["code"] == "COMPARISON_IDEMPOTENCY_CONFLICT"
        )
        listing = client.get(f"/api/v1/scenarios/{scenario}/comparisons?limit=1").json()
        assert listing["total"] == 1 and listing["items"][0]["comparison_id"] == comparison
        operational = client.get(f"/api/v1/scenarios/{scenario}/revision-runs")
        operational.raise_for_status()
        assert operational.json() == {"items": [], "next_offset": None}
        assert final["context"]["simulation_only"] is True
