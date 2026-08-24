"""Technical service-catalogue owner."""

from dotmac_service_catalog.contracts import (
    CatalogError,
    CharacteristicKind,
    CharacteristicValue,
    CharacteristicValueInput,
    Conflict,
    CreateCharacteristic,
    CreateEligibilityInput,
    CreatePlanFamily,
    CreateServiceSpecification,
    EffectiveServiceSpecification,
    NotFound,
    PublishPlanFamilyVersion,
    PublishServiceSpecificationVersion,
)
from dotmac_service_catalog.manifest import module
from dotmac_service_catalog.migrations import versions_dir
from dotmac_service_catalog.models import (
    CharacteristicDefinition,
    EligibilityInputDefinition,
    PlanFamily,
    PlanFamilyVersion,
    ServiceSpecification,
    ServiceSpecificationCharacteristic,
    ServiceSpecificationVersion,
)
from dotmac_service_catalog.service import (
    add_characteristic,
    add_eligibility_input,
    create_plan_family,
    create_specification,
    effective_service_specification,
    publish_plan_family_version,
    publish_service_specification_version,
)

__version__ = "0.1.0a1"

__all__ = [
    "CatalogError",
    "CharacteristicDefinition",
    "CharacteristicKind",
    "CharacteristicValue",
    "CharacteristicValueInput",
    "Conflict",
    "CreateCharacteristic",
    "CreateEligibilityInput",
    "CreatePlanFamily",
    "CreateServiceSpecification",
    "EffectiveServiceSpecification",
    "EligibilityInputDefinition",
    "NotFound",
    "PlanFamily",
    "PlanFamilyVersion",
    "PublishPlanFamilyVersion",
    "PublishServiceSpecificationVersion",
    "ServiceSpecification",
    "ServiceSpecificationCharacteristic",
    "ServiceSpecificationVersion",
    "__version__",
    "add_characteristic",
    "add_eligibility_input",
    "create_plan_family",
    "create_specification",
    "effective_service_specification",
    "module",
    "publish_plan_family_version",
    "publish_service_specification_version",
    "versions_dir",
]
