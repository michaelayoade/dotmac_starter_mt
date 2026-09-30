"""The bootstrap invocation: the seam, every implementation, and the standings.

## What the widening had to be careful about

`Effects` is implemented by the in-package provider and by the test doubles.
(The release facility's string-embedded probe wheel was retired with V1
authority.) `test_every_effects_implementation_conforms` derives the implementer
list rather than listing it, so a widening that misses one fails there.

## Why the step is not in `plan.steps`

A step emitted by `build_plan` lands in `FoundationExecutionPlanV1.steps`, which
is inside the V1 digest. Emitting one unconditionally would move every existing
V1 digest — including ones Control has already frozen — for deployments that
perform no bootstrap at all. The act is driven from the authorized V2 plan
instead, and that is asserted here rather than left to a comment.

## The standings are the contract, not a boolean

A provider that answers "true" has not said which history happened: an install
and a reconciliation after a crash have the same end state. The seam refuses any
standing other than `installed` or `reconciled_after_commit`, so the ambiguity
cannot be persisted.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from dotmac_deployment_foundation.canonical_plan import EXECUTION_PLAN_WRONG_TYPE
from dotmac_deployment_foundation.deployment_evidence import RunStanding, StepStanding
from dotmac_deployment_foundation.engine.plan import StepKind, build_plan
from dotmac_deployment_foundation.engine.run import Effects, Executor
from dotmac_deployment_foundation.errors import PreconditionFailed, StepFailed
from dotmac_deployment_foundation.execution_plan_v2 import (
    PostgresPrincipalCredentialBootstrapV1,
    render_execution_plan_v2,
)

from tests.unit.deployment_lock_harness import held_lock
from tests.unit.foundation_v3_support import v3_plan
from tests.unit.host_source_stance import valid_host_source_kwargs
from tests.unit.test_deployment_foundation_execution_binding import (
    AcceptingVerifier,
    _fixture,
    _grant,
    _plan_and_digest,
    evidence_policy,
)
from tests.unit.working_tree import python_files as _python_files

REPO = Path(__file__).resolve().parents[2]


def _bootstrap(principal: str = "platform_outbox_dispatcher"):
    return PostgresPrincipalCredentialBootstrapV1(
        service="platform_cp",
        principal=principal,
        secret_path="bao://secret/dotmac/platform/outbox",
        secret_field="password",
        expected_version=1,
    )


def _run_v2(bootstraps=(), standing=StepStanding.INSTALLED, raises=None):
    """A V3 executor carrying V2's approved bootstrap acts."""
    spec, plan, effects = _fixture()
    baseline, _ = _plan_and_digest(spec, plan, effects=effects)
    v2 = render_execution_plan_v2(baseline.base, principal_bootstraps=bootstraps)
    execution_plan = v3_plan(v2)
    effects.bootstrap_standing = standing
    if raises is not None:

        def refusing(bootstrap):  # type: ignore[no-untyped-def]
            raise raises

        effects.bootstrap_principal_credential = refusing  # type: ignore[method-assign]
    grant = _grant(spec, execution_plan=execution_plan)
    outcome = Executor(
        spec,
        effects,
        grant,
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
        **valid_host_source_kwargs(),
    ).run(plan, lock=held_lock(spec.product))
    # SELF-CHECK, because this helper has been wrong once already in this
    # package's history and the failure mode is silent: a run that dies BEFORE
    # the bootstrap (the first version omitted the evidence verifier, so it
    # refused at `verify_release_evidence`) leaves every assertion about
    # bootstrap behaviour passing over a run that never reached it. The
    # assertions about ABSENCE are the ones that would never have complained.
    if bootstraps:
        assert (
            effects.bootstrapped or outcome.failed_step is not None
        ), "the fixture never reached the bootstrap; it proves nothing"
    return effects, outcome


# ── every implementation, derived rather than listed ────────────────────────


def _effects_implementations() -> set[str]:
    """Any class defining `prune_images` implements this protocol. AST, so a
    mention in a docstring is not an implementation."""
    found: set[str] = set()
    for path in _python_files():
        if "engine/run.py" in path.as_posix():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                isinstance(b, ast.FunctionDef) and b.name == "prune_images"
                for b in node.body
            ):
                found.add(f"{path.name}:{node.name}")
    return found


