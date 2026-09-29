import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PlanningRun } from "./types";

const apiMocks = vi.hoisted(() => ({
  getLatestRun: vi.fn(),
  createDemoRun: vi.fn(),
}));

vi.mock("./api", () => apiMocks);
vi.mock("./RouteMap", () => ({
  RouteMap: ({ selectedVehicleId }: { selectedVehicleId: string | null }) => (
    <div data-testid="route-map" data-selected={selectedVehicleId ?? "all"} />
  ),
}));

import App from "./App";

const run = {
  run_id: "run-1",
  scenario_name: "test",
  status: "PARTIAL",
  started_at: "2026-09-24T12:00:00Z",
  completed_at: "2026-09-24T12:01:00Z",
  input: {
    dataset_name: "test",
    dataset_seed: 1,
    synthetic: true,
    solution_quality: "BALANCED",
  },
  result: {
    routes: [
      {
        vehicle_id: "vehicle-1",
        source_vehicle_id: "VEH-CENTRO-01",
        distribution_center_id: "DC-CENTRO",
        geometry: [
          { longitude: -70.66, latitude: -33.44 },
          { longitude: -70.65, latitude: -33.43 },
        ],
        steps: [],
        totals: {
          distance_meters: 1000,
          driving_seconds: 300,
          service_seconds: 0,
          waiting_seconds: 0,
          total_duration_seconds: 300,
          objective_cost_units: 100,
        },
      },
    ],
    unassigned: [],
    solver: {
      engine: "vroom",
      engine_version: "1.15.0",
      routing_engine: "osrm",
      routing_engine_version: "26.9.0",
      solve_duration_ms: 10,
    },
  },
  kpis: {
    routes: 1,
    vehicles_used: 1,
    assigned_orders: 1,
    unassigned_orders: 0,
    distance_km: 1,
    driving_hours: 0.1,
    service_hours: 0,
    waiting_hours: 0,
    total_hours: 0.1,
    solver_time_ms: 10,
    estimated_cost: 1000,
    currency: "CLP",
  },
  error: null,
} as PlanningRun;

describe("route selection", () => {
  it("selects a route card and restores the all-routes view", async () => {
    apiMocks.getLatestRun.mockResolvedValue(run);
    render(<App />);

    const routeCard = await screen.findByRole("button", { name: /VEH-CENTRO-01/ });
    expect(screen.getByTestId("route-map").dataset.selected).toBe("all");

    fireEvent.click(routeCard);
    expect(routeCard.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("route-map").dataset.selected).toBe("VEH-CENTRO-01");

    fireEvent.click(screen.getByRole("button", { name: "Mostrar todas" }));
    expect(screen.getByTestId("route-map").dataset.selected).toBe("all");
  });
});
