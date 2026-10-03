"""Deterministic, inert exports of query documents; no calculation or inventory writes."""

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, cast
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

EXPORT_VERSION = "analytics-export-v1"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"


def scalar(value: Any, *, csv_safe: bool = False) -> str:
    if value is None:
        return "\\N"
    text = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(value, (dict, list))
        else "true"
        if value is True
        else "false"
        if value is False
        else str(value)
    )
    # XML 1.0 excludes these controls. Preserve their meaning as visible escapes.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda m: f"\\u{ord(m[0]):04x}", text)
    if text.startswith("\\"):
        text = "\\" + text
    if csv_safe and (
        text.lstrip(" \ufeff").startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
        or text.lstrip().lower().startswith(("http:", "https:", "mailto:", "file:", "ftp:"))
        or text.startswith("'")
    ):
        text = "'" + text
    return text


def flatten(value: Any, path: str = "") -> list[list[Any]]:
    if isinstance(value, dict) and value:
        return [row for key in sorted(value) for row in flatten(value[key], f"{path}/{key}")]
    if isinstance(value, list) and value:
        return [row for index, item in enumerate(value) for row in flatten(item, f"{path}/{index}")]
    kind = "null" if value is None else type(value).__name__
    return [[path, kind, value]]


def tables(document: dict[str, Any]) -> dict[str, list[list[Any]]]:
    comparison = "comparison_id" in document
    plans = (
        document.get("result", {}).get("alternatives", {})
        if comparison
        else {
            "operational": {
                "routes": document["run"].get("result", {}).get("routes", [])
                if document["run"].get("result")
                else [],
                "metrics": document["metrics"].get("plan") or {},
                "result": document["run"].get("result") or {},
            }
        }
    )
    out: dict[str, list[list[Any]]] = {
        "summary": [
            [
                "plan",
                "metric",
                "value",
                "unit",
                "denominator",
                "unavailable_reason",
                "calculation_version",
                "provenance",
            ]
        ],
        "routes": [
            [
                "plan",
                "vehicle",
                "center",
                "departure_policy",
                "departure_at",
                "distance_meters",
                "driving_seconds",
                "waiting_seconds",
                "service_seconds",
                "total_duration_seconds",
            ]
        ],
        "stops": [
            [
                "plan",
                "vehicle",
                "center",
                "sequence",
                "kind",
                "order_id",
                "arrival_at",
                "service_start_at",
                "departure_at",
                "travel_seconds",
                "waiting_seconds",
                "service_seconds",
                "load_after",
                "latitude",
                "longitude",
            ]
        ],
        "diagnostics": [
            [
                "plan",
                "order_id",
                "code",
                "stage",
                "certainty",
                "severity",
                "role",
                "detail",
                "scope",
                "evidence",
            ]
        ],
        "utilization": [
            [
                "plan",
                "vehicle",
                "dimension",
                "maximum",
                "average",
                "max_denominator",
                "average_denominator",
            ]
        ],
        "differences": [
            [
                "pair",
                "scope",
                "comparability",
                "savings_claim_allowed",
                "metric",
                "direction",
                "absolute",
                "percentage",
                "unit",
                "denominator",
                "temporal_scope",
            ]
        ],
        "context": [["path", "type", "value"]],
        "manual_input": [
            ["route_index", "vehicle", "center", "sequence", "order_id", "departure_policy"]
        ],
        "reservations": [["order_id", "status"]],
        "processing": [["path", "type", "value"]],
        "facts": [["path", "type", "value"], *flatten(document)],
        "schema": [
            ["key", "value"],
            ["version", EXPORT_VERSION],
            ["null", "\\N; literal leading backslash is doubled"],
            ["numbers", "Exact API decimal strings, no recalculation; all XLSX cells text"],
            ["dates", "ISO8601 offset preserved; IANA zone in context"],
            ["csv", "UTF-8 BOM, RFC4180, IDs lexical text; import columns as text"],
            ["safety", "CSV formula sigils prefixed apostrophe; XLSX inline text, no links"],
            ["scope", "Estimated plan, not evidence of deliveries or realized savings"],
        ],
    }
    for name, plan in plans.items():
        metrics = plan.get("metrics", {})
        for field, value in metrics.get("metrics", {}).items():
            out["summary"].append(
                [
                    name,
                    field,
                    *[
                        value.get(k)
                        for k in (
                            "value",
                            "unit",
                            "denominator",
                            "unavailable_reason",
                            "calculation_version",
                            "provenance",
                        )
                    ],
                ]
            )
        for route in plan.get("routes", []):
            vehicle, center = route["source_vehicle_id"], route["distribution_center_id"]
            condition = route.get("departure_condition", {})
            out["routes"].append(
                [
                    name,
                    vehicle,
                    center,
                    condition.get("policy", plan.get("departure_policy", "LEGACY_NOT_RECORDED")),
                    condition.get("departure_at", route["steps"][0].get("departure_at")),
                    *[
                        route["totals"].get(k)
                        for k in (
                            "distance_meters",
                            "driving_seconds",
                            "waiting_seconds",
                            "service_seconds",
                            "total_duration_seconds",
                        )
                    ],
                ]
            )
            for step in route["steps"]:
                out["stops"].append(
                    [
                        name,
                        vehicle,
                        center,
                        *[
                            step.get(k)
                            for k in (
                                "sequence",
                                "kind",
                                "order_id",
                                "arrival_at",
                                "service_start_at",
                                "departure_at",
                                "travel_seconds_from_previous",
                                "waiting_seconds",
                                "service_seconds",
                                "load_after",
                            )
                        ],
                        step["location"]["latitude"],
                        step["location"]["longitude"],
                    ]
                )
        issues = plan.get("incidences", []) + [
            {"order_id": item["order_id"], "stage": item["stage"], **reason}
            for item in plan.get("result", {}).get("unassigned", [])
            for reason in item["reasons"]
        ]
        if not comparison:
            issues = document["diagnostics"]["items"]
        for issue in issues:
            evidence = issue.get("evidence", {})
            out["diagnostics"].append(
                [
                    name,
                    issue.get("order_id", evidence.get("order_id")),
                    *[
                        issue.get(k)
                        for k in ("code", "stage", "certainty", "severity", "role", "detail")
                    ],
                    evidence.get("scope", issue.get("scope")),
                    evidence,
                ]
            )
        for route in metrics.get("routes", []):
            for dimension, value in route["utilization"].items():
                out["utilization"].append(
                    [
                        name,
                        route["source_vehicle_id"],
                        dimension,
                        value["maximum"]["value"],
                        value["time_weighted_average"]["value"],
                        value["maximum"]["denominator"],
                        value["time_weighted_average"]["denominator"],
                    ]
                )
    if comparison:
        out["context"].extend(flatten(document["context"]))
        for pair, difference in document["result"]["comparisons"].items():
            for field, value in difference["deltas"].items():
                out["differences"].append(
                    [
                        pair,
                        difference["scope"],
                        difference["comparability"],
                        difference["savings_claim_allowed"],
                        field,
                        *[
                            value.get(k)
                            for k in ("direction", "absolute", "percentage", "unit", "denominator")
                        ],
                        difference.get("temporal_scope", {"source": "LEGACY_NOT_RECORDED"}),
                    ]
                )
        for index, route in enumerate(document["manual_input"]["manual_routes"]):
            for sequence, key in enumerate(route["order_ids"], 1):
                out["manual_input"].append(
                    [
                        index,
                        route["vehicle_id"],
                        route["center_id"],
                        sequence,
                        key,
                        plans["manual"].get("departure_policy", "LEGACY_NOT_RECORDED"),
                    ]
                )
        out["processing"].extend(flatten(document["history"]))
    else:
        out["context"].extend(flatten(document["run"].get("input", {})))
        out["processing"].extend(flatten(document["metrics"]["processing"]))
        out["reservations"].extend(
            [[r["order_id"], r["status"]] for r in document["metrics"]["current_reservations"]]
        )
    return out


