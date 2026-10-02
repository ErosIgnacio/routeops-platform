from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from routeops.application.import_cli import main
from routeops.application.import_context import ValidationContext
from routeops.application.import_contract import DATASETS, LEGACY_SCHEMA, SCHEMA
from routeops.application.import_templates import csv_template, xlsx_template
from routeops.application.import_validation import ImportLimits, validate_package

ROWS: dict[str, list[str]] = {
    "orders": [
        "ORD-1",
        "SYNTH-1",
        "-33.44",
        "-70.65",
        "50",
        "2026-10-15T09:00:00-03:00",
        "2026-10-15T14:00:00-03:00",
        "5",
        "cold",
    ],
    "order_lines": ["ORD-1", "SKU-1", "2", "1.25", "0.001"],
    "inventory": ["2026-10-15T08:00:00-03:00", "DC-1", "SKU-1", "10", "2", "1"],
    "distribution_centers": ["DC-1", "SYNTH DC", "-33.45", "-70.66", "08:00", "18:00"],
    "vehicles": [
        "VEH-1",
        "DC-1",
        "van",
        "100",
        "1000",
        "10",
        "08:00",
        "18:00",
        "cold",
        "100",
        "10",
        "1",
    ],
}

EXPECTED_HEADERS = {
    "orders": (
        "order_id,customer_reference,latitude,longitude,priority,"
        "time_window_start,time_window_end,service_minutes,required_skills"
    ),
    "order_lines": "order_id,sku,quantity,unit_weight_kg,unit_volume_m3",
    "inventory": (
        "snapshot_at,distribution_center_id,sku,on_hand_quantity,"
        "externally_reserved_quantity,safety_stock_quantity"
    ),
    "distribution_centers": (
        "distribution_center_id,name,latitude,longitude,operating_start,operating_end"
    ),
    "vehicles": (
        "vehicle_id,distribution_center_id,vehicle_type,capacity_units,"
        "capacity_weight_kg,capacity_volume_m3,shift_start,shift_end,skills,"
        "fixed_cost,cost_per_hour,cost_per_km,max_route_distance_meters,"
        "max_driving_seconds,max_delivery_tasks"
    ),
}


def csv_data(
    rows: Mapping[str, list[list[str]]] | None = None, *, bom: bool = False
) -> dict[str, bytes]:
    rows = rows or {name: [values] for name, values in ROWS.items()}
    result: dict[str, bytes] = {}
    for name in DATASETS:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow([spec.name for spec in SCHEMA[name]])
        writer.writerows(
            values + [""] * (len(SCHEMA[name]) - len(values)) for values in rows[name]
        )
        result[f"{name}.csv"] = stream.getvalue().encode("utf-8-sig" if bom else "utf-8")
    return result


def codes(files: Mapping[str, bytes], limits: ImportLimits | None = None) -> set[str]:
    return {issue.code for issue in validate_package(files, limits).issues}


def modified_xlsx(path: str, replacement: bytes) -> bytes:
    source = io.BytesIO(xlsx_template())
    output = io.BytesIO()
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(output, "w") as new:
        for item in old.infolist():
            new.writestr(item.filename, replacement if item.filename == path else old.read(item))
    return output.getvalue()


def xlsx_with_rows() -> bytes:
    source = io.BytesIO(xlsx_template())
    output = io.BytesIO()
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(output, "w") as new:
        for item in old.infolist():
            content = old.read(item)
            if item.filename.startswith("xl/worksheets/sheet"):
                number = int(item.filename.split("sheet")[2].split(".")[0])
                name = DATASETS[number - 1]
                root = ET.fromstring(content)
                sheet_data = root.find(f"{ns}sheetData")
                assert sheet_data is not None
                row = ET.SubElement(sheet_data, f"{ns}row", r="2")
                padded = ROWS[name] + [""] * (len(SCHEMA[name]) - len(ROWS[name]))
                for index, value in enumerate(padded, start=1):
                    cell = ET.SubElement(row, f"{ns}c", r=f"{chr(64 + index)}2", t="inlineStr")
                    ET.SubElement(ET.SubElement(cell, f"{ns}is"), f"{ns}t").text = value
                content = ET.tostring(root)
            new.writestr(item.filename, content)
    return output.getvalue()


@pytest.mark.parametrize("value,code", [
    ("1", None),
    ("2147483647", None),
    ("0", "QUANTITY_RANGE"),
    ("2147483648", "QUANTITY_RANGE"),
    ("-1", "INTEGER_INVALID"),
    ("1.5", "INTEGER_INVALID"),
])
def test_optional_vehicle_limits_validate_exact_bounds(value: str, code: str | None) -> None:
    rows = {name: [list(values)] for name, values in ROWS.items()}
    rows["vehicles"][0].extend([value, value, value])
    report = validate_package(csv_data(rows))
    observed = {issue.code for issue in report.issues if issue.dataset == "vehicles"}
    assert (code in observed) if code else report.valid


