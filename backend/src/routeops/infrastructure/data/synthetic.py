from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from routeops.domain.models import (
    Capacity,
    Coordinate,
    DistributionCenter,
    InventoryPosition,
    Order,
    OrderLine,
    ScenarioData,
    Vehicle,
)


class SyntheticScenarioLoader:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> ScenarioData:
        with self._path.open(encoding="utf-8") as source:
            raw: dict[str, Any] = json.load(source)
        dataset = raw["dataset"]
        scenario = raw["scenario"]
        centers = tuple(
            DistributionCenter(
                id=item["id"],
                name=item["name"],
                location=Coordinate(
                    latitude=float(item["latitude"]), longitude=float(item["longitude"])
                ),
            )
            for item in raw["distribution_centers"]
        )
        inventory = tuple(InventoryPosition(**item) for item in raw["inventory"])
        vehicles = tuple(self._vehicle(item) for item in raw["vehicles"])
        orders = tuple(self._order(item) for item in raw["orders"])
        return ScenarioData(
            name=scenario["name"],
            timezone=scenario["timezone"],
            currency=scenario["currency"],
            horizon_start=self._instant(scenario["planning_horizon_start"]),
            horizon_end=self._instant(scenario["planning_horizon_end"]),
            centers=centers,
            inventory=inventory,
            vehicles=vehicles,
            orders=orders,
            dataset_name=dataset["name"],
            dataset_seed=int(dataset["seed"]),
            synthetic=bool(dataset["synthetic"]),
        )

    @classmethod
    def _vehicle(cls, item: dict[str, Any]) -> Vehicle:
        weight_grams = Decimal(item["capacity_weight_kg"]) * Decimal(1000)
        volume_cm3 = Decimal(item["capacity_volume_m3"]) * Decimal(1_000_000)
        if weight_grams != weight_grams.to_integral_value():
            raise ValueError(f"vehicle {item['id']} weight capacity is not integral grams")
        if volume_cm3 != volume_cm3.to_integral_value():
            raise ValueError(f"vehicle {item['id']} volume capacity is not integral cm3")
        return Vehicle(
            id=item["id"],
            distribution_center_id=item["distribution_center_id"],
            vehicle_type=item["vehicle_type"],
            capacity=Capacity(
                units=int(item["capacity_units"]),
                weight_grams=int(weight_grams),
                volume_cm3=int(volume_cm3),
            ),
            shift_start=cls._instant(item["shift_start"]),
            shift_end=cls._instant(item["shift_end"]),
            skills=frozenset(item["skills"]),
            fixed_cost=Decimal(item["fixed_cost"]),
            cost_per_hour=Decimal(item["cost_per_hour"]),
            cost_per_km=Decimal(item["cost_per_km"]),
        )

    @classmethod
    def _order(cls, item: dict[str, Any]) -> Order:
        return Order(
            id=item["id"],
            customer_reference=item["customer_reference"],
            location=Coordinate(
                latitude=float(item["latitude"]), longitude=float(item["longitude"])
            ),
            priority=int(item["priority"]),
            time_window_start=cls._instant(item["time_window_start"]),
            time_window_end=cls._instant(item["time_window_end"]),
            service_seconds=int(item["service_minutes"]) * 60,
            required_skills=frozenset(item["required_skills"]),
            lines=tuple(
                OrderLine(
                    sku=line["sku"],
                    quantity=int(line["quantity"]),
                    unit_weight_kg=Decimal(line["unit_weight_kg"]),
                    unit_volume_m3=Decimal(line["unit_volume_m3"]),
                )
                for line in item["lines"]
            ),
        )

    @staticmethod
    def _instant(value: str) -> datetime:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError(f"timestamp must include UTC offset: {value}")
        return result
