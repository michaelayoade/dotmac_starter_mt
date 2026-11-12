"""Offline plaintext consistency, not a publication-proof certificate.

Use only outside all runners under offline custody. No cryptography, host
operations, grants or Control consumption occur here.
The closed v1 manifest carries no plan, Control or outcome document. This
bounded join checks recorded rule arguments against a trusted descriptor;
it cannot prove applied host rules, foreign preservation or compensation.
"""

from __future__ import annotations

from collections.abc import Mapping

from dotmac_deployment_foundation.execution_plan_v3 import FoundationExecutionPlanV3
from dotmac_deployment_foundation.policy import build_firewall_plan
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from lane3_evidence_bundle import EXPOSURE_CALLS_MEMBER, require_plaintext_bundle
from lane3_exposure_evidence import parse_exposure_calls, require_exposure_join
from lane3_exposure_recording import ExposureCallRecord
from lane3_plan_v3 import exposure_reconciliations_for_spec


class BundleVerificationRefused(ValueError):
    """Plaintext evidence cannot establish the bounded consistency claim."""


def require_plaintext_exposure_bundle(
    manifest: bytes,
    members: Mapping[str, bytes],
    *,
    execution_plan: FoundationExecutionPlanV3,
    descriptor: ProductDeploymentSpec,
) -> tuple[ExposureCallRecord, ...]:
    """Join plaintext crossings to a concrete V3 and trusted exact descriptor.

    The caller owns both trusted inputs; accepting a dictionary here would
    let private evidence choose its own authority. An empty reconciliation
    set cannot establish even this limited completeness claim.
    """
    try:
        if type(execution_plan) is not FoundationExecutionPlanV3:
            raise BundleVerificationRefused("a concrete V3 plan is required")
        if type(descriptor) is not ProductDeploymentSpec:
            raise BundleVerificationRefused("a concrete descriptor is required")
        if (
            str(descriptor.to_canonical_document().sha256_digest())
            != execution_plan.descriptor_digest
            or descriptor.product != execution_plan.product
        ):
            raise BundleVerificationRefused("descriptor differs from the plan")
        reconciliations = exposure_reconciliations_for_spec(descriptor)
        if not reconciliations or (
            execution_plan.exposure_reconciliations != reconciliations
        ):
            raise BundleVerificationRefused("exposure reconciliations differ")
        # Match Executor._reconcile_exposure's public derivation and ordered
        # per-family projection. Do not recreate the firewall algorithm here.
        rules = build_firewall_plan(descriptor)
        expected = {
            (item.family, item.chain): tuple(
                rule for rule in rules if rule.family == item.family
            )
            for item in reconciliations
        }
        require_plaintext_bundle(manifest, members)
        records = parse_exposure_calls(members[EXPOSURE_CALLS_MEMBER])
        require_exposure_join(records, execution_plan.exposure_reconciliations)
        for record in records:
            # Compose belongs to the Executor's deployment effects, never its
            # exposure adapter. No trusted compose command is bound here.
            if record.method == "apply_compose":
                raise BundleVerificationRefused("unexpected compose crossing")
            if record.method == "replace_rules" and (
                (record.family, record.chain) not in expected
                or record.rules != expected[(record.family, record.chain)]
            ):
                # Failed writes crossed the same boundary and need the same
                # argument check; success elsewhere cannot excuse them.
                raise BundleVerificationRefused("rule arguments differ")
        return records
    except Exception:
        # Existing owners' diagnostics may quote private rules or JSON keys.
        raise BundleVerificationRefused(
            "plaintext exposure consistency refused"
        ) from None
