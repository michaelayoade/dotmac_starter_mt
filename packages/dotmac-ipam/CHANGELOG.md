# Changelog

## 0.2.0a1 — unreleased

Extraction re-cut from Sub main `a7cc2e8ea` (includes #3812 IPv6 link/unlink).
It replaces the 0.1 template behaviour; `0.1.0a1` must not be installed (its
tag, lineage and `ip_0001` bytes stay untouched).

- Adds additive revision `ip_0002_extracted`: refuses to run when any 0.1
  template table holds a row, drops the five template tables, and creates the
  Sub-shaped tables (`ip_pools`, `ip_blocks`, `ipv4_addresses`, `ipv6_addresses`,
  `ip_assignments`, `subscriber_additional_routes`, `ipv6_delegated_prefixes`,
  `ip_pool_utilization_snapshots`) with `tenant_id`, `(tenant_id, id)` keys,
  composite intra-module foreign keys and forced RLS. `ipam_events` is kept.
- Utilization snapshots keep append-only semantics (a deliberate difference from
  Sub, which allowed updates and cascade deletes): grants are SELECT, INSERT,
  DELETE (DELETE only for retention pruning), a refuse-UPDATE trigger, and the
  pool foreign key is ON DELETE RESTRICT.
- Template-row refusal fails closed under forced RLS (`SET LOCAL row_security =
  off`, bounded `lock_timeout`/`statement_timeout`, as bi_0004/bi_0008).
- Product identities stay opaque UUIDs under Sub's column names; `ont_unit_id`
  became `device_ref`; pools gain `site_ref`, `purpose`, `allow_network_broadcast`;
  assignments gain `released_at` / `release_reason`.
- Allocator (SKIP LOCKED lowest-free, on-demand materialisation, routed-block
  exclusion), fingerprinted idempotent repair, IPv6 link and ownership unlink,
  atomic move, release-for-subject, IPv6 prefix delegation, routed prefixes,
  utilization snapshots, management allocation and an IPAM-internal consistency
  audit. Subscription truth, session occupancy and policy windows arrive as
  typed caller facts; the module reads no session, NAS, device or subscription.
- Adds a `before_flush` write guard keyed on a command-scope contextvar and
  `dotmac_ipam.read_models` for same-database readers (explicit widening, N4).
- Raises the kernel floor to `0.1.0a98` (`dotmac_kernel.transactions`).
- No release, adoption or authority transfer is implied.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `bfc112fc` by release run
`32481475347`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Adds the tenant-only `mod_ipam` lineage (`ip` revision prefix).
- Owns tenant address-space, pool, address, reservation, assignment,
  utilization and repair lifecycle.
- Exposes `versions_dir()` as the public installed-lineage locator.
