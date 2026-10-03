import type { OptimizedRoute, PlanningRun } from "./types";
import type { Scenario } from "./import-api";

export type Metric = { value: string | number | null; unit: string; denominator: string | number | null; unavailable_reason: string | null; calculation_version: string; provenance: string; classification?: string };
export type PlanMetrics = { metrics: Record<string, Metric>; routes: Array<{source_vehicle_id: string; metrics: Record<string, Metric>; utilization: Record<string, {maximum: Metric; time_weighted_average: Metric}>}>; solver_objective: unknown; operating_cost: unknown; calculation_version: string };
export type Issue = { order_id?: string | null; code: string; stage: string; certainty: string; severity?: string; detail: string; role?: string; scope?: string; evidence: Record<string, unknown> };
export type Alternative = { feasible: boolean; routes: OptimizedRoute[]; metrics: PlanMetrics; departure_policy?: string; incidences?: Issue[]; result?: { unassigned: Array<{order_id: string; stage: string; reasons: Issue[]}> }; decisions: unknown; provenance: unknown; evaluated_input?: unknown; solver_objective: unknown };
export type Comparison = { comparison_id: string; scenario_id: string; revision_id: string; status: string; attempts: number; context_sha256: string; context: Record<string, unknown>; manual_input: {manual_routes: ManualRoute[]; solution_quality: string; revision_no: number}; result_sha256: string | null; history: Array<{sequence: number; to: string; reason: string; at: string; attempt_no: number}>; result: { alternatives: Record<string, Alternative>; comparisons: Record<string, {comparability: string; scope: string; savings_claim_allowed: boolean; temporal_scope?: unknown; deltas: Record<string, {absolute: string | null; percentage: string | null; unit: string; denominator: string | null}>}> } | null };
export type ManualRoute = {vehicle_id: string; center_id: string; order_ids: string[]};
export type InputOptions = {revision_id: string; timezone: string; orders: string[]; centers: string[]; vehicles: Array<{vehicle_id: string; center_id: string; shift_start: string}>};
export type DiagnosticPage = {items: Issue[]; total: number; next_offset: number | null; calculation_version: string; provenance: unknown; unavailable_reason: string | null};
export type RunAnalytics = {run: PlanningRun; metrics: {current_status: string; plan: PlanMetrics | null; processing: {initial_queue?:Metric;total_elapsed?:Metric;durable_total_elapsed?:Metric;active_all_attempts?:Metric}; current_reservations: Array<{order_id: string; status: string}>; unavailable_reason?: string}; diagnostics: DiagnosticPage};

export async function analyticsRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api/v1${path}`, init);
  if (!r.ok) { const body = await r.json().catch(() => ({})) as {code?: string}; throw new Error(body.code ?? `HTTP ${r.status}`); }
  return r.json() as Promise<T>;
}
export const getComparison = (id: string) => analyticsRequest<Comparison>(`/comparisons/${id}`);
export async function allScenarios(): Promise<Scenario[]> {
  const items: Scenario[] = []; let offset: number | null = 0;
  while(offset !== null) { const page: {items:Scenario[];next_offset:number|null} = await analyticsRequest(`/scenarios?limit=100&offset=${offset}`); items.push(...page.items); offset = page.next_offset; }
  return [...new Map(items.map(item => [item.id,item])).values()];
}
export const comparisonInput = (scenario: string, revision: number) => analyticsRequest<InputOptions>(`/scenarios/${scenario}/revisions/${revision}/comparison-input`);
export const getRunAnalytics = (id: string) => analyticsRequest<RunAnalytics>(`/runs/${id}/analytics`);
export const getDiagnostics = (id: string, offset: number, stage: string, certainty: string, code: string) => {
  const params = new URLSearchParams({offset: String(offset), limit: "5"});
  if (stage) params.set("stage", stage); if (certainty) params.set("certainty", certainty); if (code) params.set("code", code);
  return analyticsRequest<DiagnosticPage>(`/runs/${id}/diagnostics?${params}`);
};
export const createComparison = (scenario: string, revision: number, key: string, body: {manual_routes: ManualRoute[]; solution_quality: string}) => analyticsRequest<Comparison>(`/scenarios/${scenario}/revisions/${revision}/comparisons`, {method: "POST", headers: {"Content-Type": "application/json", "Idempotency-Key": key}, body: JSON.stringify(body)});
export const comparisonHistory = (scenario: string, offset: number) => analyticsRequest<{items: Array<{comparison_id: string; status: string}>; total: number; next_offset: number | null}>(`/scenarios/${scenario}/comparisons?offset=${offset}&limit=5`);
