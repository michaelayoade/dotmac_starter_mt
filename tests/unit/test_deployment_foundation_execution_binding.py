"""The executor refuses BEFORE any effect unless the plan and its authorization
agree — and "before any effect" is asserted as absence of effect, not as an
exception.

## The defect these tests exist for

`ExecutionPlanDigestV1` is the middle term the whole authorization chain is
built around: the Foundation renders `FoundationExecutionPlanV1`, Platform CP
submits its digest, Control freezes and signs THAT, and the Foundation
recomputes it before executing. All of it existed. None of it was reachable.

`Executor.__init__` took `execution_plan=None` and
`authorized_execution_plan_digest=""`, `_require_execution_plan` PERMITTED
"absent on both sides" and returned `""`, and `cmd_deploy` passed neither —
with no CLI flag that could. So every real deployment took that branch, mutated
a host, and wrote `deploy-evidence.json` with an empty digest. The mechanism was
built, tested, and exercised only by the subcommand that prints a digest for
somebody else.

A branch only ever taken by the path it describes as exceptional is not a
fallback. It is the behaviour.

## Why a recording `Effects` and not `FakeEffects`

Four of these tests assert ZERO EFFECTS, and `FakeEffects` cannot support that
claim: its `run_command` is stateless — canned input to canned output, no call
log — and its `backup` records nothing. A migration, a preflight or a postflight
leaves no trace on it, so `assert nothing_happened` would pass against a broken
executor. That assertion would have been the next instance of the pattern these
tests were written to close, inside the tests written to close it.

So :class:`RecordingEffects` logs every mutating call, and each zero-effect test
asserts BOTH that the log is empty AND that the host state is byte-identical to
its pre-state. A refusal has to be seen to count, and so does an absence.

## The ninth property lives elsewhere, deliberately

Eight tests here run against the source tree, and the source tree is not what
anyone installs. The ninth — that an INSTALLED wheel refuses the same way — is
in `scripts/release_facility.py`'s wheel smoke, which runs against the bytes in
the candidate artifact and again against the bytes the registry served. A test
importing this repository can only ever prove this repository.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from dotmac_deployment_foundation.authorization import authorize
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.engine.run import (
    Executor,
)
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.execution_plan import (
    HostPrestateV1,
    render_execution_plan,
)
from dotmac_deployment_foundation.provenance import (
    AuthorizationReceipt,
    verify_authorization,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.test_deployment_foundation_failure_injection import (
    DESCRIPTOR,
    GOOD_DIGEST,
    OLD_DIGEST,
    AcceptingVerifier,
    FakeClock,
    FakeEffects,
    evidence_policy,
    load,
)

TARGET = "execution-binding-target"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
CONTROL_PLAN_DIGEST = "f" * 64
WRONG_DIGEST = "sha256:" + "9" * 64


class _StubVerifier:
    """Stands in for the verifier the ASSEMBLY supplies. Attests what it is
    given: these tests exercise the binding, not the cryptography, which this
    facility deliberately does not own."""

    def attest(self, material: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(material)


class RecordingEffects(FakeEffects):  # type: ignore[misc]
    """`FakeEffects`, plus the two things a zero-effect assertion needs.

    A SUBCLASS rather than a rewrite, deliberately: every gate here must keep
    returning the healthy value so a test that expects an effect can actually
    reach one, and re-deriving that world by hand produces a fixture whose
    refusals come from somewhere other than the binding under test — which is
    how a green suite ends up proving nothing.

    What it adds is what `FakeEffects` cannot support: a log of every MUTATING
    call, and a snapshot of the pretend host. `FakeEffects.run_command` is
    stateless and its `backup` records nothing, so a migration or a preflight
    leaves no trace on it, and `assert nothing_happened` would pass against a
    broken executor.
    """

    def __init__(self, **overrides: object) -> None:
        super().__init__(**overrides)
        self.mutations: list[tuple[str, str]] = []

    def snapshot(self) -> bytes:
        """The host state a refusal must leave byte-identical.

        Everything an effect below can change, and nothing that varies for
        another reason — a timestamp in here would make every comparison fail
        and the assertion would get deleted rather than fixed.
        """
        return json.dumps(
            {
                "switched_to": list(getattr(self, "switched_to", [])),
                "evidence_written": bool(getattr(self, "evidence_written", False)),
                "annotations": [dict(a) for a in getattr(self, "annotations", [])],
                "stopped": list(getattr(self, "stopped", [])),
                "started": list(getattr(self, "started", [])),
                "mutations": list(self.mutations),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()

    # ── every mutating method, recorded then delegated ──
    def run_command(self, command: Sequence[str], **kwargs: Any) -> Any:
        self.mutations.append(("run_command", " ".join(command)))
        return super().run_command(command, **kwargs)

    def run_migration_command(self, command: Sequence[str], **kwargs: Any) -> Any:
        self.mutations.append(("run_migration_command", " ".join(command)))
        return super().run_migration_command(command, **kwargs)

    def backup(self, dataset_code: str, **kwargs: Any) -> Any:
        self.mutations.append(("backup", dataset_code))
        return super().backup(dataset_code, **kwargs)

    def stop_roles(self, roles: Sequence[str], **kwargs: Any) -> None:
        self.mutations.append(("stop_roles", ",".join(roles)))
        super().stop_roles(roles, **kwargs)

    def start_candidate(self, role: str, **kwargs: Any) -> str:
        self.mutations.append(("start_candidate", role))
        return super().start_candidate(role, **kwargs)

    def switch(self, *, timeout_seconds: int, image: str) -> None:
        self.mutations.append(("switch", image))
        super().switch(timeout_seconds=timeout_seconds, image=image)

    def write_evidence(self, evidence: Mapping[str, object]) -> str:
        self.mutations.append(("write_evidence", str(evidence.get("operation", ""))))
        return super().write_evidence(evidence)

    def prune_images(self, *, retain: int) -> None:
        self.mutations.append(("prune_images", str(retain)))
        super().prune_images(retain=retain)

    def emit_annotation(self, annotation: Mapping[str, str]) -> None:
        self.mutations.append(("emit_annotation", str(annotation.get("event", ""))))
        super().emit_annotation(annotation)


def _receipt(spec: ProductDeploymentSpec, **overrides: object) -> AuthorizationReceipt:
    fields: dict[str, object] = {
        "plan_id": "00000000-0000-4000-8000-00000000beef",
        "target_ref": TARGET,
        "descriptor_digest": spec.to_canonical_document().sha256_digest(),
        "execution_plan_digest": "sha256:" + "e" * 64,
        "control_plan_digest": CONTROL_PLAN_DIGEST,
        "policy_code": "deployment.production",
        "policy_version": 1,
        "decision_ref": "approvals:decision:1",
        "approved_at": "2026-08-30T00:00:00Z",
        "expires_at": "2026-08-31T00:00:00Z",
        "control_version": "0.1.0a4",
        "operation": "deploy",
    }
    fields.update(overrides)
    return AuthorizationReceipt(**fields)  # type: ignore[arg-type]


def _grant(spec: ProductDeploymentSpec, *, now: datetime = NOW, **overrides: object):  # type: ignore[no-untyped-def]
    return authorize(
        verified=verify_authorization(
            _receipt(spec, **overrides).as_document(), verifier=_StubVerifier()
        ),
        operation=str(overrides.get("operation", "deploy")),
        descriptor_digest=spec.to_canonical_document().sha256_digest(),
        target=TARGET,
        now=now,
    )


def _plan_and_digest(
    spec: ProductDeploymentSpec, plan, operation: str = "deploy", *, effects=None
):  # type: ignore[no-untyped-def]
    prestate = (
        HostPrestateV1.from_observations(effects.observe_roles())
        if effects is not None
        else HostPrestateV1.first_deploy()
    )
    execution_plan = render_execution_plan(
        spec,
        plan,
        target=TARGET,
        operation=operation,
        descriptor_digest=str(spec.to_canonical_document().sha256_digest()),
        prestate=prestate,
    )
    return execution_plan, execution_plan.digest()


def _fixture(**overrides: str):  # type: ignore[no-untyped-def]
    spec = load(**overrides)
    plan = build_plan(spec)
    effects = RecordingEffects()
    return spec, plan, effects


def _assert_untouched(effects: RecordingEffects, before: bytes) -> None:
    """The assertion this file exists to get right.

    NOT "an exception was raised" — that is compatible with a host that was
    half-migrated before the raise. Both halves are required: nothing was
    called, and the world is byte-identical to what it was.
    """
    assert effects.mutations == [], (
        f"the refusal happened AFTER {len(effects.mutations)} effect(s): "
        f"{effects.mutations}. A refusal that follows a mutation is not a "
        "refusal, it is a partial deployment with an error message"
    )
    assert effects.snapshot() == before, "the target changed despite the refusal"


# ── 1. absent authorization ────────────────────────────────────────────────


def test_absent_authorization_produces_zero_effects() -> None:
    """No grant, no executor, nothing to run — the seam `ExecutionGrant`
    already closed, restated here in terms of EFFECTS rather than of types."""
    spec, plan, effects = _fixture()
    before = effects.snapshot()
    execution_plan, _ = _plan_and_digest(spec, plan, effects=effects)
    with pytest.raises(TypeError):
        Executor(spec, effects, execution_plan=execution_plan)  # type: ignore[call-arg]
    _assert_untouched(effects, before)


# ── 2. absent plan ─────────────────────────────────────────────────────────


def test_absent_execution_plan_produces_zero_effects() -> None:
    """THE REGRESSION TEST for the deleted branch.

    This is the exact state every real deployment ran in: a grant, no plan, and
    an executor that proceeded to mutate a host. It must now refuse before the
    first effect.
    """
    spec, plan, effects = _fixture()
    before = effects.snapshot()
    executor = Executor(spec, effects, _grant(spec), execution_plan=None)  # type: ignore[arg-type]
    with pytest.raises(PreconditionFailed, match="no execution plan"):
        executor.run(plan)
    _assert_untouched(effects, before)


def test_an_executor_cannot_be_built_without_a_plan_at_all() -> None:
    """One layer earlier than the test above: absent is not even expressible
    without passing `None` on purpose."""
    spec, plan, effects = _fixture()
    with pytest.raises(TypeError):
        Executor(spec, effects, _grant(spec))  # type: ignore[call-arg]


# ── 3. digest mismatch ─────────────────────────────────────────────────────


def test_an_unfrozen_plan_produces_zero_effects() -> None:
    """The plan is real and internally consistent; nothing froze THIS one."""
    spec, plan, effects = _fixture()
    before = effects.snapshot()
    execution_plan, _ = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=WRONG_DIGEST),
        execution_plan=execution_plan,
    )
    with pytest.raises(PreconditionFailed):
        executor.run(plan)
    _assert_untouched(effects, before)


# ── 4. an image the authorization never covered ────────────────────────────


def test_a_plan_for_an_unauthorized_image_produces_zero_effects() -> None:
    """Distinct from the test above, and the distinction is the point.

    There the authorized digest was simply wrong. Here every value is a real
    digest of a real plan — but the plan that was FROZEN names one image and
    the plan being RUN names another, which is exactly the substitution a
    digest-pinned reference exists to prevent. One descriptor yields a
    different plan per image, so the recomputation catches it.
    """
    spec, plan, effects = _fixture()
    other = ProductDeploymentSpec.loads(
        DESCRIPTOR.replace(GOOD_DIGEST, OLD_DIGEST), source="<test>"
    )
    other_plan = build_plan(other)
    frozen, frozen_digest = _plan_and_digest(other, other_plan)
    running, _ = _plan_and_digest(spec, plan, effects=effects)
    assert frozen.image_digest != running.image_digest, (
        "the fixture no longer varies the image, so this test would pass "
        "against a broken binding"
    )
    before = effects.snapshot()
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=frozen_digest),
        execution_plan=running,
    )
    with pytest.raises(PreconditionFailed):
        executor.run(plan)
    _assert_untouched(effects, before)


# ── the host prestate: authorized against a state, executed against it ─────


def test_a_host_that_moved_after_authorization_refuses_with_zero_effects() -> None:
    """Item 6's enforcement half. The plan binds the OBSERVED starting point
    into the digest Control froze; between authorization and execution another
    deployment moves the host, and applying a reviewed change to an unreviewed
    starting point must refuse — before any effect, target untouched."""
    from dotmac_deployment_foundation.engine.run import RoleObservation

    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
    )
    # The host moves: some OTHER deployment lands a different digest.
    moved = "sha256:" + "7" * 64
    for code, observation in list(effects.roles.items()):
        effects.roles[code] = RoleObservation(
            code, observation.running, moved, observation.restarts
        )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed, match="not the host that was authorized"):
        executor.run(plan)
    _assert_untouched(effects, before)


def test_an_empty_prestate_is_a_claim_a_populated_host_fails() -> None:
    """{"roles": []} says FIRST DEPLOY. Executing it against a host that turns
    out to have containers is a mismatch like any other, not a pass."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan)  # first-deploy claim
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed, match="not the host that was authorized"):
        executor.run(plan)
    _assert_untouched(effects, before)


