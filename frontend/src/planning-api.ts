import type { Revision, Scenario } from "./import-api";
import type { PlanningRun } from "./types";

export type PlanningDecision = {
  order_id: string;
  center_id: string | null;
  reason_code: string | null;
  reservation_status: "HELD" | "CONFIRMED" | "RELEASED" | null;
  evidence: {
    policy_version: string;
    candidates: Array<{
      center_id: string;
      duration_seconds: number | null;
      discard_reason: string | null;
      missing_stock: Record<string, number>;
      compatible_vehicles: string[];
    }>;
  };
};

export type RevisionRun = PlanningRun & {
  scenario_id: string;
  scenario_revision_id: string;
  inventory_snapshot_id: string;
  allocation_attempt_id: string | null;
  attempts: number;
  max_attempts: number;
  decisions: PlanningDecision[];
  events: Array<{ sequence: number; from: string | null; to: string }>;
};

export type Page<T> = { items: T[]; next_offset: number | null };
export type DemoOption = { id: string; title: string };

export class PlanningApiError extends Error {
  constructor(public code: string, public status: number) {
    super(code);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { code?: string; detail?: string };
    throw new PlanningApiError(body.code ?? body.detail ?? "PLANNING_REQUEST_FAILED", response.status);
  }
  return (await response.json()) as T;
}

export function listScenarios(): Promise<Page<Scenario>> {
  return request("/api/v1/scenarios?limit=100");
}

export function listRevisions(scenarioId: string): Promise<{ items: Revision[]; next_after: number | null }> {
  return request(`/api/v1/scenarios/${scenarioId}/revisions?limit=100`);
}

export function listRuns(scenarioId: string): Promise<Page<RevisionRun>> {
  return request(`/api/v1/scenarios/${scenarioId}/revision-runs?limit=100`);
}

export function createRun(
  scenarioId: string,
  revisionNo: number,
  key: string,
  quality: string,
  policy: string,
): Promise<RevisionRun> {
  return request(`/api/v1/scenarios/${scenarioId}/revisions/${revisionNo}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify({ solution_quality: quality, allocation_policy: policy }),
  });
}

export function lookupRun(scenarioId: string, revisionNo: number, key: string): Promise<RevisionRun> {
  return request(`/api/v1/scenarios/${scenarioId}/revisions/${revisionNo}/runs/lookup?key=${encodeURIComponent(key)}`);
}

export function getRun(runId: string): Promise<RevisionRun> {
  return request(`/api/v1/revision-runs/${runId}`);
}

export function acceptRun(runId: string): Promise<RevisionRun> {
  return request(`/api/v1/revision-runs/${runId}/accept`, { method: "POST" });
}

export function cancelRun(runId: string): Promise<RevisionRun> {
  return request(`/api/v1/revision-runs/${runId}/cancel`, { method: "POST" });
}

export function listDemos(): Promise<DemoOption[]> {
  return request("/api/v1/allocation-demos");
}

export function prepareDemo(name: string): Promise<{ demo: string; scenario_id: string; revision: Revision }> {
  return request(`/api/v1/allocation-demos/${name}/prepare`, { method: "POST" });
}
