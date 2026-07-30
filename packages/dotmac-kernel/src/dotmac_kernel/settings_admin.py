"""Narrow settings ADMIN / registry surface (kernel-boundary Task 2).

The settings write-path and registry-introspection functions are DEFINED in
`dotmac_kernel.settings_resolver` — they have to live in the kernel so the
`settings` feature can reach them without importing another feature (features
never import each other). But they are NOT general kernel API: only the
`settings` feature package consumes them. This module re-exports them under a
deliberately narrow, separately-documented surface, kept distinct from the
READ/DECLARE contract that every feature may use
(`settings_resolver.{resolve_value, register_specs, SettingSpec}` +
`settings_models.{SettingDomain, SettingValueType}`).

A consumer therefore imports:
- `dotmac_kernel.settings_resolver` / `settings_models` for reading a value or
  declaring a spec (the common case); and
- `dotmac_kernel.settings_admin` ONLY when implementing a settings-admin
  surface (editing/seeding rows, introspecting the registry).

See `packages/dotmac-kernel/COMPATIBILITY.md`.
"""

from __future__ import annotations

from dotmac_kernel.settings_resolver import (
    all_specs,
    ensure_by_key,
    get_spec,
    resolve_with_source,
    upsert_by_key,
    validate_spec_value,
)

__all__ = [
    "all_specs",
    "ensure_by_key",
    "get_spec",
    "resolve_with_source",
    "upsert_by_key",
    "validate_spec_value",
]
