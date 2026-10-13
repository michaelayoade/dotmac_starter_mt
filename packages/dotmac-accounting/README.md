# dotmac-accounting

`dotmac-accounting` owns a tenant's chart of accounts, fiscal years and
periods, open accounting dimensions, balanced journal posting, reversal and
immutable ledger/period evidence.

It is not a receivables, payables, tax, asset, inventory, banking, payment,
numbering or approval owner. Those domains submit typed facts through the
adopting assembly. The package is tenant-only, owns `mod_accounting` through
the independent `ac` lineage, and joins the caller's transaction without
commit or rollback.

See `EXTRACTION.toml`, ADR-0041 and
`docs/inventories/accounting-payables-sources.md` for the product-first
boundary.

Candidate `0.1.0a2` adds `ac_0002_posted_content` after the unchanged published
`ac_0001_accounting` migration. Source line and dimension writes lock their
OLD and NEW ancestry; only draft ancestry is authorable. A dimension resolves
its journal through a locked source line, so concurrent line reparenting cannot
move the authorization check to stale ancestry. The caller owns the transaction.

Both migration directions drain parent and child writers under a transaction
local five-second lock timeout, restoring the caller's previous timeout on
success. Failure rolls back the migration and its timeout change. Downgrade is
permitted only with draft journals and no appended ledger evidence; it restores
the predecessor guard and therefore its weaker draft-era behavior. It refuses
non-draft journals (including VOID), posted ledger lines and posted dimensions.

Deterministic parent ordering avoids opposite reparent lock-order cycles within
one trigger. PostgreSQL may still abort a transaction in a child-edit versus
parent-delete or posting/FK deadlock; callers must retry the whole transaction,
never just its last statement. This is integrity-safe, not a deadlock-free API.
No release, adoption, production migration or repair of historical content is
implied by this candidate. Already altered historical source facts cannot be
reconstructed by the migration; posted ledger snapshots remain authoritative.
