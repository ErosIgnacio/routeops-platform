"""Deterministic, header-only CSV and XLSX templates for current imports."""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
import zipfile
from typing import cast

from routeops.application.import_contract import DATASETS, SCHEMA

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"


def csv_template(dataset: str) -> bytes:
    if dataset not in SCHEMA:
        raise ValueError("Unknown dataset")
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\r\n").writerow(item.name for item in SCHEMA[dataset])
    return stream.getvalue().encode("utf-8")


def _xml(element: ET.Element) -> bytes:
    return cast(bytes, ET.tostring(element, encoding="utf-8", xml_declaration=True))


def _column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xlsx_template() -> bytes:
    """Create a reproducible Office Open XML workbook with five header sheets."""
    entries: dict[str, bytes] = {}
    content = ET.Element(f"{{{_CONTENT}}}Types")
    ET.SubElement(
        content,
        f"{{{_CONTENT}}}Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    ET.SubElement(content, f"{{{_CONTENT}}}Default", Extension="xml", ContentType="application/xml")
    ET.SubElement(
        content,
        f"{{{_CONTENT}}}Override",
        PartName="/xl/workbook.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )
    root_rels = ET.Element(f"{{{_PKG}}}Relationships")
    ET.SubElement(
        root_rels,
        f"{{{_PKG}}}Relationship",
        Id="rId1",
        Type=f"{_REL}/officeDocument",
        Target="xl/workbook.xml",
    )
    workbook = ET.Element(f"{{{_MAIN}}}workbook")
    sheets = ET.SubElement(workbook, f"{{{_MAIN}}}sheets")
    workbook_rels = ET.Element(f"{{{_PKG}}}Relationships")
    for number, name in enumerate(DATASETS, start=1):
        path = f"/xl/worksheets/sheet{number}.xml"
        ET.SubElement(
            content,
            f"{{{_CONTENT}}}Override",
            PartName=path,
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
        )
        ET.SubElement(
            sheets,
            f"{{{_MAIN}}}sheet",
            {
                "name": name,
                "sheetId": str(number),
                f"{{{_REL}}}id": f"rId{number}",
            },
        )
        ET.SubElement(
            workbook_rels,
            f"{{{_PKG}}}Relationship",
            Id=f"rId{number}",
            Type=f"{_REL}/worksheet",
            Target=f"worksheets/sheet{number}.xml",
        )
        sheet = ET.Element(f"{{{_MAIN}}}worksheet")
        sheet_data = ET.SubElement(sheet, f"{{{_MAIN}}}sheetData")
        row = ET.SubElement(sheet_data, f"{{{_MAIN}}}row", r="1")
        for index, spec in enumerate(SCHEMA[name], start=1):
            cell = ET.SubElement(row, f"{{{_MAIN}}}c", r=f"{_column(index)}1", t="inlineStr")
            ET.SubElement(ET.SubElement(cell, f"{{{_MAIN}}}is"), f"{{{_MAIN}}}t").text = spec.name
        entries[f"xl/worksheets/sheet{number}.xml"] = _xml(sheet)
    entries["[Content_Types].xml"] = _xml(content)
    entries["_rels/.rels"] = _xml(root_rels)
    entries["xl/workbook.xml"] = _xml(workbook)
    entries["xl/_rels/workbook.xml.rels"] = _xml(workbook_rels)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, mode="w") as archive:
        for path, payload in sorted(entries.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return stream.getvalue()
