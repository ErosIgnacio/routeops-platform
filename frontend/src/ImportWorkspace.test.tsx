import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listScenarios: vi.fn(),
  createScenario: vi.fn(),
  listImports: vi.fn(),
  listRevisions: vi.fn(),
  getImport: vi.fn(),
  lookupImport: vi.fn(),
  uploadImport: vi.fn(),
  getValidation: vi.fn(),
  getIssues: vi.fn(),
  requestValidation: vi.fn(),
  publishImport: vi.fn(),
  getPublication: vi.fn(),
  getRevision: vi.fn(),
  templateUrl: (name: string) => `/api/v1/import-templates/${name}`,
}));

vi.mock("./import-api", () => api);

import { ImportWorkspace, acceptedFiles, contextPayload, publicationBlocker } from "./ImportWorkspace";
import type { ImportBatch, ValidationState } from "./import-api";

const batch: ImportBatch = {
  id: "batch-1",
  scenario_id: "scenario-1",
  status: "VALID",
  package_sha256: "a".repeat(64),
  created_at: "2026-10-15T12:00:00Z",
  expired_at: null,
  expires_at: "2026-11-15T12:00:00Z",
  files: [],
};

const validation: ValidationState = {
  batch_id: "batch-1",
  status: "VALID",
  context_sha256: "b".repeat(64),
  package_sha256: batch.package_sha256,
  contract_version: "2.1",
  validator_version: "2.3b.1",
  attempts: 1,
  max_attempts: 3,
  lease_until: null,
  report: {
    valid: true,
    counts: {
      orders: { total: 0, accepted: 0, rejected: 0 },
      order_lines: { total: 0, accepted: 0, rejected: 0 },
      inventory: { total: 1, accepted: 1, rejected: 0 },
      distribution_centers: { total: 1, accepted: 1, rejected: 0 },
      vehicles: { total: 0, accepted: 0, rejected: 0 },
    },
    report_sha256: "c".repeat(64),
    checked_rules: ["structure_and_relationships"],
    deferred_rules: ["allocation_and_reservations"],
    completed_at: "2026-10-15T12:01:00Z",
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  api.listScenarios.mockResolvedValue({
    items: [{ id: "scenario-1", name: "Escenario A", status: "ACTIVE", created_at: "2026-10-15T12:00:00Z" }],
    next_offset: null,
  });
  api.listImports.mockResolvedValue({ items: [batch], next_offset: null });
  api.listRevisions.mockResolvedValue({ items: [], next_after: null });
  api.getImport.mockResolvedValue(batch);
  api.getValidation.mockResolvedValue(validation);
  api.getIssues.mockResolvedValue({ items: [], next_after: null });
});

afterEach(cleanup);

