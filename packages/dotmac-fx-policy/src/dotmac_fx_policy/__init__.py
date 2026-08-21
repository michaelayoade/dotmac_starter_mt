"""Effective FX observation, selection policy, and determination evidence."""

from dotmac_fx_policy.contracts import (
    Conflict,
    CreateRateType,
    DetermineRate,
    FXPolicyError,
    RecordRateObservation,
    RegisterRateSource,
    SelectedRate,
    SetSelectionPolicy,
)
from dotmac_fx_policy.manifest import module
from dotmac_fx_policy.migrations import versions_dir
from dotmac_fx_policy.models import (
    FXRateDetermination,
    FXRateObservation,
    FXRateSource,
    FXRateType,
    FXSelectionPolicy,
)
from dotmac_fx_policy.service import (
    create_rate_type,
    determine_rate,
    record_rate_observation,
    register_rate_source,
    set_selection_policy,
)

__version__ = "0.1.0a1"
__all__ = [
    "Conflict",
    "CreateRateType",
    "DetermineRate",
    "FXPolicyError",
    "FXRateDetermination",
    "FXRateObservation",
    "FXRateSource",
    "FXRateType",
    "FXSelectionPolicy",
    "RecordRateObservation",
    "RegisterRateSource",
    "SelectedRate",
    "SetSelectionPolicy",
    "__version__",
    "create_rate_type",
    "determine_rate",
    "module",
    "record_rate_observation",
    "register_rate_source",
    "set_selection_policy",
    "versions_dir",
]
