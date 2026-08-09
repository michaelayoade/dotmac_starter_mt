# ADR-0012: A setting declares whether it inherits

- **Status:** Accepted (2026-08-09)
- **Scope:** Fleet-wide.
- **Relates to:** ADR-0008 (declaration registries), ADR-0011 (env is a loader)

## Context

Settings resolution walks a scope chain, most specific first, ending at the
platform row and then the spec default. One precedence policy, applied to every
setting.

That policy is right for most of them. A timezone, a date format, a retry
threshold, a feature toggle set for the deployment **is** a real answer for a
tenant that has not overridden it.

It is wrong — dangerously — for a value that IDENTIFIES something belonging to
one scope. `dotmac_erp` demonstrates both halves:

```python
# fx_revaluation.py — hand-written, organisation-only, refuses when unset
DomainSetting.organization_id == organization_id

# payment_service.py — resolve_value, inherits the global fallback
resolve_value(db, SettingDomain.payments, "paystack_transfer_fee_account_id", ...)
```

Both read a **general-ledger account identifier**. One guards against inheriting
another organisation's account; the other does not. The difference is not a
design decision — it is which author happened to think about it. ERP has at
least eight such settings (`fx_gain/loss_account_id`, three
`paystack_*_account_id`, `payroll_rounding_account_id`,
`inventory_default_warehouse_id`, and two `*_prefix`) and guards one.

The generalisation: **a fallback is the claim that a less-specific value is a
valid answer to this question.** For a preference it is. For an account number
it is not — there is no "default GL account", and inheriting one means posting
to another tenant's books.

The resolver cannot tell those apart. Only the declaration can.

## Decision

`SettingSpec` gains `inherits: bool = True`.

- `True` (default) — resolution walks the declared scope chain, then the spec
  default. Today's behaviour, and correct for the large majority.
- `False` — the setting is read at the asked-for scope and **nowhere else**. A
  less-specific row cannot answer for it. Resolution falls to the spec default,
  and pairing with `required_at` yields "must be set here, no fallback, fail
  loudly" — which is what an identifier almost always wants.

Both the single-key and bulk paths honour it. A settings screen that showed an
inherited account no individual read would return would be worse than either
behaviour alone.

`inherits` is part of a spec's fingerprint, so two declarations of one key
differing only in this are a conflict rather than a harmless re-import — they
genuinely resolve differently.

## Why a spec field and not a caller argument

A `resolve_value(..., inherit=False)` parameter was rejected. It puts the
property at the READ, so it must be repeated at every call site and is wrong the
first time someone forgets — which is precisely the failure ERP already has,
just relocated from a hand-written query to a keyword argument.

The property belongs to the VALUE. Declaring it once means the second reader of
a GL account is safe without knowing they needed to be, and that is the whole
difference between the two ERP call sites above.

This is the same move as ADR-0008 (which domains exist), ADR-0011 (whether env
outranks a row), and the typed read contract (what type a value is): something
the kernel was deciding on every consumer's behalf becomes something the owning
module declares.

## Consequences

- **A whole reason to bypass the resolver disappears.** `fx_revaluation`'s
  hand-written query is currently the *correct* engineering choice; no
  consolidation effort can succeed against that. With `inherits=False` the
  resolver expresses what it needs, so consolidation becomes possible rather
  than merely desirable.
- **Identifier settings become declarations, not vigilance.** Eight ERP settings
  stop being eight opportunities to remember to write the query correctly.
- **`required_at` gains its natural partner.** "Required at tenant" plus "does
  not inherit" is the complete statement of an org-scoped identifier.
- **No migration.** The default preserves existing behaviour exactly, and no
  product has adopted kernel settings yet — `dotmac_erp` pins `a13`,
  `dotmac_sub` imports none. As with the typed contract, this is the last point
  at which the change is free.

## What this does not do

It does not decide which settings should be non-inheriting — that is a product
judgement, made per spec. The kernel supplies the vocabulary and the guarantee,
not the classification.

It also does not add a per-scope override ("inherit from tenant but not from
platform"). Two levels of nuance without a demonstrated need would be
speculative; the binary covers every case observed so far, and a richer policy
can be declared later without changing what `inherits=False` means.

## Enforcement

`tests/unit/test_setting_inherits.py`: a platform row is invisible to a
non-inheriting tenant read but visible to an inheriting one; a non-inheriting
setting still reads its own scope and still reads platform when asked *at*
platform; bulk and single-key agree for both kinds and for a mixed set; and a
conflicting re-declaration raises.
