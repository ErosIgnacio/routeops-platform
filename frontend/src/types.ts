export type Coordinate = {
  latitude: number;
  longitude: number;
};

export type RouteStep = {
  sequence: number;
  kind: "START" | "DELIVERY" | "END" | "BREAK";
  order_id: string | null;
  location: Coordinate;
  arrival_at: string;
  time_window_status: string;
  service_start_at?: string;
  departure_at?: string;
  travel_seconds_from_previous?: number;
  waiting_seconds?: number;
  service_seconds?: number;
  load_after?: {units: number; weight_grams: number; volume_cm3: number};
};

export type OptimizedRoute = {
  vehicle_id: string;
  source_vehicle_id: string;
  distribution_center_id: string;
  steps: RouteStep[];
  geometry: Coordinate[];
  departure_condition?: {policy: string; departure_at: string; scope: string};
  totals: {
    distance_meters: number;
    driving_seconds: number;
    service_seconds: number;
    waiting_seconds: number;
    total_duration_seconds: number;
    objective_cost_units?: number;
  };
};

export type UnassignedOrder = {
  order_id: string;
  stage: string;
  reasons: Array<{
    code: string;
    certainty: "PROVEN" | "INFERRED";
    detail: string;
  }>;
};

export type PlanningRun = {
  run_id: string;
  scenario_name: string;
  status:
    | "QUEUED"
    | "RUNNING"
    | "READY"
    | "ACCEPTED"
    | "CANCELED"
    | "SUCCEEDED"
    | "PARTIAL"
    | "FAILED";
  started_at: string;
  completed_at: string | null;
  input: { solution_quality: string; [key: string]: unknown };
  result: {
    routes: OptimizedRoute[];
    unassigned: UnassignedOrder[];
    solver: {
      engine: string;
      engine_version: string;
      routing_engine: string;
      routing_engine_version: string;
      solve_duration_ms: number;
    };
  } | null;
  kpis: {
    routes: number;
    vehicles_used: number;
    assigned_orders: number;
    unassigned_orders: number;
    distance_km: number;
    driving_hours: number;
    service_hours: number;
    waiting_hours: number;
    total_hours: number;
    solver_time_ms: number;
    estimated_cost: number;
    currency: string;
    network_coverage?: {
      max_snap_distance_m: number;
      points: Array<{ role: string; business_id?: string; original: number[]; snapped: number[] | null; distance_m: number | null }>;
    };
  } | null;
  error: string | null;
};
