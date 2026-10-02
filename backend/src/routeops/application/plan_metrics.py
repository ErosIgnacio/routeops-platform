"""One decimal calculation catalog for queries, future comparisons and exports."""

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any

from routeops.application.operating_cost import OperatingCostError

METRICS_VERSION = "plan-metrics-v1"


def metric(
    value: int | Decimal | None,
    unit: str,
    source: str,
    *,
    denominator: int | Decimal | None = None,
    reason: str | None = None,
    version: str = METRICS_VERSION,
) -> dict[str, Any]:
    return {
        "value": str(value) if isinstance(value, Decimal) else value,
        "unit": unit,
        "denominator": str(denominator) if isinstance(denominator, Decimal) else denominator,
        "calculation_version": version,
        "provenance": source,
        "unavailable_reason": (reason or "NOT_RECONSTRUCTIBLE") if value is None else None,
    }


def ratio(
    numerator: int | Decimal | None,
    denominator: int | Decimal | None,
) -> Decimal | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def utilization(route: dict[str, Any], capacity: dict[str, int] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    steps = route.get("steps", [])
    duty = route["totals"]["total_duration_seconds"]
    for dimension in ("units", "weight_grams", "volume_cm3"):
        cap = capacity.get(dimension) if capacity else None
        known = bool(steps) and all(dimension in step.get("load_after", {}) for step in steps)
        weighted: int | None = 0 if known else None
        peak: int | None = 0 if known else None
        if known:
            load = steps[0]["load_after"][dimension]
            peak = load
            # START waiting (if present) also carries the initial load.
            weighted = load * (steps[0]["waiting_seconds"] + steps[0]["service_seconds"])
            for step in steps[1:]:
                interval = (
                    step["travel_seconds_from_previous"]
                    + step["waiting_seconds"]
                    + step["service_seconds"]
                )
                weighted += load * interval
                load = step["load_after"][dimension]
                peak = max(peak, load)
        result[dimension] = {
            "maximum": metric(
                ratio(peak, cap),
                "ratio",
                "route_loads_and_capacity",
                denominator=cap,
                reason="CAPACITY_OR_LOAD_UNAVAILABLE",
            ),
            "time_weighted_average": metric(
                ratio(weighted, cap * duty if cap is not None else None),
                "ratio",
                "route_loads_intervals_and_capacity",
                denominator=cap * duty if cap is not None else None,
                reason="CAPACITY_LOAD_OR_DUTY_UNAVAILABLE",
            ),
        }
    return result


def window_compliance(
    steps: list[dict[str, Any]],
    windows: dict[str, tuple[datetime, datetime]],
) -> tuple[int | None, int]:
    deliveries = [step for step in steps if step["kind"] == "DELIVERY"]
    if any(step["order_id"] not in windows for step in deliveries):
        return None, len(deliveries)
    return sum(
        windows[step["order_id"]][0]
        <= datetime.fromisoformat(step["service_start_at"])
        <= windows[step["order_id"]][1]
        for step in deliveries
    ), len(deliveries)


def plan_metrics(
    routes: list[dict[str, Any]],
    input_order_ids: set[str] | None,
    allocated_order_ids: set[str] | None,
    capacities: dict[str, dict[str, int]],
    windows: dict[str, tuple[datetime, datetime]],
    cost: dict[str, Any] | None,
    cost_unavailable: str | None = None,
) -> dict[str, Any]:
    """Input facts are stored, reconciled plan facts, never live stock or fixtures."""
    with localcontext() as context:
        context.prec = 60
        routed = {
            step["order_id"]
            for route in routes
            for step in route["steps"]
            if step["kind"] == "DELIVERY"
        }
        delivery_count = sum(
            step["kind"] == "DELIVERY" for route in routes for step in route["steps"]
        )
        if (
            delivery_count != len(routed)
            or (input_order_ids is not None and not routed <= input_order_ids)
            or any(
                not any(step["kind"] == "DELIVERY" for step in route["steps"]) for route in routes
            )
        ):
            raise OperatingCostError("METRIC_ROUTE_FACTS_INVALID")
        valid = len(input_order_ids) if input_order_ids is not None else None
        allocated = len(allocated_order_ids) if allocated_order_ids is not None else None
        totals = {
            field: sum(route["totals"][field] for route in routes)
            for field in (
                "distance_meters",
                "driving_seconds",
                "service_seconds",
                "waiting_seconds",
                "total_duration_seconds",
            )
        }
        coverage = ratio(len(routed), valid)
        compliant, _ = window_compliance(
            [step for route in routes for step in route["steps"]], windows
        )
        overview: dict[str, Any] = {
            "valid_input_orders": metric(
                valid, "orders", "immutable_input", reason="INPUT_NOT_RECORDED"
            ),
            "allocated_orders": metric(
                allocated, "orders", "allocation_decisions", reason="ALLOCATION_NOT_RECORDED"
            ),
            "routed_orders": metric(len(routed), "orders", "route_delivery_steps"),
            "unrouted_orders": metric(
                valid - len(routed) if valid is not None else None,
                "orders",
                "immutable_input_and_routes",
                reason="INPUT_NOT_RECORDED",
            ),
            "coverage": metric(
                coverage,
                "ratio",
                "immutable_input_and_routes",
                denominator=valid,
                reason="INPUT_DENOMINATOR_UNAVAILABLE",
            ),
            "vehicles_used": metric(len(routes), "vehicles", "used_route_vehicles"),
            "km_per_routed_order": metric(
                ratio(Decimal(totals["distance_meters"]) / 1000, len(routed)),
                "km/order",
                "route_totals_and_delivery_steps",
                denominator=len(routed),
                reason="NO_ROUTED_ORDERS",
            ),
            "window_compliant_orders": metric(
                compliant,
                "orders",
                "service_starts_and_input_windows",
                reason="WINDOWS_NOT_RECORDED",
            ),
            "window_compliance": metric(
                ratio(compliant, len(routed)),
                "ratio",
                "service_starts_and_input_windows",
                denominator=len(routed),
                reason="WINDOWS_OR_DENOMINATOR_UNAVAILABLE",
            ),
        }
        for field, value in totals.items():
            overview[field] = metric(
                value,
                "meters" if field == "distance_meters" else "seconds",
                "persisted_route_totals",
            )
        currency = cost["currency"] if cost else "currency_unknown"
        overview["operating_cost"] = metric(
            Decimal(cost["total"]) if cost else None,
            currency,
            "operating_cost_provenance",
            reason=cost_unavailable,
            version=cost["calculation_version"] if cost else "operating-cost-v1",
        )
        overview["operating_cost_per_routed_order"] = metric(
            ratio(Decimal(cost["total"]) if cost else None, len(routed)),
            f"{currency}/order",
            "operating_cost_and_delivery_steps",
            denominator=len(routed),
            reason=cost_unavailable or "NO_ROUTED_ORDERS",
        )
        costs_by_vehicle = (
            {item["source_vehicle_id"]: item for item in cost["routes"]} if cost else {}
        )
        per_route = []
        for route in routes:
            key = route["source_vehicle_id"]
            route_cost = costs_by_vehicle.get(key)
            count = sum(step["kind"] == "DELIVERY" for step in route["steps"])
            route_compliant, _ = window_compliance(route["steps"], windows)
            per_route.append(
                {
                    "vehicle_id": route["vehicle_id"],
                    "source_vehicle_id": key,
                    "distribution_center_id": route["distribution_center_id"],
                    "metrics": {
                        **{
                            field: metric(
                                value,
                                "meters" if field == "distance_meters" else "seconds",
                                "persisted_route_totals",
                            )
                            for field, value in route["totals"].items()
                            if field in totals
                        },
                        "routed_orders": metric(count, "orders", "route_delivery_steps"),
                        "window_compliance": metric(
                            ratio(route_compliant, count),
                            "ratio",
                            "service_starts_and_input_windows",
                            denominator=count,
                            reason="WINDOWS_OR_DENOMINATOR_UNAVAILABLE",
                        ),
                        "km_per_routed_order": metric(
                            ratio(Decimal(route["totals"]["distance_meters"]) / 1000, count),
                            "km/order",
                            "route_totals_and_delivery_steps",
                            denominator=count,
                            reason="NO_ROUTED_ORDERS",
                        ),
                        "operating_cost_per_routed_order": metric(
                            ratio(Decimal(route_cost["total"]) if route_cost else None, count),
                            f"{currency}/order",
                            "operating_cost_and_delivery_steps",
                            denominator=count,
                            reason=cost_unavailable or "NO_ROUTED_ORDERS",
                        ),
                        "operating_cost": metric(
                            Decimal(route_cost["total"]) if route_cost else None,
                            currency,
                            "operating_cost_provenance",
                            reason=cost_unavailable,
                            version="operating-cost-v1",
                        ),
                    },
                    "utilization": utilization(route, capacities.get(key)),
                    "cost_components": route_cost,
                }
            )
        duties = [Decimal(route["totals"]["total_duration_seconds"]) for route in routes]
        mean = sum(duties, Decimal(0)) / len(duties) if duties else None
        stddev = (
            (sum(((duty - mean) ** 2 for duty in duties), Decimal(0)) / len(duties)).sqrt()
            if mean is not None
            else None
        )
        balance = {
            name: metric(
                value,
                "seconds",
                "used_route_duty",
                reason="NO_USED_VEHICLES",
                denominator=len(duties) if name in ("mean", "population_stddev") else None,
            )
            for name, value in (
                ("minimum", min(duties) if duties else None),
                ("maximum", max(duties) if duties else None),
                ("mean", mean),
                ("population_stddev", stddev),
            )
        }
        balance["coefficient_of_variation"] = metric(
            ratio(stddev, mean) if len(duties) >= 2 else None,
            "ratio",
            "used_route_duty",
            denominator=mean,
            reason="FEWER_THAN_TWO_VEHICLES_OR_ZERO_MEAN",
        )
        return {
            "calculation_version": METRICS_VERSION,
            "scope": "computed_plan_not_execution",
            "metrics": overview,
            "routes": per_route,
            "duty_balance": balance,
            "operating_cost": cost,
            "solver_objective": cost["solver_objective"] if cost else None,
        }