def test_legacy_vehicle_headers_keep_old_normalized_rows() -> None:
    files = csv_data()
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(spec.name for spec in LEGACY_SCHEMA["vehicles"])
    writer.writerow(ROWS["vehicles"])
    files["vehicles.csv"] = stream.getvalue().encode()
    context = ValidationContext(
        planning_date=date(2026, 10, 15),
        horizon_start_at=datetime.fromisoformat("2026-10-15T08:00:00-03:00"),
        horizon_end_at=datetime.fromisoformat("2026-10-15T18:00:00-03:00"),
        timezone_iana="America/Santiago",
        currency="CLP",
        contract_version="2.1",
        validator_version="2.3b.1",
    )
    normalized: list[dict[str, object]] = []
    report = validate_package(
        files, context=context,
        row_sink=lambda name, _source, _row, values: normalized.append(values)
        if name == "vehicles" else None,
    )
    assert report.valid and report.contract_version == "2.1"
    assert len(normalized) == 1
    assert not any(key.startswith("max_") for key in normalized[0])
    assert validate_package(files, context=context).to_dict() == report.to_dict()


def test_old_xlsx_vehicle_sheet_remains_compatible() -> None:
    source = io.BytesIO(xlsx_with_rows())
    target = io.BytesIO()
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for entry in old.infolist():
            content = old.read(entry)
            if entry.filename == "xl/worksheets/sheet5.xml":
                root = ET.fromstring(content)
                sheet = root.find(f"{ns}sheetData")
                assert sheet is not None
                for row in sheet.findall(f"{ns}row"):
                    for cell in list(row):
                        if cell.get("r", "")[:1] in {"M", "N", "O"}:
                            row.remove(cell)
                content = ET.tostring(root)
            new.writestr(entry, content)
    files = {"legacy.xlsx": target.getvalue()}
    assert validate_package(files).valid
    legacy = ValidationContext(
        planning_date=date(2026, 10, 15),
        horizon_start_at=datetime.fromisoformat("2026-10-15T08:00:00-03:00"),
        horizon_end_at=datetime.fromisoformat("2026-10-15T18:00:00-03:00"),
        timezone_iana="America/Santiago", currency="CLP",
        contract_version="2.1", validator_version="2.3b.1",
    )
    assert validate_package(files, context=legacy).valid


def test_templates_are_deterministic_and_round_trip() -> None:
    assert tuple(EXPECTED_HEADERS) == DATASETS
    for name, header in EXPECTED_HEADERS.items():
        assert csv_template(name).decode("utf-8").strip() == header
    assert csv_template("orders") == csv_template("orders")
    assert xlsx_template() == xlsx_template()
    with zipfile.ZipFile(io.BytesIO(xlsx_template())) as archive:
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.find(f"{ns}sheets")
        assert sheets is not None
        assert [sheet.get("name") for sheet in sheets] == list(DATASETS)
        for index, name in enumerate(DATASETS, start=1):
            sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            assert [node.text for node in sheet.iter(f"{ns}t")] == EXPECTED_HEADERS[name].split(",")
    assert validate_package({f"{name}.csv": csv_template(name) for name in DATASETS}).valid
    assert validate_package({"routeops.xlsx": xlsx_template()}).valid
    csv_report = validate_package(csv_data())
    xlsx_report = validate_package({"routeops.xlsx": xlsx_with_rows()})
    assert csv_report.valid and xlsx_report.valid
    assert all(item.accepted == 1 for item in csv_report.counts.values())
    assert all(item.accepted == 1 for item in xlsx_report.counts.values())


def test_csv_bom_and_multiple_independent_errors() -> None:
    assert validate_package(csv_data(bom=True)).valid
    rows = {name: [list(values)] for name, values in ROWS.items()}
    rows["orders"][0][2] = "200"
    rows["orders"][0][4] = "not-an-integer"
    rows["order_lines"][0][0] = "missing"
    rows["inventory"][0][4] = "11"
    report = validate_package(csv_data(rows))
    assert {
        "COORDINATE_RANGE",
        "INTEGER_INVALID",
        "RELATION_MISSING",
        "STOCK_INCONSISTENT",
        "ORDER_LINES_MISSING",
    } <= {i.code for i in report.issues}
    assert report.counts["orders"].rejected == 1


