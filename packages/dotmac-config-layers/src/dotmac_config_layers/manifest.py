"""Stateless optional-module declaration for the layered-composition engine."""

from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="config_layers",
    version="0.1.0a1",
    core=False,
    # Deliberately no short_code, migration prefix, tables or plane declaration.
    # The engine is pure: it holds no rows and serves every security plane.
)

__all__ = ["module"]
