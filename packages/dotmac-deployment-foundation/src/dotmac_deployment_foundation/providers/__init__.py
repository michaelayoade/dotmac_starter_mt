"""Concrete `Effects` implementations for `engine.run.Executor`.

`dotmac_deployment_foundation` itself ships none of these — the facility owns
order, refusal and evidence (`engine/`), never HOW to talk to Docker, Postgres
or Nginx (ADR-0070). A provider is the piece a deployment host supplies, and
this package's absence of one is precisely what made `dotmac-deploy deploy
--execute` always refuse (see `cli.py`'s prior `cmd_deploy`).

`ComposeHostEffects` is the first, and as of this package version the only,
provider: the dedicated-VM Docker Compose profile every one of the four
inventoried products (`dotmac_sub`, `dotmac_erp`, `dotmac_integrator`,
`dotmac_starter_mt`) already runs in production today. A future Kubernetes
provider lives here beside it, implementing the same `Effects` Protocol,
without `engine/run.py` or `engine/plan.py` changing a line.
"""

from __future__ import annotations

from .compose_host import ComposeHostEffects, NginxInstaller
from .exposure_host import ComposeHostExposureEffects, ownership_comment

__all__ = [
    "ComposeHostEffects",
    "ComposeHostExposureEffects",
    "NginxInstaller",
    "ownership_comment",
]