def test_inventory_snapshot_must_be_uniform_and_text_has_no_control_chars() -> None:
    rows = {name: [list(values)] for name, values in ROWS.items()}
    second = list(rows["inventory"][0])
    second[0] = "2026-10-16T08:00:00-03:00"
    second[2] = "SKU-2"
    rows["inventory"].append(second)
    rows["orders"][0][1] = "bad\x00label"
    report = validate_package(csv_data(rows))
    assert {"SNAPSHOT_MISMATCH", "TEXT_INVALID"} <= {issue.code for issue in report.issues}
    assert report.counts["inventory"].rejected == 1


def test_headers_ids_and_relationships() -> None:
    files = csv_data()
    files["orders.csv"] = files["orders.csv"].replace(b"order_id", b"bad_name", 1)
    assert {"HEADER_MISSING", "HEADER_UNKNOWN"} <= codes(files)
    files = csv_data()
    files["orders.csv"] = files["orders.csv"].replace(b"customer_reference", b"order_id", 1)
    assert "HEADER_DUPLICATE" in codes(files)
    rows = {name: [list(values)] for name, values in ROWS.items()}
    rows["orders"].append(list(rows["orders"][0]))
    rows["vehicles"][0][1] = "OTHER"
    assert {"ID_DUPLICATE", "RELATION_MISSING"} <= codes(csv_data(rows))
    files = csv_data()
    files["orders.csv"] = files["orders.csv"].replace(
        b"required_skills", b"secret-token-in-header", 1
    )
    report = validate_package(files)
    assert report.valid
    assert "HEADER_UNKNOWN" in {issue.code for issue in report.issues}
    assert all("secret" not in str(issue) for issue in report.issues)


@pytest.mark.parametrize(
    ("dataset", "index", "invalid", "code"),
    [
        ("orders", 5, "2026-10-15T09:00:00", "INSTANT_INVALID"),
        ("orders", 2, "-91", "COORDINATE_RANGE"),
        ("orders", 2, "NaN", "DECIMAL_INVALID"),
        ("orders", 3, "Infinity", "DECIMAL_INVALID"),
        ("order_lines", 2, "0", "QUANTITY_RANGE"),
        ("order_lines", 2, "-1", "INTEGER_INVALID"),
        ("order_lines", 2, "1.5", "INTEGER_INVALID"),
        ("order_lines", 3, "1.1234567", "DECIMAL_PRECISION"),
        ("order_lines", 3, "1.000001", "DECIMAL_SCALE"),
        ("order_lines", 4, "0.000000001", "DECIMAL_SCALE"),
        ("vehicles", 7, "07:00", "SHIFT_OUTSIDE_HOURS"),
        ("vehicles", 9, "-1", "QUANTITY_RANGE"),
        ("orders", 0, "", "VALUE_REQUIRED"),
        ("orders", 8, "cold|COLD", "SKILLS_INVALID"),
        ("distribution_centers", 4, "25:00", "TIME_INVALID"),
        ("orders", 6, "2026-10-15T08:00:00-03:00", "INTERVAL_INVALID"),
    ],
)
def test_field_and_cross_field_validation(
    dataset: str, index: int, invalid: str, code: str
) -> None:
    rows = {name: [list(values)] for name, values in ROWS.items()}
    rows[dataset][0][index] = invalid
    assert code in codes(csv_data(rows))


def test_package_formats_and_limits() -> None:
    files = csv_data()
    assert "PACKAGE_INCOMPLETE" in codes({"orders.csv": files["orders.csv"]})
    assert "FORMAT_UNSUPPORTED" in codes({"data.xls": b"not supported"})
    assert "FILE_UNKNOWN" in codes({**files, "extra.csv": b"x"})
    assert "FILE_LIMIT" in codes(files, ImportLimits(max_file_bytes=10))
    assert "PACKAGE_LIMIT" in codes(files, ImportLimits(max_package_bytes=10))
    rows = {name: [list(values)] for name, values in ROWS.items()}
    rows["orders"].append(["ORD-2", *rows["orders"][0][1:]])
    assert "ROW_LIMIT" in codes(csv_data(rows), ImportLimits(max_rows_per_dataset=1))
    assert "COLUMN_LIMIT" in codes(files, ImportLimits(max_columns=3))
    assert "CELL_LENGTH_LIMIT" in codes(files, ImportLimits(max_cell_characters=3))
    files = csv_data()
    files["orders.csv"] += b",unexpected"
    assert "ROW_WIDTH" in codes(files)
    files = csv_data()
    files["orders.csv"] = b"\xff" + files["orders.csv"]
    assert "CSV_INVALID" in codes(files)


