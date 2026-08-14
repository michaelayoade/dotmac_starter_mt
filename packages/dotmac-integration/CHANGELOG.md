# Changelog — dotmac-integration

## 0.1.0a2 — 2026-08-14

Fixes a public function that could never have run, and the gate gap that let it
ship. `pyproject.toml` declares `dotmac_integration.*` under mypy's strict
settings, but the Makefile never passed the package to `mypy` or `bandit` — so
the strictness was declared and unenforced, and `0.1.0a1` published with 42 type
errors and one broken export.

### Fixed

- **`run_effect_once` raised `TypeError` on its first call.** It passed a
  `payload=` keyword `dotmac_kernel.idempotency.execute_once_platform` does not
  accept, and an `operation` taking no arguments where the kernel calls
  `operation(db)`. It is exported from `__init__` and had no caller and no test,
  which is why nothing noticed. Now a faithful adapter: every parameter is the
  kernel's, and the only addition is `mechanism` → `scope`. The old `payload`
  becomes `fingerprint`, the kernel's own column (ADR-0014).
- `assert_connector_conforms` used `try/except/pass` carrying a `# noqa: S110` —
  ruff's code, not bandit's `B110`, so the suppression named the wrong tool.
  Rewritten as a positive assertion with no swallowed exception.

### Changed

- Every JSON-shaped payload is annotated `dict[str, object]` in house style
  (32 sites). `secret_refs` is `dict[str, str]` downstream of validation and
  `dict[str, object]` in the validator itself, which exists to reject
  non-strings.
- `resolve_binding`'s rows are typed once, so its three returns stop escaping as
  `Any` from a declared `CapabilityBinding` return type.
- `_is_reference` narrows with `is not None` rather than `bool(match)`. The old
  form was safe at runtime — `and` short-circuits — but unprovable to a checker.

## 0.1.0a1 — 2026-08-14

The connector control plane, as a Starter module (ADR-0024).
