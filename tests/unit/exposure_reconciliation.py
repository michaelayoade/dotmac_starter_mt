"""Drive the exposure reconciliation the way production reaches it.

`ExposureTransaction` used to be constructible in one line, which is exactly
what made it dangerous: applying a product's firewall rules to a live host cost
a test — and an operator — no more ceremony than reading one. The act now lives
on `Executor`. Production reaches it only through an `ExecutionGrant` issued by
`authorize_v3()`, a `FoundationExecutionPlanV3`, and committed consumption of
the exact Control V2 dispatch.

This focused helper does not claim to reproduce that production chain: it calls
the private reconciliation method directly to test the exposure algorithm with
historical V2 act data. V3 authorization, dispatch consumption, host admission,
lock ordering, and the real `run()` path are proved in their dedicated tests.
"""

from __future__ import annotations

from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.engine.run import DeploymentOutcome, Executor
from dotmac_deployment_foundation.execution_plan import (
    HostPrestateV1,
    render_execution_plan,
)
from dotmac_deployment_foundation.execution_plan_v2 import (
    ExposureReconciliationV1,
    render_execution_plan_v2,
)
from dotmac_deployment_foundation.ingress import FAMILIES, FILTER_CHAIN
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.foundation_v3_support import v3_plan
from tests.unit.host_source_stance import valid_host_source_kwargs
from tests.unit.test_deployment_foundation_execution_binding import (
    TARGET,
    AcceptingVerifier,
    RecordingEffects,
    _grant,
    evidence_policy,
)


def reconciliations(
    *families: str,
) -> tuple[ExposureReconciliationV1, ...]:
    """The authorized acts, defaulting to both concrete address families."""
    return tuple(
        ExposureReconciliationV1(family=family, chain=FILTER_CHAIN[family])
        for family in (families or FAMILIES)
    )


def authorized_executor(
    spec: ProductDeploymentSpec,
    exposure_effects: object,
    *,
    families: tuple[str, ...] = FAMILIES,
) -> tuple[Executor, DeploymentOutcome]:
    """A real `Executor`, authorized for exposure, plus a fresh outcome."""
    plan = build_plan(spec)
    effects = RecordingEffects()
    # The same V1 base `_plan_and_digest` renders, carrying the exposure acts
    # in its V2 layer, wrapped as the V3 plan the grant is issued for — so the
    # receipt's execution plan digest is the digest of exactly this plan.
    base = render_execution_plan(
        spec,
        plan,
        target=TARGET,
        operation="deploy",
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        prestate=HostPrestateV1.from_observations(effects.observe_roles()),
        application_profile_digest="",
    )
    v2 = render_execution_plan_v2(
        base, exposure_reconciliations=reconciliations(*families)
    )
    v3 = v3_plan(v2)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan=v3),
        execution_plan=v3,
        sleep=lambda _: None,
        exposure_effects=exposure_effects,  # type: ignore[arg-type]
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
        **valid_host_source_kwargs(),
    )
    return executor, DeploymentOutcome(plan=plan)


def reconcile(
    spec: ProductDeploymentSpec,
    exposure_effects: object,
    *,
    families: tuple[str, ...] = FAMILIES,
) -> DeploymentOutcome:
    """Perform the authorized reconciliation. Raises exactly as production does."""
    executor, outcome = authorized_executor(spec, exposure_effects, families=families)
    executor._reconcile_exposure(outcome)
    return outcome
