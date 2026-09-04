"""The stage-two transition: Foundation owns it, and proves it by CALLING it.

## The defect this closes

`build_platform_cp_effects` could not be reached through the published binding
contract at all. Foundation's contract is `build_effects(spec, deploy_dir)` — two
positionals — and the factory required `target` and `incumbent_roles` as
keyword-only, so every real invocation raised `TypeError` before any deployment
logic ran. There was **no production caller anywhere**: only tests, each calling
the factory the way the factory wanted to be called, never the way the facility
calls it.

So this file's discipline is the point. The double below is a faithful
reproduction of the PUBLISHED stage two — keyword-only `target` and
`incumbent_roles`, returning `None`, idempotent for identical facts, refusing
different ones — read from Platform `main` at `bb77d3121c`. A double shaped the
way the caller wishes it were would reproduce exactly the defect: a seam proven
against itself.

## The circularity, stated by both sides in the same terms

Effects are built from `(spec, deploy_dir)`; the plan is rendered by reading
`observe_roles()` on those effects; the plan carries the target and the frozen
prestate the provider needs. Neither can be known at construction. Two stages is
what works against the facility as published.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

import pytest
from dotmac_deployment_foundation.canonical_plan import EXECUTION_PLAN_WRONG_TYPE
from dotmac_deployment_foundation.engine.run import (
    STAGE_TWO_ABSENT,
    STAGE_TWO_REFUSED,
    bind_authorized_effects,
)
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.execution_plan import (
    FoundationExecutionPlanV1,
    HostPrestateV1,
)
from dotmac_deployment_foundation.execution_plan_v2 import render_execution_plan_v2
from dotmac_deployment_foundation.version import VERSION

PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "dotmac-deployment-foundation"
    / "src"
    / "dotmac_deployment_foundation"
)

C = "sha256:" + "c" * 64
D = "sha256:" + "d" * 64


class _Unbound:
    """Its own TYPE, not `None` and not `()`.

    Reproduced because the distinction is load-bearing: an empty incumbent set is
    already the positive claim "first deployment", so collapsing unbound into it
    would label the previous release's own bytes a first deployment — visible
    only during a restore.
    """


class _PublishedStageTwo:
    """A faithful reproduction of the published provider's stage-two seam."""

    def __init__(self) -> None:
        self.target: object = _Unbound()
        self.incumbent: object = _Unbound()
        self.binds = 0

    def bind_authorized_execution(
        self, *, target: str, incumbent_roles: Sequence[tuple[str, str]]
    ) -> None:
        self.binds += 1
        checked = tuple((str(r), str(d)) for r, d in incumbent_roles)
        codes = [r for r, _ in checked]
        if codes != sorted(codes):
            raise ValueError("incumbent roles are not sorted")
        if len(set(codes)) != len(codes):
            raise ValueError("a role is named twice")
        if not str(target):
            raise ValueError("target is empty")
        already = not isinstance(self.target, _Unbound) or not isinstance(
            self.incumbent, _Unbound
        )
        if already and (self.target, self.incumbent) != (target, checked):
            raise ValueError(
                "these effects are already bound to a different authorized " "execution"
            )
        self.target, self.incumbent = target, checked

    def prune_images(self, *, retain: int) -> None: ...


def _plan(**over) -> FoundationExecutionPlanV1:
    kwargs = {
        "product": "dotmac_starter_mt",
        "target": "prod-lagos-01",
        "operation": "deploy",
        "foundation_version": VERSION,
        "image_reference": "ghcr.io/dotmac/starter:1.2.3",
        "image_digest": D,
        "source_revision": "0" * 40,
        "manifest_digest": C,
        "descriptor_digest": "sha256:" + "a" * 64,
        "strategy": "warm_candidate",
        "environment_inventory": ("DATABASE_URL",),
        "host_prestate": HostPrestateV1(roles=(("app", C), ("worker", D))),
        "application_profile_digest": "",
        "steps": (("command", "app", ("echo", "hi"), 60, 0),),
    }
    kwargs.update(over)
    return FoundationExecutionPlanV1(**kwargs)


# ── it is CALLED, not matched ──────────────────────────────────────────────


def test_the_caller_reaches_the_published_seam() -> None:
    """The assertion the original defect would have failed: the facility's own
    call shape reaches the provider's own signature."""
    effects, plan = _PublishedStageTwo(), _plan()
    bind_authorized_effects(effects, plan)
    assert effects.target == "prod-lagos-01"
    assert effects.incumbent == (("app", C), ("worker", D))
    assert effects.binds == 1


def test_it_works_for_a_v2_plan_too() -> None:
    effects = _PublishedStageTwo()
    bind_authorized_effects(effects, render_execution_plan_v2(_plan()))
    assert effects.target == "prod-lagos-01"


def test_the_projection_is_the_IDENTITY() -> None:
    """`HostPrestateV1.roles` is already sorted `(role, digest)` pairs and the
    provider applies the same rules, refusing an unsorted sequence rather than
    repairing it. So the frozen tuple passes through unchanged — a sort or a
    rebuild here would be a second opinion about a fact the plan froze."""
    plan = _plan()
    effects = _PublishedStageTwo()
    bind_authorized_effects(effects, plan)
    assert effects.incumbent == plan.host_prestate.roles


