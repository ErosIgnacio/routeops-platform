"""Bounded, read-only validation of the five RouteOps import datasets."""

from __future__ import annotations

import codecs
import csv
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation
from typing import IO, Any, Literal
from zoneinfo import ZoneInfo

from routeops.application.import_context import (
    CONTRACT_VERSION,
    ValidationContext,
    local_instant,
    prepare_area,
    prepared_area_covers,
)
from routeops.application.import_contract import DATASETS, LEGACY_SCHEMA, SCHEMA, Field

Severity = Literal["ERROR", "WARNING"]
_ID = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
_SKILL = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_INTEGER = re.compile(r"[0-9]+\Z")
_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
_CELL = re.compile(r"([A-Z]+)([1-9][0-9]*)\Z")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_RELATION_FIELDS = {
    "orders": frozenset({"order_id"}),
    "order_lines": frozenset({"order_id"}),
    "inventory": frozenset({"distribution_center_id", "snapshot_at"}),
    "distribution_centers": frozenset(
        {"distribution_center_id", "operating_start", "operating_end"}
    ),
    "vehicles": frozenset({"distribution_center_id", "shift_start", "shift_end"}),
}


@dataclass(frozen=True, slots=True)
class ImportLimits:
    max_file_bytes: int = 20 * 1024 * 1024
    max_package_bytes: int = 100 * 1024 * 1024
    max_rows_per_dataset: int = 250_000
    max_columns: int = 200
    max_cell_characters: int = 4_096
    max_xlsx_cells: int = 1_000_000
    max_xlsx_expanded_bytes: int = 200 * 1024 * 1024
    max_xlsx_metadata_bytes: int = 4 * 1024 * 1024
    max_xlsx_shared_strings_bytes: int = 32 * 1024 * 1024
    max_xlsx_entries: int = 200
    max_xlsx_compression_ratio: int = 100
    max_issues: int = 1_000

    def __post_init__(self) -> None:
        if any(value <= 0 for value in asdict(self).values()):
            raise ValueError("All import limits must be positive")

    @classmethod
    def from_environment(cls) -> ImportLimits:
        defaults = cls()
        values = {
            item.name: int(
                os.getenv(f"ROUTEOPS_IMPORT_{item.name.upper()}", str(getattr(defaults, item.name)))
            )
            for item in fields(cls)
        }
        return cls(**values)


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    severity: Severity
    dataset: str | None
    source: str | None
    row: int | None
    field: str | None
    message: str
    value_excerpt: str | None = None


@dataclass(slots=True)
class DatasetCount:
    total: int = 0
    accepted: int = 0
    rejected: int = 0


@dataclass(slots=True)
class ValidationReport:
    contract_version: str = CONTRACT_VERSION
    issues: list[Issue] = field(default_factory=list)
    counts: dict[str, DatasetCount] = field(
        default_factory=lambda: {name: DatasetCount() for name in DATASETS}
    )

    @property
    def valid(self) -> bool:
        return all(issue.severity != "ERROR" for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "valid": self.valid,
            "counts": {name: asdict(count) for name, count in self.counts.items()},
            "issues": [asdict(issue) for issue in self.issues],
        }


def _excerpt(value: str | None) -> str | None:
    if value is None:
        return None
    return f"[value omitted; {min(len(value), 9999)} characters]"