def test_every_effects_implementation_conforms_to_the_WIDENED_protocol() -> None:
    """The gate this widening needed a ruling for.

    Derived, not listed: a hand-maintained list cannot see the implementation
    added tomorrow. A widening that misses one fails here rather than at a
    release. (The release facility's string-embedded probe wheel, which this
    sweep could not see, was retired with V1 authority.)
    """
    implementations = _effects_implementations()
    assert implementations, "the sweep found no implementation at all"
    missing: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            names = {b.name for b in node.body if isinstance(b, ast.FunctionDef)}
            if "prune_images" in names and (
                "bootstrap_principal_credential" not in names
            ):
                missing.append(f"{path.name}:{node.name}")
    assert missing == [], (
        f"{missing} implement Effects without the widened method. Every "
        "implementation must be updated in the same change"
    )


@pytest.mark.parametrize(
    "double",
    ["tests.unit.test_deployment_foundation_failure_injection:FakeEffects"],
)
def test_the_shared_double_satisfies_the_runtime_protocol(double: str) -> None:
    from tests.unit.test_deployment_foundation_failure_injection import FakeEffects

    assert isinstance(FakeEffects(), Effects)


# ── the step is NOT in plan.steps, so V1 digests do not move ────────────────


def test_build_plan_does_not_emit_the_bootstrap_step() -> None:
    """THE constraint. A step in `build_plan` lands in
    `FoundationExecutionPlanV1.steps`, inside the V1 digest — so emitting one
    would move every frozen V1 digest for deployments that bootstrap nothing."""
    spec, _, _ = _fixture()
    assert StepKind.BOOTSTRAP_PRINCIPALS not in [s.kind for s in build_plan(spec).steps]


def test_a_v3_plan_without_bootstraps_records_none() -> None:
    spec, plan, effects = _fixture()
    execution_plan, _ = _plan_and_digest(spec, plan, effects=effects)
    outcome = Executor(
        spec,
        effects,
        _grant(spec, execution_plan=execution_plan),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        **valid_host_source_kwargs(),
    ).run(plan, lock=held_lock(spec.product))
    assert effects.bootstrapped == []
    kinds = [s["kind"] for s in outcome.as_evidence()["steps"]]
    assert StepKind.BOOTSTRAP_PRINCIPALS.value not in kinds


# ── the invocation ──────────────────────────────────────────────────────────


def test_each_authorized_bootstrap_is_invoked_once() -> None:
    effects, outcome = _run_v2(bootstraps=(_bootstrap(), _bootstrap("platform_reader")))
    assert sorted(effects.bootstrapped) == [
        "platform_outbox_dispatcher",
        "platform_reader",
    ]
    assert outcome.succeeded


def test_the_bootstraps_come_from_the_PLAN_and_nowhere_else() -> None:
    """They are inside `ExecutionPlanDigestV1`, so Control froze them. A set
    taken from anywhere but the plan is a set nobody approved."""
    effects, _ = _run_v2(bootstraps=())
    assert effects.bootstrapped == []


def test_it_runs_BEFORE_the_step_loop() -> None:
    """A migration may need the role to exist and authenticate, so a bootstrap
    after `migrate` is a bootstrap for the run after this one."""
    _, outcome = _run_v2(bootstraps=(_bootstrap(),))
    kinds = [record.kind for record in outcome.records]
    assert kinds[0] == StepKind.BOOTSTRAP_PRINCIPALS


@pytest.mark.parametrize(
    "standing", [StepStanding.INSTALLED, StepStanding.RECONCILED_AFTER_COMMIT]
)
def test_both_histories_are_accepted_and_land_in_the_document(
    standing: StepStanding,
) -> None:
    """Same end state, different histories. The DOCUMENT says which."""
    _, outcome = _run_v2(bootstraps=(_bootstrap(),), standing=standing)
    step = next(
        s
        for s in outcome.as_evidence()["steps"]
        if s["kind"] == StepKind.BOOTSTRAP_PRINCIPALS.value
    )
    assert step["standing"] == standing.value
    assert step["target"] == "platform_outbox_dispatcher"


@pytest.mark.parametrize("ambiguous", [StepStanding.OK, StepStanding.NON_FATAL])
def test_any_other_standing_is_REFUSED_as_ambiguous(ambiguous: StepStanding) -> None:
    """ "It is present now" is true of both an install and a reconciliation. A
    provider that will not say which has not answered, and the seam refuses the
    ambiguity rather than persisting it."""
    _, outcome = _run_v2(bootstraps=(_bootstrap(),), standing=ambiguous)
    assert not outcome.succeeded
    assert outcome.failed_step == StepKind.BOOTSTRAP_PRINCIPALS


