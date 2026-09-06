"""Drive the exposure reconciliation the way production reaches it.

`ExposureTransaction` used to be constructible in one line, which is exactly
what made it dangerous: applying a product's firewall rules to a live host cost
a test — and an operator — no more ceremony than reading one. The act now lives
on `Executor`, and reaching it requires what reaching any other host mutation
requires: a Control receipt, an `ExecutionGrant` only `authorize()` can issue,
and a `FoundationExecutionPlanV2` whose digest covers the reconciliation.

That ceremony is the point, so this helper does not shortcut it. It builds the
real chain and hands back a real executor. What it does NOT do is take a
deployment lock: `_reconcile_exposure` is called from inside `run()`, which has
already required the caller's `DeploymentLockHeld`, and the lock requirement is
proven in `test_deployment_foundation_lock_capability.py` rather than re-proven
in every file that touches exposure.
"""

from __future__ import annotations

from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.engine.run import DeploymentOutcome, Executor
from dotmac_deployment_foundation.execution_plan_v2 import (
    ExposureReconciliationV1,
    render_execution_plan_v2,
)
from dotmac_deployment_foundation.ingress import FAMILIES, FILTER_CHAIN
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.test_deployment_foundation_execution_binding import (
    AcceptingVerifier,
    RecordingEffects,
    _grant,
    _plan_and_digest,
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
    v1, _ = _plan_and_digest(spec, plan, effects=effects)
    v2 = render_execution_plan_v2(
        v1, exposure_reconciliations=reconciliations(*families)
    )
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=v2.digest()),
        execution_plan=v2,
        sleep=lambda _: None,
        exposure_effects=exposure_effects,  # type: ignore[arg-type]
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
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
