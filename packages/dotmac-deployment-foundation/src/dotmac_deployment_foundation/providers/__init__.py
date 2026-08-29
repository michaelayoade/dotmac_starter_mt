"""Concrete `Effects` implementations for `engine.run.Executor`.

Foundation owns these provider implementations as the host mechanism behind its
typed executor. Direct CLI mutation remains disabled: only the authenticated
controller invokes a provider after verifying the external execution and trust
evidence (ADR-0070).

`ComposeHostEffects` is the first, and as of this package version the only,
provider: the dedicated-VM Docker Compose profile every one of the four
inventoried products (`dotmac_sub`, `dotmac_erp`, `dotmac_integrator`,
`dotmac_starter_mt`) already runs in production today. A future Kubernetes
provider lives here beside it, implementing the same `Effects` Protocol,
without `engine/run.py` or `engine/plan.py` changing a line.
"""

from __future__ import annotations

from .compose_host import ComposeHostEffects, NginxInstaller

__all__ = ["ComposeHostEffects", "NginxInstaller"]
