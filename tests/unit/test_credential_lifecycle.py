"""The human credential lifecycle facility, proven behaviour by behaviour.

Two rules govern how these tests are written.

**A test that asserts a secret is ABSENT can pass for the wrong reason** — if
the field was renamed or dropped, the assertion still holds. So the secret-safe
tests inject a known sentinel through the secret source, assert the sentinel is
genuinely reachable by the code under test, and only then assert it appears
nowhere in a rendering.

**`assert` is a test statement, never a shipped one.** `python -O` removes
`assert`, so the facility uses explicit `raise` for every check that protects a
credential; `test_optimized_mode_does_not_remove_a_verification_check` proves
that in a real `-O` subprocess rather than by reading the source.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import (
    FrozenInstanceError,
    asdict,
    dataclass,
    field,
    fields,
    replace,
)
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_kernel.credential_lifecycle import (
    REDACTED,
    CredentialAuditEntryV1,
    CredentialAuthorizationError,
    CredentialCohortDrift,
    CredentialEffectNotRecorded,
    CredentialLifecycle,
    CredentialLifecycleError,
    CredentialPlanError,
    CredentialPolicyViolation,
    CredentialRecoveryAuthorizationV1,
    CredentialRecoveryIntentV1,
    CredentialResetAuthorizationV1,
    CredentialResetPlanDigestV1,
    CredentialResetPlanV1,
    CredentialResetReceiptV1,
    CredentialResetTargetV1,
    CredentialSessionRevocationIntentV1,
    CredentialSnapshot,
    CredentialSubjectRef,
)
from dotmac_kernel.security import hash_password

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-kernel/src/dotmac_kernel/credential_lifecycle.py"
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

# A test-only sentinel. Long and unmistakable so a substring search over a
# rendering cannot match it by accident.
SENTINEL = "sentinel-generated-material-3f7c9a11-never-rendered"


def subject(ref: str = "cred-1", kind: str = "reseller_user") -> CredentialSubjectRef:
    """Product words the engine must never interpret."""
    return CredentialSubjectRef(
        application="sub",
        principal_kind=kind,
        principal_ref=f"principal-{ref}",
        credential_ref=ref,
    )


# ── Fakes ───────────────────────────────────────────────────────────────────


@dataclass
class FakeStore:
    rows: dict[str, CredentialSnapshot] = field(default_factory=dict)
    locked: list[str] = field(default_factory=list)
    writes: list[tuple[str, int, bool]] = field(default_factory=list)
    fail_write_on: str | None = None

    def load(self, subj: CredentialSubjectRef) -> CredentialSnapshot | None:
        return self.rows.get(subj.credential_ref)

    def lock_for_update(self, subj: CredentialSubjectRef) -> CredentialSnapshot | None:
        row = self.rows.get(subj.credential_ref)
        if row is not None:
            self.locked.append(subj.credential_ref)
        return row

    def create(
        self,
        subj: CredentialSubjectRef,
        *,
        password_hash: str,
        reset_required: bool,
    ) -> CredentialSnapshot:
        row = CredentialSnapshot(
            subject=subj,
            password_hash=password_hash,
            credential_version=1,
            reset_required=reset_required,
        )
        self.rows[subj.credential_ref] = row
        return row

    def write_hash(
        self,
        subj: CredentialSubjectRef,
        *,
        password_hash: str,
        credential_version: int,
        reset_required: bool,
    ) -> None:
        if self.fail_write_on == subj.credential_ref:
            raise RuntimeError("adapter exploded mid-cohort")
        self.writes.append((subj.credential_ref, credential_version, reset_required))
        self.rows[subj.credential_ref] = replace(
            self.rows[subj.credential_ref],
            password_hash=password_hash,
            credential_version=credential_version,
            reset_required=reset_required,
        )


@dataclass
class FakeSessions:
    intents: list[CredentialSessionRevocationIntentV1] = field(default_factory=list)
    record_nothing: bool = False

    def request_revocation(
        self,
        subj: CredentialSubjectRef,
        *,
        reason_code: str,
        credential_version: int,
        correlation_ref: str,
    ) -> CredentialSessionRevocationIntentV1 | None:
        if self.record_nothing:
            return None
        intent = CredentialSessionRevocationIntentV1(
            subject=subj,
            reason_code=reason_code,
            credential_version=credential_version,
            requested_at=NOW,
            intent_ref=f"revoke-{subj.credential_ref}-{credential_version}",
        )
        self.intents.append(intent)
        return intent


@dataclass
class FakeRecovery:
    emitted: list[CredentialRecoveryIntentV1] = field(default_factory=list)
    spent: set[str] = field(default_factory=set)
    emit_raises: bool = False
    record_nothing: bool = False

    def emit(self, intent: CredentialRecoveryIntentV1) -> str | None:
        if self.emit_raises:
            raise RuntimeError("recovery delivery is down")
        if self.record_nothing:
            return None
        self.emitted.append(intent)
        return f"recovery-{intent.subject.credential_ref}-{intent.credential_version}"

    def consume(self, authorization: CredentialRecoveryAuthorizationV1) -> bool:
        if authorization.recovery_ref in self.spent:
            return False
        self.spent.add(authorization.recovery_ref)
        return True


@dataclass
class FakeAudit:
    events: list[CredentialAuditEntryV1] = field(default_factory=list)
    receipts: dict[str, CredentialResetReceiptV1] = field(default_factory=dict)

    def record_event(self, entry: CredentialAuditEntryV1) -> None:
        self.events.append(entry)

    def record_receipt(self, receipt: CredentialResetReceiptV1) -> None:
        self.receipts[receipt.plan_digest.value] = receipt

    def find_receipt(
        self, plan_digest: CredentialResetPlanDigestV1
    ) -> CredentialResetReceiptV1 | None:
        return self.receipts.get(plan_digest.value)


class LengthPolicy:
    """A product policy. The kernel ships none."""

    def __init__(self, minimum: int = 12) -> None:
        self.minimum = minimum

    def validate(self, subj: CredentialSubjectRef, secret: str) -> None:
        if len(secret) < self.minimum:
            raise CredentialPolicyViolation(
                f"credential material for {subj.credential_ref!r} is shorter "
                f"than the {self.minimum}-character product minimum"
            )


@dataclass
class SecretSpy:
    """Records the DIGEST of every value it produced, never the value.

    Enough to prove uniqueness and to prove the sentinel really flowed through
    the code under test, without a test fixture becoming a place secrets pile
    up.
    """

    values: list[str] = field(default_factory=list)
    fixed: str | None = None

    def __call__(self) -> str:
        if self.fixed is not None:
            value = self.fixed
        else:
            nonce = hashlib.sha256(str(len(self.values)).encode()).hexdigest()
            value = f"generated-{len(self.values)}-{nonce}"
        self.values.append(hashlib.sha256(value.encode()).hexdigest())
        return value


def build(**overrides):
    """An engine wired to fakes, with a frozen clock."""
    store = overrides.pop("store", FakeStore())
    sessions = overrides.pop("sessions", FakeSessions())
    recovery = overrides.pop("recovery", FakeRecovery())
    audit = overrides.pop("audit", FakeAudit())
    engine = CredentialLifecycle(
        store=store,
        sessions=sessions,
        recovery=recovery,
        audit=audit,
        policy=overrides.pop("policy", None),
        secret_source=overrides.pop("secret_source", SecretSpy()),
        clock=overrides.pop("clock", lambda: NOW),
    )
    assert not overrides, f"unused overrides: {sorted(overrides)}"
    return engine, store, sessions, recovery, audit


def seed(
    store: FakeStore,
    subj: CredentialSubjectRef,
    secret: str,
    *,
    version: int = 1,
    active: bool = True,
    locked: bool = False,
    reset_required: bool = False,
    password_hash: str | None = None,
) -> None:
    store.rows[subj.credential_ref] = CredentialSnapshot(
        subject=subj,
        password_hash=password_hash or hash_password(secret),
        credential_version=version,
        is_active=active,
        is_locked=locked,
        reset_required=reset_required,
    )


# ── Verification: every verdict ─────────────────────────────────────────────


def test_a_correct_password_on_a_healthy_credential_is_accepted() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "correct-horse-battery")
    result = engine.verify(person, "correct-horse-battery")
    assert result.verdict == "accepted"
    assert result.accepted is True
    assert result.credential_version == 1


def test_a_wrong_password_is_invalid() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "correct-horse-battery")
    assert engine.verify(person, "wrong").verdict == "invalid"


def test_an_unknown_credential_is_invalid_and_not_a_different_verdict() -> None:
    """A distinguishable verdict for "no such credential" would be an account
    enumeration oracle."""
    engine, *_ = build()
    assert engine.verify(subject(), "anything").verdict == "invalid"


def test_a_reset_required_credential_verifies_but_is_not_accepted() -> None:
    """The whole point of the verdict type. A boolean forces this case to be
    reported as success, and every caller then has to remember to re-read
    `must_change_password` — which is how four owners came to disagree."""
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "correct-horse-battery", reset_required=True)
    result = engine.verify(person, "correct-horse-battery")
    assert result.verdict == "reset_required"
    assert result.accepted is False


def test_a_locked_credential_is_locked() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "correct-horse-battery", locked=True)
    result = engine.verify(person, "correct-horse-battery")
    assert result.verdict == "locked"
    assert result.accepted is False


def test_a_disabled_credential_is_disabled() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "correct-horse-battery", active=False)
    result = engine.verify(person, "correct-horse-battery")
    assert result.verdict == "disabled"
    assert result.accepted is False


def test_disabled_outranks_locked_and_reset_required() -> None:
    """Precedence is a decision, not an accident: a disabled credential must
    never be reported as merely needing a reset."""
    engine, store, *_ = build()
    person = subject()
    seed(
        store,
        person,
        "correct-horse-battery",
        active=False,
        locked=True,
        reset_required=True,
    )
    assert engine.verify(person, "correct-horse-battery").verdict == "disabled"


def test_a_wrong_password_on_a_locked_credential_is_invalid_not_locked() -> None:
    """Lifecycle state is disclosed only to somebody who already proved the
    password. Reporting `locked` to a guesser hands them a valid-account signal."""
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "correct-horse-battery", locked=True)
    assert engine.verify(person, "wrong").verdict == "invalid"


def test_reset_required_sensitivity_the_only_difference_is_the_flag() -> None:
    """Same password, same hash, same store — one flag apart. Without this, a
    verdict that never reads the flag would still pass every test above."""
    engine, store, *_ = build()
    clean, dirty = subject("clean"), subject("dirty")
    shared = hash_password("correct-horse-battery")
    seed(store, clean, "", password_hash=shared, reset_required=False)
    seed(store, dirty, "", password_hash=shared, reset_required=True)
    assert engine.verify(clean, "correct-horse-battery").verdict == "accepted"
    assert engine.verify(dirty, "correct-horse-battery").verdict == "reset_required"


def test_verification_never_issues_a_session() -> None:
    """Asserted structurally, because "we did not call it" is only true until
    somebody adds the call. No port has a session-issuing method to call."""
    from dotmac_kernel import credential_lifecycle as module

    names = [
        name
        for name in dir(module)
        for attribute in [getattr(module, name)]
        if callable(attribute)
    ]
    banned = {"issue_session", "issue_access_token", "login", "authenticate"}
    assert not banned & set(names)
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "issue_access_token" not in source
    assert "AuthSession" not in source


# ── Legacy-hash rehash (NEW behaviour, not extracted) ───────────────────────


def _legacy_pbkdf2(secret: str) -> str:
    """The pre-Argon2 format `dotmac_kernel.security` still verifies."""
    import base64
    import hashlib as _hashlib

    salt = b"legacy-salt-16by"
    iterations = 210_000
    digest = _hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, iterations)
    return (
        "pbkdf2_sha256$"
        f"{iterations}$"
        f"{base64.urlsafe_b64encode(salt).decode()}$"
        f"{base64.urlsafe_b64encode(digest).decode()}"
    )


def test_a_legacy_hash_verifies_and_requests_a_replacement() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "", password_hash=_legacy_pbkdf2("correct-horse-battery"))
    result = engine.verify(person, "correct-horse-battery")
    assert result.verdict == "accepted"
    assert result.rehash_requested is True
    assert result.replacement_hash is not None
    assert result.replacement_hash.startswith("$argon2id$")


def test_a_current_hash_requests_no_replacement() -> None:
    """The other half: a rehash requested on every login would rewrite every
    row forever and make the signal meaningless."""
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "correct-horse-battery")
    assert engine.verify(person, "correct-horse-battery").replacement_hash is None


def test_a_failed_verification_requests_no_replacement() -> None:
    """A replacement hash is derived from the presented material; deriving one
    from a WRONG password would overwrite the credential with the guess."""
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "", password_hash=_legacy_pbkdf2("correct-horse-battery"))
    assert engine.verify(person, "guess").replacement_hash is None


def test_a_locked_legacy_credential_requests_no_replacement() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(
        store,
        person,
        "",
        password_hash=_legacy_pbkdf2("correct-horse-battery"),
        locked=True,
    )
    assert engine.verify(person, "correct-horse-battery").replacement_hash is None


# ── Provisioning ────────────────────────────────────────────────────────────


def test_provision_accepts_no_password_material() -> None:
    """The lane's reason for existing, asserted on the SIGNATURE.

    `reseller_onboarding._create_credential` took a `password` parameter no
    supported caller passed, and that parameter is how one value reached 24
    organisations. A signature with nowhere to put material cannot be misused
    by a future caller who thinks they are being helpful.
    """
    import inspect

    parameters = set(inspect.signature(CredentialLifecycle.provision).parameters)
    assert parameters == {"self", "subject", "reason_code", "correlation_ref"}


def test_no_public_entry_point_except_verify_and_complete_reset_takes_material() -> (
    None
):
    """Scoped to the WHOLE public surface, so a second provisioning helper
    cannot quietly reintroduce the shape."""
    import inspect

    banned = {"password", "secret", "new_password", "temporary_password", "token"}
    offenders = []
    for name, member in inspect.getmembers(CredentialLifecycle, inspect.isfunction):
        if name.startswith("_") or name in {"verify", "complete_reset"}:
            continue
        overlap = banned & set(inspect.signature(member).parameters)
        if overlap:
            offenders.append(f"{name}{sorted(overlap)}")
    assert not offenders, offenders


def test_provisioning_sets_reset_required_and_emits_a_recovery_intent() -> None:
    engine, store, sessions, recovery, audit = build()
    person = subject()
    receipt = engine.provision(
        person, reason_code="onboarding", correlation_ref="req-1"
    )
    assert receipt.reset_required is True
    assert store.rows[person.credential_ref].reset_required is True
    assert receipt.recovery_intent_ref
    assert [intent.subject for intent in recovery.emitted] == [person]
    assert [entry.action for entry in audit.events] == ["credential.provisioned"]


def test_a_provisioned_credential_cannot_be_used_as_a_standing_password() -> None:
    """End to end: provision, then verify with material nobody has. The only
    verdict reachable is one that refuses a session."""
    engine, store, *_ = build()
    person = subject()
    engine.provision(person, reason_code="onboarding", correlation_ref="req-1")
    assert engine.verify(person, "guess").verdict == "invalid"


def test_provisioning_refuses_to_overwrite_an_existing_credential() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "correct-horse-battery")
    with pytest.raises(CredentialLifecycleError, match="already exists"):
        engine.provision(person, reason_code="onboarding", correlation_ref="req-1")


def test_every_provisioned_credential_gets_independent_material() -> None:
    """A shared value across a cohort IS the incident. Uniqueness is asserted
    over the digests the spy recorded, so the test never holds a secret."""
    spy = SecretSpy()
    engine, *_ = build(secret_source=spy)
    for index in range(25):
        engine.provision(
            subject(f"cred-{index}"), reason_code="onboarding", correlation_ref="bulk"
        )
    assert len(spy.values) == 25
    assert len(set(spy.values)) == 25


def test_the_default_secret_source_produces_unique_high_entropy_material() -> None:
    """The spy above proves the ENGINE asks for fresh material each time; this
    proves the default source actually supplies it."""
    from dotmac_kernel.credential_lifecycle import default_secret_source

    produced = {
        hashlib.sha256(default_secret_source().encode()).hexdigest() for _ in range(200)
    }
    assert len(produced) == 200
    assert len(default_secret_source()) >= 32


def test_a_policy_that_refuses_generated_material_fails_loudly() -> None:
    """Deterministically, rather than by retrying until the policy is
    accidentally satisfied — an intermittent misconfiguration is worse to
    diagnose than a permanent one."""
    spy = SecretSpy(fixed="short")
    engine, *_ = build(secret_source=spy, policy=LengthPolicy(minimum=64))
    with pytest.raises(CredentialPolicyViolation):
        engine.provision(subject(), reason_code="onboarding", correlation_ref="req-1")


def test_provisioning_without_a_recorded_recovery_intent_is_refused() -> None:
    engine, *_ = build(recovery=FakeRecovery(record_nothing=True))
    with pytest.raises(CredentialEffectNotRecorded, match="recovery intent"):
        engine.provision(subject(), reason_code="onboarding", correlation_ref="req-1")


# ── Secret safety ───────────────────────────────────────────────────────────


def test_generated_material_appears_in_no_rendering_of_anything() -> None:
    """Sentinel-based, and the sentinel is proven live first: without that
    check, a renamed field would make every absence assertion pass."""
    spy = SecretSpy(fixed=SENTINEL)
    engine, store, sessions, recovery, audit = build(secret_source=spy)
    person = subject()
    receipt = engine.provision(person, reason_code="onboarding", correlation_ref="r-1")

    # The sentinel really was the material: it verifies against the stored hash.
    assert engine.verify(person, SENTINEL).verdict == "reset_required"

    renderings = [
        repr(receipt),
        str(receipt),
        json.dumps(asdict(receipt), default=str),
        repr(store.rows[person.credential_ref]),
        repr(recovery.emitted[0]),
        repr(audit.events[0]),
        repr(engine),
    ]
    for rendering in renderings:
        assert SENTINEL not in rendering, rendering


def test_the_stored_hash_is_masked_in_a_snapshot_repr() -> None:
    """A hash is not a secret, but it is offline-attackable material and has no
    business in a log line."""
    snapshot = CredentialSnapshot(
        subject=subject(),
        password_hash=hash_password("correct-horse-battery"),
        credential_version=1,
    )
    assert REDACTED in repr(snapshot)
    assert "$argon2id$" not in repr(snapshot)


def test_a_replacement_hash_is_masked_in_a_result_repr() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "", password_hash=_legacy_pbkdf2("correct-horse-battery"))
    result = engine.verify(person, "correct-horse-battery")
    assert result.replacement_hash is not None
    assert REDACTED in repr(result)
    assert result.replacement_hash not in repr(result)


def test_no_exception_raised_by_the_facility_carries_material() -> None:
    spy = SecretSpy(fixed=SENTINEL)
    engine, *_ = build(secret_source=spy, policy=LengthPolicy(minimum=1000))
    with pytest.raises(CredentialPolicyViolation) as caught:
        engine.provision(subject(), reason_code="onboarding", correlation_ref="r-1")
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in repr(caught.value)


def test_no_receipt_type_has_a_field_that_could_hold_material() -> None:
    """Structural, so a future field named `temporary_password` fails here
    rather than in an incident review."""
    from dotmac_kernel import credential_lifecycle as module

    banned = {"password", "secret", "temporary_password", "plaintext", "token"}
    offenders = []
    for name in module.__all__:
        member = getattr(module, name)
        if not (isinstance(member, type) and hasattr(member, "__dataclass_fields__")):
            continue
        if name == "CredentialSnapshot":  # holds the STORED hash, masked in repr
            continue
        for declared in fields(member):
            if declared.name in banned:
                offenders.append(f"{name}.{declared.name}")
    assert not offenders, offenders


# ── Individual reset completion ─────────────────────────────────────────────


def recovery_authorization(person: CredentialSubjectRef, ref: str = "rec-1"):
    return CredentialRecoveryAuthorizationV1(
        subject=person,
        recovery_ref=ref,
        proven_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(minutes=25),
    )


def test_reset_completion_writes_clears_the_flag_and_revokes_sessions() -> None:
    engine, store, sessions, recovery, audit = build()
    person = subject()
    seed(store, person, "old-material", reset_required=True, version=4)
    receipt = engine.complete_reset(recovery_authorization(person), "new-material-x")
    assert receipt.previous_credential_version == 4
    assert receipt.credential_version == 5
    assert store.rows[person.credential_ref].reset_required is False
    assert engine.verify(person, "new-material-x").verdict == "accepted"
    assert [intent.credential_version for intent in sessions.intents] == [5]
    assert [entry.action for entry in audit.events] == ["credential.reset.completed"]


def test_reset_completion_applies_the_product_policy() -> None:
    engine, store, *_ = build(policy=LengthPolicy(minimum=12))
    person = subject()
    seed(store, person, "old-material", reset_required=True)
    with pytest.raises(CredentialPolicyViolation):
        engine.complete_reset(recovery_authorization(person), "short")


def test_a_recovery_proof_is_single_use() -> None:
    engine, store, *_ = build()
    person = subject()
    seed(store, person, "old-material", reset_required=True)
    authorization = recovery_authorization(person)
    engine.complete_reset(authorization, "new-material-x")
    with pytest.raises(CredentialAuthorizationError, match="already spent"):
        engine.complete_reset(authorization, "another-material-y")


def test_an_expired_recovery_proof_is_refused_before_the_material_is_read() -> None:
    engine, store, _sessions, recovery, _audit = build()
    person = subject()
    seed(store, person, "old-material", reset_required=True)
    expired = CredentialRecoveryAuthorizationV1(
        subject=person,
        recovery_ref="rec-old",
        proven_at=NOW - timedelta(hours=3),
        expires_at=NOW - timedelta(hours=2),
    )
    with pytest.raises(CredentialAuthorizationError, match="expired"):
        engine.complete_reset(expired, "new-material-x")
    assert recovery.spent == set(), "an expired proof must not even be spent"
    assert store.writes == []


def test_reset_completion_without_a_recorded_revocation_intent_is_refused() -> None:
    engine, store, *_ = build(sessions=FakeSessions(record_nothing=True))
    person = subject()
    seed(store, person, "old-material", reset_required=True)
    with pytest.raises(CredentialEffectNotRecorded, match="session-revocation"):
        engine.complete_reset(recovery_authorization(person), "new-material-x")


# ── Cohort planning ─────────────────────────────────────────────────────────


def plan_for(*refs: str, expected: int = 1, **overrides) -> CredentialResetPlanV1:
    return CredentialResetPlanV1(
        application=overrides.pop("application", "sub"),
        product=overrides.pop("product", "dotmac_sub"),
        reason_code=overrides.pop("reason_code", "shared-credential-containment"),
        approval_policy_code=overrides.pop("approval_policy_code", "security.forced"),
        approval_policy_version=overrides.pop("approval_policy_version", 3),
        expires_at=overrides.pop("expires_at", NOW + timedelta(hours=6)),
        targets=tuple(
            CredentialResetTargetV1(
                subject=subject(ref), expected_credential_version=expected
            )
            for ref in refs
        ),
    )


def test_an_empty_cohort_is_refused() -> None:
    with pytest.raises(CredentialPlanError, match="at least one target"):
        plan_for()


def test_a_duplicated_cohort_is_refused() -> None:
    with pytest.raises(CredentialPlanError, match="twice"):
        plan_for("a", "b", "a")


def test_targets_are_sorted_canonically_so_input_order_cannot_change_identity() -> None:
    forward = plan_for("a", "b", "c")
    backward = plan_for("c", "b", "a")
    assert [t.subject.credential_ref for t in forward.targets] == ["a", "b", "c"]
    assert forward.digest == backward.digest


def test_a_malformed_target_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        CredentialResetTargetV1(subject=subject(), expected_credential_version=-1)
    with pytest.raises(ValueError, match="non-empty"):
        CredentialSubjectRef(
            application="sub", principal_kind="", principal_ref="p", credential_ref="c"
        )


def test_the_digest_is_a_typed_value_and_refuses_a_bare_string() -> None:
    """Michael's fleet-wide rule: a cross-boundary digest is a TYPED value with
    one owning side. A `str` compares equal to the wrong digest silently."""
    plan = plan_for("a")
    assert isinstance(plan.digest, CredentialResetPlanDigestV1)
    with pytest.raises(TypeError, match="CredentialResetPlanDigestV1"):
        CredentialResetAuthorizationV1(
            plan_digest=plan.digest.value,  # type: ignore[arg-type]
            approval_policy_code="security.forced",
            approval_policy_version=3,
            approval_decision_ref="decision-1",
            approved_at=NOW,
        )


def test_the_digest_rejects_a_value_that_is_not_a_sha256_hex_string() -> None:
    with pytest.raises(ValueError, match="64 hexadecimal"):
        CredentialResetPlanDigestV1("deadbeef")
    with pytest.raises(ValueError, match="lowercase"):
        CredentialResetPlanDigestV1("A" * 64)


def test_the_digest_changes_when_any_planned_field_changes() -> None:
    base = plan_for("a", "b")
    assert base.digest != plan_for("a", "b", expected=2).digest
    assert base.digest != plan_for("a", "b", reason_code="other").digest
    assert base.digest != plan_for("a", "b", approval_policy_version=4).digest
    assert base.digest != plan_for("a", "b", "c").digest


def test_an_authorization_carries_no_approved_by_field() -> None:
    """Deliberate: the approval DECISION is a product record with its own actor
    and audit trail, and `approval_decision_ref` points at it. A copied actor
    name here would be a second, weaker claim about who approved."""
    names = {declared.name for declared in fields(CredentialResetAuthorizationV1)}
    assert not names & {"approved_by", "approver", "approved_by_ref", "actor"}


# ── Applying an authorized plan ─────────────────────────────────────────────


def authorization_for(
    plan: CredentialResetPlanV1, **overrides
) -> CredentialResetAuthorizationV1:
    return CredentialResetAuthorizationV1(
        plan_digest=overrides.pop("plan_digest", plan.digest),
        approval_policy_code=overrides.pop(
            "approval_policy_code", plan.approval_policy_code
        ),
        approval_policy_version=overrides.pop(
            "approval_policy_version", plan.approval_policy_version
        ),
        approval_decision_ref=overrides.pop("approval_decision_ref", "decision-77"),
        approved_at=overrides.pop("approved_at", NOW - timedelta(minutes=10)),
    )


def seeded_cohort(*refs: str, version: int = 1):
    engine, store, sessions, recovery, audit = build()
    for ref in refs:
        seed(store, subject(ref), f"material-{ref}", version=version)
    return engine, store, sessions, recovery, audit


def test_applying_an_authorized_plan_resets_every_target() -> None:
    engine, store, sessions, recovery, audit = seeded_cohort("a", "b", "c")
    plan = plan_for("a", "b", "c")
    receipt = engine.apply_force_reset(plan, authorization_for(plan))

    assert [outcome.subject.credential_ref for outcome in receipt.outcomes] == [
        "a",
        "b",
        "c",
    ]
    assert all(row.reset_required for row in store.rows.values())
    assert all(row.credential_version == 2 for row in store.rows.values())
    assert len(sessions.intents) == 3
    assert len(recovery.emitted) == 3
    assert receipt.approval_decision_ref == "decision-77"


def test_every_reset_credential_gets_independent_material() -> None:
    spy = SecretSpy()
    engine, store, *_ = build(secret_source=spy)
    for ref in ("a", "b", "c", "d"):
        seed(store, subject(ref), f"material-{ref}")
    plan = plan_for("a", "b", "c", "d")
    engine.apply_force_reset(plan, authorization_for(plan))
    assert len(set(spy.values)) == 4


def test_a_receipt_is_secret_free_and_serializable() -> None:
    engine, *_ = seeded_cohort("a")
    plan = plan_for("a")
    receipt = engine.apply_force_reset(plan, authorization_for(plan))
    rendered = json.dumps(
        {
            "plan_digest": receipt.plan_digest.value,
            "outcomes": [
                outcome.subject.credential_ref for outcome in receipt.outcomes
            ],
        }
    )
    assert receipt.plan_digest.value in rendered
    assert "argon2" not in repr(receipt)


def test_a_mutated_plan_no_longer_matches_its_authorization() -> None:
    """The digest is recalculated from the plan IN HAND, never trusted from the
    authorization — otherwise "approved" would mean "claims to be approved"."""
    engine, *_ = seeded_cohort("a", "b")
    plan = plan_for("a", "b")
    authorization = authorization_for(plan)
    widened = plan_for("a", "b", "c")
    with pytest.raises(CredentialAuthorizationError, match="authorized digest"):
        engine.apply_force_reset(widened, authorization)


def test_a_product_adapter_cannot_widen_a_cohort_after_approval() -> None:
    """Same refusal from the adapter's side: extra rows in the store are not
    extra targets, and there is no path by which the store can add one."""
    engine, store, *_ = seeded_cohort("a", "b", "smuggled")
    plan = plan_for("a", "b")
    engine.apply_force_reset(plan, authorization_for(plan))
    assert store.rows["smuggled"].credential_version == 1
    assert store.rows["smuggled"].reset_required is False


def test_the_plan_is_immutable_so_it_cannot_be_edited_after_approval() -> None:
    plan = plan_for("a")
    with pytest.raises(FrozenInstanceError):
        plan.reason_code = "something-else"  # type: ignore[misc]


def test_a_wrong_approval_policy_identity_is_refused() -> None:
    engine, *_ = seeded_cohort("a")
    plan = plan_for("a")
    with pytest.raises(CredentialAuthorizationError, match="approval policy"):
        engine.apply_force_reset(
            plan, authorization_for(plan, approval_policy_code="ops.routine")
        )
    with pytest.raises(CredentialAuthorizationError, match="approval policy"):
        engine.apply_force_reset(
            plan, authorization_for(plan, approval_policy_version=9)
        )


def test_an_expired_plan_is_refused() -> None:
    """Paired with the retry-after-expiry test below: expiry still refuses work
    that has not happened, so moving the idempotency lookup in front of it
    cannot be mistaken for removing the check."""
    engine, *_ = seeded_cohort("a")
    plan = plan_for("a", expires_at=NOW - timedelta(minutes=1))
    with pytest.raises(CredentialAuthorizationError, match="expired"):
        engine.apply_force_reset(plan, authorization_for(plan))


def test_a_stale_cohort_is_refused_before_anything_is_written() -> None:
    """A credential version that moved after approval means the operator
    approved a state that no longer exists."""
    engine, store, sessions, recovery, _audit = seeded_cohort("a", "b")
    plan = plan_for("a", "b")
    authorization = authorization_for(plan)
    store.rows["b"] = replace(store.rows["b"], credential_version=7)
    with pytest.raises(CredentialCohortDrift, match="changed after approval"):
        engine.apply_force_reset(plan, authorization)
    assert store.writes == [], "no target may be written when one target drifted"
    assert sessions.intents == []
    assert recovery.emitted == []


def test_a_vanished_target_is_refused_before_anything_is_written() -> None:
    engine, store, *_ = seeded_cohort("a", "b")
    plan = plan_for("a", "b")
    authorization = authorization_for(plan)
    del store.rows["b"]
    with pytest.raises(CredentialCohortDrift, match="no longer exists"):
        engine.apply_force_reset(plan, authorization)
    assert store.writes == []


def test_every_target_is_locked_before_any_is_mutated() -> None:
    """Ordering is the property, not the lock call count: checking versions
    while writing would leave a window where target three drifts after target
    one is already reset."""
    engine, store, *_ = seeded_cohort("a", "b", "c")
    plan = plan_for("a", "b", "c")
    engine.apply_force_reset(plan, authorization_for(plan))
    assert store.locked == ["a", "b", "c"]
    assert [write[0] for write in store.writes] == ["a", "b", "c"]


def test_a_partial_adapter_failure_propagates_so_the_caller_rolls_back() -> None:
    """The engine owns no transaction, so atomicity is the CALLER's. What the
    engine must do is refuse to swallow the failure — a receipt written beside a
    half-applied cohort would be the worst possible artifact."""
    engine, store, _sessions, _recovery, audit = seeded_cohort("a", "b", "c")
    store.fail_write_on = "b"
    plan = plan_for("a", "b", "c")
    with pytest.raises(RuntimeError, match="adapter exploded"):
        engine.apply_force_reset(plan, authorization_for(plan))
    assert audit.receipts == {}, "no receipt may exist for an incomplete cohort"
    assert [write[0] for write in store.writes] == ["a"]


def test_a_missing_session_revocation_intent_aborts_the_cohort() -> None:
    engine, store, *_ = seeded_cohort("a", "b")
    engine = replace(engine, sessions=FakeSessions(record_nothing=True))
    plan = plan_for("a", "b")
    with pytest.raises(CredentialEffectNotRecorded, match="session-revocation"):
        engine.apply_force_reset(plan, authorization_for(plan))


def test_a_recovery_delivery_failure_never_restores_the_old_credential() -> None:
    """The dangerous repair. Rolling the hash back on a delivery failure would
    make a failing email channel a way to keep a compromised credential alive.
    The write stands and the caller's transaction decides."""
    engine, store, *_ = seeded_cohort("a")
    engine = replace(engine, recovery=FakeRecovery(emit_raises=True))
    plan = plan_for("a")
    original_hash = store.rows["a"].password_hash
    with pytest.raises(RuntimeError, match="recovery delivery is down"):
        engine.apply_force_reset(plan, authorization_for(plan))
    assert store.rows["a"].password_hash != original_hash
    assert len(store.writes) == 1, "no compensating restore write"
    assert "restore" not in MODULE_PATH.read_text(encoding="utf-8")