def test_spaces_case_and_skill_normalization() -> None:
    rows = {name: [list(values)] for name, values in ROWS.items()}
    rows["orders"][0][0] = " ORD-1 "
    rows["orders"][0][8] = " Cold | fragile "
    rows["order_lines"][0][0] = "ORD-1"
    assert validate_package(csv_data(rows)).valid
    rows["order_lines"][0][0] = "ord-1"
    assert "RELATION_MISSING" in codes(csv_data(rows))
    files = csv_data()
    files["orders.csv"] = files["orders.csv"].replace(b"order_id", b"Order_ID", 1)
    assert "HEADER_MISSING" in codes(files)


def test_xlsx_corruption_formulas_links_sheets_and_expansion() -> None:
    template = xlsx_template()
    assert "XLSX_INVALID" in codes({"broken.xlsx": b"garbage"})
    assert "XLSX_EXPANSION_LIMIT" in codes(
        {"book.xlsx": template}, ImportLimits(max_xlsx_expanded_bytes=100)
    )
    assert "XLSX_CELL_LIMIT" in codes({"book.xlsx": template}, ImportLimits(max_xlsx_cells=2))
    sheet = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(io.BytesIO(template)) as archive:
        sheet_bytes = archive.read(sheet)
        workbook = archive.read("xl/workbook.xml")
        rels = archive.read("xl/_rels/workbook.xml.rels")
    formula = sheet_bytes.replace(
        b"</ns0:row>", b'<ns0:c r="J1"><ns0:f>SECRET()</ns0:f></ns0:c></ns0:row>', 1
    )
    formula_report = validate_package({"book.xlsx": modified_xlsx(sheet, formula)})
    formula_issue = next(issue for issue in formula_report.issues if issue.code == "XLSX_FORMULA")
    assert (
        formula_issue.dataset,
        formula_issue.source,
        formula_issue.row,
        formula_issue.field,
    ) == ("orders", "workbook.xlsx:orders", 1, "J1")
    assert all("SECRET" not in issue.message for issue in formula_report.issues)
    altered = workbook.replace(b'name="orders"', b'name="other"', 1)
    assert "XLSX_SHEETS" in codes({"book.xlsx": modified_xlsx("xl/workbook.xml", altered)})
    linked = rels.replace(
        b"</ns0:Relationships>",
        b'<ns0:Relationship TargetMode="External" Target="https://example.com"/></ns0:Relationships>',
    )
    assert "XLSX_EXTERNAL_LINK" in codes(
        {"book.xlsx": modified_xlsx("xl/_rels/workbook.xml.rels", linked)}
    )


def test_issues_are_sanitized_and_have_stable_codes() -> None:
    rows = {name: [list(values)] for name, values in ROWS.items()}
    rows["orders"][0][0] = "secret-token-abc<>"
    issue = next(
        i for i in validate_package(csv_data(rows)).issues if i.code == "IDENTIFIER_INVALID"
    )
    assert issue.value_excerpt is not None and "secret" not in issue.value_excerpt
    assert issue.source == "orders.csv" and issue.row == 2 and issue.field == "order_id"
    assert "traceback" not in issue.message.lower()
    report = validate_package({"C:\\private\\token.xlsx": b"broken"})
    assert all("private" not in (item.source or "") for item in report.issues)
    assert all("token" not in (item.source or "") for item in report.issues)
    limited = validate_package(csv_data(rows), ImportLimits(max_issues=1))
    assert "REPORT_TRUNCATED" in {item.code for item in limited.issues}


def test_limits_are_configurable_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTEOPS_IMPORT_MAX_ROWS_PER_DATASET", "1")
    limits = ImportLimits.from_environment()
    assert limits.max_rows_per_dataset == 1


def test_cli_template_and_validation_round_trip(tmp_path: Path) -> None:
    directory = tmp_path / "templates"
    assert main(["templates", str(directory)]) == 0
    generated = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert main(["templates", str(directory)]) == 2
    assert generated == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert main(["validate", *[str(directory / f"{name}.csv") for name in DATASETS]]) == 0
    assert main(["validate", str(directory / "routeops-template.xlsx")]) == 0


def test_cli_expected_errors_are_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["validate", str(tmp_path / "missing.xlsx")]) == 2
    assert '"error": "FILE_READ_ERROR"' in capsys.readouterr().out
    monkeypatch.setenv("ROUTEOPS_IMPORT_MAX_FILE_BYTES", "not-an-integer")
    assert main(["validate", str(tmp_path / "missing.xlsx")]) == 2
    output = capsys.readouterr()
    assert '"error": "CONFIG_INVALID"' in output.out
    assert "Traceback" not in output.err
