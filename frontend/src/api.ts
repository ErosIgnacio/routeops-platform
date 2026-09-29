import type { PlanningRun } from "./types";

type ProblemDetail = { detail?: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ProblemDetail;
    throw new Error(body.detail ?? `Request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function getLatestRun(): Promise<PlanningRun | null> {
  const response = await fetch("/api/v1/runs/latest");
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("Could not load the latest planning run");
  return (await response.json()) as PlanningRun;
}

export function createDemoRun(): Promise<PlanningRun> {
  return request<PlanningRun>("/api/v1/demo/runs", {
    method: "POST",
    body: JSON.stringify({ solution_quality: "BALANCED" }),
  });
}
