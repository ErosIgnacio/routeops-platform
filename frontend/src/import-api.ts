export type Scenario = {
  id: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  created_at: string;
};

export type Page<T> = { items: T[]; next_offset: number | null };
export type CursorPage<T> = { items: T[]; next_after: number | null };

export type ImportFile = {
  dataset: string;
  display_name: string;
  sha256: string;
  size_bytes: number;
};

export type ImportBatch = {
  id: string;
  scenario_id: string;
  parser_version?: string;
  status: "RECEIVED" | "VALIDATING" | "VALID" | "INVALID" | "FAILED" | "PUBLISHED" | "EXPIRED";
  package_sha256: string;
  created_at: string;
  expired_at: string | null;
  expires_at: string | null;
  files: ImportFile[];
};

export type DatasetCount = { total: number; accepted: number; rejected: number };
export type ValidationReport = {
  valid: boolean;
  counts: Record<string, DatasetCount>;
  report_sha256: string;
  checked_rules: string[];
  deferred_rules: string[];
  completed_at: string;
};

export type ValidationState = {
  batch_id: string;
  status: ImportBatch["status"];
  context_sha256: string | null;
  package_sha256: string;
  contract_version: string | null;
  validator_version: string | null;
  attempts: number;
  max_attempts: number;
  lease_until: string | null;
  report: ValidationReport | null;
};

export type ValidationIssue = {
  ordinal: number;
  code: string;
  severity: "ERROR" | "WARNING";
  dataset: string | null;
  source: string | null;
  row: number | null;
  field: string | null;
  message: string;
  value_excerpt: string | null;
};

export type ValidationContext = {
  planning_date: string;
  horizon_start_at: string;
  horizon_end_at: string;
  timezone_iana: string;
  currency: string;
  operational_area: Record<string, unknown> | null;
  contract_version?: string;
  validator_version?: string;
};

export type Revision = {
  id: string;
  scenario_id: string;
  revision_no: number;
  import_batch_id: string;
  planning_date: string;
  timezone_iana: string;
  currency: string;
  horizon_start_at: string;
  horizon_end_at: string;
  content_sha256: string;
  contract_version: string;
  published_at: string;
};

export type RevisionDetail = Revision & {
  context_sha256: string | null;
  report_sha256: string | null;
  counts: Record<string, DatasetCount> | null;
  snapshot_at: string | null;
  operational_area: Record<string, unknown> | null;
};

export class ImportApiError extends Error {
  constructor(
    public readonly code: string,
    public readonly status: number,
    detail: string,
  ) {
    super(detail);
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let body: { code?: string; detail?: string } = {};
    try {
      body = (await response.json()) as typeof body;
    } catch {
      // A disconnected or malformed response still gets a stable client error.
    }
    throw new ImportApiError(
      body.code ?? `HTTP_${response.status}`,
      response.status,
      body.detail ?? `Request failed (${response.status})`,
    );
  }
  return (await response.json()) as T;
}

function scenarioPath(scenarioId: string): string {
  return `/api/v1/scenarios/${encodeURIComponent(scenarioId)}`;
}

export const templateUrl = (name: string): string =>
  `/api/v1/import-templates/${encodeURIComponent(name)}`;

export const listScenarios = (offset = 0): Promise<Page<Scenario>> =>
  json(`/api/v1/scenarios?offset=${offset}&limit=50`);

export const createScenario = (name: string): Promise<Scenario> =>
  json("/api/v1/scenarios", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });

export const listImports = (scenarioId: string, offset = 0): Promise<Page<ImportBatch>> =>
  json(`${scenarioPath(scenarioId)}/imports?offset=${offset}&limit=50`);

export const getImport = (scenarioId: string, batchId: string): Promise<ImportBatch> =>
  json(`${scenarioPath(scenarioId)}/imports/${encodeURIComponent(batchId)}`);

export const lookupImport = (scenarioId: string, key: string): Promise<ImportBatch> =>
  json(`${scenarioPath(scenarioId)}/imports/lookup?key=${encodeURIComponent(key)}`);

export function uploadImport(
  scenarioId: string,
  files: File[],
  key: string,
): Promise<ImportBatch> {
  const body = new FormData();
  for (const file of files) body.append("files", file);
  return json(`${scenarioPath(scenarioId)}/imports`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body,
  });
}

export const requestValidation = (
  scenarioId: string,
  batchId: string,
  context: ValidationContext,
): Promise<ValidationState> =>
  json(`${scenarioPath(scenarioId)}/imports/${batchId}/validation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(context),
  });

export const getValidation = (scenarioId: string, batchId: string): Promise<ValidationState> =>
  json(`${scenarioPath(scenarioId)}/imports/${batchId}/validation`);

export const getIssues = (
  scenarioId: string,
  batchId: string,
  after = 0,
): Promise<CursorPage<ValidationIssue>> =>
  json(`${scenarioPath(scenarioId)}/imports/${batchId}/validation/issues?after=${after}&limit=100`);

export const publishImport = (scenarioId: string, batchId: string): Promise<Revision> =>
  json(`${scenarioPath(scenarioId)}/imports/${batchId}/publish`, { method: "POST" });

export const getPublication = (scenarioId: string, batchId: string): Promise<Revision> =>
  json(`${scenarioPath(scenarioId)}/imports/${batchId}/publication`);

export const listRevisions = (scenarioId: string, after = 0): Promise<CursorPage<Revision>> =>
  json(`${scenarioPath(scenarioId)}/revisions?after=${after}&limit=50`);

export const getRevision = (
  scenarioId: string,
  revisionNo: number,
): Promise<RevisionDetail> =>
  json(`${scenarioPath(scenarioId)}/revisions/${revisionNo}`);
