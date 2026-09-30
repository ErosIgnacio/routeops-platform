"""Measure a bounded synthetic OSRM matrix and real VROOM solve.

Run with PYTHONPATH=src; this script creates no database rows or reservations.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from routeops.application.revision_problem import reconcile_result
from routeops.domain.models import Capacity, Coordinate, Order, OrderLine
from routeops.domain.optimization import (
    DeliveryTask,
    OptimizationOptions,
    OptimizationProblem,
    OptimizationVehicle,
    SolutionQuality,
    VehicleCost,
)
from routeops.infrastructure.routing.osrm import OsrmClient
from routeops.infrastructure.solver.vroom import VroomAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--osrm-url", default="http://127.0.0.1:5000")
    parser.add_argument("--vroom-url", default="http://127.0.0.1:3000")
    parser.add_argument("--orders", type=int, default=20)
    parser.add_argument("--centers", type=int, default=4)
    parser.add_argument("--vehicles", type=int, default=6)
    parser.add_argument("--lines-per-order", type=int, default=3)
    args = parser.parse_args()
    if min(args.orders, args.centers, args.vehicles, args.lines_per_order) <= 0:
        parser.error("all workload dimensions must be positive")
    tracemalloc.start()
    prepare_started = time.perf_counter()
    zone = timezone(timedelta(hours=-3))
    start = datetime(2026, 10, 15, 8, tzinfo=zone)
    centers = tuple(
        Coordinate(-33.445 + index * 0.003, -70.66 + index * 0.004) for index in range(args.centers)
    )
    orders = tuple(
        Coordinate(-33.448 + (index % 10) * 0.002, -70.657 + (index // 10) * 0.003)
        for index in range(args.orders)
    )
    domain_orders = tuple(
        Order(
            id=f"BENCH-{index:03d}",
            customer_reference=f"BENCH-{index:03d}",
            location=location,
            priority=50,
            time_window_start=start + timedelta(hours=1),
            time_window_end=start + timedelta(hours=9),
            service_seconds=300,
            required_skills=frozenset(),
            lines=tuple(
                OrderLine(f"SKU-{line:03d}", 1, Decimal("1.000"), Decimal("0.001000"))
                for line in range(args.lines_per_order)
            ),
        )
        for index, location in enumerate(orders)
    )
    vehicles = tuple(
        OptimizationVehicle(
            vehicle_id=uuid4(),
            source_vehicle_id=f"BENCH-VEH-{index:02d}",
            distribution_center_id=f"CD-{index % args.centers}",
            vehicle_type="van",
            start=centers[index % args.centers],
            end=centers[index % args.centers],
            shift_start=start,
            shift_end=start + timedelta(hours=10),
            capacity=Capacity(200, 200_000, 200_000),
            skills=frozenset(),
            costs=VehicleCost("CLP", 10_000, 1_000_000, 100_000, 10_000),
        )
        for index in range(args.vehicles)
    )
    tasks = tuple(
        DeliveryTask(
            task_id=uuid4(),
            order_id=order.id,
            distribution_center_id=f"CD-{index % args.centers}",
            location=order.location,
            demand=order.demand,
            service_seconds=order.service_seconds,
            time_window_start=order.time_window_start,
            time_window_end=order.time_window_end,
            priority=order.priority,
            required_skills=order.required_skills,
        )
        for index, order in enumerate(domain_orders)
    )
    problem = OptimizationProblem(
        contract_version="1.0",
        problem_id=uuid4(),
        scenario_id=uuid4(),
        horizon_start=start,
        horizon_end=start + timedelta(hours=10),
        timezone="America/Santiago",
        tasks=tasks,
        vehicles=vehicles,
        options=OptimizationOptions(timeout_seconds=15, solution_quality=SolutionQuality.BALANCED),
    )
    prepare_ms = round((time.perf_counter() - prepare_started) * 1000)
    started = time.perf_counter()
    matrix = OsrmClient(args.osrm_url).duration_matrix(centers, orders)
    matrix_ms = round((time.perf_counter() - started) * 1000)
    started = time.perf_counter()
    result = VroomAdapter(args.vroom_url).solve(problem)
    solve_ms = round((time.perf_counter() - started) * 1000)
    reconcile_result(problem, result)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(
        json.dumps(
            {
                "machine": platform.platform(),
                "logical_cpus": os.cpu_count(),
                "orders": args.orders,
                "lines": args.orders * args.lines_per_order,
                "centers": args.centers,
                "vehicles": args.vehicles,
                "matrix_cells": sum(len(row) for row in matrix),
                "solver_locations_upper_bound": args.orders + 2 * args.vehicles,
                "solver_matrix_cells_upper_bound": (args.orders + 2 * args.vehicles) ** 2,
                "prepare_ms": prepare_ms,
                "osrm_matrix_ms": matrix_ms,
                "vroom_solve_ms": solve_ms,
                "python_tracemalloc_peak_bytes": peak_bytes,
                "routes": len(result.routes),
                "assigned": result.summary.assigned_task_count,
                "unassigned": result.summary.unassigned_task_count,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
