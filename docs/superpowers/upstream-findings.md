# Upstream findings — gaps in source repos discovered while porting

Policy (Michael, 2026-07-17): when porting reveals a real bug or never-wired feature in a
source repo, log it here and batch PRs back to that repo after the current plan lands.
Upstream only what fixes the source repo on its own terms — not starter-specific redesigns
(tenancy, domain exceptions, layout).

## dotmac_erp

1. **`custom_fields_max_per_entity` is defined but never enforced.** The setting exists
   (`app/services/settings_spec.py:325`, seeded at `settings_seed.py:331`) but no code reads
   it — `create_field` performs no limit check. PR: count check at the top of
   `CustomFieldsService.create_field`, reading the setting via ERP's own `resolve_value`.
2. **`CustomFieldDefinition.validate_value` ignores its own `min_value`/`max_value`.**
   Columns exist and are settable via the form, but validation never compares NUMBER/DECIMAL
   values against them. PR: Decimal comparison in `validate_value`.
   **Extended (Task 9 review, 2026-07-17):** the same method validates NONE of BOOLEAN, DATE,
   DATETIME, URL, PHONE, or CURRENCY — a `BOOLEAN` field accepts the string `"true"`, a `DATE`
   field accepts `"not a date"`, etc.; only TEXT/NUMBER/DECIMAL/EMAIL/SELECT get any check at
   all. dotmac_starter_mt's port (`app/features/custom_fields/models.py`) added real checks for
   BOOLEAN (`isinstance(value, bool)`) and DATE/DATETIME (`fromisoformat`-parseable); URL/PHONE/
   CURRENCY are left as a *documented* passthrough (format varies too much per project — use
   `validation_regex`) rather than silently unchecked. PR against ERP: at minimum add the
   BOOLEAN/DATE/DATETIME checks; consider documenting the URL/PHONE/CURRENCY passthrough
   explicitly rather than leaving it implicit.
   **Extended (Task 7 review, 2026-07-18):** the same `if self.field_options:` guard shape
   also means an options-less SELECT/MULTISELECT definition silently skips membership
   validation entirely — dotmac_starter_mt closed this in `service.py`'s `create_field`/
   `update_field` (`_validate_select_options`: reject at write time unless the effective
   `field_options["options"]` is non-empty), the same "definition self-consistency checked
   up front" pattern as the min/max and regex guards above. PR against ERP: add the same
   create/update-time guard rather than leaving the gap to `validate_value` at read time.
   **RESOLVED (batch 2, 2026-07-18): erp#185 merged** — write-time guard in `create_field`/
   `update_field`; also fixed two latent bugs in ERP's own tests/conftest.py JSONB shim
   discovered by the new tests (SQLite dict-bind failure; `Text.__subclasses__()` detection
   constraint documented in the shim docstring).
3. **`validate_custom_fields` silently ignores unknown field codes.** Typo'd keys pass
   validation and would be persisted (once value storage exists). PR: collect unknown codes
   as errors (or at minimum log).
4. **Custom-field VALUE storage is unbuilt.** Docstrings promise a `custom_fields` JSONB
   column on entities; no entity model has one and nothing reads/writes values. Larger PR —
   propose after our JSONB-on-entity implementation proves out in the starter (port back the
   pattern + `set_values`/`get_values` service methods).

## dotmac_sub

1. **`scripts/deploy.sh` health-gate curl has no timeouts.** A hung health endpoint stalls a
   retry iteration indefinitely. PR: `--connect-timeout/--max-time` via a `HEALTH_CURL_TIMEOUT`
   env knob (as implemented in dotmac_starter_mt `scripts/deploy.sh`).
2. **`scripts/deploy.sh` generic ERR trap repins `.env` but does not `up -d` the previous
   image.** On a mid-`up -d` failure the old container may already be stopped/removed, leaving
   nothing running; only the health-gate failure path does the full restore. PR: add the
   restore to the trap (mirrors the starter's fix).

## dotmac_starter

Frozen per ADR-0002 (no new features; archive after phase 2). Do not PR; gaps found there are
fixed in dotmac_starter_mt only.

## Process

- Add findings here as porting continues (plan 2b/2c likely add entries — e.g. auth-hardening
  port from dotmac_starter may reveal sub/starter auth gaps worth cross-checking).
- After each plan merges: one PR-batch pass per repo, each PR small and independently
  testable in that repo's own CI.