class _Collector:
    def __init__(
        self,
        limits: ImportLimits,
        context: ValidationContext | None = None,
        row_sink: Callable[[str, str, int, dict[str, Any]], None] | None = None,
    ) -> None:
        self.limits = limits
        self.context = context
        self.schema = LEGACY_SCHEMA if context and context.contract_version == "2.1" else SCHEMA
        self.row_sink = row_sink
        self.area = (
            prepare_area(context.operational_area)
            if context is not None and context.operational_area is not None
            else None
        )
        self.report = ValidationReport(
            contract_version=context.contract_version if context else CONTRACT_VERSION
        )
        self._truncated = False
        self.rows: dict[str, list[tuple[int, dict[str, Any]]]] = {name: [] for name in DATASETS}
        self.keys: dict[str, set[tuple[str, ...]]] = {name: set() for name in DATASETS}

    def add(
        self,
        code: str,
        severity: Severity,
        dataset: str | None = None,
        source: str | None = None,
        row: int | None = None,
        field_name: str | None = None,
        value: str | None = None,
    ) -> None:
        if len(self.report.issues) >= self.limits.max_issues:
            if not self._truncated:
                self._truncated = True
                self.report.issues.append(
                    Issue(
                        "REPORT_TRUNCATED",
                        "ERROR",
                        None,
                        None,
                        None,
                        None,
                        "Further issues were omitted; increase the configured report limit",
                    )
                )
            return
        self.report.issues.append(
            Issue(
                code, severity, dataset, source, row, field_name, _MESSAGES[code], _excerpt(value)
            )
        )

    def dataset(self, name: str, source: str, rows: Iterator[tuple[int, list[str]]]) -> None:
        try:
            header_row, headers = next(rows)
        except StopIteration:
            self.add("HEADER_MISSING", "ERROR", name, source, 1)
            return
        if header_row != 1:
            self.add("HEADER_MISSING", "ERROR", name, source, 1)
            return
        expected = {item.name for item in self.schema[name]}
        if len(headers) > self.limits.max_columns:
            self.add("COLUMN_LIMIT", "ERROR", name, source, 1)
            return
        if len(set(headers)) != len(headers):
            self.add("HEADER_DUPLICATE", "ERROR", name, source, 1)
        for item in self.schema[name]:
            if item.required and item.name not in headers:
                self.add("HEADER_MISSING", "ERROR", name, source, 1, item.name)
        for column_number, header in enumerate(headers, start=1):
            if header not in expected:
                self.add("HEADER_UNKNOWN", "WARNING", name, source, 1, f"column {column_number}")
        if len(set(headers)) != len(headers) or any(
            item.required and item.name not in headers for item in self.schema[name]
        ):
            for row_number, _ in rows:
                self.report.counts[name].total += 1
                if self.report.counts[name].total > self.limits.max_rows_per_dataset:
                    self.add("ROW_LIMIT", "ERROR", name, source, row_number)
                    break
            return
        for row_number, cells in rows:
            count = self.report.counts[name]
            count.total += 1
            if count.total > self.limits.max_rows_per_dataset:
                self.add("ROW_LIMIT", "ERROR", name, source, row_number)
                count.rejected += 1
                break
            if len(cells) != len(headers):
                self.add("ROW_WIDTH", "ERROR", name, source, row_number)
                continue
            raw = dict(zip(headers, cells, strict=True))
            parsed: dict[str, Any] = {}
            for spec in self.schema[name]:
                value = raw.get(spec.name, "").strip()
                if not value:
                    if spec.required:
                        self.add("VALUE_REQUIRED", "ERROR", name, source, row_number, spec.name)
                    continue
                if len(value) > self.limits.max_cell_characters:
                    self.add("CELL_LENGTH_LIMIT", "ERROR", name, source, row_number, spec.name)
                    continue
                try:
                    parsed[spec.name] = _parse(spec, value)
                except ValueError as exc:
                    self.add(str(exc), "ERROR", name, source, row_number, spec.name, value)
            self._row_rules(name, source, row_number, parsed)
            if self.row_sink is not None:
                self.row_sink(name, source, row_number, parsed)
            self.rows[name].append(
                (
                    row_number,
                    {key: value for key, value in parsed.items() if key in _RELATION_FIELDS[name]},
                )
            )

    def _row_rules(self, name: str, source: str, row: int, data: dict[str, Any]) -> None:
        key_fields = {
            "orders": ("order_id",),
            "order_lines": ("order_id", "sku"),
            "inventory": ("distribution_center_id", "sku"),
            "distribution_centers": ("distribution_center_id",),
            "vehicles": ("vehicle_id",),
        }[name]
        if all(field_name in data for field_name in key_fields):
            key = tuple(data[field_name] for field_name in key_fields)
            if key in self.keys[name]:
                self.add("ID_DUPLICATE", "ERROR", name, source, row, ",".join(key_fields))
            self.keys[name].add(key)
        pairs = {
            "orders": (("time_window_start", "time_window_end"),),
            "distribution_centers": (("operating_start", "operating_end"),),
            "vehicles": (("shift_start", "shift_end"),),
        }.get(name, ())
        for start, end in pairs:
            if start in data and end in data and data[start] >= data[end]:
                self.add("INTERVAL_INVALID", "ERROR", name, source, row, end)
        if (
            name == "inventory"
            and all(
                k in data
                for k in (
                    "on_hand_quantity",
                    "externally_reserved_quantity",
                    "safety_stock_quantity",
                )
            )
            and data["externally_reserved_quantity"] + data["safety_stock_quantity"]
            > data["on_hand_quantity"]
        ):
            self.add(
                "STOCK_INCONSISTENT", "ERROR", name, source, row, "externally_reserved_quantity"
            )
        if self.context is None:
            return
        context = self.context
        if (
            name == "orders"
            and all(field_name in data for field_name in ("time_window_start", "time_window_end"))
            and not (
                context.horizon_start_at
                <= data["time_window_start"].astimezone(UTC)
                < data["time_window_end"].astimezone(UTC)
                <= context.horizon_end_at
            )
        ):
            self.add("WINDOW_OUTSIDE_HORIZON", "ERROR", name, source, row, "time_window_start")
        if name in ("distribution_centers", "vehicles"):
            zone = ZoneInfo(context.timezone_iana)
            fields = (
                ("operating_start", "operating_end")
                if name == "distribution_centers"
                else ("shift_start", "shift_end")
            )
            for field_name in fields:
                clock = data.get(field_name)
                if clock is not None:
                    try:
                        instant = local_instant(context.planning_date, clock, zone)
                    except ValueError as exc:
                        self.add(str(exc), "ERROR", name, source, row, field_name)
                    else:
                        inside = (
                            context.horizon_start_at < instant <= context.horizon_end_at
                            if field_name.endswith("_end")
                            else context.horizon_start_at <= instant < context.horizon_end_at
                        )
                        if not inside:
                            self.add(
                                "LOCAL_TIME_OUTSIDE_HORIZON", "ERROR", name, source, row, field_name
                            )
        if (
            self.area is not None
            and all(field_name in data for field_name in ("latitude", "longitude"))
            and not prepared_area_covers(self.area, data["longitude"], data["latitude"])
        ):
            self.add("POINT_OUTSIDE_AREA", "WARNING", name, source, row, "latitude")

    def relations(self, sources: Mapping[str, str]) -> None:
        orders = {key[0] for key in self.keys["orders"]}
        centers = {key[0] for key in self.keys["distribution_centers"]}
        lines = {key[0] for key in self.keys["order_lines"]}
        for name, field_name, targets in (
            ("order_lines", "order_id", orders),
            ("inventory", "distribution_center_id", centers),
            ("vehicles", "distribution_center_id", centers),
        ):
            for row, data in self.rows[name]:
                if field_name in data and data[field_name] not in targets:
                    self.add("RELATION_MISSING", "ERROR", name, sources[name], row, field_name)
        for row, data in self.rows["orders"]:
            if data.get("order_id") not in lines:
                self.add(
                    "ORDER_LINES_MISSING", "ERROR", "orders", sources["orders"], row, "order_id"
                )
        snapshot_at: datetime | None = None
        for row, data in self.rows["inventory"]:
            current = data.get("snapshot_at")
            if current is None:
                continue
            if snapshot_at is None:
                snapshot_at = current
            elif current != snapshot_at:
                self.add(
                    "SNAPSHOT_MISMATCH",
                    "ERROR",
                    "inventory",
                    sources["inventory"],
                    row,
                    "snapshot_at",
                )
        center_hours = {
            data["distribution_center_id"]: data
            for _, data in self.rows["distribution_centers"]
            if "distribution_center_id" in data
        }
        for row, data in self.rows["vehicles"]:
            center = center_hours.get(data.get("distribution_center_id"))
            if (
                center
                and all(k in data for k in ("shift_start", "shift_end"))
                and all(k in center for k in ("operating_start", "operating_end"))
                and not (
                    center["operating_start"]
                    <= data["shift_start"]
                    < data["shift_end"]
                    <= center["operating_end"]
                )
            ):
                self.add(
                    "SHIFT_OUTSIDE_HOURS",
                    "ERROR",
                    "vehicles",
                    sources["vehicles"],
                    row,
                    "shift_start",
                )

    def finalize_counts(self) -> None:
        rejected: dict[str, set[int]] = {name: set() for name in DATASETS}
        invalid_headers = {
            issue.dataset
            for issue in self.report.issues
            if issue.severity == "ERROR" and issue.row == 1
        }
        for issue in self.report.issues:
            if issue.severity == "ERROR" and issue.dataset in rejected and issue.row is not None:
                rejected[issue.dataset].add(issue.row)
        for name, count in self.report.counts.items():
            count.rejected = (
                count.total if name in invalid_headers else min(count.total, len(rejected[name]))
            )
            count.accepted = count.total - count.rejected


