# Delivery 2.4: operational allocation and reservations

This delivery prepares published revisions for planning. It does not start a
VROOM run or expose imported-revision execution in the interface; that wiring
belongs to 2.5. The original Milestone 1 demo remains unchanged, including
`ORD-003`: 20 units of `SKU-C` are required and each center has 5 available,
so `STOCK_NO_FULL_COVERAGE` is the correct result. VROOM remains behind
`SolverGateway`.

## Inventory scope and transitions

The immutable imported snapshot records stock on hand, external reservations,
and safety stock per center and SKU. Mutable operational positions belong to
the **scenario**, keyed by business center ID and SKU. Their database-generated
availability is:

```text
on_hand_quantity
  - externally_reserved_quantity
  - safety_stock_quantity
  - routeops_reserved_quantity
```

The initial RouteOps counter is zero. A `HELD` attempt increments it;
`CONFIRMED` retains the hold; `RELEASED` decrements it once. Release never
changes external reservations. Confirmation and release are idempotent and
append status events. There is no automatic expiration of a hold ready for
review. Recovery of an interrupted run in 2.5 must decide whether to confirm
or compensate it explicitly.

The scenario lock serializes allocation, confirmation, release, and activation
of a newer imported snapshot. A new revision replaces the *base* quantities
of existing positions but retains active RouteOps holds. If its base
availability is below those holds, activation fails with
`INVENTORY_RECONCILIATION_CONFLICT`; it never grants fresh stock merely because
the published snapshot has a zero RouteOps counter. Positions absent from the
new snapshot remain for historical release, but cannot serve new allocations.
New allocations use only the latest published revision. Existing attempts on
older revisions remain readable and can be released.

Migration `d42e4a91b6c0` adds the operational state, positions, attempts,
decision snapshots, order decisions, reservation lines, and event ledger.
Position `CHECK` constraints and the database transition trigger reject
negative availability. Reservation changes visit positions in stable
`(scenario, center ID, SKU)` order. The decision snapshot, all per-order
evidence, reservation lines, counters, and `BUILDING → HELD` event commit in one
transaction. A failed insert or stock update rolls everything back. Deferred
checks require a complete decision for each order and a matching reservation
for every line of every selected order. Evidence and events reject updates and
deletes. The downgrade is permitted only while the new operational tables are
empty and leaves prior PostGIS objects intact.

## Deterministic policies

Orders are considered by descending priority, then earlier window end, then
business ID. An order is allocated entirely to one center or not allocated.
For each center, the policy records stock considered by SKU, missing stock,
each local vehicle's skill and exact decimal capacity checks, matching vehicles, OSRM travel
duration for eligible candidates, inventory slack, ranking tuple, and discard
reason. The chosen center, reason, policy version, and sequence are stored
alongside a SHA-256 fingerprint of the operational inventory seen by the
attempt. Weight and volume use exact decimal comparisons at the precision
accepted by the import contract; the persistent schema permits finer values
but normal publication validates the narrower input contract. These
preliminary fleet checks do **not** prove shift, time-window, or complete route
feasibility. A routing service failure produces `ROUTING_DEPENDENCY_FAILED` and
rolls back; it is not reported as missing stock.

`greedy-v1` ranks eligible centers by `(duration, -stock slack, center ID)`.
`alternatives-v2` first maximizes the count of later orders that still have at
least one stock and fleet eligible center after the candidate allocation,
then uses the same duration, slack, and center tie breaks. This one-step
lookahead is deterministic and helps the restricted-order fixture. It does
not guarantee a global maximum of covered orders, feasible routes, or minimum
cost. Its work grows with orders, centers, and candidate evaluations; a
measured workload limit and strategy comparison remain for 2.5 and Hito 3.

## Reproducible allocation demos

Fixtures are in `backend/src/routeops/infrastructure/data/allocation_demos.py`;
assertions are in `backend/tests/unit/test_operational_allocation.py`.
Their travel durations are fixed so ranking stays reproducible independently
of the OSRM extract. Production allocation receives a travel-time provider.

| Demo | Expected result |
|---|---|
| Exclusive stock | `ORD-A → CD-A`, `ORD-B → CD-B`; mixed-line `ORD-MIX` gets `STOCK_NO_FULL_COVERAGE`. |
| Several eligible centers | `ORD-CHOICE → CD-B` (100 s versus 200 s); CD-A is recorded as a ranking loss. |
| Shared stock and restricted order | `greedy-v1` sends `ORD-FLEX → CD-A` and strands `ORD-RESTRICTED`; `alternatives-v2` sends flexible to CD-B and restricted to CD-A. |
| Fleet restrictions | Stock covers both orders, but one lacks a matching skill and the other exceeds vehicle capacity; both receive `NO_COMPATIBLE_VEHICLE`. |

Demo selection and display of the assigned CD, candidates, and exceptions
belong to 2.5–2.6. Comparison of broader strategy outcomes belongs to Hito 3.

## Verification boundary

PostgreSQL/PostGIS integration tests cover concurrent stock competitors,
same-key idempotency, multiline reservations, repeated confirmation/release,
rollback after an intermediate counter update, newer-revision reconciliation,
immutable evidence, and migration upgrade/downgrade. The original VROOM/OSRM
smoke checks the unchanged Milestone 1 path. Imported-revision execution and
its API/UI are deferred to 2.5.
