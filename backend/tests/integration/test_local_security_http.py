"""Real HTTP boundary checks on the isolated stack selected by CI."""

import os
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_browser_write_boundary_and_body_limit_before_database_side_effects():
    base = os.getenv("ROUTEOPS_INTEGRATION_BASE_URL")
    if not base:
        pytest.skip("set ROUTEOPS_INTEGRATION_BASE_URL for real HTTP checks")
    label = f"M42 blocked browser {uuid4().hex}"
    with httpx.Client(base_url=base, timeout=20) as client:
        foreign = client.post(
            "/api/v1/scenarios", json={"name": label}, headers={"Origin": "https://foreign.example"}
        )
        assert foreign.status_code == 403 and foreign.json()["code"] == "ORIGIN_NOT_ALLOWED"
        assert client.get("/health/live", headers={"Host": "rebound.example"}).status_code == 400
        invalid = client.post("/api/v1/scenarios", json={"name": {"private": "private-token"}})
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "REQUEST_VALIDATION_FAILED"
        assert "private-token" not in invalid.text
        assert invalid.json()["errors"][0]["loc"] == ["body", "name"]
        assert (
            client.post(
                "/api/v1/scenarios",
                content=b"x" * 1_048_577,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        assert label not in {
            item["name"] for item in client.get("/api/v1/scenarios?limit=100").json()["items"]
        }
        created = client.post(
            "/api/v1/scenarios",
            json={"name": f"M42 allowed upload {uuid4().hex}"},
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert created.status_code == 201
        scenario = created.json()["id"]
        prefix = f"/api/v1/scenarios/{scenario}/imports"
        malformed = client.post(
            prefix,
            content=(
                b'--abc\r\nContent-Disposition: form-data; name="files"; filename="orders.csv"\r\n'
                b"X-Long: " + b"x" * 3000 + b"\r\n\r\nx\r\n--abc--\r\n"
            ),
            headers={
                "Content-Type": "multipart/form-data; boundary=abc",
                "Idempotency-Key": uuid4().hex,
            },
        )
        assert malformed.status_code == 413
        assert malformed.json()["code"] == "MULTIPART_HEADER_LIMIT"
        empty = client.get(prefix)
        empty.raise_for_status()
        assert empty.json()["items"] == [] and empty.json()["next_offset"] is None
        live = client.get("/health/live", headers={"X-Request-ID": "invalid/request/id"})
        assert live.status_code == 200
        assert live.headers["X-Request-ID"] != "invalid/request/id"
