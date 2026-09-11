"""The central ``dimensional-composition`` contract.

``composition_schema.py`` holds the v2 derivation primitives relocated from a
test collection. ``observations.py`` is the breaking v3 acquisition boundary:
products do not import or vendor it; Starter independently re-derives fixed
observations from immutable product Git objects.
"""

from tools.composition_contract.observations import (
    CANONICAL_RECORD_PATH,
    CONTRACT_REPOSITORY,
    OBSERVATION_SCHEMA_VERSION,
    ObservationAcquisitionError,
    ObservationClaim,
    ObservationRefusal,
    ObservationSpec,
    PoetryInstallCommandLocator,
    ProductObservationSpec,
    PythonAssignmentKeywordLocator,
    PythonStringAssignmentLocator,
    VerifiedObservation,
    VerifiedObservationEnvelope,
    WholeFileLocator,
    build_product_checkout_document,
    checkout_head_revision,
    extract_observation,
    load_and_verify_product_checkout_envelope,
    read_checkout_json_document,
    read_regular_git_blob,
    verify_observation_envelope,
    verify_product_checkout_envelope,
)
from tools.composition_contract.specs import (
    ACADEMY_OBSERVATION_SPEC,
    ERP_OBSERVATION_SPEC,
    PRODUCT_OBSERVATION_SPECS,
    SUB_OBSERVATION_SPEC,
)

__all__ = [
    "ACADEMY_OBSERVATION_SPEC",
    "CANONICAL_RECORD_PATH",
    "CONTRACT_REPOSITORY",
    "ERP_OBSERVATION_SPEC",
    "OBSERVATION_SCHEMA_VERSION",
    "ObservationAcquisitionError",
    "ObservationClaim",
    "ObservationRefusal",
    "ObservationSpec",
    "PoetryInstallCommandLocator",
    "PRODUCT_OBSERVATION_SPECS",
    "ProductObservationSpec",
    "PythonAssignmentKeywordLocator",
    "PythonStringAssignmentLocator",
    "SUB_OBSERVATION_SPEC",
    "VerifiedObservation",
    "VerifiedObservationEnvelope",
    "WholeFileLocator",
    "build_product_checkout_document",
    "checkout_head_revision",
    "extract_observation",
    "load_and_verify_product_checkout_envelope",
    "read_checkout_json_document",
    "read_regular_git_blob",
    "verify_observation_envelope",
    "verify_product_checkout_envelope",
]
