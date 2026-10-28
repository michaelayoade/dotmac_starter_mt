# dotmac-ipam 0.2.0a1 parity with Sub (a7cc2e8ea)

Parity is by behaviour class on synthetic data, not by file-for-file replay.
Sub's 23 IPAM-related test files hold 247 tests; this map names, per file,
where each class lives here or why it is N/A. "Sub" below means the adopting
product keeps it (G10), not that it is dropped.

## Intended, expected differences

| # | Class | Sub | dotmac-ipam 0.2.0a1 | Test |
|---|---|---|---|---|
| D1 | Lowest-free ordering | `ORDER BY address` on the string column: `10.0.0.10` sorts before `10.0.0.2` | Numeric order (candidates fetched, sorted by integer value, then locked with SKIP LOCKED one at a time) | `test_lowest_free_is_numeric_not_string_order` |
| D2 | Network/broadcast, gateway | materialised rows could be handed out; no gateway check | never allocated unless `allow_network_broadcast`; pool gateway never allocated | `test_gateway_is_never_allocated`, `test_network_and_broadcast_only_when_the_pool_opts_in` |
| D3 | Reuse | any inactive row immediately reusable | caller-supplied cool-down window (same subject exempt) | `test_cooldown_*` |
| D4 | Occupancy | IPAM rows only; Sub read sessions/served column inside the allocator | typed `occupied` argument | `test_occupied_addresses_held_by_others_are_skipped` |
| D5 | Utilization snapshots | updatable, cascade-deleted with pool | append-only: no UPDATE grant, refuse-UPDATE trigger, FK RESTRICT (Postgres) | migration tests |
| D6 | Selection grain | subscriber `.first()` | exact `subscription_id` | `test_two_services_of_one_subscriber_get_separate_addresses` |

## Sub test file -> dotmac-ipam

| Sub file (tests) | Disposition |
|---|---|
| `architecture/test_ip_assignment_service_ownership.py` (11) | module side: `test_ipam_guard_and_schema.py` (guard, no-commit, read_models). Sub side (no Sub module writes a read_models class): N/A here, Sub G10 |
| `architecture/test_ip_consistency_alerts.py` (5) | N/A, alert rules stay in Sub |
| `architecture/test_ipv4_primary_marker_ownership.py` (5) | single writer: guard tests + `test_exactly_one_active_primary_per_service` |
| `architecture/test_served_ipv6_writer_boundary.py` (3) | N/A, served column is Sub |
| `test_ip_address_list_projection.py` (7), `test_ipam_olt_detail_adapters.py` (8), `test_web_network_ipam_ledger.py` (3) | N/A, Sub UI |
| `test_ip_assignment_ipv6_link_unlink.py` (15) | link/unlink decisions, stale, replay: `test_ipam_repair.py` (ipv6 link x3, unlink x3). CLI, served-column and audit-row parts: N/A, Sub |
| `test_ip_assignment_lifecycle.py` (18), `test_ip_assignment_repair.py` (9) | `test_ipam_repair.py`; subscription status checks become `RepairFacts` |
| `test_ip_assignment_projection_handler.py` (1), `test_ip_assignment_subscription_sync.py` (10) | N/A, served-column projection is Sub |
| `test_ip_assignment_schema.py` (3) | `test_ipam_guard_and_schema.py` (constraints, indexes), DB-level primary/active uniqueness in `test_ipam_allocator.py` |
| `test_ip_consistency_audit.py` (32) | IPAM-internal classes: `test_audit_*` in `test_ipam_pools_prefixes_queries.py`. Session/RADIUS/served-column classes: N/A, Sub |
| `test_ip_lifecycle.py` (17) | release parts: release tests in `test_ipam_allocator.py`; backlog planner deleted after pre-clean (SPEC 2.2) |
| `test_ipam_collision_prevention.py` (17), `test_ipv4_allocator_collision_guards.py` (11) | `test_ipam_allocator.py` explicit-assign refusals and exclusion tests |
| `test_ipv4_on_demand_allocator.py` (11) | `test_ipam_allocator.py` on-demand tests |
| `test_ipv4_primary_assignment_marker.py` (9) | `test_ipam_allocator.py` primary tests |
| `test_ipv4_retention_sweep.py` (28) | policy stays Sub; module part: retention path in `test_ipam_repair.py`, `sweep_expired` tests |
| `test_ipv6_pd_allocator.py` (14) | `test_ipam_pools_prefixes_queries.py` PD tests |
| `test_ipv6_pd_provisioning.py` (8) | N/A, feature flag and profile-based pool choice stay Sub |
| `test_legacy_ip_assignment_migration_bridge.py` (2) | N/A, deleted after pre-clean |

Postgres-only behaviour (migration round trip, RLS fail-closed, SKIP LOCKED
concurrency, index enforcement, DML guard) is in
`tests/test_ipam_postgres_integration.py`.
