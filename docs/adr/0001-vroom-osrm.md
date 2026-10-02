# ADR-0001: Encapsulate VROOM and use OSRM as the routing service

- Status: Accepted (implemented and verified in Milestones 1–2)
- Date: 2026-09-24

## Context

RouteOps v1 needs route optimization, travel-time matrices, distances, and map
geometry while keeping business logic independent from an external solver's
JSON contract. The requested first-stage engine is VROOM through vroom-express,
with OSRM as a separate routing service.

## Decision

1. Use VROOM as the only v1 optimization engine through the application port
   `SolverGateway` and infrastructure adapter `VroomAdapter`.
2. Use the official `vroom-docker:v1.15.0` image, which bundles VROOM `1.15.0`
   and vroom-express `0.12.0`.
3. Use a separate, pinned OSRM `26.9.0-debian` service with an MLD dataset
   processed by the same exact image version used at runtime.
4. Keep `OptimizationProblem` and `OptimizationResult` solver-neutral. Only the
   adapter maps IDs, units, arrays, error codes, and geometry.
5. Store sanitized normalized facts for queries plus bounded raw integration
   payloads and hashes for audit. Domain services never consume raw payloads.
6. The backend uses an independent `RoutingGateway` for allocation ranking,
   feasibility diagnostics, and manual-baseline evaluation; VROOM calls the
   same OSRM instance for optimization.
7. Pin tags and manifest digests. Upgrades require contract and smoke tests and,
   for OSRM monthly releases, a map dataset rebuild.

## Consequences

### Positive

- VROOM can be replaced or complemented without rewriting inventory or KPI
  rules.
- Official images reduce local native-build complexity on Windows and Linux.
- A shared OSRM dataset makes allocation, optimization, and comparison more
  internally consistent.
- Raw payload retention and version metadata make runs auditable.

### Costs and risks

- Two OSRM clients (backend and VROOM) require consistent profiles and limits.
- vroom-express is a thin wrapper with a slower release cadence; RouteOps needs
  timeouts, health checks, input bounds, and defensive response validation.
- HTTP cancellation cannot guarantee that a timed-out solver subprocess has
  immediately stopped; solver concurrency and container resources must be
  bounded.
- VROOM unassigned output is not a complete cause analysis, so RouteOps owns a
  separate evidence-based explanation component.
- Map data changes can alter results even with identical application and solver
  versions; the extract checksum is part of run provenance.

## Later implementation decisions

Milestone 2 introduced durable PostgreSQL job leases (no additional queue),
private `ObjectStorageGateway`, scenario-wide operating inventory and per-order
reservations. Allocation's CD–order duration matrix and snapping evidence are
persisted in decision snapshots; VROOM still builds its own routing matrices.
3.1a audits response facts and adds a separate decimal cost derivation. Raw
vendor JSON retention and an optional matrix field were initial design ideas;
the current DTO has neither. Normalized results, decision evidence, versions
and source hashes are the current audit facts. Baseline evaluation is 3.2.

## Alternatives rejected for v1

- Calling the VROOM JSON API directly from domain/application services: creates
  vendor coupling and contaminates stable models.
- Linking `libvroom` or `libosrm` into the Python backend: increases build and
  platform complexity without MVP value.
- Precomputing every matrix in the backend from day one: useful later for exact
  replay, but it expands the first vertical slice. The internal contract already
  supports an optional matrix.
- Building RouteEngine now: explicitly outside the v1 scope.