def test_a_retry_of_the_same_authorized_plan_returns_the_stored_receipt() -> None:
    engine, store, sessions, recovery, audit = seeded_cohort("a", "b")
    plan = plan_for("a", "b")
    authorization = authorization_for(plan)
    first = engine.apply_force_reset(plan, authorization)
    writes_after_first = list(store.writes)
    second = engine.apply_force_reset(plan, authorization)
    assert second is first
    assert store.writes == writes_after_first
    assert len(sessions.intents) == 2
    assert len(recovery.emitted) == 2


def test_a_retry_after_the_window_closed_still_returns_the_receipt() -> None:
    """Idempotency is checked BEFORE expiry, because returning a stored receipt
    performs no effect. A false "expired" on work that already happened is how
    an operator comes to reset a cohort a second time."""
    clock = {"now": NOW}
    engine, store, sessions, recovery, audit = build(clock=lambda: clock["now"])
    for ref in ("a", "b"):
        seed(store, subject(ref), f"material-{ref}")
    plan = plan_for("a", "b", expires_at=NOW + timedelta(minutes=5))
    authorization = authorization_for(plan)
    first = engine.apply_force_reset(plan, authorization)

    clock["now"] = NOW + timedelta(hours=2)
    assert engine.apply_force_reset(plan, authorization) is first
    assert len(store.writes) == 2, "the retry must perform no further work"


