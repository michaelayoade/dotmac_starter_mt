"""Authoritative tenant service-instance lifecycle."""

from dotmac_services.contracts import (
    Conflict,
    CreateService,
    ServicesError,
    ServiceStatus,
    TransitionService,
)
from dotmac_services.manifest import module
from dotmac_services.migrations import versions_dir
from dotmac_services.models import ServiceInstance, ServiceLifecycleEvent
from dotmac_services.service import create_service, transition_service

__version__ = "0.1.0a1"
__all__ = [
    "Conflict",
    "CreateService",
    "ServiceInstance",
    "ServiceLifecycleEvent",
    "ServicesError",
    "ServiceStatus",
    "TransitionService",
    "__version__",
    "create_service",
    "module",
    "transition_service",
    "versions_dir",
]
