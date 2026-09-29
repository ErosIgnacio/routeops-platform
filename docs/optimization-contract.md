# Solver-neutral optimization contracts

## Design goals

`OptimizationProblem` and `OptimizationResult` are immutable, versioned domain
DTOs. They use business identifiers, named capacity dimensions, UTC instants,
SI units, and canonical geometry. They contain no VROOM JSON field names,
integer surrogate IDs, array positions, status codes, or encoded polylines.

The application owns these contracts. A `SolverGateway` maps them to an engine:

```python
class SolverGateway(Protocol):
    def solve(self, problem: OptimizationProblem) -> OptimizationResult: ...
```

The first implementation is `VroomAdapter`. Request/response payloads and ID
maps are integration artifacts, not domain models.

## `OptimizationProblem` v1

```text
OptimizationProblem
  contract_version: "1.0"
  problem_id: UUID
  scenario_id: UUID
  horizon_start: datetime               # timezone-aware instant
  horizon_end: datetime                 # timezone-aware instant
  timezone: IANA timezone name
  tasks: tuple[DeliveryTask, ...]
  vehicles: tuple[OptimizationVehicle, ...]
  options: OptimizationOptions

DeliveryTask
  task_id: UUID
  order_id: str
  distribution_center_id: str          # fixed by stock allocation
  location: Coordinate
  demand: Capacity(units, weight_grams, volume_cm3)
  service_seconds: int > 0
  time_window_start: datetime
  time_window_end: datetime
  priority: int                         # 0..100
  required_skills: frozenset[str]

OptimizationVehicle
  vehicle_id: UUID
  source_vehicle_id: str
  distribution_center_id: str
  vehicle_type: str
  start: Coordinate
  end: Coordinate                       # same center as start in v1
  shift_start: datetime
  shift_end: datetime
  capacity: Capacity
  skills: frozenset[str]
  costs: VehicleCost

VehicleCost
  currency: ISO-4217 code
  scale: int                            # cost units per currency unit
  fixed_units: int >= 0
  per_duty_hour_units: int >= 0
  per_km_units: int >= 0

OptimizationOptions
  timeout_seconds: int
  solution_quality: FAST | BALANCED | THOROUGH
  objective: MINIMIZE_ESTIMATED_COST
  request_geometry: bool
```

The initial vertical slice lets VROOM call the pinned routing service. A future
additive contract may introduce a materialized, auditable travel matrix; the
current contract does not name the routing provider.

Although v1 routes are deliveries, the contract names `DeliveryTask` rather
than pretending to support pickups/shipments. A future additive contract
version can introduce other task types.

### Problem invariants

- All IDs are unique in their respective collections.
- Every task's center exists in the vehicle set and every task has at least one
  vehicle at that center with a superset of its required skills.
- A task is never offered to a vehicle from another center in v1. The adapter
  enforces this with internal per-center skills that cannot collide with user
  skills.
- Capacity values and solver costs fit the target engine's integer range; an
  overflow is a mapping error, not truncation.
- Instants fall within the planning horizon after timezone normalization.

## `OptimizationResult` v1

```text
OptimizationResult
  contract_version: "1.0"
  problem_id: UUID
  status: SUCCEEDED | PARTIAL | FAILED
  solver: SolverMetadata
  summary: OptimizationSummary
  routes: tuple[OptimizedRoute, ...]
  unassigned: tuple[UnassignedTask, ...]
  warnings: tuple[str, ...]

SolverMetadata
  engine: str                           # "vroom"
  engine_version: str
  adapter_version: str
  routing_engine: str | null
  routing_engine_version: str | null
  solve_duration_ms: int

OptimizationSummary
  route_count: int
  assigned_task_count: int
  unassigned_task_count: int
  distance_meters: int
  driving_seconds: int
  service_seconds: int
  waiting_seconds: int
  total_duration_seconds: int
  objective_cost_units: int
  cost_scale: int
  currency: ISO-4217 code

OptimizedRoute
  vehicle_id: UUID
  distribution_center_id: UUID
  steps: tuple[RouteStep, ...]
  geometry: tuple[Coordinate, ...]
  totals: RouteTotals

RouteStep
  sequence: int
  kind: START | DELIVERY | END | BREAK
  task_id: UUID | null
  location: Coordinate
  arrival_at: UTC instant
  service_start_at: UTC instant
  departure_at: UTC instant
  travel_seconds_from_previous: int
  distance_meters_from_previous: int
  waiting_seconds: int
  service_seconds: int
  load_after: Capacity
  time_window_status: NOT_APPLICABLE | ON_TIME | EARLY_WAIT | LATE

UnassignedTask
  task_id: UUID | null
  order_id: str
  stage: ALLOCATION | OPTIMIZATION | ROUTING
  reasons: tuple[UnassignedReason, ...]

UnassignedReason
  code: stable domain code
  certainty: PROVEN | INFERRED
  detail: str
  evidence: mapping[str, scalar]
```

`PARTIAL` means the solver completed successfully but left tasks unassigned;
`FAILED` means there is no valid result to consume. A transport timeout, VROOM
input error, and OSRM routing error map to distinct application errors and
domain reason codes.

## VROOM adapter mapping

- UUIDs map to deterministic positive integers through a per-request ID table;
  they are never exposed as business IDs.
- `Capacity` maps in the fixed order `[units, weight_grams, volume_cm3]`.
- Task center isolation is encoded with reserved adapter skills in addition to
  user skills.
- Coordinates map from named `{latitude, longitude}` values to VROOM's
  `[longitude, latitude]` arrays.
- Time-aware instants map to seconds from the planning-horizon start. This
  avoids platform-dependent Unix timestamp width and makes results easier to
  reproduce. The adapter converts them back to UTC instants.
- Costs map to VROOM's integer `fixed`, `per_hour`, and `per_km` values.
- `solution_quality` maps through an adapter-owned policy to VROOM exploration
  and thread settings; changing VROOM tuning does not change the domain
  contract.
- VROOM's polyline geometry is decoded to canonical GeoJSON.
- VROOM `code` 1/2/3 maps to internal solver/internal, invalid-problem, and
  routing-dependency failures. Raw error text is retained in the bounded audit
  payload, while the public API receives a sanitized problem detail.
- The adapter checks that every returned task and vehicle ID exists, a task
  appears at most once, routes respect their allocated center, and summary
  counts reconcile before accepting the result.

VROOM does not provide a definitive business reason for each unassigned job.
`OptimizationResult.unassigned` contains only tasks that were submitted in its
matching `OptimizationProblem`. The application-level `PlanningOutcome` wraps
this result together with validation/allocation exclusions, then the explanation
service combines those facts, deterministic feasibility checks, and the solver
outcome. It never labels an inference as proven.
