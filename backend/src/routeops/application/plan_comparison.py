"""Fixed manual chronology and analytic deltas; never an optimizing solver."""

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID, uuid5

from routeops.application.operating_cost import VehicleRate, estimate_operating_cost
from routeops.application.plan_metrics import plan_metrics
from routeops.application.ports.fixed_route import FixedRouteGateway
from routeops.application.revision_problem import PreparedRevision
from routeops.application.serialization import to_primitive
from routeops.domain.models import Capacity
from routeops.domain.optimization import OptimizationVehicle
from routeops.domain.policies.operational_allocation import OperationalStock

COMPARISON_VERSION = "plan-comparison-v1"
MANUAL_VERSION = "fixed-sequence-earliest-v1"
MANUAL_DEPARTURE = "EFFECTIVE_SHIFT_START"
OPTIMIZED_DEPARTURE = "SOLVER_CHOSEN_WITHIN_EFFECTIVE_SHIFT"


class ComparisonError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def incident(code: str, evidence: dict[str, Any], *, severity: str = "ERROR") -> dict[str, Any]:
    return {
        "code": code,
        "stage": "MANUAL_EVALUATION",
        "certainty": "PROVEN",
        "severity": severity,
        "detail": "Fixed submitted plan rule check.",
        "evidence": evidence,
        "calculation_version": MANUAL_VERSION,
    }


