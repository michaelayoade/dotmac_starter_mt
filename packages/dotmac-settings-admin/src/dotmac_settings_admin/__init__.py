"""DotMac Settings Admin: generic, stateless administration of declared settings.

Every setting a module declares (`SettingSpec` in a declared `setting_domains`
domain) is editable here with no per-module code: edit with validation, actor and
reason on every write, an audit event, history, restore (a new audited write),
JSON export/import with preview, branding, an organisation profile, a read-only
view of declared feature flags, and an optional maker-checker (default OFF).

## Public surface

- `module`: the `ModuleManifest` an assembly puts in `ProductAssemblySpec.modules`.
- `configure_audit_writer(write_audit_event)`: the assembly binds the kernel audit
  writer once (a stateless module cannot declare the audit-storage prerequisite).
- `template_dir()`: the packaged Jinja directory (declared by the manifest).
- `service` / `transfer`: the write path, for a product that must change a
  setting from its own code with the same actor/reason/audit guarantees.

Stateless: no table, no migration lineage, no namespace allocation.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dotmac_settings_admin import service, transfer
    from dotmac_settings_admin.manifest import module
    from dotmac_settings_admin.web import template_dir

_LAZY = {
    "module": ("dotmac_settings_admin.manifest", "module"),
    "configure_audit_writer": (
        "dotmac_settings_admin.audit_seam",
        "configure_audit_writer",
    ),
    "template_dir": ("dotmac_settings_admin.web", "template_dir"),
}
__all__ = ["configure_audit_writer", "module", "service", "template_dir", "transfer"]


def __getattr__(name: str) -> object:
    if name in _LAZY:
        mod, attr = _LAZY[name]
        return getattr(import_module(mod), attr)
    if name in {"service", "transfer"}:
        return import_module(f"dotmac_settings_admin.{name}")
    raise AttributeError(name)
