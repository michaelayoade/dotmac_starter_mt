# Subscriber account disposition — Sub source inventory

Dated 2026-08-22, measured against `dotmac_sub` at `883a0ff1a`. Written to
satisfy `dotmac-records`' `next_action`, which asks for a retention/deletion
writer inventory before adoption, and to supply the Sub half of it.

Records' dossier records `source_mode = "greenfield-after-inventory"` and states
that **no existing product is credited as a qualifying implementation source**.
This inventory confirms that judgement for Sub and explains why in specifics
rather than leaving it as an assertion.

## Scope

Account **disposition** only: the lifecycle by which a customer account is
deleted, retained for a period, becomes eligible for purge, and is purged.

Sub has other retention sweeps — event-store cleanup, device-metric pruning,
infrastructure-availability snapshots, WireGuard log retention, field-location
history. Those age out operational telemetry, carry no record-series or hold
semantics, and are **not** account disposition. They are out of scope for this
adoption and are named here so their absence below is deliberate rather than
missed.

## The implementation

Two modules, and between them they are the whole of it.

| Path | Role |
|---|---|
| `app/services/account_deletion.py` | The customer's deletion **request**. One function, `request_deletion`. |
| `app/services/web_system_restore_tool.py` | Deletion **execution**, retention window, restore, and purge. |

Neither is registry-declared as an owner. Both write their state into
`subscribers.metadata`, a JSONB column with no schema — inventoried separately
in `dotmac_sub:docs/SUBSCRIBER_METADATA_OWNERSHIP.md`.

## Retention is one integer, with no holds and no approval

`get_retention_days` reads a single domain setting, `restore_retention_days`,
defaulting to a constant and clamped to 1–3650. The purge due date is
`deleted_at + retention_days`, computed at deletion time and re-derived at sweep
time if absent.

That is the entire retention model. There is:

- **no record series** — every account gets the same number;
- **no schedule version** — changing the setting silently re-dates every
  in-flight account, because the sweep recomputes from the current value when
  `recovery_purge_due_at` is missing;
- **no trigger observation** — nothing outside the deletion itself can start,
  extend or reset the clock;
- **no legal hold** of any kind, at any level;
- **no approval** — the sweep is autonomous;
- **no disposition evidence** — nothing records who authorized destruction, or
  that it was verified.

Records supplies all seven. The gap is not a difference of implementation
quality; Sub has no representation for the concepts.

## The finding that matters most: purge destroys nothing

`purge_expired_from_recovery_queue` is the only function named "purge". Its
entire effect on an eligible account is:

```python
metadata[PURGED_AT_KEY] = cutoff.isoformat()
subscriber.metadata_ = metadata
purged += 1
```

There is no `db.delete`, no column clearing, no anonymisation, no redaction and
no call into any owner that performs one. Verified by search across the module:
zero matches for deletion, anonymisation or scrubbing of any kind.

**"Purge" sets a flag whose only functional consequence is that
`restore_subscriber` then refuses with a 409.** The subscriber row and every
byte of its personal data — name, email, NIN, address, coordinates — remain, as
do invoices, payments, RADIUS users and credentials, indefinitely.

The code states the opposite in two places. `account_deletion.py`'s docstring
says *"operations then purge personal data per the privacy policy"*, and an
inline comment at the stamping site says *"the eventual personal-data purge"*.
Both describe an operation that is not implemented anywhere in the repository.

This is recorded as a factual gap between documented and actual behaviour. Its
regulatory significance is for the accountable owner to assess, not for this
inventory to conclude; what the inventory can say is that an account marked
purged has had nothing removed, and that no other code path performs the
removal the comments promise.

## Consequences for adoption

1. **Records is not replacing a working implementation.** It is supplying a
   capability Sub documents and does not have. `source_mode =
   "greenfield-after-inventory"` is correct and this inventory does not change
   it.
2. **There is no parity baseline.** Nothing in Sub's test tree exercises
   `purge_expired_from_recovery_queue`, `mark_subscriber_deleted`,
   `restore_subscriber` or `build_restore_preview`; the module appears in tests
   only in architecture baselines and as a named exception in three boundary
   tests. Characterization tests must be written against current behaviour
   before anything moves.
3. **Adoption changes behaviour by design.** Sub's purge is a flag; Records'
   disposition authorizes destruction only from matching physical confirmation.
   Adopting it means implementing destruction that does not exist today, which
   is a decision with a data-protection dimension and an explicit approval
   gate — not a refactor, and not something a cutover should perform silently.
4. **`restore_retention_days` cannot be carried across as-is.** A single
   unversioned integer maps to no Records schedule; the migration must choose a
   record series and a schedule version, which is an ownership decision rather
   than a data transformation.

## Adjacent, not owned here

The deletion **cascade** — nine resource classes deactivated across
subscriptions, service orders, invoices, payments, access credentials, RADIUS
users, IP/ONT/splitter assignments — is coordination across those owners, not
disposition. It belongs to the Sub assembly, with `dotmac-customers` owning the
account's own lifecycle state. Records governs only whether and when
destruction is permitted, and records the evidence that it was.