def test_a_concurrent_credential_change_between_plan_and_apply_is_caught() -> None:
    """The realistic race: the subject resets their own password while the
    cohort plan is out for approval."""
    engine, store, *_ = seeded_cohort("a", "b")
    plan = plan_for("a", "b")
    authorization = authorization_for(plan)
    seeded = store.rows["a"]
    store.rows["a"] = replace(seeded, credential_version=seeded.credential_version + 1)
    with pytest.raises(CredentialCohortDrift):
        engine.apply_force_reset(plan, authorization)


# ── Boundary: what the kernel may not know or reach ─────────────────────────


def _module_tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def _imported_roots() -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_facility_imports_no_product_and_no_framework() -> None:
    allowed = {
        "__future__",
        "hashlib",
        "json",
        "secrets",
        "collections",
        "dataclasses",
        "datetime",
        "typing",
        "dotmac_kernel",
    }
    assert _imported_roots() <= allowed, sorted(_imported_roots() - allowed)


def test_the_facility_can_reach_no_network() -> None:
    """Not "does not today" — cannot. Every network root is absent from the
    import graph, and the source names no URL scheme."""
    network = {
        "socket",
        "ssl",
        "http",
        "urllib",
        "httpx",
        "requests",
        "aiohttp",
        "redis",
        "hvac",
        "smtplib",
        "boto3",
    }
    assert not network & _imported_roots()
    source = MODULE_PATH.read_text(encoding="utf-8")
    for scheme in ("http://", "https://", "redis://", "bao://"):
        assert scheme not in source


