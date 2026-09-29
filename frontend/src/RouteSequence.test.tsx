// @vitest-environment jsdom

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RouteSequence } from "./RouteSequence";
import type { OptimizedRoute } from "./types";

describe("RouteSequence", () => {
  it("renders persisted steps in route sequence order", () => {
    const route = {
      vehicle_id: "vehicle-id",
      source_vehicle_id: "VEH-1",
      distribution_center_id: "DC-1",
      steps: [
        {
          sequence: 0,
          kind: "START",
          order_id: null,
          location: { latitude: -33.44, longitude: -70.66 },
          arrival_at: "2026-10-15T08:00:00-03:00",
          time_window_status: "NOT_APPLICABLE",
        },
        {
          sequence: 1,
          kind: "DELIVERY",
          order_id: "ORD-1",
          location: { latitude: -33.43, longitude: -70.65 },
          arrival_at: "2026-10-15T08:10:00-03:00",
          time_window_status: "ON_TIME",
        },
        {
          sequence: 2,
          kind: "END",
          order_id: null,
          location: { latitude: -33.44, longitude: -70.66 },
          arrival_at: "2026-10-15T08:20:00-03:00",
          time_window_status: "NOT_APPLICABLE",
        },
      ],
      geometry: [
        { latitude: -33.44, longitude: -70.66 },
        { latitude: -33.43, longitude: -70.65 },
      ],
      totals: {
        distance_meters: 1000,
        driving_seconds: 600,
        service_seconds: 300,
        waiting_seconds: 0,
        total_duration_seconds: 900,
        objective_cost_units: 100,
      },
    } satisfies OptimizedRoute;

    render(<RouteSequence routes={[route]} />);

    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]!).getByText("START")).toBeTruthy();
    expect(within(rows[1]!).getByText("ORD-1")).toBeTruthy();
    expect(within(rows[2]!).getByText("END")).toBeTruthy();
  });
});