def _parse(spec: Field, value: str) -> Any:
    if spec.kind == "id":
        if not _ID.fullmatch(value):
            raise ValueError("IDENTIFIER_INVALID")
        return value
    if spec.kind == "text":
        if (spec.maximum is not None and len(value) > spec.maximum) or any(
            ord(char) < 32 for char in value
        ):
            raise ValueError("TEXT_INVALID")
        return value
    if spec.kind == "skills":
        parts = [part.strip().lower() for part in value.split("|")]
        if len(set(parts)) != len(parts) or any(
            len(part) > 100 or not _SKILL.fullmatch(part) for part in parts
        ):
            raise ValueError("SKILLS_INVALID")
        return frozenset(parts)
    if spec.kind == "integer":
        if not _INTEGER.fullmatch(value):
            raise ValueError("INTEGER_INVALID")
        if len(value) > 18:
            raise ValueError("QUANTITY_RANGE")
        integer_value = int(value)
        if integer_value < spec.minimum or (
            spec.maximum is not None and integer_value > spec.maximum
        ):
            raise ValueError("QUANTITY_RANGE")
        return integer_value
    if spec.kind in ("decimal", "latitude", "longitude"):
        if not _DECIMAL.fullmatch(value):
            raise ValueError("DECIMAL_INVALID")
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("DECIMAL_INVALID") from exc
        if not decimal_value.is_finite():
            raise ValueError("DECIMAL_INVALID")
        if spec.kind == "latitude" and not -90 <= decimal_value <= 90:
            raise ValueError("COORDINATE_RANGE")
        if spec.kind == "longitude" and not -180 <= decimal_value <= 180:
            raise ValueError("COORDINATE_RANGE")
        if spec.kind == "decimal":
            if decimal_value < 0 or (spec.minimum == 1 and decimal_value <= 0):
                raise ValueError("QUANTITY_RANGE")
            exponent = decimal_value.as_tuple().exponent
            if (
                spec.decimal_places is not None
                and isinstance(exponent, int)
                and -exponent > spec.decimal_places
            ):
                raise ValueError("DECIMAL_PRECISION")
            if spec.scale_places is not None and isinstance(exponent, int):
                extra_places = -exponent - spec.scale_places
                if (
                    extra_places > 0
                    and decimal_value != 0
                    and any(digit != 0 for digit in decimal_value.as_tuple().digits[-extra_places:])
                ):
                    raise ValueError("DECIMAL_SCALE")
        return decimal_value
    if spec.kind == "instant":
        try:
            result = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("INSTANT_INVALID") from exc
        if result.tzinfo is None or result.utcoffset() is None or "T" not in value:
            raise ValueError("INSTANT_INVALID")
        return result
    if spec.kind == "time":
        if not re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", value):
            raise ValueError("TIME_INVALID")
        try:
            return time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("TIME_INVALID") from exc
    raise AssertionError(spec.kind)


