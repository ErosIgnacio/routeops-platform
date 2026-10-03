import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listScenarios: vi.fn(),
  listRevisions: vi.fn(),
  listRuns: vi.fn(),
  listDemos: vi.fn(),
  createRun: vi.fn(),
  lookupRun: vi.fn(),
  getRun: vi.fn(),
  acceptRun: vi.fn(),
  cancelRun: vi.fn(),
  prepareDemo: vi.fn(),
}));

vi.mock("./planning-api", () => api);
vi.mock("./RouteMap", () => ({ RouteMap: () => <div data-testid="planning-map" /> }));
vi.mock("./RouteSequence", () => ({ RouteSequence: () => <div data-testid="planning-sequence" /> }));
vi.mock("./AnalyticsPanel", () => ({ AnalyticsPanel: () => <div data-testid="run-analytics" /> }));

import { PlanningWorkspace } from "./PlanningWorkspace";
import type { RevisionRun } from "./planning-api";

const queued = {
  run_id: "run-1",
  scenario_id: "scenario-1",
  scenario_revision_id: "revision-1",
  inventory_snapshot_id: "snapshot-1",
  allocation_attempt_id: null,
  scenario_name: "Demo",
  status: "QUEUED",
  attempts: 0,
  max_attempts: 3,
  started_at: "2026-10-15T12:00:00Z",
  completed_at: null,
  input: { solution_quality: "BALANCED", allocation_policy: "alternatives-v2" },
  result: null,
  kpis: null,
  error: null,
  decisions: [],
  events: [{ sequence: 0, from: null, to: "QUEUED" }],
} as RevisionRun;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  api.listScenarios.mockResolvedValue({ items: [{ id: "scenario-1", name: "Demo", status: "ACTIVE" }], next_offset: null });
  api.listRevisions.mockResolvedValue({ items: [{ id: "revision-1", revision_no: 1 }], next_after: null });
  api.listRuns.mockResolvedValue({ items: [], next_offset: null });
  api.listDemos.mockResolvedValue([{ id: "original", title: "Original" }, { id: "exclusive_stock", title: "Exclusivo" }]);
  api.createRun.mockResolvedValue(queued);
  api.lookupRun.mockResolvedValue(queued);
  api.getRun.mockResolvedValue(queued);
});

afterEach(cleanup);

describe("published revision planning", () => {
  it("keeps an additive diagnostic code and its certainty readable", async () => {
    api.getRun.mockResolvedValue({
      ...queued, status: "READY", result: { routes: [], unassigned: [{
        order_id: "ORD-SERVICE", stage: "OPTIMIZATION", reasons: [
          { code: "WINDOW_SERVICE_SHIFT_INFEASIBLE", certainty: "PROVEN", detail: "Service cannot fit", evidence: { diagnostic: { calculation_version: "diagnostics-v1" } } },
          { code: "SOLVER_NO_FEASIBLE_ROUTE", certainty: "INFERRED", detail: "Solver omission", evidence: {} },
        ],
      }] },
    });
    localStorage.setItem("routeops.planning.v1", JSON.stringify({ scenarioId: "scenario-1", revisionNo: 1, key: "key", runId: "run-1" }));
    render(<PlanningWorkspace />);
    expect(await screen.findByText("WINDOW_SERVICE_SHIFT_INFEASIBLE")).toBeTruthy();
    expect(screen.getByText("Comprobado")).toBeTruthy();
    expect(screen.getByText(/ORD-SERVICE/)).toBeTruthy();
  });

  it("recovers a lost submission response using its saved key", async () => {
    localStorage.setItem("routeops.planning.v1", JSON.stringify({ scenarioId: "scenario-1", revisionNo: 1, key: "stable-key", runId: "" }));
    render(<PlanningWorkspace />);
    await waitFor(() => expect(api.lookupRun).toHaveBeenCalledWith("scenario-1", 1, "stable-key"));
    expect(await screen.findByText(/Corrida run-1/)).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("routeops.planning.v1") ?? "{}").runId).toBe("run-1");
  });

  it("keeps one key for an operation and creates a new identity explicitly", async () => {
    localStorage.setItem("routeops.planning.v1", JSON.stringify({ scenarioId: "scenario-1", revisionNo: 1, key: "", runId: "" }));
    render(<PlanningWorkspace />);
    fireEvent.click(await screen.findByText("Solicitar corrida"));
    await waitFor(() => expect(api.createRun).toHaveBeenCalledTimes(1));
    const firstKey = api.createRun.mock.calls[0][2];
    expect(firstKey).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("routeops.planning.v1") ?? "{}").key).toBe(firstKey);
    fireEvent.click(screen.getByText("Nueva clave"));
    await waitFor(() => expect(JSON.parse(localStorage.getItem("routeops.planning.v1") ?? "{}").key).toBe(""));
  });

  it("offers only valid transitions for a ready run", async () => {
    const ready = { ...queued, status: "READY" as const, decisions: [{ order_id: "ORD-1", center_id: "CD-A", reason_code: null, reservation_status: "HELD" as const, evidence: { policy_version: "alternatives-v2", candidates: [] } }] };
    api.getRun.mockResolvedValue(ready);
    api.acceptRun.mockResolvedValue({ ...ready, status: "ACCEPTED" });
    localStorage.setItem("routeops.planning.v1", JSON.stringify({ scenarioId: "scenario-1", revisionNo: 1, key: "key", runId: "run-1" }));
    render(<PlanningWorkspace />);
    fireEvent.click(await screen.findByText("Aceptar"));
    await waitFor(() => expect(api.acceptRun).toHaveBeenCalledWith("run-1"));
    expect(screen.queryByText("Cancelar")).toBeNull();
  });

  it("updates history when a running job is refreshed", async () => {
    const ready = { ...queued, status: "READY" as const };
    api.listRuns.mockResolvedValue({ items: [queued], next_offset: null });
    api.getRun.mockResolvedValueOnce(queued).mockResolvedValue(ready);
    localStorage.setItem("routeops.planning.v1", JSON.stringify({ scenarioId: "scenario-1", revisionNo: 1, key: "key", runId: "run-1" }));
    render(<PlanningWorkspace />);
    expect(await screen.findByText(/run-1 · QUEUED/)).toBeTruthy();
    fireEvent.click(screen.getByText("Actualizar"));
    expect(await screen.findByText(/run-1 · READY/)).toBeTruthy();
  });

  it("describes a finished run with no routes without claiming held reservations", async () => {
    api.getRun.mockResolvedValue({
      ...queued, status: "READY", result: { routes: [], unassigned: [{ order_id: "ORD-X", stage: "ALLOCATION", reasons: [{ code: "NO_COMPATIBLE_VEHICLE", certainty: "PROVEN" }] }] },
    });
    localStorage.setItem("routeops.planning.v1", JSON.stringify({ scenarioId: "scenario-1", revisionNo: 1, key: "key", runId: "run-1" }));
    render(<PlanningWorkspace />);
    expect(await screen.findByText(/El procesamiento terminó sin rutas/)).toBeTruthy();
    expect(screen.queryByText(/Las rutas están listas para revisión/)).toBeNull();
    expect(screen.getByText(/No hay vehículo compatible/)).toBeTruthy();
  });
});
