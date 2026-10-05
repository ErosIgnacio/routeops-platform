"""Record installed Python notices and locked frontend licenses, without legal certification."""

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = re.compile(r"routeops-review-[a-z0-9][a-z0-9-]{0,48}\Z")

CODE = """
import importlib.metadata as m,json,hashlib
out=[]
for d in m.distributions():
 meta=d.metadata
 value=meta.get('License') or ''
 out.append({'name':meta['Name'],'version':d.version,
 'expression':meta.get('License-Expression'),
 'legacy_license':value if len(value)<200 else 'SEE_INSTALLED_LICENSE',
 'legacy_license_sha256':hashlib.sha256(value.encode()).hexdigest() if value else None,
 'classifiers':[c for c in meta.get_all('Classifier',[]) if c.startswith('License ::')],
 'notice_files':[str(f) for f in (d.files or [])
                 if any(s in f.name.lower() for s in ('license','copying','copyright'))]})
print(json.dumps(sorted(out,key=lambda r:r['name'].lower())))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not PROJECT.fullmatch(args.project):
        parser.error("Read only from an explicit isolated routeops-review-* project")
    if args.output.exists():
        parser.error("Inventory already exists; review before replacing it")
    process = subprocess.run(
        ["docker", "exec", f"{args.project}-backend-1", "python", "-c", CODE],
        capture_output=True,
        text=True,
        check=True,
    )
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    npm = [
        {
            "path": path,
            "version": item.get("version"),
            "license": item.get("license", "NOASSERTION"),
            "dev": item.get("dev", False),
            "optional": item.get("optional", False),
            "integrity": item.get("integrity"),
        }
        for path, item in lock["packages"].items()
        if path
    ]
    inventory = {
        "method": (
            "Installed Python wheel metadata; complete frontend lock including dev/optional. "
            "Lock presence does not prove platform installation or bundle inclusion."
        ),
        "scope": (
            "Application dependencies only; base-image OS/vendor notices remain in upstream "
            "images and the M42 SBOM. No certification of redistribution compliance."
        ),
        "python": json.loads(process.stdout),
        "npm": npm,
        "sources": ["backend/requirements.lock.txt", "frontend/package-lock.json"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(inventory, output, ensure_ascii=False, indent=2)
    print(f"Recorded {len(inventory['python'])} Python packages and {len(npm)} npm lock entries")


if __name__ == "__main__":
    main()
