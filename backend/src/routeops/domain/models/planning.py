from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Coordinate:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be in [-90, 90]")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be in [-180, 180]")


@dataclass(frozen=True, slots=True)
class Capacity:
    units: int
    weight_grams: int
    volume_cm3: int

    def __post_init__(self) -> None:
        if min(self.units, self.weight_grams, self.volume_cm3) < 0:
            raise ValueError("capacity dimensions cannot be negative")

    def fits(self, demand: Capacity) -> bool:
        return (
            demand.units <= self.units
            and demand.weight_grams <= self.weight_grams
            and demand.volume_cm3 <= self.volume_cm3
        )

    def as_vroom_array(self) -> list[int]:
        return [self.units, self.weight_grams, self.volume_cm3]


@dataclass(frozen=True, slots=True)
class DistributionCenter:
    id: str
    name: str
    location: Coordinate


@dataclass(frozen=True, slots=True)
class InventoryPosition:
    distribution_center_id: str
    sku: str
    on_hand: int
    reserved: int
    safety_stock: int

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved - self.safety_stock


@dataclass(frozen=True, slots=True)
class OrderLine:
    sku: str
    quantity: int
    unit_weight_kg: Decimal
    unit_volume_m3: Decimal


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    customer_reference: str
    location: Coordinate
    priority: int
    time_window_start: datetime
    time_window_end: datetime
    service_seconds: int
    required_skills: frozenset[str]
    lines: tuple[OrderLine, ...]

    @property
    def demand(self) -> Capacity:
        units = sum(line.quantity for line in self.lines)
        weight = sum(
            (Decimal(line.quantity) * line.unit_weight_kg * Decimal(1000) for line in self.lines),
            start=Decimal(0),
        )
        volume = sum(
            (
                Decimal(line.quantity) * line.unit_volume_m3 * Decimal(1_000_000)
                for line in self.lines
            ),
            start=Decimal(0),
        )
        if weight != weight.to_integral_value() or volume != volume.to_integral_value():
            raise ValueError(f"order {self.id} cannot be represented in solver integer units")
        return Capacity(units=units, weight_grams=int(weight), volume_cm3=int(volume))


@dataclass(frozen=True, slots=True)
class Vehicle:
    id: str
    distribution_center_id: str
    vehicle_type: str
    capacity: Capacity
    shift_start: datetime
    shift_end: datetime
    skills: frozenset[str]
    fixed_cost: Decimal
    cost_per_hour: Decimal
    cost_per_km: Decimal


@dataclass(frozen=True, slots=True)
class ScenarioData:
    name: str
    timezone: str
    currency: str
    horizon_start: datetime
    horizon_end: datetime
    centers: tuple[DistributionCenter, ...]
    inventory: tuple[InventoryPosition, ...]
    vehicles: tuple[Vehicle, ...]
    orders: tuple[Order, ...]
    dataset_name: str
    dataset_seed: int
    synthetic: bool
