import csv
import io
import xml.etree.ElementTree as ET
from copy import deepcopy
from zipfile import ZipFile

import pytest

from routeops.application.analytics_export import MAIN, csv_package, scalar, tables, workbook


@pytest.mark.parametrize(
    "text",
    [
        "=2+2",
        "+cmd",
        "-2+2",
        "@SUM(A1)",
        " =HYPERLINK(1)",
        "\t=1",
        "\r=1",
        "https://example.invalid",
        "mailto:test@example.invalid",
    ],
)
def test_untrusted_text_cannot_become_formula_or_active_external_link(text):
    data = {"data": [["identifier", "text"], ["00007", text]]}
    zipped = ZipFile(io.BytesIO(csv_package(data)))
    rows = list(csv.reader(io.StringIO(zipped.read("data.csv").decode("utf-8-sig"))))
    assert rows[1] == ["00007", "'" + text]
    xlsx = ZipFile(io.BytesIO(workbook(data)))
    sheet = ET.fromstring(xlsx.read("xl/worksheets/sheet1.xml"))
    assert [n.text for n in sheet.findall(f".//{{{MAIN}}}t")][-2:] == ["00007", text]
    assert all(c.attrib["t"] == "inlineStr" for c in sheet.findall(f".//{{{MAIN}}}c"))
    assert not sheet.findall(f".//{{{MAIN}}}f")
    assert not sheet.findall(f".//{{{MAIN}}}hyperlink")
    assert b'TargetMode="External"' not in b"".join(xlsx.read(p) for p in xlsx.namelist())


def test_null_zero_decimals_controls_and_special_characters_remain_distinct_and_deterministic():
    data = {
        "facts": [
            ["null", None],
            ["zero", 0],
            ["decimal", "1.002000"],
            ["literal", "\\N"],
            ["accent", 'Ñ & < > " ,\n'],
            ["control", "a\x00b"],
        ]
    }
    assert scalar(None) == "\\N" and scalar("\\N") == "\\\\N"
    assert scalar(0) == "0" and scalar("1.002000") == "1.002000"
    assert scalar("a\x00b") == "a\\u0000b"
    assert workbook(data) == workbook(data)
    assert csv_package(data) == csv_package(data)
    sheet = ET.fromstring(ZipFile(io.BytesIO(workbook(data))).read("xl/worksheets/sheet1.xml"))
    assert 'Ñ & < > " ,\n' in [c.text for c in sheet.findall(f".//{{{MAIN}}}t")]
    with pytest.raises(ValueError, match="EXPORT_CELL_LIMIT"):
        workbook({"long": [["x" * 32768]]})


def test_tables_copy_authoritative_metrics_without_recalculation_and_preserve_partial_processing():
    m = {
        "value": "0",
        "unit": "ratio",
        "denominator": 2,
        "unavailable_reason": None,
        "calculation_version": "plan-metrics-v1",
        "provenance": "facts",
    }
    document = {
        "run": {"input": {"timezone": "America/Santiago"}, "result": None},
        "metrics": {
            "plan": {"metrics": {"coverage": m}},
            "processing": {
                "total_elapsed": {"value": "12", "classification": "PARTIAL"},
                "durable_total_elapsed": {"value": None, "classification": "UNKNOWN"},
            },
            "current_reservations": [{"order_id": "0001", "status": "RELEASED"}],
        },
        "diagnostics": {"items": []},
    }
    before = deepcopy(document)
    result = tables(document)
    assert result["summary"][1][2] == "0"
    assert result["reservations"][1] == ["0001", "RELEASED"]
    assert ["/total_elapsed/classification", "str", "PARTIAL"] in result["processing"]
    assert ["/durable_total_elapsed/value", "null", None] in result["processing"]
    assert document == before


def test_export_does_not_invent_missing_departure_policy_and_keeps_diagnostic_role():
    document = {
        "run": {
            "input": {},
            "result": {
                "routes": [
                    {
                        "source_vehicle_id": "0001",
                        "distribution_center_id": "CD",
                        "totals": {},
                        "steps": [
                            {
                                "sequence": 0,
                                "kind": "START",
                                "departure_at": "2026-10-15T08:00:00-03:00",
                                "location": {"latitude": -33.4, "longitude": -70.6},
                            }
                        ],
                    }
                ],
                "unassigned": [],
            },
        },
        "metrics": {"plan": None, "processing": {}, "current_reservations": []},
        "diagnostics": {"items": [{"code": "LEGACY", "role": "PRIMARY", "evidence": {}}]},
    }
    values = tables(document)
    assert values["routes"][1][3] == "LEGACY_NOT_RECORDED"
    assert values["routes"][1][4] == "2026-10-15T08:00:00-03:00"
    assert values["diagnostics"][1][values["diagnostics"][0].index("role")] == "PRIMARY"
