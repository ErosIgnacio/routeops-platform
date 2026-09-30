"""Contextual rules reuse the 2.1 CSV/XLSX parser and stable issue model."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from routeops.application.import_context import ValidationContext, area_covers, local_instant
from routeops.application.import_templates import csv_template, xlsx_template
from routeops.application.import_validation import validate_package


def context(day: str = "2026-10-15", area: dict[str, object] | None = None) -> ValidationContext:
    return ValidationContext(
        planning_date=date.fromisoformat(day),
        horizon_start_at=datetime.fromisoformat(f"{day}T00:00:00-03:00"),
        horizon_end_at=datetime.fromisoformat(f"{day}T23:59:59-03:00"),
        timezone_iana="America/Santiago",
        currency="CLP",
        operational_area=area,
    )


def files(**rows: str) -> dict[str, bytes]:
    return {
        f"{name}.csv": csv_template(name) + rows.get(name, "").encode()
        for name in ("orders", "order_lines", "inventory", "distribution_centers", "vehicles")
    }


def codes(**rows: str) -> set[str]:
    return {issue.code for issue in validate_package(files(**rows), context=context()).issues}


def test_context_fingerprint_is_canonical_and_invalid_context_is_rejected() -> None:
    assert context().sha256 == context().sha256
    with pytest.raises(ValueError, match="CONTEXT_TIMEZONE_INVALID"):
        ValidationContext(
            planning_date=date(2026, 10, 15),
            horizon_start_at=datetime.fromisoformat("2026-10-15T00:00:00-03:00"),
            horizon_end_at=datetime.fromisoformat("2026-10-15T23:59:00-03:00"),
            timezone_iana="Not/AZone",
            currency="CLP",
        )
    with pytest.raises(ValueError, match="CONTEXT_CURRENCY_INVALID"):
        ValidationContext(
            planning_date=date(2026, 10, 15),
            horizon_start_at=datetime.fromisoformat("2026-10-15T00:00:00-03:00"),
            horizon_end_at=datetime.fromisoformat("2026-10-15T23:59:00-03:00"),
            timezone_iana="America/Santiago",
            currency="clp",
        )


def test_order_offset_is_absolute_and_must_fit_horizon() -> None:
    assert "WINDOW_OUTSIDE_HORIZON" not in codes(
        orders="ORD-1,C,-33.4,-70.6,1,2026-10-15T12:00:00Z,2026-10-15T14:00:00Z,5,\n"
    )
    assert "WINDOW_OUTSIDE_HORIZON" in codes(
        orders="ORD-1,C,-33.4,-70.6,1,2026-10-16T02:00:00Z,2026-10-16T04:00:00Z,5,\n"
    )


@pytest.mark.parametrize(
    ("day", "clock", "expected"),
    [
        ("2026-11-01", "01:30", "LOCAL_TIME_AMBIGUOUS"),
        ("2026-03-08", "02:30", "LOCAL_TIME_NONEXISTENT"),
    ],
)
def test_dst_local_times(day: str, clock: str, expected: str) -> None:
    from datetime import time
    from zoneinfo import ZoneInfo

    with pytest.raises(ValueError, match=expected):
        local_instant(
            date.fromisoformat(day), time.fromisoformat(clock), ZoneInfo("America/New_York")
        )


@pytest.mark.parametrize(
    ("day", "start_offset", "end_offset", "clock", "expected"),
    [
        ("2026-11-01", "-04:00", "-05:00", "01:30", "LOCAL_TIME_AMBIGUOUS"),
        ("2026-03-08", "-05:00", "-04:00", "02:30", "LOCAL_TIME_NONEXISTENT"),
    ],
)
def test_dataset_local_hours_report_dst_issue(
    day: str, start_offset: str, end_offset: str, clock: str, expected: str
) -> None:
    ny = ValidationContext(
        planning_date=date.fromisoformat(day),
        horizon_start_at=datetime.fromisoformat(f"{day}T00:00:00{start_offset}"),
        horizon_end_at=datetime.fromisoformat(f"{day}T23:59:59{end_offset}"),
        timezone_iana="America/New_York",
        currency="USD",
    )
    report = validate_package(
        files(distribution_centers=f"DC-1,Depot,40.0,-73.0,{clock},04:00\n"), context=ny
    )
    assert expected in {issue.code for issue in report.issues}


def test_operational_area_boundary_is_covered_and_outside_is_warning() -> None:
    area: dict[str, object] = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[-71.0, -34.0], [-70.0, -34.0], [-70.0, -33.0], [-71.0, -33.0], [-71.0, -34.0]]]
        ],
    }
    assert area_covers(area, Decimal("-71.0"), Decimal("-33.5"))
    assert not area_covers(area, Decimal("-69.0"), Decimal("-33.5"))
    assert not area_covers(area, Decimal("-71.000000000001"), Decimal("-33.5"))
    report = validate_package(
        files(
            distribution_centers="DC-1,Depot,-33.5,-71.0,08:00,18:00\n",
            orders="ORD-1,C,-33.5,-69.0,1,2026-10-15T09:00:00-03:00,2026-10-15T10:00:00-03:00,5,\n",
        ),
        context=context(area=area),
    )
    assert any(
        issue.code == "POINT_OUTSIDE_AREA" and issue.severity == "WARNING"
        for issue in report.issues
    )
    assert not any(
        issue.code == "POINT_OUTSIDE_AREA" and issue.dataset == "distribution_centers"
        for issue in report.issues
    )


def test_csv_and_xlsx_share_contextual_path() -> None:
    assert validate_package(files(), context=context()).valid
    assert validate_package({"workbook.xlsx": xlsx_template()}, context=context()).valid
