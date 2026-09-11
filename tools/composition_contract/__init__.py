"""The central ``dimensional-composition`` contract.

``composition_schema.py`` holds the v2 derivation primitives relocated from a
test collection. ``observations.py`` is the breaking v3 acquisition boundary:
products do not import or vendor it; Starter independently re-derives fixed
observations from immutable product Git objects.
"""

from tools.composition_contract.observations import (
    CONTRACT_REPOSITORY,
    OBSERVATION_SCHEMA_VERSION,
    ObservationAcquisitionError,
    ObservationClaim,
    ObservationRefusal,
    ObservationSpec,
    PoetryInstallCommandLocator,
    ProductObservationSpec,
    PythonAssignmentKeywordLocator,
    VerifiedObservation,
    VerifiedObservationEnvelope,
    WholeFileLocator,
    extract_observation,
    read_regular_git_blob,
    verify_observation_envelope,
)

__all__ = [
    "CONTRACT_REPOSITORY",
    "OBSERVATION_SCHEMA_VERSION",
    "ObservationAcquisitionError",
    "ObservationClaim",
    "ObservationRefusal",
    "ObservationSpec",
    "PoetryInstallCommandLocator",
    "ProductObservationSpec",
    "PythonAssignmentKeywordLocator",
    "VerifiedObservation",
    "VerifiedObservationEnvelope",
    "WholeFileLocator",
    "extract_observation",
    "read_regular_git_blob",
    "verify_observation_envelope",
]