# ── unbound is not empty ───────────────────────────────────────────────────


def test_an_empty_prestate_is_a_POSITIVE_claim_not_unbound() -> None:
    """ "No role containers — a first deployment" is a claim the plan makes.
    Collapsing it into unbound would label the previous release's own bytes a
    first deployment, and that is visible only during a restore."""
    effects = _PublishedStageTwo()
    assert isinstance(effects.incumbent, _Unbound)
    bind_authorized_effects(effects, _plan(host_prestate=HostPrestateV1.first_deploy()))
    assert effects.incumbent == ()
    assert not isinstance(effects.incumbent, _Unbound)


def test_None_can_never_be_passed() -> None:
    """`host_prestate` is required with no default, so the caller has no path to
    `None` — structural rather than checked."""
    import dataclasses

    field = next(
        f
        for f in dataclasses.fields(FoundationExecutionPlanV1)
        if f.name == "host_prestate"
    )
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING


# ── the three prohibitions ─────────────────────────────────────────────────


def test_the_stage_two_caller_never_reobserves() -> None:
    """Derived from the AST, not from reading the body.

    "No re-observation after authorization" is not a rule to remember — it is an
    absent call. A prestate read here would be a second authority over a fact the
    plan already fixed, arriving after the authorization that froze it.
    """
    tree = ast.parse((PACKAGE / "engine" / "run.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "bind_authorized_effects"
    )
    called = {
        getattr(node.func, "attr", getattr(node.func, "id", ""))
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }
    assert "observe_roles" not in called, called


def test_the_caller_takes_no_attempt_number() -> None:
    """Nothing here can assert what the envelope should carry; the coordinate
    reaches the report from the receipt, through the grant."""
    import inspect

    parameters = set(inspect.signature(bind_authorized_effects).parameters)
    assert parameters == {"effects", "plan"}


def test_a_raw_mapping_is_not_a_plan() -> None:
    """A mapping with the right keys is not the document that was frozen."""
    effects = _PublishedStageTwo()
    with pytest.raises(PreconditionFailed) as exc:
        bind_authorized_effects(
            effects,
            {"target": "prod-lagos-01", "host_prestate": {"roles": []}},  # type: ignore[arg-type]
        )
    assert exc.value.code == EXECUTION_PLAN_WRONG_TYPE
    assert effects.binds == 0


# ── refusals ───────────────────────────────────────────────────────────────


def test_a_provider_without_the_seam_is_refused_by_name() -> None:
    class NoStageTwo:
        def prune_images(self, *, retain: int) -> None: ...

    with pytest.raises(PreconditionFailed) as exc:
        bind_authorized_effects(NoStageTwo(), _plan())  # type: ignore[arg-type]
    assert exc.value.code == STAGE_TWO_ABSENT


def test_rebinding_IDENTICAL_facts_is_accepted() -> None:
    """A retry within one authorized run is not a contradiction."""
    effects, plan = _PublishedStageTwo(), _plan()
    bind_authorized_effects(effects, plan)
    bind_authorized_effects(effects, plan)
    assert effects.binds == 2


def test_rebinding_DIFFERENT_facts_is_refused() -> None:
    """The drift the frozen prestate exists to catch, arriving from inside the
    process rather than from the host."""
    effects = _PublishedStageTwo()
    bind_authorized_effects(effects, _plan())
    with pytest.raises(PreconditionFailed) as exc:
        bind_authorized_effects(effects, _plan(target="prod-abuja-02"))
    assert exc.value.code == STAGE_TWO_REFUSED


def test_a_provider_ValueError_is_surfaced_as_a_TYPED_refusal() -> None:
    """The provider raises `ValueError`; a caller deciding what to do next needs
    a code rather than an exception class shared with every other mistake."""

    class Rejecting(_PublishedStageTwo):
        def bind_authorized_execution(self, *, target, incumbent_roles) -> None:
            raise ValueError("the host-identity file names another machine")

    with pytest.raises(PreconditionFailed) as exc:
        bind_authorized_effects(Rejecting(), _plan())
    assert exc.value.code == STAGE_TWO_REFUSED


def test_the_providers_empty_target_guard_is_UNREACHABLE_through_this_caller() -> None:
    """Found by driving it rather than by reading, and worth recording.

    The provider refuses an empty `target`. That refusal cannot fire through this
    caller: `FoundationExecutionPlanV1` already refuses to CONSTRUCT with an
    empty target — *"a plan with no target authorizes every host"* — so no plan
    carrying one can exist to be passed on.

    That is not a defect on either side. The provider's guard is defensive
    against callers other than this one, and it should stay. What is worth
    knowing is that its coverage does not come from here, so a test asserting it
    through this seam would be asserting something the type makes unreachable.
    """
    from dotmac_deployment_foundation.errors import SpecError

    with pytest.raises(SpecError):
        _plan(target=" ")
