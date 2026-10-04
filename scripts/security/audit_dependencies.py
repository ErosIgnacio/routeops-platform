"""Report current OSV advisories against exact Python locks; never edit dependencies."""

import argparse
import hashlib
import json
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    packages = []
    locks = [
        root / "backend" / name for name in ("requirements.lock.txt", "requirements-dev.lock.txt")
    ]
    for lock in locks:
        for line in lock.read_text().splitlines():
            if re.fullmatch(r"[A-Za-z0-9_.-]+==[^ ]+", line):
                name, version = line.split("==")
                packages.append(
                    {
                        "package": {"name": name, "ecosystem": "PyPI"},
                        "version": version,
                        "lock": lock.name,
                    }
                )
    queries = [{k: v for k, v in item.items() if k != "lock"} for item in packages]
    request = urllib.request.Request(
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps({"queries": queries}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        results = json.load(response)["results"]
    incomplete = any(item.get("next_page_token") for item in results)
    report = {
        "queried_at": datetime.now(UTC).isoformat(),
        "source": "https://api.osv.dev/v1/querybatch",
        "locks": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in locks
        },
        "incomplete": incomplete,
        "packages": [dict(p, **r) for p, r in zip(packages, results, strict=True)],
        "scope": "PyPI locks only; excludes pip vendoring, Debian packages and container images",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    findings = sum(bool(item.get("vulns")) for item in results)
    print(
        json.dumps(
            {
                "queried_packages": len(packages),
                "packages_with_advisories": findings,
                "incomplete": incomplete,
            }
        )
    )
    return int(incomplete or bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
