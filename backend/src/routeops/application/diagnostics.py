"""One explanation catalog: observations are not proofs of global infeasibility."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

from routeops.application.serialization import to_primitive
from routeops.domain.optimization import (
    Certainty,
    OptimizationProblem,
    OptimizationResult,
    UnassignedReason,
)

DIAGNOSTIC_VERSION = "diagnostics-v1"


def reason(
    code: str,
    certainty: Certainty,
    detail: str,
    evidence: dict[str, Any],
    source: dict[str, Any],
    scope: str,
) -> UnassignedReason:
    return UnassignedReason(
        code,
        certainty,
        detail,
        {
            **evidence,
            "diagnostic": {
                "calculation_version": DIAGNOSTIC_VERSION,
                "provenance": source,
                "scope": scope,
            },
        },
    )


def allocation_reason(
    code: str,
    evidence: dict[str, Any],
    source: dict[str, Any],
) -> UnassignedReason:
    """Compatibility codes stay primary, finer fleet checks are supplementary."""
    candidates = evidence.get("candidates", [])
    depleted = any(
        stock["available_before"]
        < stock["on_hand"]
        - stock["externally_reserved"]
        - stock["safety_stock"]
        - stock["routeops_reserved"]
        for candidate in candidates
        for stock in candidate.get("stock", {}).values()
    )
    detail = (
        "No single center covers all lines at this policy decision; "
        "this is not a joint-assignment proof."
        if code == "STOCK_NO_FULL_COVERAGE"
        else "No stock-eligible evaluated center has an individually compatible vehicle."
    )
    return reason(
        code,
        Certainty.PROVEN,
        detail,
        {
            **evidence,
            "prior_policy_consumption": depleted,
            "global_infeasibility_proven": False,
        },
        source,
        "recorded_policy_decision_across_evaluated_centers",
    )


def fleet_causes(evidence: dict[str, Any], source: dict[str, Any]) -> list[UnassignedReason]:
    checks = [
        check
        for candidate in evidence.get("candidates", [])
        if not candidate.get("missing_stock")
        for check in candidate.get("vehicle_checks", [])
    ]
    if not checks:
        return []  # Missing historical checks are not invented.
    skilled = [check for check in checks if check["skills_compatible"]]
    if not skilled:
        return [
            reason(
                "NO_SKILL_COMPATIBLE_VEHICLE",
                Certainty.PROVEN,
                "Required skills are absent from every evaluated stock-eligible vehicle.",
                {"required_skills": evidence.get("required_skills"), "vehicle_checks": checks},
                source,
                "stock_eligible_centers_and_recorded_fleet",
            )
        ]
    if not any(check["capacity_compatible"] for check in skilled):
        return [
            reason(
                "INDIVIDUAL_CAPACITY_EXCEEDED",
                Certainty.PROVEN,
                "Whole-order demand fails at least one dimension "
                "on every skill-compatible vehicle.",
                {
                    "vehicle_checks": skilled,
                    "required": evidence.get("required"),
                    "demand": evidence.get("demand"),
                },
                source,
                "stock_eligible_centers_and_skill_compatible_vehicles",
            )
        ]
    return []


def solver_causes(
    order_id: str,
    problem: OptimizationProblem,
    source: dict[str, Any],
) -> list[UnassignedReason]:
    task = next((task for task in problem.tasks if task.order_id == order_id), None)
    if task is None:
        return []
    fleet = [
        vehicle
        for vehicle in problem.vehicles
        if vehicle.distribution_center_id == task.distribution_center_id
    ]
    scope = "assigned_center_and_its_recorded_vehicles"
    evidence: dict[str, Any] = {
        "assigned_center_id": task.distribution_center_id,
        "required_skills": sorted(task.required_skills),
        "demand": to_primitive(task.demand),
        "window_start": task.time_window_start.isoformat(),
        "window_end": task.time_window_end.isoformat(),
        "service_seconds": task.service_seconds,
        "vehicles": [to_primitive(vehicle) for vehicle in fleet],
        "scope_does_not_include_reassignment": True,
    }
    skilled = [vehicle for vehicle in fleet if task.required_skills <= vehicle.skills]
    if not skilled:
        return [
            reason(
                "NO_SKILL_COMPATIBLE_VEHICLE",
                Certainty.PROVEN,
                "No vehicle at the assigned center has the required skills.",
                evidence,
                source,
                scope,
            )
        ]
    fitting = [vehicle for vehicle in skilled if vehicle.capacity.fits(task.demand)]
    if not fitting:
        return [
            reason(
                "INDIVIDUAL_CAPACITY_EXCEEDED",
                Certainty.PROVEN,
                "No skill-compatible vehicle can carry this complete order in all dimensions.",
                evidence,
                source,
                scope,
            )
        ]
    intervals = [
        {
            "vehicle_id": vehicle.source_vehicle_id,
            "earliest_service_start": max(task.time_window_start, vehicle.shift_start).isoformat(),
            "latest_service_start": min(
                task.time_window_end,
                vehicle.shift_end - timedelta(seconds=task.service_seconds),
            ).isoformat(),
            "can_fit_with_zero_travel": max(task.time_window_start, vehicle.shift_start)
            <= min(
                task.time_window_end,
                vehicle.shift_end - timedelta(seconds=task.service_seconds),
            ),
        }
        for vehicle in fitting
    ]
    causes: list[UnassignedReason] = []
    if not any(item["can_fit_with_zero_travel"] for item in intervals):
        causes.append(
            reason(
                "WINDOW_SERVICE_SHIFT_INFEASIBLE",
                Certainty.PROVEN,
                "Service cannot fit any effective vehicle shift even with zero travel. "
                "The order window constrains service start; shift end constrains completion.",
                {
                    **evidence,
                    "necessary_intervals": intervals,
                    "assumptions": [
                        "nonnegative_travel",
                        "no_split_order",
                        "shift_intersects_center_hours_and_horizon",
                    ],
                    "travel_lower_bound_seconds": 0,
                },
                source,
                scope,
            )
        )
    limits = [
        {
            "vehicle_id": vehicle.source_vehicle_id,
            "max_route_distance_meters": vehicle.max_route_distance_meters,
            "max_driving_seconds": vehicle.max_driving_seconds,
            "max_delivery_tasks": vehicle.max_delivery_tasks,
        }
        for vehicle in fitting
    ]
    if any(
        any(value is not None for key, value in item.items() if key != "vehicle_id")
        for item in limits
    ):
        causes.append(
            reason(
                "VEHICLE_LIMITS_CONTEXT",
                Certainty.INFERRED,
                "Recorded route limits constrain the solver; no particular limit is proven "
                "to explain this omission. Direct OSRM travel is not used as a lower bound.",
                {
                    "limits": limits,
                    "necessary_intervals": intervals,
                    "units": {"distance": "meters", "driving": "seconds", "tasks": "orders"},
                },
                source,
                scope,
            )
        )
    # A collective necessary bound does not identify which particular order must be omitted.
    center_tasks = [
        item for item in problem.tasks if item.distribution_center_id == task.distribution_center_id
    ]
    if fleet and all(vehicle.max_delivery_tasks is not None for vehicle in fleet):
        slots = sum(vehicle.max_delivery_tasks or 0 for vehicle in fleet)
        if len(center_tasks) > slots:
            causes.append(
                reason(
                    "FLEET_TASK_LIMIT_SHORTFALL",
                    Certainty.PROVEN,
                    "At least this many assigned-center orders cannot all be routed together; "
                    "this bound does not prove why this particular order was omitted.",
                    {
                        "assigned_center_id": task.distribution_center_id,
                        "assigned_orders": len(center_tasks),
                        "task_slots": slots,
                        "minimum_unrouted": len(center_tasks) - slots,
                        "individual_cause_proven": False,
                    },
                    source,
                    "collective_assigned_center_task_capacity",
                )
            )
    return causes


def diagnose_result(
    result: OptimizationResult,
    problem: OptimizationProblem,
    allocations: dict[str, dict[str, Any]],
    source: dict[str, Any],
) -> OptimizationResult:
    explained = []
    for item in result.unassigned:
        if item.stage == "ALLOCATION":
            evidence = allocations.get(item.order_id, item.reasons[0].evidence)
            primary = allocation_reason(item.reasons[0].code, evidence, source)
            extra = (
                fleet_causes(evidence, source) if primary.code == "NO_COMPATIBLE_VEHICLE" else []
            )
            reasons = [primary, *extra]
        else:
            local = solver_causes(item.order_id, problem, source)
            conclusive = [
                entry
                for entry in local
                if entry.code
                in (
                    "NO_SKILL_COMPATIBLE_VEHICLE",
                    "INDIVIDUAL_CAPACITY_EXCEEDED",
                    "WINDOW_SERVICE_SHIFT_INFEASIBLE",
                )
            ]
            observed = [
                reason(
                    entry.code,
                    Certainty.INFERRED
                    if entry.code == "SOLVER_NO_FEASIBLE_ROUTE"
                    else entry.certainty,
                    "The solver omitted this order; omission alone proves no specific "
                    "constraint or global infeasibility.",
                    entry.evidence,
                    source,
                    "solver_response_for_fixed_assignment",
                )
                for entry in item.reasons
            ]
            reasons = [
                *conclusive,
                *observed,
                *[entry for entry in local if entry not in conclusive],
            ]
        explained.append(replace(item, reasons=tuple(reasons)))
    return replace(result, unassigned=tuple(explained))


def result_document(result: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    return {
        "calculation_version": DIAGNOSTIC_VERSION,
        "provenance": source,
        "unassigned": result["unassigned"],
        "operational": [],
    }


def failure_document(code: str, source: dict[str, Any], evidence: Any = None) -> dict[str, Any]:
    failure = reason(
        code,
        Certainty.PROVEN,
        "Recorded processing failure; not a proof of business infeasibility.",
        {"observed_failure_code": code, "network_coverage": evidence},
        source,
        "operational_processing_observation",
    )
    return {
        "calculation_version": DIAGNOSTIC_VERSION,
        "provenance": source,
        "unassigned": [],
        "operational": [{"stage": "OPERATIONAL", **to_primitive(failure)}],
    }


def validation_diagnostics(page: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Adapt existing immutable issues without copying uploaded values into diagnostics."""
    return {
        "calculation_version": DIAGNOSTIC_VERSION,
        "provenance": source,
        "next_after": page["next_after"],
        "items": [
            {
                "ordinal": item["ordinal"],
                "code": item["code"],
                "stage": "VALIDATION",
                "certainty": "PROVEN",
                "severity": item["severity"],
                "detail": item["message"],
                "evidence": {
                    "dataset": item["dataset"],
                    "row": item["row"],
                    "field": item["field"],
                    "diagnostic": {
                        "calculation_version": DIAGNOSTIC_VERSION,
                        "provenance": source,
                        "scope": "import_validation_rule",
                    },
                },
            }
            for item in page["items"]
        ],
    }