def test_a_refused_compare_and_set_is_a_REFUSAL_not_a_failure() -> None:
    """The expected answer for a record that already exists: nothing was
    mutated in the store, so the operator may fix the cause and re-run."""
    _, outcome = _run_v2(
        bootstraps=(_bootstrap(),),
        raises=PreconditionFailed("the record already exists at version 1"),
    )
    assert outcome.standing == RunStanding.REFUSED
    step = next(
        s
        for s in outcome.as_evidence()["steps"]
        if s["kind"] == StepKind.BOOTSTRAP_PRINCIPALS.value
    )
    assert step["standing"] == StepStanding.REFUSED.value


def test_a_provider_failure_is_a_FAILURE() -> None:
    _, outcome = _run_v2(
        bootstraps=(_bootstrap(),), raises=StepFailed("bootstrap", "the store broke")
    )
    assert outcome.standing == RunStanding.FAILED


def test_a_bootstrap_marks_the_run_MUTATED_before_the_call() -> None:
    """An install that failed partway has still touched the store, so the flag
    is claimed before the call — the convention every mutating step follows."""
    _, outcome = _run_v2(
        bootstraps=(_bootstrap(),), raises=StepFailed("bootstrap", "died midway")
    )
    assert outcome.mutated is True


def test_no_exception_text_reaches_the_document_on_the_bootstrap_path() -> None:
    """#611's property, on the new path. The failure paths are where raw text
    escapes and are the least exercised."""
    import json

    _, outcome = _run_v2(
        bootstraps=(_bootstrap(),),
        raises=PreconditionFailed("psql: FATAL: password authentication failed"),
    )
    assert outcome.failure
    assert outcome.failure not in json.dumps(outcome.as_evidence(), sort_keys=True)


# ── the in-package provider conforms by REFUSING ───────────────────────────


def test_the_compose_host_provider_refuses_rather_than_lacking_the_method() -> None:
    """A missing method makes the class non-conforming and fails as an
    `AttributeError` mid-deployment. A present one that refuses fails as a
    `PreconditionFailed` before any effect, naming what to install."""
    from dotmac_deployment_foundation.providers.compose_host import ComposeHostEffects

    assert hasattr(ComposeHostEffects, "bootstrap_principal_credential")
    with pytest.raises(PreconditionFailed):
        ComposeHostEffects.bootstrap_principal_credential(
            object(),  # type: ignore[arg-type]
            _bootstrap(),
        )


# ── the third acceptance point completes the 3x3 matrix ────────────────────


def test_the_executor_accepts_only_v3_and_refuses_historical_plans() -> None:
    """V1/V2/recovery plans cannot issue V3 execution authority."""
    from tests.unit.test_deployment_foundation_execution_plan_v2 import _recovery

    spec, plan, effects = _fixture()
    v3, _ = _plan_and_digest(spec, plan, effects=effects)
    v2 = render_execution_plan_v2(v3.base, principal_bootstraps=(_bootstrap(),))
    accepted = v3_plan(v2)
    grant = _grant(spec, execution_plan=accepted)
    Executor(
        spec,
        effects,
        grant,
        execution_plan=accepted,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        **valid_host_source_kwargs(),
    )
    for historical in (v3.base, v2):
        with pytest.raises(PreconditionFailed) as exc:
            Executor(
                spec,
                effects,
                grant,
                execution_plan=historical,  # type: ignore[arg-type]
                sleep=lambda _: None,
                evidence_policy=evidence_policy(),
                **valid_host_source_kwargs(),
            )
        assert exc.value.code == EXECUTION_PLAN_WRONG_TYPE
    with pytest.raises(PreconditionFailed) as exc:
        Executor(
            spec,
            effects,
            grant,
            execution_plan=_recovery(),  # type: ignore[arg-type]
            sleep=lambda _: None,
            **valid_host_source_kwargs(),
        )
    assert exc.value.code == EXECUTION_PLAN_WRONG_TYPE


# ── the sweep's field of view ───────────────────────────────────────────────


def _nonconforming_source(name: str) -> str:
    """A class the conformance test WOULD flag: `prune_images`, no bootstrap."""
    return f"class {name}:\n    def prune_images(self):\n        return None\n"


def _plant_checkout(root: Path) -> None:
    subprocess.run(  # noqa: S603 # nosec B603 — fixed argv, no shell
        ["git", "init", "-q", str(root)],  # noqa: S607 # nosec B607
        check=True,
    )


