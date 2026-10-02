from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from test_vroom_adapter import problem

from routeops.application.diagnostics import (
    allocation_reason,
    diagnose_result,
    failure_document,
    fleet_causes,
    solver_causes,
    validation_diagnostics,
)
from routeops.application.import_context import ValidationContext
from routeops.application.import_validation import validate_package
from routeops.application.ports.errors import (
    RoutingDependencyError,
    SolverDependencyError,
    operational_failure_code,
)
from routeops.domain.optimization import (
    Certainty,
    OptimizationResult,
    OptimizationSummary,
    ResultStatus,
    SolverMetadata,
    UnassignedReason,
    UnassignedTask,
)
from routeops.domain.policies.operational_allocation import OperationalAllocationPolicy
from routeops.infrastructure.data.allocation_demos import allocation_demos
from routeops.infrastructure.data.operation_cases import CASE_NAMES, case_payloads

SOURCE: dict[str, Any] = {"source": "immutable_test_input", "content_sha256": "a" * 64}


class Travel:
    def duration_seconds(self, origin: object, destination: object) -> int:
        return 100


def test_stock_is_proven_only_at_recorded_policy_decision_and_keeps_greedy_context() -> None:
    demo = allocation_demos()["shared_stock_restricted"]
    outcomes = OperationalAllocationPolicy(Travel(), "greedy-v1").allocate(
        demo.centers, demo.orders, demo.vehicles, demo.stock
    )
    evidence = deepcopy(outcomes[-1].evidence)
    value = allocation_reason("STOCK_NO_FULL_COVERAGE", evidence, SOURCE)
    assert value.certainty == Certainty.PROVEN
    assert value.evidence["prior_policy_consumption"] is True
    assert value.evidence["global_infeasibility_proven"] is False
    assert value.evidence["diagnostic"]["scope"].startswith("recorded_policy")
    assert evidence == outcomes[-1].evidence  # diagnostics cannot mutate policy evidence


def test_fleet_priority_is_skills_then_capacity_and_keeps_compatibility_code() -> None:
    demo = allocation_demos()["fleet_restrictions"]
    outcomes = OperationalAllocationPolicy(Travel()).allocate(
        demo.centers, demo.orders, demo.vehicles, demo.stock
    )
    for item, cause in zip(
        outcomes, ("NO_SKILL_COMPATIBLE_VEHICLE", "INDIVIDUAL_CAPACITY_EXCEEDED"), strict=True
    ):
        assert allocation_reason(item.reason_code or "", item.evidence, SOURCE).code == (
            "NO_COMPATIBLE_VEHICLE"
        )
        assert fleet_causes(item.evidence, SOURCE)[0].code == cause
        assert fleet_causes(item.evidence, SOURCE)[0].certainty == Certainty.PROVEN
    assert fleet_causes({}, SOURCE) == []  # no fabricated historical checks


@pytest.mark.parametrize("dimension", ["units", "weight_grams", "volume_cm3"])
def test_individual_capacity_checks_each_dimension_without_summing_fleet(dimension: str) -> None:
    data = problem()
    capacity = replace(data.vehicles[0].capacity, **{dimension: 0})
    fleet = (replace(data.vehicles[0], capacity=capacity),) * 2
    causes = solver_causes("ORD-1", replace(data, vehicles=fleet), SOURCE)
    assert causes[0].code == "INDIVIDUAL_CAPACITY_EXCEEDED"
    assert causes[0].certainty == Certainty.PROVEN
    assert causes[0].evidence["demand"][dimension] > 0


def test_assigned_center_scope_does_not_prove_no_reassignment_alternative() -> None:
    data = problem()
    unrelated = replace(
        data.vehicles[0], distribution_center_id="OTHER", skills=frozenset({"fragile"})
    )
    incompatible = replace(data.vehicles[0], skills=frozenset())
    causes = solver_causes("ORD-1", replace(data, vehicles=(incompatible, unrelated)), SOURCE)
    assert causes[0].code == "NO_SKILL_COMPATIBLE_VEHICLE"
    assert causes[0].evidence["scope_does_not_include_reassignment"]
    assert len(causes[0].evidence["vehicles"]) == 1


@pytest.mark.parametrize("overrun", [0, 1])
def test_service_completion_at_shift_boundary_and_one_second_beyond(overrun: int) -> None:
    data = problem()
    task = replace(
        data.tasks[0],
        time_window_start=data.vehicles[0].shift_end - timedelta(seconds=600),
        time_window_end=data.vehicles[0].shift_end,
        service_seconds=600 + overrun,
    )
    causes = solver_causes("ORD-1", replace(data, tasks=(task,)), SOURCE)
    assert bool(causes) is bool(overrun)
    if causes:
        assert causes[0].code == "WINDOW_SERVICE_SHIFT_INFEASIBLE"
        assert causes[0].evidence["travel_lower_bound_seconds"] == 0
        assert causes[0].certainty == Certainty.PROVEN