# ── item 7: the candidate image reaches every migration-family invocation ──


def test_migration_family_work_runs_in_the_candidate_image() -> None:
    """Before this, `migrate` and its preflight were bare host commands with
    no image concept at all, and `verify_heads`/`start_candidate` used the
    on-disk compose file — which still pins the PREVIOUS image until `switch`
    re-renders it at the end. The engine now states the image for every one of
    them, and it is the plan's: the one the grant authorized."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    outcome = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
    ).run(plan)
    assert outcome.succeeded, outcome.failure
    assert effects.migration_images, "no migration-family work was recorded"
    for command, image in effects.migration_images:
        assert (
            image == plan.image
        ), f"{command} ran against {image!r}, not the authorized {plan.image!r}"
    for role, image in effects.candidate_started_with:
        assert (
            image == plan.image
        ), f"candidate {role} started on {image!r}, not {plan.image!r}"


# ── item 8: readiness is REAL for every strategy, after the switch ─────────


def test_a_role_that_never_becomes_ready_fails_the_deployment() -> None:
    """Running is not ready. A process that binds its port and hangs on a dead
    dependency is running, on the right digest, with zero restarts — and the
    old verification declared victory on exactly those three facts plus a
    sleep. The role's own declared probe is now polled, and polled more than
    once, for every strategy."""
    spec, plan, effects = _fixture()
    effects.role_never_ready = True
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    # A FAKE clock, so the readiness poll's deadline ARRIVES instead of the
    # loop spinning against the wall clock with a no-op sleep. The first
    # version of this test did exactly that and hung the suite — a poll whose
    # time cannot be moved is untestable, which is itself why the executor
    # takes an injected clock.
    clock = FakeClock()
    outcome = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=clock.sleep,
        clock=clock.read,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
    ).run(plan)
    assert not outcome.succeeded
    assert outcome.failed_step is not None
    assert outcome.failed_step.value == "verify_roles"
    assert "readiness probe" in outcome.failure
    assert effects._role_ready_calls > 1, (
        "the probe was consulted once or never; a readiness gate that does "
        "not poll is a coin flip against a service still starting"
    )


# ── 5. the substitution ────────────────────────────────────────────────────


def test_controls_plan_digest_supplied_as_the_descriptor_digest_refuses() -> None:
    """The conflation this repair removes, asserted as behaviour.

    `AuthorizationReceipt.descriptor_digest` used to HOLD Control's plan digest
    by design, and `build_provenance` refused unless it equalled the
    descriptor's. Two different measurements asserted equal. Now the receipt
    names three terms and only the descriptor term is compared to a descriptor
    — so Control's plan digest arriving in that field is refused rather than
    silently accepted as the value it is not.
    """
    spec, _plan, _effects = _fixture()
    with pytest.raises(PreconditionFailed, match="not an approval for this"):
        _grant(spec, descriptor_digest=CONTROL_PLAN_DIGEST)


# ── 6. an expired approval ─────────────────────────────────────────────────


def test_an_expired_authorization_refuses_and_produces_zero_effects() -> None:
    """Time is checked BEFORE any digest, so an expired approval refuses for
    being expired even when every other term agrees."""
    spec, _plan, effects = _fixture()
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed, match="expired"):
        _grant(spec, now=datetime(2026, 9, 1, 0, 0, tzinfo=UTC))
    _assert_untouched(effects, before)


def test_an_approval_with_no_duration_is_refused_at_construction() -> None:
    """A receipt expiring at or before its own approval would refuse every run,
    which reads as a broken deployment rather than as a malformed receipt."""
    spec, _plan, _effects = _fixture()
    with pytest.raises(Exception, match="not after"):
        _receipt(spec, expires_at="2026-08-30T00:00:00Z")


# ── 7. the valid tuple mutates, once ───────────────────────────────────────


def test_the_exact_authorized_tuple_mutates_once() -> None:
    """The positive control, without which every refusal above could be a
    permanently broken executor rather than a discriminating one."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
    )
    outcome = executor.run(plan)
    assert outcome.succeeded, outcome.failure
    switches = [call for call in effects.mutations if call[0] == "switch"]
    assert len(switches) == 1, f"expected exactly one switch, saw {switches}"
    assert (
        outcome.execution_plan_digest == digest
    ), "the outcome must carry the digest that was frozen, not an empty string"
    assert outcome.descriptor_digest, "the descriptor digest is not persisted"
    assert (
        outcome.control_plan_digest == CONTROL_PLAN_DIGEST
    ), "Control's plan digest must be persisted verbatim, under its own name"
    evidence = outcome.as_evidence()
    assert evidence["execution_plan_digest"] == digest
    assert evidence["descriptor_digest"] == outcome.descriptor_digest
    assert evidence["control_plan_digest"] == CONTROL_PLAN_DIGEST


