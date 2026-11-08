"""Stateless optional-module declaration for the settings policy layer."""

from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="settings",
    version="0.1.0a1",
    core=False,
    # Deliberately no short_code, migration prefix, tables or plane
    # declaration. Storage is the kernel's domain_settings table; this
    # package is the validation/CAS policy layer above it.
)

__all__ = ["module"]
