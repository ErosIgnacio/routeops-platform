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
  contract_version: "1.0" | "1.1"      # 1.1 when vehicle limits are present
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
  service_seconds: int >= 0             # imports require >= 1 minute
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
  max_route_distance_meters: int | null # closed-route meters, optional in 1.1
  max_driving_seconds: int | null       # travel seconds, optional in 1.1
  max_delivery_tasks: int | null        # complete delivery jobs, optional in 1.1

VehicleCost
  currency: uppercase three-letter code  # no currency-catalog lookup
  scale: int                            # cost units per currency unit
  fixed_units: int >= 0
  per_duty_hour_units: int >= 0
  per_km_units: int >= 0

OptimizationOptions
  timeout_seconds: int
  solution_quality: FAST | BALANCED | THOROUGH
  request_geometry: bool
```

VROOM calls the pinned routing service for routing matrices and geometry.
The allocation CD–order matrix and coverage evidence already exist in decision
snapshots; they are not routing matrices or a field of `OptimizationProblem`.
An objective selector was an initial design idea, not a current option.

Although v1 routes are deliveries, the contract names `DeliveryTask` rather
than pretending to support pickups/shipments. A future additive contract
version can introduce other task types.

### Problem invariants

- All IDs are unique in their respective collections.
- Allocation requires full stock coverage and an individually skill/capacity-
  compatible vehicle at the selected center. It does not guarantee full-route
  feasibility; VROOM may still leave the task unassigned.
- A task is never offered to a vehicle from another center in v1. The adapter
  enforces this with internal per-center skills that cannot collide with user
  skills.
- Capacity values and solver costs fit the target engine's integer range; an
  overflow is a mapping error, not truncation.
- Offset-aware instants use whole seconds and fit the horizon in UTC.
  `validate_problem` rejects duplicate IDs, out-of-horizon windows/shifts,
  mixed currencies/scales, open routes and integer overflow.
- Version 1.1 accepts positive optional vehicle limits through 2,147,483,647;
  version 1.0 requires them absent. VROOM receives `max_distance`,
  `max_travel_time` and `max_tasks`; RouteOps checks route totals and delivery
  count again on response. START/END do not count as tasks. An omitted limit
  does not relax shift, horizon or center rules.

## `OptimizationResult` v1

```text
OptimizationResult
  contract_version: "1.0" | "1.1"      # matches the submitted problem
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
  currency: uppercase three-letter code  # no currency-catalog lookup

OptimizedRoute
  vehicle_id: UUID
  source_vehicle_id: str
  distribution_center_id: str
  steps: tuple[RouteStep, ...]
  geometry: tuple[Coordinate, ...]
  totals: RouteTotals

RouteStep
  sequence: int
  kind: START | DELIVERY | END          # BREAK enum exists; unsupported in v1
  task_id: UUID | null
  order_id: str | null
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
- The legacy cost mapping remains `fixed`, `per_hour` and `per_km`. Despite
  the historical DTO name `per_duty_hour_units`, VROOM `per_hour` prices driving
  only. VROOM v1.15.0 supports `per_task_hour` for setup and service, but the
  current RouteOps mapping does not supply it (default zero). Waiting is not priced.
  A separate decimal business-cost query prices the complete duty duration.
- `solution_quality` maps through an adapter-owned policy to VROOM exploration
  and thread settings; changing VROOM tuning does not change the domain
  contract.
- VROOM's polyline geometry is decoded to canonical GeoJSON.
- VROOM `code` 1/2/3 maps to internal solver/internal, invalid-problem, and
  routing-dependency failures. The public API receives sanitized problem detail; normalized outcomes and
  versions are retained. Raw payload retention was an initial design proposal,
  not a claim of the current persistence model.
- The adapter checks that every returned task and vehicle ID exists, a task
  appears at most once, routes respect their allocated center, and summary
  counts reconcile before accepting the result.

VROOM does not provide a definitive business reason for each unassigned job.
`OptimizationResult.unassigned` contains only tasks that were submitted in its
matching `OptimizationProblem`. The application coordinator merges allocation
exclusions with the solver result
and updates counts before persistence. A `PlanningOutcome` DTO and the full
layered explanation service were initial design ideas; 3.1c owns the latter.
It never labels an inference as proven.

## 3.1a response integrity and cost query

The adapter and application reconcile complete deliveries, center/skills,
closed endpoints, window service start, shifts, integer loads and chronology.
Steps cannot invent setup/breaks or report violations. Cumulative metrics cannot
decrease; step sums, elapsed duty, route totals and summary metrics must agree.
Coordinates/polylines produce controlled response errors. Geometry checks retain
v0.2.1's 250 m coverage gap and 2 m delivery tolerance. If geometry is explicitly
disabled, geometry/leg-distance verification is unavailable. Route distance is still required
from VROOM/OSRM; a missing total is rejected, never treated as zero. Normal planning requests geometry.

`GET /api/v1/runs/{run_id}/estimated-operating-cost` is the 3.1a read-only base
for the later catalog, applicable to original and revision runs. Amounts are
four-place decimal strings. It returns calculation version, provenance hashes
and the unchanged solver objective separately. No historical `estimated_cost`
is renamed or overwritten. See the [matrix, formula and pending route limits](milestone-3-1a-contracts-costs.md).