def validate_package(
    files: Mapping[str, bytes],
    limits: ImportLimits | None = None,
    *,
    context: ValidationContext | None = None,
    row_sink: Callable[[str, str, int, dict[str, Any]], None] | None = None,
) -> ValidationReport:
    """Validate exactly five named CSVs or one XLSX, without side effects."""
    limits = limits or ImportLimits()
    collector = _Collector(limits, context, row_sink)
    if not files:
        collector.add("PACKAGE_INCOMPLETE", "ERROR")
        return collector.report
    total = sum(len(content) for content in files.values())
    if total > limits.max_package_bytes:
        collector.add("PACKAGE_LIMIT", "ERROR")
        return collector.report
    if any(len(content) > limits.max_file_bytes for content in files.values()):
        collector.add("FILE_LIMIT", "ERROR")
        return collector.report
    names = set(files)
    xlsx = len(files) == 1 and next(iter(names)).endswith(".xlsx")
    csv_names = {f"{name}.csv" for name in DATASETS}
    if not xlsx and names != csv_names:
        if any(not name.endswith(".csv") for name in names):
            collector.add("FORMAT_UNSUPPORTED", "ERROR")
        if names - csv_names:
            collector.add("FILE_UNKNOWN", "ERROR")
        if csv_names - names:
            collector.add("PACKAGE_INCOMPLETE", "ERROR")
        return collector.report
    if xlsx:
        _, content = next(iter(files.items()))
        _validate_xlsx(collector, content)
        sources = {name: f"workbook.xlsx:{name}" for name in DATASETS}
    else:
        sources = {name: f"{name}.csv" for name in DATASETS}
        for name in DATASETS:
            try:
                decoded = files[sources[name]].decode("utf-8-sig")
                rows = enumerate(csv.reader(io.StringIO(decoded, newline=""), strict=True), start=1)
                collector.dataset(name, sources[name], rows)
            except UnicodeDecodeError, csv.Error:
                collector.add("CSV_INVALID", "ERROR", name, sources[name])
    collector.relations(sources)
    collector.finalize_counts()
    return collector.report