def metrics_for_plan(
    prepared: PreparedRevision,
    routes: list[dict[str, Any]],
    allocated: set[str],
    objective: dict[str, Any] | None,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    rates = {
        v.source_vehicle_id: VehicleRate(
            Decimal(v.costs.fixed_units) / v.costs.scale,
            Decimal(v.costs.per_duty_hour_units) / v.costs.scale,
            Decimal(v.costs.per_km_units) / v.costs.scale,
            v.costs.scale,
        )
        for v in prepared.vehicles
    }
    cost = {
        **estimate_operating_cost(routes, rates, prepared.currency),
        "provenance": provenance,
        "solver_objective": objective,
    }
    return plan_metrics(
        routes,
        {o.id for o in prepared.orders},
        allocated,
        {v.source_vehicle_id: to_primitive(v.capacity) for v in prepared.vehicles},
        {o.id: (o.time_window_start, o.time_window_end) for o in prepared.orders},
        cost,
    )


def _step(
    sequence: int,
    kind: str,
    location: Any,
    arrival: datetime,
    load: list[int],
    *,
    order_id: str | None = None,
    task_id: UUID | None = None,
    travel: int = 0,
    distance: int = 0,
    wait: int = 0,
    service: int = 0,
    late: bool = False,
) -> dict[str, Any]:
    start = arrival + timedelta(seconds=wait)
    return {
        "sequence": sequence,
        "kind": kind,
        "task_id": str(task_id) if task_id else None,
        "order_id": order_id,
        "location": to_primitive(location),
        "arrival_at": arrival.isoformat(),
        "service_start_at": start.isoformat(),
        "departure_at": (start + timedelta(seconds=service)).isoformat(),
        "travel_seconds_from_previous": travel,
        "distance_meters_from_previous": distance,
        "waiting_seconds": wait,
        "service_seconds": service,
        "load_after": dict(zip(("units", "weight_grams", "volume_cm3"), load, strict=True)),
        "time_window_status": "LATE" if late else ("EARLY_WAIT" if wait else "ON_TIME"),
    }


def _vehicle_limits(
    vehicle: OptimizationVehicle,
    totals: dict[str, int],
    count: int,
) -> list[dict[str, Any]]:
    return [
        incident(
            code,
            {
                "vehicle_id": vehicle.source_vehicle_id,
                "observed": observed,
                "maximum": maximum,
                "unit": unit,
            },
        )
        for code, observed, maximum, unit in (
            (
                "MANUAL_DISTANCE_LIMIT",
                totals["distance_meters"],
                vehicle.max_route_distance_meters,
                "meters",
            ),
            (
                "MANUAL_DRIVING_LIMIT",
                totals["driving_seconds"],
                vehicle.max_driving_seconds,
                "seconds",
            ),
            ("MANUAL_TASK_LIMIT", count, vehicle.max_delivery_tasks, "orders"),
        )
        if maximum is not None and observed > maximum
    ]


def evaluate_manual(
    prepared: PreparedRevision,
    submitted: list[dict[str, Any]],
    stock: tuple[OperationalStock, ...],
    roads: FixedRouteGateway,
    provenance: dict[str, Any],
    max_snap_distance_m: float,
) -> dict[str, Any]:
    """Start at effective shift opening, wait only until window start, never repair."""
    orders = {o.id: o for o in prepared.orders}
    vehicles = {v.source_vehicle_id: v for v in prepared.vehicles}
    centers = {c.id: c for c in prepared.centers}
    occurrences = Counter(key for r in submitted for key in r["order_ids"])
    vehicle_counts = Counter(r["vehicle_id"] for r in submitted)
    available = {(row.center_id, row.sku): row.available for row in stock}
    issues: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for index, route in enumerate(submitted):
        scope = {"route_index": index, "submitted_route": route}
        vehicle = vehicles.get(route["vehicle_id"])
        center = centers.get(route["center_id"])
        keys = route["order_ids"]
        invalid = False
        for code, condition in (
            ("MANUAL_VEHICLE_UNKNOWN", vehicle is None),
            ("MANUAL_CENTER_UNKNOWN", center is None),
            ("MANUAL_VEHICLE_DUPLICATE", vehicle_counts[route["vehicle_id"]] > 1),
            ("MANUAL_ORDER_UNKNOWN", any(key not in orders for key in keys)),
            ("MANUAL_ORDER_DUPLICATE", any(occurrences[key] > 1 for key in keys)),
            ("MANUAL_EMPTY_ROUTE", not keys),
        ):
            if condition:
                issues.append(incident(code, scope))
                invalid = True
        if invalid:
            continue  # retain the exact input and issues; no repaired/fabricated route
        assert vehicle is not None and center is not None
        if vehicle.distribution_center_id != center.id:
            issues.append(incident("MANUAL_VEHICLE_CENTER_MISMATCH", scope))
        route_orders = [orders[key] for key in keys]
        required: Counter[str] = Counter()
        for order in route_orders:
            for line in order.lines:
                required[line.sku] += line.quantity
            if not order.required_skills <= vehicle.skills:
                issues.append(
                    incident(
                        "MANUAL_SKILLS",
                        {
                            **scope,
                            "order_id": order.id,
                            "required": sorted(order.required_skills),
                            "skills": sorted(vehicle.skills),
                        },
                    )
                )
        before = {sku: available.get((center.id, sku), 0) for sku in required}
        missing = {
            sku: amount - before[sku] for sku, amount in required.items() if before[sku] < amount
        }
        if missing:
            issues.append(
                incident(
                    "MANUAL_STOCK_NO_FULL_COVERAGE",
                    {
                        **scope,
                        "required": dict(required),
                        "available_before": before,
                        "missing": missing,
                        "scope": "frozen_inventory_submitted_route_order",
                    },
                )
            )
        for sku, amount in required.items():
            available[(center.id, sku)] = before[sku] - amount
        load = [
            sum(getattr(o.demand, name) for o in route_orders)
            for name in ("units", "weight_grams", "volume_cm3")
        ]
        if not vehicle.capacity.fits(Capacity(*load)):
            issues.append(
                incident(
                    "MANUAL_CAPACITY",
                    {**scope, "initial_load": load, "capacity": to_primitive(vehicle.capacity)},
                )
            )
        road = roads.route_sequence(
            (center.location, *(o.location for o in route_orders), center.location),
            max_snap_distance_m,
        )
        if len(road.durations) != len(keys) + 1 or len(road.distances) != len(keys) + 1:
            raise ComparisonError("MANUAL_ROAD_FACTS_INVALID")
        steps = [_step(0, "START", center.location, vehicle.shift_start, load.copy())]
        now = vehicle.shift_start
        for sequence, order in enumerate(route_orders, start=1):
            travel, distance = road.durations[sequence - 1], road.distances[sequence - 1]
            arrival = now + timedelta(seconds=travel)
            wait = max(0, int((order.time_window_start - arrival).total_seconds()))
            start = arrival + timedelta(seconds=wait)
            if start > order.time_window_end:
                issues.append(
                    incident(
                        "MANUAL_WINDOW",
                        {
                            **scope,
                            "order_id": order.id,
                            "service_start_at": start.isoformat(),
                            "window_end": order.time_window_end.isoformat(),
                        },
                    )
                )
            load = [
                left - used for left, used in zip(load, order.demand.as_vroom_array(), strict=True)
            ]
            steps.append(
                _step(
                    sequence,
                    "DELIVERY",
                    order.location,
                    arrival,
                    load.copy(),
                    order_id=order.id,
                    task_id=uuid5(prepared.revision_id, order.id),
                    travel=travel,
                    distance=distance,
                    wait=wait,
                    service=order.service_seconds,
                    late=start > order.time_window_end,
                )
            )
            now = start + timedelta(seconds=order.service_seconds)
        end = now + timedelta(seconds=road.durations[-1])
        steps.append(
            _step(
                len(steps),
                "END",
                center.location,
                end,
                load.copy(),
                travel=road.durations[-1],
                distance=road.distances[-1],
            )
        )
        if end > vehicle.shift_end:
            issues.append(
                incident(
                    "MANUAL_EFFECTIVE_SHIFT",
                    {
                        **scope,
                        "returned_at": end.isoformat(),
                        "shift_end": vehicle.shift_end.isoformat(),
                    },
                )
            )
        totals = {
            "distance_meters": sum(road.distances),
            "driving_seconds": sum(road.durations),
            "service_seconds": sum(o.service_seconds for o in route_orders),
            "waiting_seconds": sum(s["waiting_seconds"] for s in steps),
            "total_duration_seconds": int((end - vehicle.shift_start).total_seconds()),
        }
        issues.extend(_vehicle_limits(vehicle, totals, len(keys)))
        routes.append(
            {
                "vehicle_id": str(vehicle.vehicle_id),
                "source_vehicle_id": vehicle.source_vehicle_id,
                "distribution_center_id": center.id,
                "steps": steps,
                "geometry": to_primitive(road.geometry),
                "totals": totals,
                "departure_condition": {
                    "policy": MANUAL_DEPARTURE,
                    "departure_at": vehicle.shift_start.isoformat(),
                    "scope": "manual_plan_condition",
                },
            }
        )
        evidence.append(
            {
                "route_index": index,
                "stock_before": before,
                "required": dict(required),
                "road_snaps": to_primitive(road.snaps),
            }
        )
    routed = {s["order_id"] for r in routes for s in r["steps"] if s["kind"] == "DELIVERY"}
    for key in sorted(orders.keys() - routed):
        issues.append(incident("MANUAL_ORDER_NOT_SERVED", {"order_id": key}, severity="WARNING"))
    metrics = metrics_for_plan(prepared, routes, routed, None, provenance)
    return {
        "kind": "MANUAL",
        "evaluated_input": [
            {
                **r,
                "departure_policy": MANUAL_DEPARTURE,
                "departure_at": vehicles[r["vehicle_id"]].shift_start.isoformat()
                if r["vehicle_id"] in vehicles
                else None,
            }
            for r in submitted
        ],
        "departure_policy": MANUAL_DEPARTURE,
        "version": MANUAL_VERSION,
        "feasible": not any(i["severity"] == "ERROR" for i in issues),
        "metrics_informative_only": any(i["severity"] == "ERROR" for i in issues),
        "routes": routes,
        "incidences": issues,
        "decisions": evidence,
        "routed_order_ids": sorted(routed),
        "metrics": metrics,
        "solver_objective": None,
        "provenance": provenance,
    }


DELTA_FIELDS = (
    "distance_meters",
    "total_duration_seconds",
    "driving_seconds",
    "operating_cost",
    "vehicles_used",
    "routed_orders",
    "coverage",
)


def differences(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Candidate minus baseline; percentage uses the baseline, never zero division."""
    with localcontext() as ctx:
        ctx.prec = 60
        deltas = {}
        for field in DELTA_FIELDS:
            left = baseline["metrics"]["metrics"][field]
            right = candidate["metrics"]["metrics"][field]
            a = Decimal(str(left["value"])) if left["value"] is not None else None
            b = Decimal(str(right["value"])) if right["value"] is not None else None
            absolute = b - a if a is not None and b is not None else None
            deltas[field] = {
                "absolute": str(absolute) if absolute is not None else None,
                "percentage": str(absolute / a * 100)
                if absolute is not None and a is not None and a != 0
                else None,
                "unit": left["unit"],
                "denominator": str(a) if a is not None else None,
                "direction": "candidate_minus_baseline",
            }
        same = baseline["routed_order_ids"] == candidate["routed_order_ids"]
        feasible = baseline["feasible"] and candidate["feasible"]
        full = all(
            p["metrics"]["metrics"]["coverage"]["value"] == "1" for p in (baseline, candidate)
        )
        return {
            "scope": "complete_input_set",
            "temporal_scope": {
                "baseline_departure_policy": baseline.get("departure_policy"),
                "candidate_departure_policy": candidate.get("departure_policy"),
                "attribution": "Observed plan differences, not sequence-only savings; "
                "departure choice and waiting can differ.",
            },
            "deltas": deltas,
            "same_served_set": same,
            "both_feasible": feasible,
            "both_full_coverage": full,
            "savings_claim_allowed": feasible and same and full,
            "comparability": "INFEASIBLE_PLAN"
            if not feasible
            else (
                "DIFFERENT_COVERAGE"
                if not same
                else ("FULL_COVERAGE" if full else "SAME_PARTIAL_COVERAGE")
            ),
            "winner": None,
            "solver_objective_delta": None,
        }
