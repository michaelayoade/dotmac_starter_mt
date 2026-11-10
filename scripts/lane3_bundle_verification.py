"""Offline plaintext consistency, not a publication-proof certificate.

Use only outside all runners under offline custody. No cryptography, host
operations, grants or Control consumption occur here.
The closed v1 manifest carries no plan, Control or outcome document. This
bounded join cannot prove exact rules, foreign preservation or compensation.
"""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_deployment_foundation.execution_plan_v3 import FoundationExecutionPlanV3
from lane3_evidence_bundle import EXPOSURE_CALLS_MEMBER, require_plaintext_bundle
from lane3_exposure_evidence import parse_exposure_calls, require_exposure_join
from lane3_exposure_recording import ExposureCallRecord


class BundleVerificationRefused(ValueError):
    """Plaintext evidence cannot establish the bounded consistency claim."""


def require_plaintext_exposure_bundle(
    manifest: bytes,
    members: Mapping[str, bytes],
    *,
    execution_plan: FoundationExecutionPlanV3,
) -> tuple[ExposureCallRecord, ...]:
    """Join the complete plaintext set and recorded crossings to a concrete V3.

    The caller owns the trusted plan input; accepting a dictionary here would
    let private evidence choose its own authority. An empty reconciliation
    set cannot establish even this limited completeness claim.
    """
    try:
        if type(execution_plan) is not FoundationExecutionPlanV3:
            raise BundleVerificationRefused("a concrete V3 plan is required")
        if not execution_plan.exposure_reconciliations:
            raise BundleVerificationRefused("exposure reconciliations are empty")
        require_plaintext_bundle(manifest, members)
        records = parse_exposure_calls(members[EXPOSURE_CALLS_MEMBER])
        require_exposure_join(records, execution_plan.exposure_reconciliations)
        return records
    except (ValueError, TypeError, KeyError, AttributeError):
        # Existing owners' diagnostics may quote private rules or JSON keys.
        raise BundleVerificationRefused(
            "plaintext exposure consistency refused"
        ) from None
