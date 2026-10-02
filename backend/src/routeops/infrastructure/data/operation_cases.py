"""Small explicit B2B/B2C contract fixtures, not label-dependent routing rules."""

import argparse
import csv
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from copy import deepcopy
from pathlib import Path

from routeops.application.import_contract import DATASETS, SCHEMA
from routeops.application.import_templates import _column, xlsx_template

CASE_NAMES = (
    "b2b-feasible",
    "b2b-diagnostics",
    "b2c-feasible",
    "b2c-task-pressure",
    "b2c-distance-inferred",
)


def case_rows(name: str) -> dict[str, list[dict[str, str]]]:
    if name not in CASE_NAMES:
        raise ValueError("OPERATION_CASE_NOT_FOUND")
    b2b = name.startswith("b2b")
    center = "CD-B2B" if b2b else "CD-B2C"
    rows: dict[str, list[dict[str, str]]] = {dataset: [] for dataset in DATASETS}
    rows["distribution_centers"] = [
        {
            "distribution_center_id": center,
            "name": "Synthetic operation 3.1c",
            "latitude": "-33.4445",
            "longitude": "-70.6635",
            "operating_start": "08:00",
            "operating_end": "18:00",
        }
    ]
    rows["vehicles"] = [
        {
            "vehicle_id": "TRUCK" if b2b else "VAN",
            "distribution_center_id": center,
            "vehicle_type": "truck" if b2b else "van",
            "capacity_units": "40" if b2b else "10",
            "capacity_weight_kg": "500" if b2b else "50",
            "capacity_volume_m3": "2",
            "shift_start": "08:00",
            "shift_end": "18:00",
            "skills": "cold" if b2b else "parcel",
            "fixed_cost": "1000",
            "cost_per_hour": "100",
            "cost_per_km": "50",
            "max_route_distance_meters": "",
            "max_driving_seconds": "",
            "max_delivery_tasks": "" if b2b else "6",
        }
    ]

    def add(
        key: str,
        quantity: int,
        *,
        weight: str = "20",
        volume: str = "0.02",
        skills: str = "cold",
        service: int = 30,
        start: str = "09:00",
        end: str = "12:00",
        priority: int = 50,
        latitude: str = "-33.446",
        longitude: str = "-70.660",
    ) -> None:
        rows["orders"].append(
            {
                "order_id": key,
                "customer_reference": "Synthetic 3.1c",
                "latitude": latitude,
                "longitude": longitude,
                "priority": str(priority),
                "time_window_start": f"2026-10-15T{start}:00-03:00",
                "time_window_end": f"2026-10-15T{end}:00-03:00",
                "service_minutes": str(service),
                "required_skills": skills,
            }
        )
        rows["order_lines"].append(
            {
                "order_id": key,
                "sku": key,
                "quantity": str(quantity),
                "unit_weight_kg": weight,
                "unit_volume_m3": volume,
            }
        )
        rows["inventory"].append(
            {
                "snapshot_at": "2026-10-15T08:00:00-03:00",
                "distribution_center_id": center,
                "sku": key,
                "on_hand_quantity": "200",
                "externally_reserved_quantity": "5",
                "safety_stock_quantity": "5",
            }
        )

    if b2b:
        add("B2B-OK", 12, priority=90)
        if name == "b2b-feasible":
            add("B2B-SECOND", 8, service=20)
        else:
            add("B2B-SKILL", 2, skills="hazmat")
            add("B2B-WEIGHT", 30)
            add("B2B-VOLUME", 2, volume="1.5")
            add("B2B-UNITS", 41, weight="0.1", volume="0.001")
            add("B2B-SERVICE", 1, service=120, start="17:00", end="17:30")
    else:
        for number in range(6):
            add(
                f"B2C-{number + 1:02d}",
                1,
                weight="0.1",
                volume="0.001",
                skills="parcel",
                service=5,
                end="13:00",
                priority=100 - 10 * number,
                latitude="-33.446" if number % 2 == 0 else "-33.443",
                longitude="-70.660" if number % 2 == 0 else "-70.648",
            )
        if name == "b2c-task-pressure":
            rows["vehicles"][0]["max_delivery_tasks"] = "2"
        elif name == "b2c-distance-inferred":
            rows["vehicles"][0]["max_route_distance_meters"] = "1"
            rows["vehicles"][0]["max_driving_seconds"] = "1"
    return deepcopy(rows)


def case_payloads(name: str, *, workbook: bool = False) -> dict[str, bytes]:
    rows = case_rows(name)
    if not workbook:
        result = {}
        for dataset in DATASETS:
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=[spec.name for spec in SCHEMA[dataset]])
            writer.writeheader()
            writer.writerows(rows[dataset])
            result[f"{dataset}.csv"] = stream.getvalue().encode("utf-8")
        return result
    target = io.BytesIO()
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with (
        zipfile.ZipFile(io.BytesIO(xlsx_template())) as original,
        zipfile.ZipFile(target, "w") as output,
    ):
        for entry in original.infolist():
            payload = original.read(entry.filename)
            for index, dataset in enumerate(DATASETS, start=1):
                if entry.filename != f"xl/worksheets/sheet{index}.xml":
                    continue
                sheet = ET.fromstring(payload)
                sheet_data = sheet.find(f"{ns}sheetData")
                assert sheet_data is not None
                for number, values in enumerate(rows[dataset], start=2):
                    row = ET.SubElement(sheet_data, f"{ns}row", r=str(number))
                    for column, spec in enumerate(SCHEMA[dataset], start=1):
                        cell = ET.SubElement(
                            row, f"{ns}c", r=f"{_column(column)}{number}", t="inlineStr"
                        )
                        ET.SubElement(ET.SubElement(cell, f"{ns}is"), f"{ns}t").text = values.get(
                            spec.name, ""
                        )
                payload = ET.tostring(sheet, encoding="utf-8", xml_declaration=True)
            output.writestr(entry, payload)
    return {"workbook.xlsx": target.getvalue()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASE_NAMES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", choices=("csv", "xlsx"), default="csv")
    args = parser.parse_args()
    payloads = case_payloads(args.case, workbook=args.format == "xlsx")
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error("Output directory must be empty; existing files are preserved.")
    for name, content in payloads.items():
        with (args.output / name).open("xb") as stream:
            stream.write(content)
    print(json.dumps({"case": args.case, "format": args.format, "files": sorted(payloads)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
