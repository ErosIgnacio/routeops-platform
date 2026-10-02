"""Versioned business cost from persisted facts; never a solver objective."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

from routeops.domain.optimization.validation import INTEGER_MAX

COST_VERSION = "operating-cost-v1"
MONEY_QUANTUM = Decimal("0.0001")


class OperatingCostError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class VehicleRate:
    fixed: Decimal
    per_duty_hour: Decimal
    per_km: Decimal
    input_scale: int = 10_000

    def __post_init__(self) -> None:
        if type(self.input_scale) is not int or not 1 <= self.input_scale <= INTEGER_MAX:
            raise OperatingCostError("COST_RATE_INVALID")
        for value in (self.fixed, self.per_duty_hour, self.per_km):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise OperatingCostError("COST_RATE_INVALID")
            with localcontext() as context:
                context.prec = 60
                if (
                    value > Decimal(INTEGER_MAX) / self.input_scale
                    or value != value.quantize(MONEY_QUANTUM)
                    or value * self.input_scale != (value * self.input_scale).to_integral_value()
                ):
                    raise OperatingCostError("COST_RATE_INVALID")


def estimate_operating_cost(
    routes: list[dict[str, Any]],
    rates: dict[str, VehicleRate],
    currency: str,
) -> dict[str, Any]:
    """Round each payable component once at 4 decimals; sum those components.

    The 60-digit decimal context safely exceeds all bounded source products.
    No binary floats, intermediate hour rounding or unused-vehicle fixed fees.
    """
    used: set[str] = set()
    breakdown: list[dict[str, Any]] = []
    total = Decimal(0)
    with localcontext() as context:
        context.prec = 60
        for route in routes:
            vehicle_id = str(route["source_vehicle_id"])
            if vehicle_id in used or vehicle_id not in rates:
                raise OperatingCostError("COST_ROUTE_RATE_MISMATCH")
            used.add(vehicle_id)
            rate = rates[vehicle_id]
            facts = route["totals"]
            for field in (
                "distance_meters",
                "driving_seconds",
                "service_seconds",
                "waiting_seconds",
                "total_duration_seconds",
            ):
                value = facts[field]
                if type(value) is not int or not 0 <= value <= INTEGER_MAX:
                    raise OperatingCostError("COST_ROUTE_FACTS_INVALID")
            duty = facts["driving_seconds"] + facts["service_seconds"] + facts["waiting_seconds"]
            if facts["total_duration_seconds"] != duty:
                raise OperatingCostError("COST_ROUTE_FACTS_INVALID")
            fixed = rate.fixed.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            hourly = (rate.per_duty_hour * Decimal(duty) / 3600).quantize(
                MONEY_QUANTUM, rounding=ROUND_HALF_UP
            )
            distance = (rate.per_km * Decimal(facts["distance_meters"]) / 1000).quantize(
                MONEY_QUANTUM, rounding=ROUND_HALF_UP
            )
            route_total = fixed + hourly + distance
            total += route_total
            breakdown.append(
                {
                    "vehicle_id": route["vehicle_id"],
                    "source_vehicle_id": vehicle_id,
                    "distribution_center_id": route["distribution_center_id"],
                    "facts": {
                        field: facts[field]
                        for field in (
                            "distance_meters",
                            "driving_seconds",
                            "service_seconds",
                            "waiting_seconds",
                            "total_duration_seconds",
                        )
                    },
                    "rates": {
                        "fixed": str(rate.fixed),
                        "per_duty_hour": str(rate.per_duty_hour),
                        "per_km": str(rate.per_km),
                    },
                    "fixed_cost": str(fixed),
                    "duty_cost": str(hourly),
                    "distance_cost": str(distance),
                    "total": str(route_total),
                }
            )
        return {
            "calculation_version": COST_VERSION,
            "currency": currency,
            "precision_decimal_places": 4,
            "rounding": "ROUND_HALF_UP_PER_COMPONENT",
            "duty_definition": "driving_seconds + service_seconds + waiting_seconds",
            "total": str(total.quantize(MONEY_QUANTUM)),
            "routes": breakdown,
        }
