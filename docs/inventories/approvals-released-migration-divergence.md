# Approvals released-migration divergence

**As of:** 2026-08-17
**Repository:** `dotmac_starter_mt`
**Disposition:** confirmed released history; guarded, not repaired in place

## Finding

`ap_0001_approvals` has now been published under five tags with three different
byte sets. The first four tags existed before `dotmac-approvals` entered the
released-migration guard; a5 reuses the canonical bytes and adds a new revision:

| Release | SHA-256 | Meaning |
|---|---|---|
| `0.1.0a1` | `ec5e1aa9e504de8143eebaafacb0615cf24b6ea930648f5b9cfd1a9afc2db70e` | Always builds tenant and platform planes |
| `0.1.0a2` | `6c7b3263e05f860982dda125439171f62bba716d36d95b21e2c3a3224f19ad6a` | Always builds platform; builds tenant when its prerequisite is bound |
| `0.1.0a3` | `102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb` | Builds the assembly's explicit tenant/platform selection |
| `0.1.0a4` | `102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb` | Byte-identical to a3 |
| `0.1.0a5` | `102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb` | Retains a3/a4 and ships additive `ap_0002_outbox_relay` |

Alembic records that revision `ap_0001_approvals` ran, not which bytes gave the
revision its meaning. Rewriting the file again would therefore give a fresh
installation a fourth schema history while an existing installation silently
kept one of the first three.

## Enforced disposition

The tree retains the a3/a4/a5 digest as the canonical released bytes. The guard in
`tests/architecture/test_released_migrations.py` cross-checks every entry
against its Git tag, requires the exact three-variant census, and has a
sensitivity proof that a fourth byte set fails. This is a grandfathered fact,
not a grandfathered permission.

Repairs are additive. `ap_0002_outbox_relay` verifies the newly named
`outbox_relay.v1` prerequisite and never edits or reruns `ap_0001`. Release run
`32062654126` installed and registered a5 from the private index before tagging
`dotmac-approvals-v0.1.0a5` on `8d4ddfd`.

## Upgrade evidence

`tests/test_approvals_released_migration_upgrades.py` reconstructs the exact
tagged source and runs six PostgreSQL cases:

1. a1's mandatory two-plane database;
2. a2 platform-only with no tenant binding;
3. a2 with both planes and a tenant binding;
4. a3/a4/a5's `ap_0001` meaning, tenant-only;
5. a3/a4/a5's `ap_0001` meaning, platform-only;
6. a3/a4/a5's `ap_0001` meaning, with both planes.

Each case seeds a policy row in every selected plane, switches to the current
lineage, upgrades to `ap_0002`, and proves the rows and selected table set are
unchanged. The test also verifies the historical blob digest before executing
it, so a reconstructed approximation cannot pass as release evidence.
