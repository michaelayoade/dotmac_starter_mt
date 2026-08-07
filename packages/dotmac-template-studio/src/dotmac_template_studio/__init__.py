"""DotMac Template Studio — tenant-authored notification and document templates.

The FIRST stateful module (ADR-0006: "dotmac-template-studio a MODULE — the first
stateful one, not a kernel facility"). It owns the `mod_tstudio` schema and its
own `ts` migration lineage, and it is installed by an assembly rather than
imported by the kernel.

## The public surface

Everything below is what a consuming assembly may use. Anything else is internal
and may change without a contract bump.

- `module` — the `ModuleManifest` an assembly puts in `ProductAssemblySpec.modules`.
- `template_dir()` — the packaged Jinja directory for
  `ProductAssemblySpec.packaged_template_dirs`.
- `migrations_dir()` — the packaged Alembic version location for the assembly's
  `version_locations`.
- `service` — the module's own logic, for a product that wants to render a
  template from its own code rather than over HTTP. This is the SUPPORTED way to
  reach Template Studio in-process; the models are not.

## What a consumer must NOT do

Import `dotmac_template_studio.models` and query it directly. The tables are an
implementation detail behind `service`, and reaching past it re-creates the
parallel-writer problem the source-of-truth standard exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from dotmac_template_studio import service
from dotmac_template_studio.manifest import module
from dotmac_template_studio.web import template_dir

_PKG_DIR = Path(__file__).resolve().parent

__version__ = "0.1.0a1"


def migrations_dir() -> Path:
    """This module's Alembic version location.

    An assembly appends this to `version_locations` — one of exactly two edits
    that install a module's migrations, the other being its ledger row (which
    ships in the kernel). Resolved by package path so it works installed.
    """
    return _PKG_DIR / "migrations" / "versions"


__all__ = [
    "__version__",
    "migrations_dir",
    "module",
    "service",
    "template_dir",
]
