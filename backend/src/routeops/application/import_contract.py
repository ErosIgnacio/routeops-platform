"""Versioned, declarative input contract for milestone 2 imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldKind = Literal[
    "id", "text", "latitude", "longitude", "integer", "decimal", "instant", "time", "skills"
]


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    kind: FieldKind
    required: bool = True
    minimum: int = 0
    maximum: int | None = None
    decimal_places: int | None = None
    scale_places: int | None = None


SCHEMA: dict[str, tuple[Field, ...]] = {
    "orders": (
        Field("order_id", "id"),
        Field("customer_reference", "text", maximum=200),
        Field("latitude", "latitude"),
        Field("longitude", "longitude"),
        Field("priority", "integer", maximum=100),
        Field("time_window_start", "instant"),
        Field("time_window_end", "instant"),
        Field("service_minutes", "integer", minimum=1, maximum=1440),
        Field("required_skills", "skills", required=False),
    ),
    "order_lines": (
        Field("order_id", "id"),
        Field("sku", "id"),
        Field("quantity", "integer", minimum=1),
        Field("unit_weight_kg", "decimal", decimal_places=6, scale_places=3),
        Field("unit_volume_m3", "decimal", decimal_places=9, scale_places=6),
    ),
    "inventory": (
        Field("snapshot_at", "instant"),
        Field("distribution_center_id", "id"),
        Field("sku", "id"),
        Field("on_hand_quantity", "integer"),
        Field("externally_reserved_quantity", "integer"),
        Field("safety_stock_quantity", "integer"),
    ),
    "distribution_centers": (
        Field("distribution_center_id", "id"),
        Field("name", "text", maximum=200),
        Field("latitude", "latitude"),
        Field("longitude", "longitude"),
        Field("operating_start", "time"),
        Field("operating_end", "time"),
    ),
    "vehicles": (
        Field("vehicle_id", "id"),
        Field("distribution_center_id", "id"),
        Field("vehicle_type", "id"),
        Field("capacity_units", "integer", minimum=1),
        Field("capacity_weight_kg", "decimal", minimum=1, decimal_places=6, scale_places=3),
        Field("capacity_volume_m3", "decimal", minimum=1, decimal_places=9, scale_places=6),
        Field("shift_start", "time"),
        Field("shift_end", "time"),
        Field("skills", "skills", required=False),
        Field("fixed_cost", "decimal", decimal_places=4, scale_places=4),
        Field("cost_per_hour", "decimal", decimal_places=4, scale_places=4),
        Field("cost_per_km", "decimal", decimal_places=4, scale_places=4),
        Field(
            "max_route_distance_meters",
            "integer",
            required=False,
            minimum=1,
            maximum=2_147_483_647,
        ),
        Field("max_driving_seconds", "integer", required=False, minimum=1, maximum=2_147_483_647),
        Field("max_delivery_tasks", "integer", required=False, minimum=1, maximum=2_147_483_647),
    ),
}

DATASETS = tuple(SCHEMA)
LEGACY_SCHEMA = SCHEMA | {"vehicles": SCHEMA["vehicles"][:-3]}