describe("import workflow", () => {
  it("requires exactly the accepted file package", () => {
    const csv = ["orders", "order_lines", "inventory", "distribution_centers", "vehicles"]
      .map((name) => new File(["header"], `${name}.csv`));
    expect(acceptedFiles(csv)).toBe(true);
    expect(acceptedFiles(csv.slice(0, 4))).toBe(false);
    expect(acceptedFiles([new File(["book"], "scenario.xlsx")])).toBe(true);
    expect(acceptedFiles([new File(["book"], "scenario.xls")])).toBe(false);
  });

  it("explains why empty, invalid and expired batches cannot publish", () => {
    expect(publicationBlocker(batch, validation)).toBeNull();
    const empty = {
      ...validation,
      report: {
        ...validation.report!,
        counts: Object.fromEntries(Object.entries(validation.report!.counts).map(
          ([name]) => [name, { total: 0, accepted: 0, rejected: 0 }],
        )),
      },
    };
    expect(publicationBlocker(batch, empty)).toContain("completamente vacío");
    expect(publicationBlocker(batch, { ...validation, status: "EXPIRED" })).toContain("expiró");
    expect(publicationBlocker(batch, { ...validation, status: "INVALID" })).toContain("errores");
  });

  it("turns a simple rectangular area into the validated polygon contract", () => {
    const fields = {
      planningDate: "2026-10-15", startTime: "08:00", endTime: "20:00",
      utcOffset: "-03:00", timezone: "America/Santiago", currency: "CLP",
      areaWest: "-70.8", areaSouth: "-33.7", areaEast: "-70.3", areaNorth: "-33.2",
    };
    expect(contextPayload(fields).operational_area).toEqual({
      type: "MultiPolygon",
      coordinates: [[[[-70.8, -33.7], [-70.3, -33.7], [-70.3, -33.2], [-70.8, -33.2], [-70.8, -33.7]]]],
    });
    expect(() => contextPayload({ ...fields, areaNorth: "" })).toThrow(/cuatro límites/);
    expect(() => contextPayload({ ...fields, areaWest: "181" })).toThrow(/rangos geográficos/);
  });

  it("recovers a validated batch after reload without treating it as published", async () => {
    localStorage.setItem("routeops.import-flow.v1", JSON.stringify({
      scenarioId: "scenario-1", batchId: "batch-1", uploadKey: "key-1",
    }));
    render(<ImportWorkspace />);
    expect(await screen.findByText(/Lote validado. Todavía no es una revisión publicada/)).toBeTruthy();
    expect(api.getImport).toHaveBeenCalledWith("scenario-1", "batch-1");
    expect(screen.getByRole("button", { name: "Publicar revisión" }).hasAttribute("disabled")).toBe(false);
  });

  it("recovers a confirmed publication after the response is lost", async () => {
    localStorage.setItem("routeops.import-flow.v1", JSON.stringify({
      scenarioId: "scenario-1", batchId: "batch-1", uploadKey: "key-1",
    }));
    const revision = {
      id: "revision-1", scenario_id: "scenario-1", revision_no: 1,
      import_batch_id: "batch-1", planning_date: "2026-10-15",
      timezone_iana: "America/Santiago", currency: "CLP",
      horizon_start_at: "2026-10-15T11:00:00Z", horizon_end_at: "2026-10-15T23:00:00Z",
      content_sha256: batch.package_sha256, contract_version: "2.1",
      published_at: "2026-10-15T12:02:00Z",
    };
    api.publishImport.mockRejectedValue(new Error("network lost"));
    api.getPublication.mockResolvedValue(revision);
    api.getImport.mockResolvedValueOnce(batch).mockResolvedValue({ ...batch, status: "PUBLISHED" });
    api.getValidation.mockResolvedValueOnce(validation).mockResolvedValue({ ...validation, status: "PUBLISHED" });
    render(<ImportWorkspace />);
    const button = await screen.findByRole("button", { name: "Publicar revisión" });
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    expect(await screen.findByText(/Se recuperó la revisión 1 ya publicada/)).toBeTruthy();
    expect(api.publishImport).toHaveBeenCalledTimes(1);
    expect(api.getPublication).toHaveBeenCalledWith("scenario-1", "batch-1");
    expect(screen.getByText(/Revisión 1 publicada/)).toBeTruthy();
  });

  it("allows an idempotent retry of an already published batch", async () => {
    localStorage.setItem("routeops.import-flow.v1", JSON.stringify({
      scenarioId: "scenario-1", batchId: "batch-1", uploadKey: "key-1",
    }));
    const published = { ...batch, status: "PUBLISHED" as const };
    const revision = {
      id: "revision-1", scenario_id: "scenario-1", revision_no: 1,
      import_batch_id: "batch-1", planning_date: "2026-10-15",
      timezone_iana: "America/Santiago", currency: "CLP",
      horizon_start_at: "2026-10-15T11:00:00Z", horizon_end_at: "2026-10-15T23:00:00Z",
      content_sha256: batch.package_sha256, contract_version: "2.1",
      published_at: "2026-10-15T12:02:00Z",
    };
    api.getImport.mockResolvedValue(published);
    api.getValidation.mockResolvedValue({ ...validation, status: "PUBLISHED" });
    api.getPublication.mockResolvedValue(revision);
    api.publishImport.mockResolvedValue(revision);
    render(<ImportWorkspace />);
    const retry = await screen.findByRole("button", { name: "Reintentar publicación" });
    fireEvent.click(retry);
    await waitFor(() => expect(api.publishImport).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Validación conservada en la revisión publicada/)).toBeTruthy();
  });
});
