"""The executor calls `require_host_source` ITSELF, before anything else it does.

## The ruling this file proves (Michael, verbatim)

* `engine/run.py` owns the mandatory `require_host_source` invocation;
  `cli.py` owns loading the candidate receipt and handing it in.
* Fired after the deployment lock is proven held, and before authorization
  revalidation, plan validation, annotations, bootstrap, or any effect.
* The same prerequisite covers every mutating executor path, including
  rollback.
* Passing an already-constructed `HostSource` is insufficient — it is a plain
  dataclass, constructible by anyone who imports it. The executor must call
  `require_host_source` itself, against inputs it cannot forge.

`Executor` therefore accepts the RAW INGREDIENTS `require_host_source` itself
checks (`host_source_receipt`, plus test-only override hooks
`host_source_installed`/`host_source_metadata`/`host_source_probe`), never a
`HostSource`. `Executor.__all__`-style surface has no `host_source=` parameter
at all — there is nothing to hand it that would satisfy the gate without going
through `_verify_host_source`.

## Measured, not assumed: what happens under the real default

`Executor`'s production default (`host_source_metadata=None`) reads the REAL
installed distribution via `importlib.metadata`. On this workstation's system
`python3`, `dotmac-deployment-foundation` is not installed at all
(`No package metadata was found for dotmac-deployment-foundation` —
measured directly, see the task report). In this repository's own CI unit-test
job, `pyproject.toml` declares the package as a Poetry path dependency with
`develop = true`, which installs it EDITABLE — the exact shape
`host_source.py`'s own docstring describes measured for `dotmac_observability`:
`{"dir_info": {"editable": true}}`, no `archive_info`, and a `RECORD` that
lists a `.pth` and metadata rather than the importable source. Either way, the
real default refuses. `test_a_bare_executor_refuses_by_default` asserts this
against the REAL default rather than a double, and only requires the refusal
to carry one of `host_source`'s own four codes — which is what makes it
correct in both environments without having to assume which one CI is.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import dotmac_deployment_foundation.engine.run as run_module
import pytest
from dotmac_deployment_foundation.engine.run import Executor
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.host_source import (
    ABSENT,
    DISAGREES,
    NO_RECEIPT,
    WRONG_KIND,
)

from tests.unit.deployment_lock_harness import held_lock
from tests.unit.test_deployment_foundation_execution_binding import (
    _assert_untouched,
    _fixture,
    _grant,
    _plan_and_digest,
)
from tests.unit.test_deployment_foundation_failure_injection import (
    AcceptingVerifier,
    evidence_policy,
)
from tests.unit.test_deployment_foundation_host_source import (
    OTHER_WHEEL_SHA256,
    SOURCE_SHA,
    FakeInstall,
    _receipt,
    _source_tree_digest,
)

HOST_SOURCE_CODES = {ABSENT, WRONG_KIND, DISAGREES, NO_RECEIPT}


def _valid_executor(*, spec=None, plan=None, effects=None, receipt=None, install=None):  # type: ignore[no-untyped-def]
    """An otherwise fully-authorized executor, wired to a GENUINE host source
    (a `FakeInstall` shaped like a real one, plus a matching receipt) unless a
    test overrides one half to make it invalid."""
    if spec is None or plan is None or effects is None:
        spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    return (
        spec,
        plan,
        effects,
        Executor(
            spec,
            effects,
            _grant(spec, execution_plan_digest=digest),
            execution_plan=execution_plan,
            sleep=lambda _: None,
            evidence_policy=evidence_policy(),
            evidence_verifier=AcceptingVerifier(),
            host_source_receipt=receipt if receipt is not None else _receipt(),
            host_source_metadata=install if install is not None else FakeInstall(),
            host_source_probe=_source_tree_digest,
        ),
    )


# ── 1. constructing an Executor directly cannot bypass the gate ────────────


def test_a_bare_executor_refuses_by_default() -> None:
    """THE HOLE THE RULING CLOSES. A caller that builds an `Executor` and
    supplies nothing about host source at all gets the REAL default reader —
    no fake, no override — and it refuses, because nothing in this sandbox
    (or, per `pyproject.toml`'s `develop = true`, in this repo's own CI
    unit-test job) is a non-editable install with a receipt behind it.

    The refusal's CODE is asserted to be one of `host_source`'s own four,
    which is what proves this specific gate fired rather than some other
    refusal happening to come first.
    """
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(plan, lock=held_lock(spec.product))
    assert refusal.value.code in HOST_SOURCE_CODES, (
        f"refused with {refusal.value.code!r}, not one of the host-source "
        f"codes {HOST_SOURCE_CODES}. That would mean some OTHER gate fired "
        "first, which is precisely what this test exists to rule out"
    )
    _assert_untouched(effects, before)


def test_a_bare_executor_refuses_on_rollback_too() -> None:
    """The same hole, on the sharper path. Boundary 3 names rollback
    explicitly; a gate proven only on `run` would leave this one open."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(
        spec, plan, operation="rollback", effects=effects
    )
    executor = Executor(
        spec,
        effects,
        _grant(spec, operation="rollback", execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as refusal:
        executor.rollback(plan, lock=held_lock(spec.product))
    assert refusal.value.code in HOST_SOURCE_CODES
    _assert_untouched(effects, before)


# ── 2. the cross matrix, both arms ──────────────────────────────────────────


def test_valid_host_source_and_invalid_authorization_refuses() -> None:
    """Arm 1. A genuine host source does not launder a bad grant."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    wrong_digest = "sha256:" + "9" * 64
    assert wrong_digest != digest
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=wrong_digest),  # INVALID authorization
        execution_plan=execution_plan,
        sleep=lambda _: None,
        host_source_receipt=_receipt(),  # VALID host source
        host_source_metadata=FakeInstall(),
        host_source_probe=_source_tree_digest,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed):
        executor.run(plan, lock=held_lock(spec.product))
    _assert_untouched(effects, before)


def test_valid_authorization_and_invalid_host_source_refuses() -> None:
    """Arm 2. A genuine grant does not launder a bad (disagreeing) host
    source. `DISAGREES` specifically, so this is not merely "some receipt was
    missing" but "the wrong artifact was offered against a real receipt"."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),  # VALID authorization
        execution_plan=execution_plan,
        sleep=lambda _: None,
        # INVALID host source: a receipt binding a DIFFERENT wheel than the
        # one FakeInstall() reports as installed.
        host_source_receipt=_receipt(sha256=OTHER_WHEEL_SHA256),
        host_source_metadata=FakeInstall(),
        host_source_probe=_source_tree_digest,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(plan, lock=held_lock(spec.product))
    assert refusal.value.code == DISAGREES
    _assert_untouched(effects, before)


def test_valid_authorization_and_absent_host_source_refuses() -> None:
    """The other shape of arm 2: no receipt at all behind a real artifact."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        host_source_receipt=None,
        host_source_metadata=FakeInstall(),
        host_source_probe=_source_tree_digest,
    )
    before = effects.snapshot()
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(plan, lock=held_lock(spec.product))
    assert refusal.value.code == NO_RECEIPT
    _assert_untouched(effects, before)


# ── 3. ordering, observed rather than read ──────────────────────────────────


def test_the_gate_fires_before_the_grant_when_both_are_invalid(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Race proof: make BOTH the grant and the host source invalid, and show
    the host-source refusal wins — which can only happen if it runs first.
    An `_annotate` spy additionally proves the grant step, and everything
    after it, was never reached."""
    spec, plan, effects = _fixture()
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    wrong_digest = "sha256:" + "9" * 64
    assert wrong_digest != digest
    executor = Executor(
        spec,
        effects,
        _grant(spec, execution_plan_digest=wrong_digest),  # invalid
        execution_plan=execution_plan,
        sleep=lambda _: None,
        host_source_receipt=None,  # invalid: NO_RECEIPT
        host_source_metadata=FakeInstall(),
        host_source_probe=_source_tree_digest,
    )
    annotate_calls: list[str] = []
    monkeypatch.setattr(
        executor,
        "_annotate",
        lambda *a, **k: annotate_calls.append(a[0] if a else k.get("event", "")),
    )
    with pytest.raises(PreconditionFailed) as refusal:
        executor.run(plan, lock=held_lock(spec.product))
    assert refusal.value.code == NO_RECEIPT, (
        "the grant's refusal surfaced instead of the host source's, which "
        "would mean the grant is checked FIRST — the opposite of the ruling"
    )
    assert annotate_calls == [], (
        "an annotation was sent despite both the host source and the grant "
        "being invalid; the gate did not run before annotation"
    )


def test_the_gate_fires_before_the_annotation_and_therefore_before_the_grant(
    monkeypatch,
) -> None:
    """Direct call-order proof via spies on two module/class-level callables.

    `_verify_host_source` calls the unqualified name `require_host_source`,
    resolved from `engine.run`'s module globals at call time — monkeypatching
    that module attribute intercepts every call `Executor` makes. `_annotate`
    is patched on the CLASS (`Executor._annotate`), not on the `executor`
    instance or on `executor._grant`: `ExecutionGrant` is a
    `frozen=True, slots=True` dataclass (`authorization.py`), and patching an
    attribute onto a frozen, slotted INSTANCE is exactly the shape that broke
    this test's first draft. Patching the plain `Executor` class avoids that
    entirely.

    `run()`'s body is linear between the host-source call and the annotation
    — `_verify_host_source()`, then `self._grant.require(...)`, then
    `_require_execution_plan(...)`, then `self._annotate("deployment.start",
    ...)` — so "host source fires before the annotation" already establishes
    "before the grant and before the plan check", without needing a second,
    riskier spy on the grant itself.
    """
    calls: list[str] = []
    real_require = run_module.require_host_source

    def require_spy(**kwargs):  # type: ignore[no-untyped-def]
        calls.append("host_source")
        return real_require(**kwargs)

    monkeypatch.setattr(run_module, "require_host_source", require_spy)

    real_annotate = run_module.Executor._annotate

    def annotate_spy(self, *a, **kw):  # type: ignore[no-untyped-def]
        calls.append("annotate")
        return real_annotate(self, *a, **kw)

    monkeypatch.setattr(run_module.Executor, "_annotate", annotate_spy)

    spec, plan, effects, executor = _valid_executor()
    outcome = executor.run(plan, lock=held_lock(spec.product))

    assert calls, "no calls were recorded; this proves nothing"
    assert calls[0] == "host_source", (
        f"host source was not FIRST: {calls}. The ruling requires it to fire "
        "immediately after the lock and before everything else, including "
        "the grant, the plan check and every annotation"
    )
    assert "annotate" in calls, (
        "the annotation was never reached; this shows nothing about ORDER, "
        "only that the run stopped somewhere"
    )
    assert calls.index("host_source") < calls.index("annotate")
    assert outcome.succeeded, outcome.failure


# ── 4. the COMPOSITION admit proof, plus non-vacuity ────────────────────────
#
# Neither test below is THE real admit proof. Both drive the gate with an
# injected `FakeInstall`/`CandidateReceipt` pair — a genuinely AGREEING pair,
# shaped after the real fixtures `test_deployment_foundation_host_source.py`
# already measured, but no wheel was built and no digest was matched against
# an actual installed artifact. What this proves is COMPOSITION: the gate is
# actually reached, called exactly once, and admits when its own inputs
# agree — i.e. that `Executor` does not skip the call, double-call it, or
# refuse unconditionally regardless of what it is given. The real admit proof
# — an installed, digest-matched WHEEL reaching this same code path — is not
# available from this repository: no published `dotmac-deployment-foundation`
# version carries this gate yet, and `test_a_bare_executor_refuses_by_default`
# below is what shows the REAL default (no fakes) still refuses today, in
# this and every editable checkout. That test remains the standing proof that
# nothing here is a bypass.


def test_valid_host_source_and_valid_authorization_proceeds_past_the_gate() -> None:
    """COMPOSITION PROOF, not the real admit proof (see above). Without this,
    every refusal test above could be passing because the gate refuses
    UNCONDITIONALLY. Proceeding past the gate is observed as the
    `deployment.start` annotation actually being emitted — which happens only
    after the lock, the host source gate AND the grant all accepted."""
    spec, plan, effects, executor = _valid_executor()
    executor.run(plan, lock=held_lock(spec.product))
    started = [
        m for m in effects.mutations if m == ("emit_annotation", "deployment.start")
    ]
    assert started, (
        f"deployment.start was never annotated; mutations were {effects.mutations}. "
        "The run did not get past the gates that precede it"
    )
    assert executor._host_source is not None
    assert executor._host_source.source_revision == SOURCE_SHA


def test_the_verification_call_happens_exactly_once_on_the_admit_path(
    monkeypatch,
) -> None:
    """NON-VACUITY, for the composition proof above. A `require_host_source`
    that is imported but never CALLED would let every refusal test in this
    file fail for an unrelated reason and let this one pass by accident,
    because nothing here yet asserts the call actually happened. A
    `MagicMock` wrapping the real function proves both that it fires, and
    that it fires exactly once — not once per step, not zero times because
    some earlier branch short-circuited it."""
    real = run_module.require_host_source
    spy = MagicMock(side_effect=real)
    monkeypatch.setattr(run_module, "require_host_source", spy)

    spec, plan, effects, executor = _valid_executor()
    executor.run(plan, lock=held_lock(spec.product))

    assert spy.call_count == 1, (
        f"require_host_source was called {spy.call_count} time(s), not "
        "exactly once. An implementation that skipped the call, or called it "
        "more than once per run, must fail this assertion"
    )


def test_rollback_also_reaches_the_verification_call(monkeypatch) -> None:
    """The admit control's rollback half.

    `_fixture()`'s plan carries no `previous_image`, so `steps_for_rollback`
    returns nothing and `rollback()` refuses with "no previous release" —
    AFTER `_verify_host_source` already ran and admitted, since the ruling
    puts the gate ahead of that check too, but a refusal there would still
    make this test's own `.rollback(...)` call raise, obscuring the
    assertion below. A plan built WITH a previous image
    (`build_plan(..., previous_image=...)`, the same fixture shape
    `test_deployment_foundation_failure_injection.py::
    test_rollback_actually_restores_the_previous_digest` uses) lets rollback
    actually complete, so the call count is asserted on a genuine admit
    rather than merely on "it didn't raise before the gate"."""
    from dotmac_deployment_foundation.engine.plan import build_plan

    from tests.unit.test_deployment_foundation_execution_binding import (
        RecordingEffects,
    )
    from tests.unit.test_deployment_foundation_failure_injection import (
        OLD_DIGEST,
        load,
    )

    real = run_module.require_host_source
    spy = MagicMock(side_effect=real)
    monkeypatch.setattr(run_module, "require_host_source", spy)

    spec = load()
    effects = RecordingEffects()
    plan = build_plan(spec, previous_image=f"ghcr.io/example/app@{OLD_DIGEST}")
    execution_plan, digest = _plan_and_digest(
        spec, plan, operation="rollback", effects=effects
    )
    executor = Executor(
        spec,
        effects,
        _grant(spec, operation="rollback", execution_plan_digest=digest),
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
        evidence_verifier=AcceptingVerifier(),
        host_source_receipt=_receipt(),
        host_source_metadata=FakeInstall(),
        host_source_probe=_source_tree_digest,
    )
    outcome = executor.rollback(plan, lock=held_lock(spec.product))
    assert outcome.succeeded, outcome.failure
    assert spy.call_count == 1
