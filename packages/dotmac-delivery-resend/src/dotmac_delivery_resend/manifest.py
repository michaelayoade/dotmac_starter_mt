"""Stateless optional-module declaration for the delivery-resend fence."""

from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="delivery_resend",
    version="0.1.0a1",
    core=False,
    # Deliberately no short_code, migration prefix, tables or plane
    # declaration. The package owns no storage: it fences writes the
    # product performs against its own consequence tables.
)

__all__ = ["module"]