# ── 8. the replay rule ─────────────────────────────────────────────────────


def test_a_second_execution_of_one_authorization_switches_again_not_silently() -> None:
    """The typed replay rule, stated as what it IS rather than as what would be
    convenient.

    This facility owns no idempotency ledger — `AGENTS.md` rule 23 puts
    at-most-once execution behind one owner, and it is not this one. So a
    second run of the same authorized tuple is a second DEPLOYMENT, and it says
    so: it mutates again and records again, rather than returning a cached
    success that would let an operator believe a re-run was a no-op.

    Asserted because the alternative is worse in the direction that matters: a
    silent replay makes two deployments indistinguishable from one in the
    evidence, and the evidence is the only account of what happened.
    """
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)

    def run_once() -> Any:
        executor = Executor(
            spec,
            effects,
            _grant(spec, execution_plan_digest=digest),
            execution_plan=execution_plan,
            sleep=lambda _: None,
            evidence_policy=evidence_policy(),
            evidence_verifier=AcceptingVerifier(),
        )
        return executor.run(plan)

    def counts() -> tuple[int, int]:
        return (
            len([c for c in effects.mutations if c[0] == "switch"]),
            len([c for c in effects.mutations if c[0] == "write_evidence"]),
        )

    first = run_once()
    after_first = counts()
    second = run_once()
    after_second = counts()

    assert (
        first.succeeded and second.succeeded
    ), f"{first.failure or ''} {second.failure or ''}".strip()
    # Measured as a DOUBLING rather than against a literal: how many evidence
    # records one deployment writes is the engine's business and it writes more
    # than one, so a hard-coded count would be asserting an unrelated fact and
    # would break the day that changes.
    assert after_second == (after_first[0] * 2, after_first[1] * 2), (
        f"a second run of the same authorized tuple did not repeat the "
        f"deployment: {after_first} then {after_second}. A silent replay makes "
        "two deployments indistinguishable from one in the only account of "
        "what happened"
    )
    assert after_first[0] >= 1, "the fixture never switched, so this proves nothing"
    assert first.execution_plan_digest == second.execution_plan_digest == digest
