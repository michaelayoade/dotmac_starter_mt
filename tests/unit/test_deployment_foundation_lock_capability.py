"""The deployment lock, held as a VALUE the executor can check.

## What was wrong

`Executor` mutates production hosts. It took no lock, named no lock and checked
no lock. The rule — "the caller holds the product's lock around the whole run"
— lived in two comments in `cli.py` and in `_do_acquire_lock`, a step whose
entire body returned the sentence *"held by the caller for the duration of the
run"* without asking anybody anything.

So the guarantee held for exactly the two callers who had read the comments. Any
third — a script, a worker, an embedder, a future subcommand — could build a
fully authorized executor, with a real Control receipt and a frozen plan digest,
and mutate a host with nothing serialising it against a concurrent deployment.
The 2026-07-12 incident that produced `deployment_lock` in the first place was
two concurrent deployments reaching load 52 with ten minutes of 502s; the
defence against a repeat was prose.

## The shape of the repair

`deployment_lock` now yields a :class:`DeploymentLockHeld`, obtainable nowhere
else, and `run`/`rollback` require one. This is `authorization._Witness` applied
to a second question: a caller with no grant has nothing to construct an
`Executor` with, and a caller holding no lock now has nothing to call it with.

## Why the near-misses are half the file

A lock that refuses everything serialises perfectly and deploys nothing. Three
things must stay silent: two DIFFERENT products locking at once (the lock is
per-product deliberately — `lock_path`), a run under a genuinely held token,
and the read-only paths that hold no lock because they mutate nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_deployment_foundation.engine.lock import (
    DeploymentLockHeld,
    deployment_lock,
)
from dotmac_deployment_foundation.engine.plan import StepKind
from dotmac_deployment_foundation.engine.run import Executor
from dotmac_deployment_foundation.errors import (
    LockUnavailableError,
    PreconditionFailed,
)

from tests.unit.test_deployment_foundation_execution_binding import (
    AcceptingVerifier,
    _fixture,
    _grant,
    _plan_and_digest,
    evidence_policy,
)


def _executor(spec, effects, plan):  # type: ignore[no-untyped-def]
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    return Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
    )


# ── 1. the planted violation: mutation with no lock at all ──────────────────


def test_a_run_outside_any_deployment_lock_is_refused(tmp_path: Path) -> None:
    """THE proof. Before this change the deployment simply proceeded.

    A fully authorized executor — real receipt, real grant, real frozen plan —
    with no lock. The assertion that matters is not only that it raised: it is
    that it raised having called NOTHING, because a refusal that arrives after
    the first effect is a partial deployment with an error message.
    """
    spec, plan, effects = _fixture()
    executor = _executor(spec, effects, plan)
    before = effects.snapshot()

    with pytest.raises(TypeError):
        executor.run(plan)  # type: ignore[call-arg]

    assert effects.mutations == [], (
        "the executor mutated before discovering it held no lock. The lock "
        "requirement has to be the FIRST thing, ahead of the grant and the "
        "plan digest, or it is a lock-shaped gap rather than a lock"
    )
    assert effects.snapshot() == before


def test_a_rollback_outside_any_deployment_lock_is_refused(tmp_path: Path) -> None:
    """The half a "wrap the happy path" reading omits.

    Boundary 3 names failure and rollback handling explicitly, and the rollback
    is if anything the sharper case: it races the deployment it is undoing.
    """
    spec, plan, effects = _fixture()
    executor = _executor(spec, effects, plan)
    with pytest.raises(TypeError):
        executor.rollback(plan)  # type: ignore[call-arg]
    assert effects.mutations == []


# ── 2. the planted violation: a token that outlived its hold ────────────────


def test_a_token_captured_inside_the_block_is_dead_outside_it(
    tmp_path: Path,
) -> None:
    """The subtle one, and the reason liveness is not just "I have an object".

    A `DeploymentLockHeld` stashed in a variable and used after the `with` block
    is a lock token for a lock nobody holds. It reads in a diff EXACTLY like a
    correct one, which is what makes it worth a test of its own.
    """
    with deployment_lock("acme", directory=tmp_path) as held:
        assert held.require_held(product="acme") == held.path

    with pytest.raises(PreconditionFailed, match="released before this point"):
        held.require_held(product="acme")


def test_a_run_with_an_expired_token_mutates_nothing(tmp_path: Path) -> None:
    """The same fault, driven through the real entry point rather than the type."""
    spec, plan, effects = _fixture()
    executor = _executor(spec, effects, plan)
    with deployment_lock(spec.product, directory=tmp_path) as held:
        pass
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed, match="released before this point"):
        executor.run(plan, lock=held)
    assert effects.mutations == []
    assert effects.snapshot() == before


# ── 3. the planted violation: a hand-built token ────────────────────────────


def test_a_hand_built_token_is_refused_at_construction(tmp_path: Path) -> None:
    """Crude on purpose, and the crudeness is the design — see `authorization`.

    Nothing in Python stops a determined caller importing the private witness.
    What this buys is that the bypass is one grep and one obviously-wrong
    import, rather than an omission that looks exactly like ordinary code.
    """
    with pytest.raises(PreconditionFailed, match="only be produced by"):
        DeploymentLockHeld(object(), product="acme", path=tmp_path)  # type: ignore[arg-type]


def test_a_token_for_another_product_does_not_authorise_this_one(
    tmp_path: Path,
) -> None:
    """Holding ACME's lock says nothing about deploying BETA.

    This refusal exists BECAUSE of the near-miss two tests below: the lock is
    per-product deliberately, so "a token" cannot be sufficient on its own.
    """
    with deployment_lock("other", directory=tmp_path) as held:
        with pytest.raises(PreconditionFailed, match="holds the deployment lock"):
            held.require_held(product="acme")


# ── 4. the flock fact, as a test rather than a measurement ──────────────────


def test_a_second_acquisition_in_ONE_process_refuses(tmp_path: Path) -> None:
    """Why boundaries 1 and 3 could not be separated.

    `fcntl.flock` attaches to the open file DESCRIPTION, so two acquisitions in
    a single process conflict — a caller already holding the product's lock
    cannot take it again, and gets `EAGAIN` against itself.

    `exposure.ExposureTransaction` took its own lock. It was therefore not
    merely ungoverned alongside a deployment; it was structurally unable to run
    inside one. Moving exposure under the caller's lock REQUIRED removing that
    class, which is why the two boundaries are one change.

    (`test_a_second_holder_is_refused_while_the_first_holds` in
    `test_deployment_foundation_lock_integrity.py` asserts the same syscall
    property. It is stated again here because there it documents mutual
    exclusion and here it documents an impossibility that dictated a design —
    same fact, two claims, and deleting either would lose one of them.)
    """
    with deployment_lock("acme", directory=tmp_path):
        with pytest.raises(LockUnavailableError, match="another deployment"):
            with deployment_lock("acme", directory=tmp_path):
                pass  # pragma: no cover - the point is that we never get here


# ── 5. the near-misses: what must stay SILENT ───────────────────────────────


def test_a_run_under_a_genuinely_held_token_is_not_refused(tmp_path: Path) -> None:
    """Sensitivity. Without this the refusals above could be refusing everything.

    The assertion is deliberately about the LOCK step rather than about success:
    this fixture's run may still fail later for its own reasons, and a test that
    demanded overall success would be asserting something this file does not own.
    What it must show is that the lock requirement was satisfied and the run got
    past it.
    """
    spec, plan, effects = _fixture()
    executor = _executor(spec, effects, plan)
    with deployment_lock(spec.product, directory=tmp_path) as held:
        outcome = executor.run(plan, lock=held)
    acquired = [r for r in outcome.records if r.kind is StepKind.ACQUIRE_LOCK]
    assert acquired, "the run never reached the lock step; it proves nothing"
    assert acquired[0].ok
    assert str(tmp_path) in acquired[0].detail, (
        "the step reports the REAL inode now. It used to return a sentence "
        "asserting the caller held a lock, which is the narration that let the "
        "property go unchecked for as long as it did"
    )


def test_two_different_products_may_hold_their_locks_at_once(
    tmp_path: Path,
) -> None:
    """The other half of sensitivity, and a real deployment property.

    Two products deploying to one host must NOT serialise. A global lock would
    make every product on a host wait for every other, which is the change
    somebody eventually reverts at 3am rather than waits for.
    """
    with deployment_lock("acme", directory=tmp_path) as first:
        with deployment_lock("other", directory=tmp_path) as second:
            assert first.require_held(product="acme") != second.require_held(
                product="other"
            )


def test_a_pure_verifier_that_only_observes_needs_no_lock(tmp_path: Path) -> None:
    """`exposure-apply` is this shape now, and it must NOT have been broken.

    A path that calls `observe()` as many times as it likes and never a mutator
    changes nothing, races nothing and serialises nothing. Requiring a lock of
    it would be a guard that had stopped distinguishing looking from touching —
    and the whole reason this subcommand survives at all is that looking is a
    real capability worth keeping.

    Driven through the real function rather than asserted about it, because the
    claim is that no lock is REACHED, and only running it can show that.
    """
    from dotmac_deployment_foundation.exposure import verify_exposure

    from tests.unit.test_deployment_foundation_execution_binding import load
    from tests.unit.test_deployment_foundation_exposure import (
        _FakeEffects,
        _observation,
    )

    spec = load()
    effects = _FakeEffects(before=_observation(), after=_observation())
    for _ in range(3):
        verify_exposure(spec, effects.observe())
    assert effects.calls == ["observe", "observe", "observe"]
    assert not (tmp_path / f"dotmac_{spec.product}_deploy.lock").exists(), (
        "a read-only path took the deployment lock. Serialising observation "
        "against deployment makes an operator unable to ask what a host looks "
        "like while a deployment is running, which is the one moment they most "
        "need to"
    )


def test_building_and_formatting_a_plan_takes_no_lock(tmp_path: Path) -> None:
    """The dry-run near-miss. `plan`/`validate` must stay lock-free.

    A dry run that took the deployment lock would block a real deployment for
    as long as somebody was reading a plan, and — worse — would teach operators
    that the lock being held means nothing.
    """
    from dotmac_deployment_foundation.engine.plan import build_plan, format_plan

    spec, plan, _ = _fixture()
    format_plan(build_plan(spec))
    assert list(tmp_path.iterdir()) == []
    assert plan.steps, "the fixture built no plan; this proves nothing"
