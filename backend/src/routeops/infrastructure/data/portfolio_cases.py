"""Small portfolio inputs; no change to historical demos or solver policy."""

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
from routeops.infrastructure.data.operation_cases import case_rows

CONTEXT = {
    "planning_date": "2026-10-15",
    "horizon_start_at": "2026-10-15T08:00:00-03:00",
    "horizon_end_at": "2026-10-15T18:00:00-03:00",
    "timezone_iana": "America/Santiago",
    "currency": "CLP",
}
POINTS = (
    ("-33.446", "-70.660"),
    ("-33.443", "-70.648"),
    ("-33.445", "-70.654"),
    ("-33.441", "-70.655"),
    ("-33.448", "-70.651"),
    ("-33.439", "-70.650"),
)


def portfolio_rows(model: str) -> dict[str, list[dict[str, str]]]:
    if model not in ("b2b", "b2c"):
        raise ValueError("PORTFOLIO_CASE_NOT_FOUND")
    rows = case_rows(f"{model}-feasible")
    vehicle = rows["vehicles"][0]
    rows["vehicles"] = [deepcopy(vehicle), deepcopy(vehicle)]
    for index, item in enumerate(rows["vehicles"], start=1):
        item["vehicle_id"] = f"{model.upper()}-V{index}"
        item["max_delivery_tasks"] = "2" if model == "b2b" else "3"
        if model == "b2c":
            item["capacity_units"] = "3"
    if model == "b2b":
        # Exclusive stock forces one truck per CD, independently of solver ranking.
        center = rows["distribution_centers"][0]
        rows["distribution_centers"] = [deepcopy(center), deepcopy(center)]
        for index, item in enumerate(rows["distribution_centers"], start=1):
            item["distribution_center_id"] = f"CD-B2B-{index}"
            item["name"] = f"Synthetic B2B center {index}"
            if index == 2:
                item["latitude"], item["longitude"] = POINTS[1]
            rows["vehicles"][index - 1]["distribution_center_id"] = item["distribution_center_id"]
        rows["orders"] *= 2
        rows["order_lines"] *= 2
        rows["inventory"] *= 2
        rows = deepcopy(rows)
        # Deepcopy retains aliasing: copy each duplicated record separately.
        for dataset in ("orders", "order_lines", "inventory"):
            rows[dataset] = [deepcopy(item) for item in rows[dataset]]
    for index, order in enumerate(rows["orders"]):
        key = f"{model.upper()}-{index + 1:03d}"
        order["order_id"] = key
        order["customer_reference"] = f"Synthetic {model.upper()} {index + 1:03d} & delivery"
        order["latitude"], order["longitude"] = POINTS[index]
        rows["order_lines"][index]["order_id"] = key
        rows["order_lines"][index]["sku"] = key
        rows["inventory"][index]["sku"] = key
        if model == "b2b":
            rows["inventory"][index]["distribution_center_id"] = f"CD-B2B-{1 + index // 2}"
    return rows


def payloads(model: str, workbook: bool) -> dict[str, bytes]:
    rows = portfolio_rows(model)
    if not workbook:
        result = {}
        for dataset in DATASETS:
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=[field.name for field in SCHEMA[dataset]])
            writer.writeheader()
            writer.writerows(rows[dataset])
            result[f"{dataset}.csv"] = stream.getvalue().encode("utf-8")
        return result
    destination = io.BytesIO()
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with (
        zipfile.ZipFile(io.BytesIO(xlsx_template())) as template,
        zipfile.ZipFile(destination, "w") as output,
    ):
        for entry in template.infolist():
            content = template.read(entry.filename)
            for index, dataset in enumerate(DATASETS, start=1):
                if entry.filename != f"xl/worksheets/sheet{index}.xml":
                    continue
                sheet = ET.fromstring(content)
                data = sheet.find(f"{ns}sheetData")
                assert data is not None
                for number, item in enumerate(rows[dataset], start=2):
                    row = ET.SubElement(data, f"{ns}row", r=str(number))
                    for column, field in enumerate(SCHEMA[dataset], start=1):
                        cell = ET.SubElement(
                            row, f"{ns}c", r=f"{_column(column)}{number}", t="inlineStr"
                        )
                        ET.SubElement(ET.SubElement(cell, f"{ns}is"), f"{ns}t").text = item.get(
                            field.name, ""
                        )
                content = ET.tostring(sheet, encoding="utf-8", xml_declaration=True)
            output.writestr(entry, content)
    return {"workbook.xlsx": destination.getvalue()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("b2b", "b2c"), required=True)
    parser.add_argument("--format", choices=("csv", "xlsx"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error("Output directory must be empty; existing files are preserved")
    for name, content in payloads(args.model, args.format == "xlsx").items():
        with (args.output / name).open("xb") as stream:
            stream.write(content)
    (args.output / "context.json").write_text(json.dumps(CONTEXT, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
