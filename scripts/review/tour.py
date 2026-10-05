"""Run or verify bounded synthetic portfolio tours through the public local API."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4


def request(
    base: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(base + path, data=body, headers=headers or {}),
            timeout=60,
        ) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"API {path} returned HTTP {exc.code}: {exc.read(4096).decode('utf-8')}"
        ) from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", choices=("b2b", "b2c"))
    parser.add_argument("--package", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-report", type=Path)
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username
        or parsed.password
    ):
        parser.error("Use an explicit loopback HTTP API without credentials")
    base = args.base_url.rstrip("/")

    def api(path: str, body: object | None = None, key: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        return json.loads(
            request(
                base,
                path,
                json.dumps(body).encode() if body is not None else None,
                headers,
            )
        )

    def wait(path: str, states: set[str]) -> dict:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = api(path)
            if result["status"] in states:
                return result
            time.sleep(1)
        raise RuntimeError("Worker did not reach a recorded terminal state within 180s")

    if args.verify_report:
        report = json.loads(args.verify_report.read_text(encoding="utf-8"))
        for path, expected in report["readback"].items():
            if api(path) != expected:
                raise RuntimeError(f"Restored/restarted resource differs: {path}")
        for artifact in report["exports"]:
            if (
                hashlib.sha256(request(base, artifact["path"])).hexdigest()
                != artifact["sha256"]
            ):
                raise RuntimeError("Export changed across backup/restart")
        print(
            f"Verified {len(report['readback'])} exact API resources and {len(report['exports'])} export hashes; no writes"
        )
        return
    if args.model is None or args.package is None or args.output is None:
        parser.error("Tour requires --model, --package and --output")
    if args.output.exists():
        parser.error("Evidence destination already exists; refusing to overwrite")
    args.output.mkdir(parents=True)
    context = json.loads((args.package / "context.json").read_text(encoding="utf-8"))
    scenario = api(
        "/api/v1/scenarios",
        {"name": f"Portfolio 4.3 {args.model.upper()} {uuid4().hex[:8]}"},
    )["id"]
    prefix = f"/api/v1/scenarios/{scenario}"
    files = sorted(args.package.glob("*.csv")) + sorted(args.package.glob("*.xlsx"))
    boundary = "routeops-" + uuid4().hex
    parts = []
    for file in files:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{file.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + file.read_bytes()
            + b"\r\n"
        )
    content = b"".join(parts) + f"--{boundary}--\r\n".encode()
    key = uuid4().hex
    upload_headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Idempotency-Key": key,
    }
    batch = json.loads(request(base, prefix + "/imports", content, upload_headers))[
        "id"
    ]
    duplicate = json.loads(request(base, prefix + "/imports", content, upload_headers))
    assert duplicate["id"] == batch
    import_path = f"{prefix}/imports/{batch}"
    api(import_path + "/validation", context)
    validated = wait(import_path + "/validation", {"VALID", "INVALID", "FAILED"})
    assert validated["status"] == "VALID", validated
    revision = api(import_path + "/publish", {})
    assert api(import_path + "/publish", {})["id"] == revision["id"]
    orders = [
        f"{args.model.upper()}-{number:03d}"
        for number in range(1, 5 if args.model == "b2b" else 7)
    ]
    size = len(orders) // 2
    manual = [
        {
            "vehicle_id": f"{args.model.upper()}-V{index + 1}",
            "center_id": f"CD-B2B-{index + 1}" if args.model == "b2b" else "CD-B2C",
            "order_ids": list(reversed(orders[index * size : (index + 1) * size])),
        }
        for index in range(2)
    ]
    comparison = api(
        prefix + "/revisions/1/comparisons", {"manual_routes": manual}, uuid4().hex
    )["comparison_id"]
    compared = wait(f"/api/v1/comparisons/{comparison}", {"READY", "FAILED"})
    assert compared["status"] == "READY", compared
    snapshots = {"validation_before_publication": validated, "comparison": compared}
    runs = {}
    for action in ("accept", "cancel"):
        run_key = uuid4().hex
        run_path = prefix + "/revisions/1/runs"
        run = api(run_path, {}, run_key)["run_id"]
        assert api(run_path, {}, run_key)["run_id"] == run
        ready = wait(f"/api/v1/revision-runs/{run}", {"READY", "FAILED"})
        assert ready["status"] == "READY", ready
        routes = ready["result"]["routes"]
        assert len(routes) == 2, routes
        routed = {
            step["order_id"]
            for route in routes
            for step in route["steps"]
            if step.get("order_id")
        }
        assert routed == set(orders), routed
        snapshots[action + "_ready"] = ready
        final = api(f"/api/v1/revision-runs/{run}/{action}", {})
        assert final["status"] == ("ACCEPTED" if action == "accept" else "CANCELED")
        assert (
            api(f"/api/v1/revision-runs/{run}/{action}", {})["status"]
            == final["status"]
        )
        runs[action] = run
        snapshots[action + "_final"] = final
    readback = {
        import_path: api(import_path),
        prefix + "/revisions/1": api(prefix + "/revisions/1"),
        prefix + "/revision-runs": api(prefix + "/revision-runs"),
        f"/api/v1/comparisons/{comparison}": compared,
    }
    exports = []
    for resource, identity in (("runs", runs["accept"]), ("comparisons", comparison)):
        path = f"/api/v1/{resource}/{identity}"
        if resource == "runs":
            for suffix in ("analytics", "metrics", "diagnostics"):
                readback[path + "/" + suffix] = api(path + "/" + suffix)
        for format_name in ("csv", "xlsx"):
            export_path = path + "/exports/" + format_name
            data = request(base, export_path)
            filename = f"{resource}-{identity}." + (
                "zip" if format_name == "csv" else "xlsx"
            )
            (args.output / filename).write_bytes(data)
            exports.append(
                {
                    "file": filename,
                    "path": export_path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
    for run in runs.values():
        readback[f"/api/v1/revision-runs/{run}"] = api(f"/api/v1/revision-runs/{run}")
    report = {
        "model": args.model,
        "scenario": scenario,
        "batch": batch,
        "revision": revision,
        "runs": runs,
        "comparison": comparison,
        "context": context,
        "manual_routes": manual,
        "snapshots": snapshots,
        "readback": readback,
        "exports": exports,
        "method": "API, not browser observations; no benchmark",
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "scenario": scenario,
                "batch": batch,
                "runs": runs,
                "comparison": comparison,
                "output": str(args.output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