def test_the_sweep_does_not_reach_into_a_SIBLING_CHECKOUT(tmp_path: Path) -> None:
    """The sensitivity proof, in the shape this lane has been using.

    Plant a non-conforming implementation inside a sibling-checkout-shaped path
    and prove the sweep does not report it — otherwise the exclusion can quietly
    become a no-op and nobody finds out until a lane chases a defect that is not
    in its tree.

    The nested directory is deliberately NOT named `*_worktree`. A name-based
    skip would pass a test that used that name and fail in reality the moment
    someone named a checkout differently; this proves the exclusion is
    structural.
    """
    outer = tmp_path / "outer"
    (outer / "sibling_lane").mkdir(parents=True)
    _plant_checkout(outer)
    _plant_checkout(outer / "sibling_lane")

    (outer / "tracked.py").write_text("class Conforming:\n    pass\n")
    subprocess.run(  # noqa: S603 # nosec B603 — fixed argv, no shell
        ["git", "-C", str(outer), "add", "tracked.py"],  # noqa: S607 # nosec B607
        check=True,
    )
    (outer / "untracked.py").write_text("class AlsoConforming:\n    pass\n")
    (outer / "sibling_lane" / "other_lane.py").write_text(
        _nonconforming_source("FromAnotherBranch")
    )

    swept = {path.name for path in _python_files(outer)}

    # The fix.
    assert "other_lane.py" not in swept, (
        "the sweep reached into a sibling checkout — a local run would report "
        "another lane's branch as this tree's result"
    )
    # Positive control: it still sees this tree, or the assertion above would
    # hold for a sweep that had simply stopped working.
    assert "tracked.py" in swept
    # And it did NOT degrade to tracked-only, which would hide a file that
    # exists here but is not staged yet — the false-green direction again.
    assert "untracked.py" in swept


def test_the_planted_implementation_WOULD_have_been_reported(tmp_path: Path) -> None:
    """Non-vacuity for the test above.

    "The sweep did not report it" is also true of a file containing nothing
    interesting. Parse the same planted source with the same detector and show it
    yields exactly the non-conforming class the conformance test flags — so the
    previous assertion is about the sweep's field of view and not about an inert
    fixture.
    """
    planted = tmp_path / "other_lane.py"
    planted.write_text(_nonconforming_source("FromAnotherBranch"))
    tree = ast.parse(planted.read_text())
    flagged = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and {b.name for b in node.body if isinstance(b, ast.FunctionDef)}
        == {"prune_images"}
    ]
    assert flagged == ["FromAnotherBranch"]


def test_the_sweep_refuses_rather_than_falling_back(tmp_path: Path) -> None:
    """A fallback to `rglob` would silently restore the over-reach.

    A directory that is not a checkout at all is the reachable form of "git
    could not answer here". It must raise, not quietly widen.
    """
    with pytest.raises(RuntimeError, match="refuses"):
        _python_files(tmp_path / "not-a-checkout")


def test_the_PROTOCOL_ITSELF_is_deliberately_not_counted() -> None:
    """Whether this sweep reports 5 or 6 depends entirely on this one exclusion.

    Two lanes counting the same tree reported 6 and 5 and each had a coherent
    story. The whole difference was `engine/run.py:Effects` — the protocol
    DEFINITION, excluded here by `_effects_implementations`'s explicit skip — and
    the discrepancy arrived in the same change that narrowed the sweep's field of
    view, which made a benign counting convention look exactly like a dropped
    implementation.

    A count cannot distinguish those two. A name can. So the convention is
    pinned here by name instead of living in a skip expression that a reader has
    to notice, and the second assertion is the control: the definition really
    does define `prune_images`, so its absence from the set is a DELIBERATE
    exclusion rather than the sweep failing to reach it — which is the reading
    that would have been true if the fix had dropped something.
    """
    assert "run.py:Effects" not in _effects_implementations()

    run_py = Path(Effects.__module__.replace(".", "/"))
    source = (REPO / "packages/dotmac-deployment-foundation/src" / run_py).with_suffix(
        ".py"
    )
    defines = [
        node.name
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(b, ast.FunctionDef) and b.name == "prune_images"
            for b in node.body
        )
    ]
    assert defines == ["Effects"], (
        "the protocol definition no longer matches what the sweep looks for, so "
        "the exclusion above is now excluding nothing and the count it explains "
        "means something different"
    )