def _column_index(address: str) -> int:
    match = _CELL.fullmatch(address)
    if match is None:
        raise ValueError("XLSX_INVALID")
    result = 0
    for char in match[1]:
        result = result * 26 + ord(char) - ord("A") + 1
    return result


class _CheckedXmlStream:
    def __init__(self, stream: IO[bytes]) -> None:
        self.stream = stream
        self.tail = b""
        self.prefix = b""
        self.decoder: codecs.IncrementalDecoder | None = None
        self.encoding_checked = False

    def read(self, size: int = -1) -> bytes:
        chunk = self.stream.read(size)
        # Expat's vendored UTF-16 parser may accept malformed surrogate pairs
        # (CVE-2026-93990). Validate before passing any such input to Expat.
        if not self.encoding_checked:
            self.prefix += chunk
            if len(self.prefix) >= 4 or not chunk:
                self.encoding_checked = True
                signatures = (
                    (b"\xff\xfe\x00\x00", "utf-32"),
                    (b"\x00\x00\xfe\xff", "utf-32"),
                    (b"\x00\x00\x00<", "utf-32-be"),
                    (b"<\x00\x00\x00", "utf-32-le"),
                    (b"\xff\xfe", "utf-16"),
                    (b"\xfe\xff", "utf-16"),
                    (b"\x00<", "utf-16-be"),
                    (b"<\x00", "utf-16-le"),
                )
                for signature, encoding in signatures:
                    if self.prefix.startswith(signature):
                        self.decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
                        break
                checked = self.prefix
                self.prefix = b""
            else:
                checked = b""
        else:
            checked = chunk
        if self.decoder is not None:
            try:
                self.decoder.decode(checked, final=not chunk)
            except UnicodeDecodeError as exc:
                raise ValueError("XLSX_INVALID") from exc
        # Null-separated UTF-16/32 must not bypass the DTD/entity guard.
        sample = (self.tail + chunk).replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in sample or b"<!ENTITY" in sample:
            raise ValueError("XLSX_INVALID")
        self.tail = (self.tail + chunk)[-64:]
        return chunk


