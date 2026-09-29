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
};

export type OptimizedRoute = {
  vehicle_id: string;
  source_vehicle_id: string;
  distribution_center_id: string;
  steps: RouteStep[];
  geometry: Coordinate[];
  totals: {
    distance_meters: number;
    driving_seconds: number;
    service_seconds: number;
    waiting_seconds: number;
    total_duration_seconds: number;
    objective_cost_units: number;
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
  status: "RUNNING" | "SUCCEEDED" | "PARTIAL" | "FAILED";
  started_at: string;
  completed_at: string | null;
  input: {
    dataset_name: string;
    dataset_seed: number;
    synthetic: boolean;
    solution_quality: string;
  };
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
  } | null;
  error: string | null;
};
