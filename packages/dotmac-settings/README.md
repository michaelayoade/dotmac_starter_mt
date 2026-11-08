# dotmac-settings — PARKED (2026-11-08)

Do not adopt. The kernel at `0.1.0a107` already ships
`dotmac_kernel.settings_resolver` (spec registry, strict resolution,
scopes, history) plus `settings_shadow`, whose ADR-0003 adoption path
(shadow-compare phases) is the sanctioned route for products with
their own resolvers. Academy will adopt the kernel resolver through
those phases post-flip instead of extracting its store. This scaffold
is retained only as the inventory of gaps the adoption must close or
propose upstream: relation-checked bulk writes, whole-object override
expansion with split-row refusal, advisory-lock CAS writes,
schema-shadow-immune reads, and Session pending/autoflush semantics.

---

Validated operator-settings policy layer over the kernel
`domain_settings` store. A consuming assembly declares its keys
(defaults + validators), optional whole-object override keys, relation
rules and an environment overlay; the store then enforces:

- unknown keys refused on write, ignored on read;
- strict reads — `effective(require_store=True)` never authorizes
  from defaults when the store cannot be read;
- stored NULLs are malformed, retained only for `retain_null` keys;
- whole-object overrides expand onto member keys, refuse split member
  rows, and write/delete under compare-and-swap (exactly one
  concurrent writer wins; the loser is refused before writing, with
  its transaction left alive);
- canonical statements address `public.domain_settings` with literal
  schema-qualified SQL, immune to caller `search_path` and schema
  translation, with ORM autoflush/pending semantics preserved.

The package owns no tables, no migrations and no product vocabulary.
Storage remains the kernel's `domain_settings` (platform scope);
tenant-scoped settings are out of scope for 0.1.

```python
from dotmac_settings import KeySpec, OverrideSpec, SettingsStore

store = SettingsStore(
    domain="academy",
    keys={
        "branding_name": KeySpec(default="Academy"),
        "password_min_length": KeySpec(default=12, validate=parse_positive_int),
        "execution_overrides": KeySpec(default=None),
        "worker_poll_seconds": KeySpec(default=5, validate=parse_positive_int),
    },
    overrides={
        "execution_overrides": OverrideSpec(
            members=("worker_poll_seconds",),
            validate=validate_execution_overrides,
        ),
    },
    validate_relations=check_relations,
)

settings = store.effective(db, require_store=True)
store.set_many(db, {"password_min_length": 14})
```

`0.1.0a1` is source-only: not released, not published, not adopted.
Extracted from Academy v2 (`app/services/settings_store.py` at
`b26c56c`), where the behavior is proven by the issue #2 qualification
campaign (fresh-policy, shadow-schema, CAS race, and pending-state
suites). Academy adoption follows a reviewed release, like Learning.
