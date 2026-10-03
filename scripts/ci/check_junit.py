"""Fail closed on missing, empty, failed or skipped CI test evidence."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def verify(path: Path) -> int:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    count = sum(int(s.attrib.get("tests", "0")) for s in suites)
    if count == 0 or any(
        int(s.attrib.get(field, "0")) for s in suites for field in ("errors", "failures", "skipped")
    ):
        raise ValueError("JUnit must contain tests and zero errors, failures and skips")
    return count


if __name__ == "__main__":
    print(f"Verified {verify(Path(sys.argv[1]))} tests; zero failures/errors/skips.")
