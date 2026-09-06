"""A provocation is authorized by Control, or it does not happen.

## What was true before this module

Lane 3 item 8 is `provoked_rollback` — *"Rollback, provoked rather than
simulated"* — and `scripts/lane3_provocation.py` performs it by writing rules
that belong to nobody into `DOCKER-USER` and `INPUT` and arming a condition a
running deployment structurally cannot reconcile. Chains shared with every other
service on the host, broken on purpose.

Reading the tree at `6147618a`: `seed_foreign_rules(runner, *, timeout_seconds)`
and `provoke_apply_failure(effects, *, port)` took no permission of any kind,
`rehearsal.py` contained no grant type, and `authorization.OPERATIONS` was
`("deploy", "rollback")` with no member that names this act. **Every test in
this file fails before the change for the same reason: the symbols it imports —
`RehearsalGrantV1`, `permit_provocation`, `ProvocationPermit` — did not exist,
and the two functions it drives had no `permit` parameter to refuse.**

## The four claims

* a hand-constructed grant, unattested and unsigned, is REFUSED at the
  provocation path;
* a properly verified grant is ACCEPTED and the provocation proceeds — the
  admit control, because a rule observed only refusing is indistinguishable
  from one that refuses everything;
* each binding term refuses on its own, so a caller is told WHICH term
  disagreed;
* the facility issues nothing: there is no route from material to a permit that
  does not pass through an injected verifier.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from dotmac_deployment_foundation.engine.plan import StepKind
from dotmac_deployment_foundation.errors import (
    PreconditionFailed,
    SpecError,
    UnknownFieldError,
)
from dotmac_deployment_foundation.rehearsal_grant import (
    REHEARSAL_GRANT_SCHEMA,
    REHEARSAL_GRANT_VERSION,
    REHEARSAL_PURPOSE,
    ProvocableRefusal,
    ProvocationPermit,
    ProvokedTerminal,
    RehearsalGrantV1,
    VerifiedRehearsalGrant,
    permit_provocation,
    verify_rehearsal_grant,
)

REPO = Path(__file__).resolve().parents[2]

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
PLAN_DIGEST = "sha256:" + "e" * 64
TARGET = "rehearsal-host.dotmac.internal"
STEP = StepKind.APPLY_EXPOSURE.value


def _statement(**overrides: Any) -> dict[str, Any]:
    """The accept-shape, spelled as Control's PR #45 emits it.

    Every key is present. The facility refuses in BOTH directions — a missing
    term bound nothing, an unknown term may carry a rule this version cannot
    evaluate — so a builder that quietly omitted one would test a document
    nobody can send.
    """
    row: dict[str, Any] = {
        "schema": REHEARSAL_GRANT_SCHEMA,
        "version": REHEARSAL_GRANT_VERSION,
        "purpose": REHEARSAL_PURPOSE,
        "grant_id": "rg-0001",
        "single_use_reference": "rg-0001/attempt-1",
        "product_code": "dotmac-starter",
        "target_id": "tgt-9",
        "target_ref": TARGET,
        "environment": "rehearsal",
        "candidate_repository": "michaelayoade/dotmac_starter_mt",
        "candidate_run_id": "33920058598",
        "candidate_artifact_id": "9954731961",
        "execution_plan_digest": PLAN_DIGEST,
        "provocation_refusal": ProvocableRefusal.PLAN_VERIFICATION_REFUSAL.value,
        "provocation_at_step": STEP,
        "expected_terminal": ProvokedTerminal.ROLLED_BACK.value,
        "lease_id": "lease-77",
        "approval_policy_code": "lane3-provocation",
        "approval_policy_version": 1,
        "approval_decision_ref": "decision-12",
        "approval_decision_status": "approved",
        "approved_at": "2026-09-06T11:00:00Z",
        "not_before": "2026-09-06T11:30:00Z",
        "issued_at": "2026-09-06T11:31:00Z",
        "expires_at": "2026-09-06T13:00:00Z",
        "control_version": "0.1.0a13",
        "key_id": "rehearsal-signer-1",
        "algorithm": "ed25519",
        "public_key_fingerprint": "SHA256:" + "f" * 43,
    }
    for key, value in overrides.items():
        if value is _ABSENT:
            row.pop(key, None)
        else:
            row[key] = value
    return row


class _Absent:
    pass


_ABSENT = _Absent()


def _material(**overrides: Any) -> dict[str, Any]:
    return {"statement": _statement(**overrides), "signature": "sig-abcdef"}


class _Verifier:
    """A stand-in for the assembly's verifier. It attests, it does not decide.

    Returns the material unchanged, which is exactly the contract: the PRODUCT
    decides authenticity, and this module decides whether the attested document
    is a structurally complete grant. A verifier that returned something else
    would be defining what a grant IS, which is the collapse the two-step
    exists to prevent.
    """

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def attest_rehearsal(self, material: Any) -> Any:
        self.calls.append(material)
        return material


class _Rejecting:
    def attest_rehearsal(self, material: Any) -> Any:
        raise PreconditionFailed("this signature is not one of Control's")


def _verified(**overrides: Any) -> VerifiedRehearsalGrant:
    return verify_rehearsal_grant(_material(**overrides), verifier=_Verifier())


def _permit(**kw: Any) -> ProvocationPermit:
    terms: dict[str, Any] = {
        "verified": _verified(),
        "refusal": ProvocableRefusal.PLAN_VERIFICATION_REFUSAL,
        "at_step": STEP,
        "target": TARGET,
        "execution_plan_digest": PLAN_DIGEST,
        "now": NOW,
    }
    terms.update(kw)
    return permit_provocation(**terms)


# ── the admit control, FIRST ────────────────────────────────────────────────


def test_a_properly_verified_grant_is_accepted() -> None:
    """The admit. Deliberately the first test in the file.

    A guard observed only refusing is indistinguishable from one that refuses
    everything, and that failure has recurred often enough in this repository
    to be worth putting at the top rather than at the bottom.
    """
    permit = _permit()
    assert permit.refusal is ProvocableRefusal.PLAN_VERIFICATION_REFUSAL
    assert permit.at_step == STEP
    assert permit.target == TARGET
    assert permit.execution_plan_digest == PLAN_DIGEST
    assert permit.lease_id == "lease-77"
    assert permit.single_use_reference == "rg-0001/attempt-1"
    assert permit.expected_terminal is ProvokedTerminal.ROLLED_BACK
    # And the permit answers the point-of-use re-check without raising.
    permit.require(refusal=ProvocableRefusal.PLAN_VERIFICATION_REFUSAL, at_step=STEP)


def test_the_verifier_is_actually_consulted() -> None:
    """Non-vacuity on the admit: the attestation is not decoration.

    Without this, a `verify_rehearsal_grant` that ignored its verifier and
    parsed the material directly would pass every test above.
    """
    verifier = _Verifier()
    verify_rehearsal_grant(_material(), verifier=verifier)
    assert len(verifier.calls) == 1

    with pytest.raises(PreconditionFailed, match="not one of Control"):
        verify_rehearsal_grant(_material(), verifier=_Rejecting())


def test_the_step_the_control_mirror_omits_is_accepted_here() -> None:
    """`apply_exposure` is where item 8's verification refuses, and it is real.

    Control's PR #45 mirrors this facility's `StepKind` as a frozen literal set
    read at `98435a0c`, and that mirror predates `apply_exposure` and
    `restore_exposure`. This side does not mirror: the set is read from
    `engine.plan.StepKind`, which this facility owns. The assertion is that the
    accepted vocabulary is derived rather than transcribed, so it cannot go
    stale the way a mirror does.
    """
    assert STEP in {kind.value for kind in StepKind}
    assert _permit().at_step == StepKind.APPLY_EXPOSURE.value


# ── the refusal the boundary is named for ───────────────────────────────────


def test_a_hand_constructed_grant_is_refused_at_the_provocation_path() -> None:
    """No witness, no verifier, no signature — and no way through.

    `RehearsalGrantV1` is deliberately constructible, exactly as
    `AuthorizationReceipt` is: it is a document, and holding one proves the keys
    were right and nothing else. What it is not is permission. Offering it where
    a `VerifiedRehearsalGrant` belongs is the shape a caller reaches for when
    they have a JSON file and no verifier, and it must refuse.
    """
    forged = RehearsalGrantV1(
        grant_id="rg-forged",
        single_use_reference="rg-forged/1",
        product_code="dotmac-starter",
        target_id="tgt-9",
        target_ref=TARGET,
        environment="rehearsal",
        candidate_repository="michaelayoade/dotmac_starter_mt",
        candidate_run_id="1",
        candidate_artifact_id="1",
        execution_plan_digest=PLAN_DIGEST,
        refusal=ProvocableRefusal.PLAN_VERIFICATION_REFUSAL,
        at_step=STEP,
        lease_id="lease-77",
        approval_policy_code="lane3-provocation",
        approval_policy_version=1,
        approval_decision_ref="decision-12",
        approval_decision_status="approved",
        approved_at=NOW - timedelta(hours=1),
        not_before=NOW - timedelta(hours=1),
        issued_at=NOW - timedelta(minutes=30),
        expires_at=NOW + timedelta(hours=1),
        control_version="0.1.0a13",
        key_id="whoever",
        algorithm="ed25519",
        public_key_fingerprint="SHA256:" + "0" * 43,
        signature="",
    )
    with pytest.raises(PreconditionFailed, match="VerifiedRehearsalGrant"):
        permit_provocation(
            verified=forged,  # type: ignore[arg-type]
            refusal=ProvocableRefusal.PLAN_VERIFICATION_REFUSAL,
            at_step=STEP,
            target=TARGET,
            execution_plan_digest=PLAN_DIGEST,
            now=NOW,
        )


def test_neither_witness_can_be_forged() -> None:
    """The two seams, both unconstructable without their issuer.

    This does not stop someone importing the private witness; nothing in Python
    can. It makes the bypass one grep and one obviously-wrong import rather than
    an omission that reads like ordinary code — the argument `authorization.py`
    already makes and this module inherits rather than re-litigates.
    """
    with pytest.raises(PreconditionFailed, match="verify_rehearsal_grant"):
        VerifiedRehearsalGrant(object(), grant=_verified().grant)  # type: ignore[arg-type]

    with pytest.raises(PreconditionFailed, match="permit_provocation"):
        ProvocationPermit(
            object(),  # type: ignore[arg-type]
            refusal=ProvocableRefusal.PLAN_VERIFICATION_REFUSAL,
            at_step=STEP,
            target=TARGET,
            execution_plan_digest=PLAN_DIGEST,
            lease_id="lease-77",
            single_use_reference="x",
            grant=_verified().grant,
        )


# ── each binding term refuses on its own ────────────────────────────────────


def test_a_deployment_authorization_is_not_rehearsal_authority() -> None:
    """Refused on `schema`, before any field is read.

    The failure this prevents is the one Control's own module docstring names:
    a production deployment authorization issued for an act whose entire purpose
    is to fail, with every link in the chain correct and the SUBJECT wrong.
    """
    with pytest.raises(SpecError, match="is not"):
        verify_rehearsal_grant(
            _material(schema="DeploymentAuthorization.v1"), verifier=_Verifier()
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("purpose", "deployment_authorization"),
        ("version", 2),
        ("approval_policy_version", 0),
        ("provocation_refusal", "some_other_refusal"),
        ("provocation_at_step", "reticulate_splines"),
        ("expected_terminal", "succeeded"),
    ],
)
def test_a_statement_term_this_version_cannot_honour_is_refused(
    field: str, value: Any
) -> None:
    """One refusal per term. A grant read partly is a grant not read."""
    with pytest.raises(SpecError):
        verify_rehearsal_grant(_material(**{field: value}), verifier=_Verifier())


def test_an_unknown_or_missing_statement_key_is_refused() -> None:
    """Strict in both directions, like `AuthorizationReceipt.from_document`."""
    with pytest.raises(UnknownFieldError, match="unknown field"):
        verify_rehearsal_grant(
            _material(also_authorizes_deploy=True), verifier=_Verifier()
        )
    with pytest.raises(SpecError, match="missing"):
        verify_rehearsal_grant(_material(lease_id=_ABSENT), verifier=_Verifier())


def test_the_terminal_cannot_disagree_with_the_refusal() -> None:
    """Re-derived, never read back.

    The document carries `expected_terminal` so a receiver need not reach into
    Control's tables. Trusting the carried value would let one grant name a
    refusal and a terminal that contradict each other, which is the only thing
    the grant is for.
    """
    with pytest.raises(SpecError, match="must end in"):
        verify_rehearsal_grant(
            _material(expected_terminal="rolled_forward"), verifier=_Verifier()
        )


def test_a_grant_outside_its_window_is_refused_for_being_outside_it() -> None:
    """Time first, before any equality check.

    If every term agrees, an expired grant must still refuse; and it must refuse
    for being expired rather than for whichever digest happens to disagree.
    """
    with pytest.raises(PreconditionFailed, match="expired"):
        _permit(now=datetime(2026, 9, 6, 13, 0, 1, tzinfo=UTC))
    with pytest.raises(PreconditionFailed, match="not valid until"):
        _permit(now=datetime(2026, 9, 6, 11, 0, tzinfo=UTC))


def test_a_spent_replay_coordinate_is_refused() -> None:
    """A re-presentable grant is a second execution authority.

    The consumed set is supplied by the CALLER because this facility holds no
    store: it refuses a coordinate it is TOLD was spent and cannot itself know.
    That region is unmonitored rather than covered, and the module says so.
    """
    with pytest.raises(PreconditionFailed, match="already been spent"):
        _permit(consumed_references=["rg-0001/attempt-1"])
    # The near-miss: a DIFFERENT spent coordinate must not refuse this one. A
    # replay check that refused whenever the set was non-empty would pass the
    # assertion above and stop every rehearsal after the first, anywhere.
    survivor = _permit(consumed_references=["rg-0002/attempt-1"])
    assert survivor.single_use_reference == "rg-0001/attempt-1"


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"target": "production.dotmac.io"}, "authorizes target"),
        ({"at_step": StepKind.MIGRATE.value}, "provocation at"),
        ({"execution_plan_digest": "sha256:" + "1" * 64}, "execution plan"),
    ],
)
def test_a_grant_that_names_something_else_is_not_a_grant_for_this(
    kw: dict[str, Any], match: str
) -> None:
    """Binding by COMPARISON, never by presence.

    `target` is stated by the caller independently of the grant. Deriving it
    from `grant.target_ref` would compare the grant with itself and pass for
    every input, which is the shape of a check that has stopped checking.
    """
    with pytest.raises(PreconditionFailed, match=match):
        _permit(**kw)


def test_the_point_of_use_recheck_refuses_a_different_act() -> None:
    permit = _permit()
    with pytest.raises(PreconditionFailed, match="provocation at"):
        permit.require(
            refusal=ProvocableRefusal.PLAN_VERIFICATION_REFUSAL,
            at_step=StepKind.MIGRATE.value,
        )


# ── the provocation harness itself ──────────────────────────────────────────


def _provocation_module() -> Any:
    """Load `scripts/lane3_provocation.py`, REGISTERING it before executing it.

    The `sys.modules`-first ordering is this repository's known dataclass /
    `slots=True` loader hazard, fixed once already in `d2a2e9e0`. Copied
    deliberately rather than imported, because `scripts/` is not on the path.
    """
    path = REPO / "scripts" / "lane3_provocation.py"
    spec = importlib.util.spec_from_file_location("_lane3_provocation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _RecordingEffects:
    def __init__(self) -> None:
        self.replaced: list[tuple[str, str, int]] = []

    def replace_rules(self, family: str, chain: str, rules: Any) -> None:
        self.replaced.append((family, chain, len(tuple(rules))))


class _RefusingRunner:
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "the runner was reached, so the permit check did not refuse first. "
            "A host mutation attempted before authorization is the whole defect"
        )


def test_the_provocation_refuses_without_a_permit() -> None:
    """`provoke_apply_failure` and `seed_foreign_rules` take a permit or stop.

    Before the change these had no `permit` parameter at all, so this call
    signature did not exist and the mutation happened on the strength of being
    called.
    """
    provocation = _provocation_module()
    effects = _RecordingEffects()

    with pytest.raises(provocation.ProvocationError, match="ProvocationPermit"):
        provocation.provoke_apply_failure(effects, port=8443, permit=None)
    assert effects.replaced == [], "a rule was written despite the refusal"

    with pytest.raises(provocation.ProvocationError, match="ProvocationPermit"):
        provocation.seed_foreign_rules(_RefusingRunner(), permit=object())


def test_the_provocation_proceeds_on_a_real_permit() -> None:
    """The admit control at the act itself.

    Refusing is only half. If the harness refused every permit, the two tests
    above would still pass and item 8 could never run.
    """
    provocation = _provocation_module()
    effects = _RecordingEffects()

    rule = provocation.provoke_apply_failure(effects, port=8443, permit=_permit())

    assert rule.family == "ipv6"
    assert rule.host_port == 8443
    assert effects.replaced == [("ipv6", rule.chain, 1)]


def test_a_permit_for_another_step_does_not_arm_this_provocation() -> None:
    """The harness re-checks the pair it is about, not merely that a permit exists.

    A permit is not a token. `_require_permit` compares the refusal and the step
    against the constants this file declares, so a grant for a provocation
    elsewhere cannot arm this one.
    """
    provocation = _provocation_module()
    effects = _RecordingEffects()
    elsewhere = _permit(
        verified=_verified(provocation_at_step=StepKind.MIGRATE.value),
        at_step=StepKind.MIGRATE.value,
    )
    with pytest.raises(PreconditionFailed, match="provocation at"):
        provocation.provoke_apply_failure(effects, port=8443, permit=elsewhere)
    assert effects.replaced == []


# ── the binding slot ────────────────────────────────────────────────────────


def test_an_authorization_verifier_cannot_fill_the_rehearsal_slot() -> None:
    """One key answering two questions can be used to contradict itself.

    The protocol's method is `attest_rehearsal`, not `attest`, precisely so a
    deployment verifier fails the structural check here instead of satisfying
    it by accident.
    """
    from dotmac_deployment_foundation.execution_bindings import ExecutionBindings

    class _DeploymentVerifier:
        def attest(self, material: Any) -> Any:
            return material

    with pytest.raises(SpecError, match="attest_rehearsal"):
        ExecutionBindings(
            provider="assembly",
            rehearsal_grant_verifier=_DeploymentVerifier(),  # type: ignore[arg-type]
        )

    # The admit: a real rehearsal verifier IS accepted, and on its own — the
    # field satisfies the "carries no injectable at all" refusal by itself, so
    # an assembly can inject rehearsal authority without also injecting a
    # provider or a deployment verifier it does not have.
    accepted = ExecutionBindings(
        provider="assembly", rehearsal_grant_verifier=_Verifier()
    )
    assert accepted.rehearsal_grant_verifier is not None
    assert accepted.authorization_verifier is None
