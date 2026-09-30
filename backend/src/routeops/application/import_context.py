"""Immutable, canonical context for a provisional import validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONTRACT_VERSION = "2.1"
VALIDATOR_VERSION = "2.3b.1"
type Point = tuple[Decimal, Decimal]
type Ring = tuple[Point, ...]


@dataclass(frozen=True, slots=True)
class PreparedPolygon:
    rings: tuple[Ring, ...]
    bounds: tuple[Decimal, Decimal, Decimal, Decimal]


@dataclass(frozen=True, slots=True)
class ValidationContext:
    planning_date: date
    horizon_start_at: datetime
    horizon_end_at: datetime
    timezone_iana: str
    currency: str
    operational_area: dict[str, Any] | None = None
    contract_version: str = CONTRACT_VERSION
    validator_version: str = VALIDATOR_VERSION

    def __post_init__(self) -> None:
        try:
            zone = ZoneInfo(self.timezone_iana)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("CONTEXT_TIMEZONE_INVALID") from exc
        if (
            self.horizon_start_at.tzinfo is None
            or self.horizon_end_at.tzinfo is None
            or self.horizon_start_at.utcoffset() is None
            or self.horizon_end_at.utcoffset() is None
            or self.horizon_start_at >= self.horizon_end_at
            or self.horizon_start_at.astimezone(zone).date() != self.planning_date
            or (self.horizon_end_at - timedelta(microseconds=1)).astimezone(zone).date()
            != self.planning_date
        ):
            raise ValueError("CONTEXT_HORIZON_INVALID")
        if (
            len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isalpha()
            or self.currency != self.currency.upper()
        ):
            raise ValueError("CONTEXT_CURRENCY_INVALID")
        if self.contract_version != CONTRACT_VERSION or self.validator_version != VALIDATOR_VERSION:
            raise ValueError("CONTEXT_VERSION_INVALID")
        if self.operational_area is not None:
            _polygons(self.operational_area)

    def canonical(self) -> dict[str, Any]:
        return {
            "planning_date": self.planning_date.isoformat(),
            "horizon_start_at": self.horizon_start_at.astimezone(UTC).isoformat(),
            "horizon_end_at": self.horizon_end_at.astimezone(UTC).isoformat(),
            "timezone_iana": self.timezone_iana,
            "currency": self.currency,
            "operational_area": self.operational_area,
            "contract_version": self.contract_version,
            "validator_version": self.validator_version,
        }

    @property
    def sha256(self) -> str:
        encoded = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ValidationContext:
        return cls(
            planning_date=date.fromisoformat(raw["planning_date"]),
            horizon_start_at=datetime.fromisoformat(raw["horizon_start_at"]),
            horizon_end_at=datetime.fromisoformat(raw["horizon_end_at"]),
            timezone_iana=raw["timezone_iana"],
            currency=raw["currency"],
            operational_area=raw.get("operational_area"),
            contract_version=raw.get("contract_version", CONTRACT_VERSION),
            validator_version=raw.get("validator_version", VALIDATOR_VERSION),
        )


def local_instant(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """Reject both DST folds and gaps for a wall-clock value."""
    wall = datetime.combine(day, clock)
    candidates = [wall.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    valid = [
        candidate
        for candidate in candidates
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == wall
    ]
    if not valid:
        raise ValueError("LOCAL_TIME_NONEXISTENT")
    if len(valid) == 2 and valid[0].utcoffset() != valid[1].utcoffset():
        raise ValueError("LOCAL_TIME_AMBIGUOUS")
    return valid[0]


def _polygons(area: dict[str, Any]) -> list[Any]:
    if not isinstance(area, dict) or area.get("type") != "MultiPolygon":
        raise ValueError("CONTEXT_AREA_INVALID")
    polygons = area.get("coordinates")
    if not isinstance(polygons, list) or not polygons or len(polygons) > 100:
        raise ValueError("CONTEXT_AREA_INVALID")
    vertices = 0
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("CONTEXT_AREA_INVALID")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4:
                raise ValueError("CONTEXT_AREA_INVALID")
            vertices += len(ring)
            if vertices > 10_000 or ring[0] != ring[-1]:
                raise ValueError("CONTEXT_AREA_INVALID")
            for point in ring:
                if (
                    not isinstance(point, list)
                    or len(point) != 2
                    or any(
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        for value in point
                    )
                    or not -180 <= point[0] <= 180
                    or not -90 <= point[1] <= 90
                ):
                    raise ValueError("CONTEXT_AREA_INVALID")
    return polygons


def prepare_area(area: dict[str, Any]) -> tuple[PreparedPolygon, ...]:
    prepared = []
    for polygon in _polygons(area):
        rings: tuple[Ring, ...] = tuple(
            tuple((Decimal(str(x)), Decimal(str(y))) for x, y in ring) for ring in polygon
        )
        outer = rings[0]
        bounds = (
            min(point[0] for point in outer),
            max(point[0] for point in outer),
            min(point[1] for point in outer),
            max(point[1] for point in outer),
        )
        prepared.append(PreparedPolygon(rings, bounds))
    return tuple(prepared)


def _ring_relation(ring: Ring, x: Decimal, y: Decimal) -> int:
    """Return 0 outside, 1 inside, 2 on the boundary."""
    inside = False
    for index in range(len(ring) - 1):
        ax, ay = ring[index]
        bx, by = ring[index + 1]
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if cross == 0 and min(ax, bx) <= x <= max(ax, bx) and min(ay, by) <= y <= max(ay, by):
            return 2
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return 1 if inside else 0


def prepared_area_covers(
    area: tuple[PreparedPolygon, ...], longitude: Decimal, latitude: Decimal
) -> bool:
    for polygon in area:
        left, right, bottom, top = polygon.bounds
        if not (left <= longitude <= right and bottom <= latitude <= top):
            continue
        outer = _ring_relation(polygon.rings[0], longitude, latitude)
        if outer == 2:
            return True
        if outer == 1:
            holes = [_ring_relation(ring, longitude, latitude) for ring in polygon.rings[1:]]
            if 2 in holes or not any(value == 1 for value in holes):
                return True
    return False


def area_covers(area: dict[str, Any], longitude: Decimal, latitude: Decimal) -> bool:
    return prepared_area_covers(prepare_area(area), longitude, latitude)