class _SheetError(ValueError):
    def __init__(self, code: str, row: int, field_name: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.row = row
        self.field_name = field_name


def _xml_root(archive: zipfile.ZipFile, path: str, max_bytes: int) -> ET.Element:
    if archive.getinfo(path).file_size > max_bytes:
        raise ValueError("XLSX_EXPANSION_LIMIT")
    payload = archive.read(path)
    checked = _CheckedXmlStream(io.BytesIO(payload))
    checked.read()
    checked.read()  # Finalize decoding, including a truncated code unit/pair.
    return ET.fromstring(payload)


def _sheet_rows(
    archive: zipfile.ZipFile,
    path: str,
    strings: list[str],
    limits: ImportLimits,
    cell_counter: list[int],
) -> Iterator[tuple[int, list[str]]]:
    with archive.open(path) as raw_stream:
        stream = _CheckedXmlStream(raw_stream)
        last_row = 0
        current_row = 0
        current_cell: str | None = None
        for event, element in ET.iterparse(stream, events=("start", "end")):
            if event == "start" and element.tag == f"{_NS}row":
                row_text = element.get("r", "")
                if len(row_text) > 10 or not row_text.isdecimal():
                    raise ValueError("XLSX_INVALID")
                current_row = int(row_text)
                if current_row <= last_row:
                    raise ValueError("XLSX_INVALID")
                last_row = current_row
                continue
            if event == "start" and element.tag == f"{_NS}c":
                address = element.get("r", "")
                current_cell = address if len(address) <= 10 and _CELL.fullmatch(address) else None
                continue
            if event != "end":
                continue
            if element.tag in (f"{_NS}f", f"{_NS}formula"):
                raise _SheetError("XLSX_FORMULA", max(current_row, 1), current_cell)
            if element.tag != f"{_NS}row":
                continue
            row_number = current_row
            cells: dict[int, str] = {}
            for cell in element.findall(f"{_NS}c"):
                cell_counter[0] += 1
                if cell_counter[0] > limits.max_xlsx_cells:
                    raise _SheetError("XLSX_CELL_LIMIT", row_number)
                if cell.find(f"{_NS}f") is not None:
                    raise _SheetError("XLSX_FORMULA", row_number, current_cell)
                address = cell.get("r", "")
                cell_match = _CELL.fullmatch(address) if len(address) <= 16 else None
                if cell_match is None or int(cell_match[2]) != row_number:
                    raise ValueError("XLSX_INVALID")
                index = _column_index(address)
                if index > limits.max_columns:
                    raise _SheetError("COLUMN_LIMIT", row_number, current_cell)
                if index in cells:
                    raise ValueError("XLSX_INVALID")
                value_node = cell.find(f"{_NS}v")
                kind = cell.get("t")
                if kind == "s":
                    try:
                        string_index = int(value_node.text or "") if value_node is not None else -1
                        if string_index < 0:
                            raise IndexError
                        value = strings[string_index]
                    except (ValueError, IndexError) as exc:
                        raise ValueError("XLSX_INVALID") from exc
                elif kind == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{_NS}t"))
                elif kind in (None, "n", "str", "b"):
                    value = value_node.text or "" if value_node is not None else ""
                else:
                    raise ValueError("XLSX_INVALID")
                cells[index] = value
            yield (
                row_number,
                [cells.get(index, "") for index in range(1, max(cells, default=0) + 1)],
            )
            element.clear()


def _validate_xlsx(collector: _Collector, content: bytes) -> None:
    limits = collector.limits
    filename = "workbook.xlsx"
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_xlsx_entries:
                raise ValueError("XLSX_EXPANSION_LIMIT")
            paths = {info.filename for info in infos}
            if len(paths) != len(infos):
                raise ValueError("XLSX_INVALID")
            if any(
                name.startswith("/") or "\\" in name or ".." in name.split("/") for name in paths
            ):
                raise ValueError("XLSX_INVALID")
            if any(name.startswith("xl/externalLinks/") for name in paths):
                raise ValueError("XLSX_EXTERNAL_LINK")
            if any(name.endswith(".bin") or name.startswith("xl/embeddings/") for name in paths):
                raise ValueError("FORMAT_UNSUPPORTED")
            if any(
                info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                for info in infos
            ):
                raise ValueError("XLSX_INVALID")
            if sum(info.file_size for info in infos) > limits.max_xlsx_expanded_bytes or any(
                info.file_size > max(1, info.compress_size) * limits.max_xlsx_compression_ratio
                for info in infos
            ):
                raise ValueError("XLSX_EXPANSION_LIMIT")
            for name in paths:
                if name.endswith(".rels"):
                    rels = _xml_root(archive, name, limits.max_xlsx_metadata_bytes)
                    if any(item.get("TargetMode") == "External" for item in rels):
                        raise ValueError("XLSX_EXTERNAL_LINK")
            workbook = _xml_root(archive, "xl/workbook.xml", limits.max_xlsx_metadata_bytes)
            rels = _xml_root(archive, "xl/_rels/workbook.xml.rels", limits.max_xlsx_metadata_bytes)
            relationships = {item.get("Id"): item.get("Target", "") for item in rels}
            sheets = workbook.find(f"{_NS}sheets")
            if (
                sheets is None
                or len(sheets) != len(DATASETS)
                or {item.get("name") for item in sheets} != set(DATASETS)
            ):
                raise ValueError("XLSX_SHEETS")
            strings: list[str] = []
            if "xl/sharedStrings.xml" in paths:
                info = archive.getinfo("xl/sharedStrings.xml")
                if info.file_size > limits.max_xlsx_shared_strings_bytes:
                    raise ValueError("XLSX_EXPANSION_LIMIT")
                root = _xml_root(archive, info.filename, limits.max_xlsx_shared_strings_bytes)
                strings = [
                    "".join(node.text or "" for node in item.iter(f"{_NS}t"))
                    for item in root.findall(f"{_NS}si")
                ]
            cell_counter = [0]
            for item in sheets:
                name = item.get("name", "")
                target = relationships.get(item.get(f"{_REL}id"), "")
                path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
                if path not in paths or not path.startswith("xl/worksheets/"):
                    raise ValueError("XLSX_INVALID")
                try:
                    collector.dataset(
                        name,
                        f"{filename}:{name}",
                        _sheet_rows(archive, path, strings, limits, cell_counter),
                    )
                except _SheetError as exc:
                    collector.add(
                        exc.code,
                        "ERROR",
                        name,
                        f"{filename}:{name}",
                        exc.row,
                        exc.field_name,
                    )
                    return
            if archive.testzip() is not None:
                raise ValueError("XLSX_INVALID")
    except KeyError, zipfile.BadZipFile, ET.ParseError, RuntimeError, OSError:
        collector.add("XLSX_INVALID", "ERROR", source=filename)
    except ValueError as exc:
        code = str(exc)
        collector.add(code if code in _MESSAGES else "XLSX_INVALID", "ERROR", source=filename)


_MESSAGES = {
    "PACKAGE_INCOMPLETE": "Provide all five named CSV files or one XLSX workbook",
    "PACKAGE_LIMIT": "The import package exceeds its configured size limit",
    "FILE_LIMIT": "A file exceeds its configured size limit",
    "FORMAT_UNSUPPORTED": "Only five CSV files or one XLSX workbook are supported",
    "FILE_UNKNOWN": "A CSV filename is not part of the contract",
    "CSV_INVALID": "CSV must be valid UTF-8 with valid comma-delimited rows",
    "XLSX_INVALID": "The XLSX workbook is corrupt or uses an unsupported structure",
    "XLSX_SHEETS": "The workbook must contain exactly the five required sheets",
    "XLSX_FORMULA": "Formulas are not accepted",
    "XLSX_EXTERNAL_LINK": "External workbook links are not accepted",
    "XLSX_EXPANSION_LIMIT": "The workbook exceeds configured ZIP expansion limits",
    "XLSX_CELL_LIMIT": "The workbook exceeds its configured cell limit",
    "COLUMN_LIMIT": "The dataset exceeds its configured column limit",
    "CELL_LENGTH_LIMIT": "The cell exceeds its configured character limit",
    "ROW_LIMIT": "The dataset exceeds its configured row limit",
    "ROW_WIDTH": "The row has a different number of cells than the header",
    "HEADER_MISSING": "A required header is missing",
    "HEADER_DUPLICATE": "A header appears more than once",
    "HEADER_UNKNOWN": "An unknown column will be ignored",
    "VALUE_REQUIRED": "This field requires a non-blank value",
    "IDENTIFIER_INVALID": "Use 1-100 letters, numbers, dots, underscores, or hyphens",
    "TEXT_INVALID": "The text exceeds its maximum length",
    "SKILLS_INVALID": "Use unique lowercase skill slugs separated by |",
    "INTEGER_INVALID": "Enter a whole non-negative number without separators",
    "QUANTITY_RANGE": "The quantity is outside its allowed range",
    "DECIMAL_INVALID": "Enter a finite base-10 number without locale separators",
    "DECIMAL_PRECISION": "The decimal has too many fractional digits",
    "DECIMAL_SCALE": "The decimal cannot be represented exactly in solver units",
    "COORDINATE_RANGE": "The coordinate is outside the global range",
    "INSTANT_INVALID": "Enter an ISO 8601 instant with a UTC offset",
    "TIME_INVALID": "Enter local time as HH:MM or HH:MM:SS",
    "INTERVAL_INVALID": "The start must be before the end",
    "STOCK_INCONSISTENT": "External reservations plus safety stock exceed stock on hand",
    "ID_DUPLICATE": "The source identifier or composite key is duplicated",
    "RELATION_MISSING": "The referenced order or distribution center is missing",
    "ORDER_LINES_MISSING": "An order requires at least one order line",
    "SNAPSHOT_MISMATCH": "All inventory rows must share one snapshot instant",
    "SHIFT_OUTSIDE_HOURS": "The vehicle shift must fit within center hours",
    "WINDOW_OUTSIDE_HORIZON": "The order window must fit within the planning horizon",
    "LOCAL_TIME_NONEXISTENT": "The local time does not exist on the planning date",
    "LOCAL_TIME_AMBIGUOUS": "The local time is ambiguous on the planning date",
    "LOCAL_TIME_OUTSIDE_HORIZON": "The local time must fit within the planning horizon",
    "POINT_OUTSIDE_AREA": "The point lies outside the optional operational area",
}
