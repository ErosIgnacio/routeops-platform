# ADR-0002: Approved v1 product decisions

- **Status:** Accepted
- **Decision date:** 2026-09-24

## Context

Milestone 0 left four choices open because each affects public licensing,
runtime assets, optimization semantics, or fair baseline comparison. The user
approved all four proposals before Milestone 1 implementation began.

## Decisions

1. RouteOps source code uses Apache License 2.0. Dependency and OSM notices
   remain separate obligations.
2. The demo uses a small, bounded Santiago road extract. The query, bbox,
   endpoint, UTC download time, size, SHA-256, ODbL notice, and attribution are
   recorded in `data/osrm/source-lock.json`.
3. V1 routes are closed: every vehicle starts and ends at its assigned center.
4. A manual baseline identifies the vehicle and ordered order IDs, with optional
   planned timestamps. Distance, travel time, feasibility, and comparable cost
   are recomputed through the same pinned OSRM data as optimized plans.

## Consequences

- The adapter always supplies VROOM vehicle `start` and `end` at the same center.
- Updating live OSM data is an explicit reviewed lock change and requires OSRM
  reprocessing.
- A baseline's claimed road metrics cannot bypass RouteOps routing facts.
- Publication must retain Apache, BSD, ODbL, and tile attribution requirements.