def test_route_limits_and_collective_task_bound_do_not_claim_individual_causality() -> None:
    data = problem()
    vehicle = replace(
        data.vehicles[0], max_delivery_tasks=1, max_route_distance_meters=1, max_driving_seconds=1
    )
    second = replace(data.tasks[0], order_id="OTHER")
    causes = solver_causes(
        "ORD-1", replace(data, tasks=(*data.tasks, second), vehicles=(vehicle,)), SOURCE
    )
    assert [item.code for item in causes] == [
        "VEHICLE_LIMITS_CONTEXT",
        "FLEET_TASK_LIMIT_SHORTFALL",
    ]
    assert causes[0].certainty == Certainty.INFERRED
    assert causes[1].certainty == Certainty.PROVEN
    assert causes[1].evidence["minimum_unrouted"] == 1
    assert not causes[1].evidence["individual_cause_proven"]


def test_local_time_cause_precedes_solver_observation_without_changing_plan() -> (
    None
):
    data = problem()
    task = replace(data.tasks[0], service_seconds=100000)
    omitted = UnassignedTask(
        task.task_id,
        task.order_id,
        "OPTIMIZATION",
        (UnassignedReason("SOLVER_NO_FEASIBLE_ROUTE", Certainty.INFERRED, "omitted", {}),),
    )
    result = OptimizationResult(
        "1.0",
        data.problem_id,
        ResultStatus.PARTIAL,
        SolverMetadata("vroom", "1.15.0", "x", "osrm", "26.9.0", 1),
        OptimizationSummary(0, 0, 1, 0, 0, 0, 0, 0, 0, 100, "CLP"),
        (),
        (omitted,),
    )
    changed = diagnose_result(result, replace(data, tasks=(task,)), {}, SOURCE)
    assert [item.code for item in changed.unassigned[0].reasons] == [
        "WINDOW_SERVICE_SHIFT_INFEASIBLE",
        "SOLVER_NO_FEASIBLE_ROUTE",
    ]
    assert changed.unassigned[0].reasons[1].certainty == Certainty.INFERRED
    assert changed.summary == result.summary and changed.routes == result.routes
    unchanged = diagnose_result(result, data, {}, SOURCE)
    assert unchanged.unassigned[0].reasons[0].code == "SOLVER_NO_FEASIBLE_ROUTE"
    assert unchanged.unassigned[0].reasons[0].certainty == Certainty.INFERRED


def test_operational_failure_does_not_expose_exception_text_or_become_stock_reason() -> None:
    for exc, code in (
        (RoutingDependencyError("private/url"), "ROUTING_DEPENDENCY_FAILED"),
        (SolverDependencyError("private/url"), "SOLVER_DEPENDENCY_FAILED"),
        (OSError("private/path"), "PLANNING_INFRASTRUCTURE_FAILED"),
    ):
        assert operational_failure_code(exc) == code
        value = failure_document(code, SOURCE)
        assert value["unassigned"] == []
        assert value["operational"][0]["stage"] == "OPERATIONAL"
        assert value["operational"][0]["certainty"] == "PROVEN"
        assert "private" not in str(value)


def test_validation_incidence_has_no_run_or_order_identity_and_omits_value_excerpt() -> None:
    page = {
        "items": [
            {
                "ordinal": 1,
                "code": "COORDINATE_RANGE",
                "severity": "ERROR",
                "dataset": "orders",
                "row": 2,
                "field": "latitude",
                "message": "Out of range",
                "value_excerpt": "private",
            }
        ],
        "next_after": None,
    }
    item = validation_diagnostics(page, SOURCE)["items"][0]
    assert item["stage"] == "VALIDATION" and item["certainty"] == "PROVEN"
    assert "order_id" not in item and "private" not in str(item)


@pytest.mark.parametrize("name", CASE_NAMES)
@pytest.mark.parametrize("workbook", [False, True])
def test_synthetic_case_contracts_are_valid_and_reproducible(name: str, workbook: bool) -> None:
    files = case_payloads(name, workbook=workbook)
    assert files == case_payloads(name, workbook=workbook)
    context = ValidationContext(
        date(2026, 10, 15),
        datetime.fromisoformat("2026-10-15T08:00:00-03:00"),
        datetime.fromisoformat("2026-10-15T18:00:00-03:00"),
        "America/Santiago",
        "CLP",
    )
    report = validate_package(files, context=context)
    assert report.valid, report.to_dict()
