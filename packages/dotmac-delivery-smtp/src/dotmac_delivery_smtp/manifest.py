"""Stateless optional-module declaration for the SMTP delivery adapter."""

from dotmac_kernel.modules import ModuleManifest

from dotmac_delivery_smtp import settings as _settings

module = ModuleManifest(
    code="delivery_smtp",
    version="0.1.0a1",
    core=False,
    # Settings only: the five connection specs are declared under one domain.
    # No tables, lineage, routes or plane declaration; the product binds its
    # own password custody through the kernel SecretSource.
    setting_domains=(_settings.DELIVERY_SMTP_DOMAIN.value,),
)

__all__ = ["module"]
