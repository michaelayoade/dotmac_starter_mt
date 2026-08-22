"""Technical service-catalogue owner."""

from dotmac_service_catalog.contracts import (
    CatalogError,
    CharacteristicKind,
    Conflict,
    CreateCharacteristic,
    CreateEligibilityInput,
    CreatePlanFamily,
    CreateServiceSpecification,
    NotFound,
)
from dotmac_service_catalog.manifest import module
from dotmac_service_catalog.migrations import versions_dir
from dotmac_service_catalog.models import (
    CharacteristicDefinition,
    EligibilityInputDefinition,
    PlanFamily,
    ServiceSpecification,
)
from dotmac_service_catalog.service import (
    add_characteristic,
    add_eligibility_input,
    create_plan_family,
    create_specification,
    set_specification_active,
)

__version__ = "0.1.0a1"

__all__ = [
    "CatalogError",
    "CharacteristicDefinition",
    "CharacteristicKind",
    "Conflict",
    "CreateCharacteristic",
    "CreateEligibilityInput",
    "CreatePlanFamily",
    "CreateServiceSpecification",
    "EligibilityInputDefinition",
    "NotFound",
    "PlanFamily",
    "ServiceSpecification",
    "__version__",
    "add_characteristic",
    "add_eligibility_input",
    "create_plan_family",
    "create_specification",
    "module",
    "set_specification_active",
    "versions_dir",
]
