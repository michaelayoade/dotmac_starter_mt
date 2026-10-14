# Changelog — dotmac-config-layers

## 0.1.0a1 — UNRELEASED

- First source: the generic layered-composition engine ported from Dotmac Sub
  draft PR 3625 (`ab2d08e0e`), including review fixes H1 (widening of an unset
  field compares against its schema default) and L1 to L4, with the spec vectors
  T1 to T25, the M10 and M11 permutation and idempotence checks, provenance
  completeness, equal-rank ties and widening classification.
- Adds the stateless `ModuleManifest` (`code="config_layers"`, no lineage) and
  an explicit public `__all__`.
- Kernel floor `>=0.1.0a88`, the first kernel that registers a stateless
  manifest; no capability beyond the manifest is consumed.
- Source only: not released, not published, not adopted.