def _zip(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, data in sorted(entries.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    return stream.getvalue()


def csv_package(data: dict[str, list[list[Any]]]) -> bytes:
    entries = {}
    for name, rows in data.items():
        stream = io.StringIO(newline="")
        csv.writer(stream).writerows([[scalar(v, csv_safe=True) for v in row] for row in rows])
        entries[f"{name}.csv"] = stream.getvalue().encode("utf-8-sig")
    return _zip(entries)


def _xml(node: ET.Element) -> bytes:
    # Literal CR is normalized to LF by XML readers; a character reference preserves it.
    return cast(bytes, ET.tostring(node, encoding="utf-8", xml_declaration=True)).replace(
        b"\r", b"&#13;"
    )


def _column(index: int) -> str:
    name = ""
    while index:
        index, tail = divmod(index - 1, 26)
        name = chr(65 + tail) + name
    return name


def workbook(data: dict[str, list[list[Any]]]) -> bytes:
    content = ET.Element(f"{{{CONTENT}}}Types")
    ET.SubElement(
        content,
        f"{{{CONTENT}}}Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    ET.SubElement(content, f"{{{CONTENT}}}Default", Extension="xml", ContentType="application/xml")
    ET.SubElement(
        content,
        f"{{{CONTENT}}}Override",
        PartName="/xl/workbook.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )
    book = ET.Element(f"{{{MAIN}}}workbook")
    sheets = ET.SubElement(book, f"{{{MAIN}}}sheets")
    links = ET.Element(f"{{{PKG}}}Relationships")
    root = ET.Element(f"{{{PKG}}}Relationships")
    ET.SubElement(
        root,
        f"{{{PKG}}}Relationship",
        Id="rId1",
        Type=f"{REL}/officeDocument",
        Target="xl/workbook.xml",
    )
    entries: dict[str, bytes] = {}
    for index, (name, rows) in enumerate(data.items(), 1):
        ET.SubElement(
            sheets,
            f"{{{MAIN}}}sheet",
            name=name,
            sheetId=str(index),
            attrib={f"{{{REL}}}id": f"rId{index}"},
        )
        ET.SubElement(
            links,
            f"{{{PKG}}}Relationship",
            Id=f"rId{index}",
            Type=f"{REL}/worksheet",
            Target=f"worksheets/sheet{index}.xml",
        )
        ET.SubElement(
            content,
            f"{{{CONTENT}}}Override",
            PartName=f"/xl/worksheets/sheet{index}.xml",
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
        )
        sheet = ET.Element(f"{{{MAIN}}}worksheet")
        content_rows = ET.SubElement(sheet, f"{{{MAIN}}}sheetData")
        for row_no, values in enumerate(rows, 1):
            row = ET.SubElement(content_rows, f"{{{MAIN}}}row", r=str(row_no))
            for col, value in enumerate(values, 1):
                text = scalar(value)
                if len(text) > 32767:
                    raise ValueError("EXPORT_CELL_LIMIT")
                cell = ET.SubElement(
                    row, f"{{{MAIN}}}c", r=f"{_column(col)}{row_no}", t="inlineStr"
                )
                ET.SubElement(
                    ET.SubElement(cell, f"{{{MAIN}}}is"),
                    f"{{{MAIN}}}t",
                    attrib={"{http://www.w3.org/XML/1998/namespace}space": "preserve"},
                ).text = text
        entries[f"xl/worksheets/sheet{index}.xml"] = _xml(sheet)
    entries.update(
        {
            "[Content_Types].xml": _xml(content),
            "_rels/.rels": _xml(root),
            "xl/workbook.xml": _xml(book),
            "xl/_rels/workbook.xml.rels": _xml(links),
        }
    )
    return _zip(entries)