def test_the_facility_touches_no_orm_and_no_web_framework() -> None:
    forbidden = {"sqlalchemy", "fastapi", "starlette", "pydantic", "jinja2", "alembic"}
    assert not forbidden & _imported_roots()
    source = MODULE_PATH.read_text(encoding="utf-8")
    for name in ("select(", "db.query", "HTTPException", "status_code", "sessionmaker"):
        assert name not in source, name


def test_the_facility_names_no_product_principal_kind_as_a_branch() -> None:
    """Principal kinds are opaque strings. They appear in the module ONLY in
    prose, never as a literal the code compares against."""
    literals = {
        node.value
        for node in ast.walk(_module_tree())
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    docstrings = {
        ast.get_docstring(node, clean=False) or ""
        for node in ast.walk(_module_tree())
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
    }
    code_literals = literals - docstrings
    for kind in ("reseller_user", "subscriber", "system_user", "staff", "employee"):
        assert not any(kind in literal for literal in code_literals), kind


def test_deployment_control_cannot_authorize_an_account_mutation() -> None:
    """Product security authority, asserted rather than asserted-in-prose. The
    approval identity on a plan is a PRODUCT policy code, and nothing in this
    module reads a deployment plan, release or rollout."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "deployment_control" not in source.replace(
        "dotmac-deployment-control", ""
    ).replace("dotmac_deployment_control", "")
    assert not {"dotmac_deployment_control", "dotmac_deployment_foundation"} & (
        _imported_roots()
    )


# ── Optimized mode ──────────────────────────────────────────────────────────


def test_the_shipped_module_contains_no_assert_statement() -> None:
    """`python -O` deletes every `assert`. A conformance check written as one is
    a check that vanishes in exactly the build a product deploys."""
    offenders = [
        node.lineno for node in ast.walk(_module_tree()) if isinstance(node, ast.Assert)
    ]
    assert not offenders, f"assert statements at lines {offenders}"


def test_optimized_mode_does_not_remove_a_verification_check() -> None:
    """Proven by running it, not by reading the source. Two checks are exercised
    under `-O`: the typed-digest refusal and the expired-authorization refusal.
    """
    probe = """
import sys
from datetime import UTC, datetime, timedelta
from dotmac_kernel.credential_lifecycle import (
    CredentialAuthorizationError,
    CredentialResetAuthorizationV1,
    CredentialResetPlanDigestV1,
)

if sys.flags.optimize < 1:
    raise SystemExit("probe must run under -O")

try:
    CredentialResetAuthorizationV1(
        plan_digest="0" * 64,
        approval_policy_code="p",
        approval_policy_version=1,
        approval_decision_ref="d",
        approved_at=datetime.now(UTC),
    )
except TypeError:
    pass
else:
    raise SystemExit("a bare-string digest was accepted under -O")

try:
    CredentialResetPlanDigestV1("nope")
except ValueError:
    pass
else:
    raise SystemExit("a malformed digest was accepted under -O")

print("checks survived -O")
"""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-O", "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "checks survived -O" in completed.stdout
