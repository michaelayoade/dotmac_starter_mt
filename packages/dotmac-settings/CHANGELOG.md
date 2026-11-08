# Changelog — dotmac-settings

## 0.1.0a1 — UNRELEASED

- First source: the operator-settings policy layer generalized from
  Academy v2 `app/services/settings_store.py` at `b26c56c` (blob
  `fdfc1e48`). Academy specifics (key registry, validators, relations,
  lab-execution override expansion, env-secret overlay) become
  injected `KeySpec`/`OverrideSpec`/hooks; the store mechanics —
  strict effective reads, stored-NULL semantics, split-row refusal,
  advisory-lock compare-and-swap, schema-qualified literal SQL with
  bound parameters, and Session autoflush preservation — port
  unchanged.
- Adds the stateless `ModuleManifest` (`code="settings"`, no lineage,
  no tables) and an explicit public `__all__`.
- Kernel floor `>=0.1.0a106` (the Learning 0.1.0a6 floor; the manifest
  is the only kernel import). SQLAlchemy is the only other runtime
  dependency.
- Reviewed deltas from the Academy source: `LabExecutionPolicyConflict`
  generalizes to `WholeObjectConflict`; the CAS advisory-lock key is
  namespaced `dotmac-settings:<domain>:<key>`; `effective()` returns a
  plain dict (Academy keeps its typed projection in-product).
- Source only: not released, not published, not adopted.
