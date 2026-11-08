"""D4: render the V3 execution plan Lane 3 executes, from public calls only.

The installed CLI renders its V3 plan in a private helper,
``cli._render_local_execution_plan_v3``. D4 drives the ``Executor`` in-process
(roadmap Q1, answered 2026-10-06), so it cannot go through the CLI, and it must
not import a private name: a private helper can change shape inside a patch
release without any contract noticing. With no descriptor supplied, this module
is the Starter-local mirror of that private render, built only from public
Foundation calls; ``tests/unit/test_lane3_plan_v3.py`` proves the same digest,
canonical bytes and refusals.

Lane 3 may instead supply the same immutable descriptor used to render its V1
base plan. The render then explicitly composes the descriptor-derived exposure
reconciliations into V2, so the resulting V3 digest intentionally differs from
the CLI default and is the digest the signed pair must authorize. Default
parity remains a separate, test-pinned property; a populated Lane 3 plan never
silently claims it.

If default parity ever fails, the mirror is wrong, not the CLI: the CLI's
render is what the candidate ships. The repair is to follow the CLI, or to ask
Foundation for a public entry point before the Gate-2 freeze. A Foundation
change after the freeze spends the candidate.
"""

from __future__ import annotations

from typing import Any

from dotmac_deployment_foundation import host_source
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.execution_plan_v2 import (
    ExposureReconciliationV1,
    render_execution_plan_v2,
)
from dotmac_deployment_foundation.execution_plan_v3 import (
    FoundationExecutionPlanV3,
    render_execution_plan_v3,
)
from dotmac_deployment_foundation.ingress import FAMILIES, FILTER_CHAIN
from dotmac_deployment_foundation.policy import build_firewall_plan


def exposure_reconciliations_for_spec(
    spec: Any,
) -> tuple[ExposureReconciliationV1, ...]:
    """The exposure families the descriptor's own firewall plan authorizes.

    ``build_firewall_plan`` is Foundation's public descriptor derivation; the
    acts carry only the concrete family and its one legal filter chain. The
    Executor later re-derives the individual rules from the same descriptor.
    """
    families = {rule.family for rule in build_firewall_plan(spec)}
    return tuple(
        ExposureReconciliationV1(family=family, chain=FILTER_CHAIN[family])
        for family in FAMILIES
        if family in families
    )


def _require_spec_matches_base_plan(spec: Any, base_plan: Any) -> None:
    try:
        descriptor_digest = str(spec.to_canonical_document().sha256_digest())
    except Exception as exc:
        raise PreconditionFailed(
            "the supplied descriptor cannot identify the base plan descriptor"
        ) from exc
    if descriptor_digest != base_plan.descriptor_digest:
        raise PreconditionFailed(
            "the supplied descriptor does not describe the base plan descriptor"
        )


def render_local_execution_plan_v3(
    base_plan: Any, *, bindings: Any, spec: Any = None
) -> FoundationExecutionPlanV3:
    """Render from fresh installed and Control facts, never from receipt fields.

    Same order of questions as the CLI: bindings, provider, observed subject,
    then the installed artifact. When ``spec`` is supplied, its identity must
    match the base plan descriptor before provider facts are observed, and its
    descriptor-derived exposure families are composed into the plan. With no
    spec, no exposure acts are composed and the render is the CLI mirror.
    ``host_source.read_installed_artifact`` is looked up at call time, as the
    CLI's function-local import does.
    """
    if bindings is None:
        raise PreconditionFailed("no startup-fixed V3 authority is installed")
    provider = bindings.authorization_v3_provider
    if provider is None:
        raise PreconditionFailed("no trusted V2 authorization provider is installed")
    exposure_reconciliations: tuple[ExposureReconciliationV1, ...] = ()
    if spec is not None:
        _require_spec_matches_base_plan(spec, base_plan)
        exposure_reconciliations = exposure_reconciliations_for_spec(spec)
    facts = provider.observe()
    if facts.target_ref != base_plan.target or facts.operation != base_plan.operation:
        raise PreconditionFailed("observed target or operation differs from the plan")
    return render_execution_plan_v3(
        render_execution_plan_v2(
            base_plan, exposure_reconciliations=exposure_reconciliations
        ),
        candidate_wheel_digest=str(
            host_source.read_installed_artifact().artifact_digest
        ),
        target_id=facts.target_id,
        controller_ssh_fingerprint=facts.controller_ssh_fingerprint,
        host_id=facts.host_id,
        host_incarnation=facts.host_incarnation,
        host_enrolment_ref=facts.host_enrolment_ref,
    )
