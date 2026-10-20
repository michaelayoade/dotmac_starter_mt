"""Hosted-CI-only contract canaries for the additive lifecycle account reset.

All material below is synthetic. Mock ports prove owner ordering/contracts,
not Academy proof admission or database transaction atomicity.
"""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dotmac_kernel import credential_lifecycle as lifecycle


@pytest.fixture
def account_case(monkeypatch):
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    principal = lifecycle.CredentialAccountRef(
        application="synthetic-product",
        principal_kind="person",
        principal_ref="synthetic-tenant/synthetic-person",
    )
    subjects = tuple(
        lifecycle.CredentialSubjectRef(
            application=principal.application,
            principal_kind=principal.principal_kind,
            principal_ref=principal.principal_ref,
            credential_ref=f"synthetic-credential-{index}",
        )
        for index in range(2)
    )
    members = tuple(
        lifecycle.CredentialSnapshot(
            subject=subject,
            password_hash="non-authenticating-synthetic-snapshot",
            credential_version=version,
            is_locked=True,
        )
        for subject, version in zip(subjects, (0, 7), strict=True)
    )
    authorization = lifecycle.CredentialAccountRecoveryAuthorizationV1(
        account=principal,
        recovery_ref="synthetic-proven-proof",
        proven_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=1),
    )
    events = []
    store = Mock(spec=lifecycle.CredentialStorePort)
    current = {member.subject: member for member in members}

    def lock(subject):
        events.append(("lock", subject))
        return current.get(subject)

    def write(subject, **values):
        events.append(("write", subject))

    store.lock_for_update.side_effect = lock
    store.write_hash.side_effect = write
    account = Mock(spec=lifecycle.CredentialAccountResetPort)

    def consume(proof):
        events.append(("consume", proof.account))
        return True

    def membership(principal):
        events.append(("membership", principal))
        return members

    def clear(subject):
        events.append(("clear", subject))

    def revoke(principal, **facts):
        events.append(("revoke", principal))
        return lifecycle.CredentialAccountSessionRevocationCompletionV1(
            account=principal,
            completion_ref="synthetic-completed-revocation",
            completed_at=facts["completed_at"],
            revoked_session_count=3,
        )

    account.consume.side_effect = consume
    account.lock_current_credentials.side_effect = membership
    account.clear_failure_state.side_effect = clear
    account.revoke_sessions.side_effect = revoke
    policy = Mock(spec=lifecycle.PasswordPolicyPort)
    policy.validate.side_effect = lambda subject, secret: events.append(
        ("policy", subject)
    )
    audit = Mock(spec=lifecycle.CredentialAuditPort)
    audit.record_event.side_effect = lambda entry: events.append(
        ("audit", entry.subject)
    )

    def hash_once(secret):
        events.append(("hash", None))
        return "non-authenticating-synthetic-new-hash"

    hasher = Mock(side_effect=hash_once)
    monkeypatch.setattr(lifecycle, "hash_password", hasher)
    generator = Mock(side_effect=AssertionError("generation must not occur"))
    verification = Mock(side_effect=AssertionError("verification must not occur"))
    monkeypatch.setattr(lifecycle, "verify_password", verification)
    monkeypatch.setattr(lifecycle, "password_needs_rehash", verification)
    sessions = Mock(spec=lifecycle.SessionRevocationPort)
    recovery = Mock(spec=lifecycle.RecoveryIntentPort)
    clock = Mock(return_value=now)
    owner = lifecycle.CredentialLifecycle(
        store, sessions, recovery, audit, policy, secret_source=generator, clock=clock
    )
    return SimpleNamespace(
        now=now,
        principal=principal,
        subjects=subjects,
        members=members,
        current=current,
        authorization=authorization,
        events=events,
        store=store,
        account=account,
        policy=policy,
        audit=audit,
        hasher=hasher,
        generator=generator,
        verification=verification,
        sessions=sessions,
        recovery=recovery,
        clock=clock,
        owner=owner,
        secret="synthetic-only-chosen-value",
    )


def _complete(case, authorization=None):
    return case.owner.complete_account_reset(
        case.authorization if authorization is None else authorization,
        case.secret,
        account=case.account,
    )


def _assert_no_member_writes(case):
    case.hasher.assert_not_called()
    case.store.write_hash.assert_not_called()
    case.account.clear_failure_state.assert_not_called()
    case.account.revoke_sessions.assert_not_called()
    case.audit.record_event.assert_not_called()


def test_one_proof_one_hash_complete_membership_and_distinct_generation_increments(
    account_case,
):
    case = account_case
    receipt = _complete(case)
    case.clock.assert_called_once_with()
    case.account.consume.assert_called_once_with(case.authorization)
    case.account.lock_current_credentials.assert_called_once_with(case.principal)
    assert case.hasher.call_count == 1
    assert case.policy.validate.call_count == 2
    assert case.store.lock_for_update.call_count == 2
    assert case.store.write_hash.call_count == 2
    assert case.account.clear_failure_state.call_count == 2
    case.account.revoke_sessions.assert_called_once_with(
        case.principal,
        reason_code="credential.reset.completed",
        correlation_ref=case.authorization.recovery_ref,
        completed_at=case.now,
    )
    for call, member in zip(
        case.store.write_hash.call_args_list, case.members, strict=True
    ):
        assert call.args == (member.subject,)
        if call.kwargs["password_hash"] != "non-authenticating-synthetic-new-hash":
            pytest.fail("account members received different material", pytrace=False)
        assert call.kwargs["credential_version"] == member.credential_version + 1
        assert call.kwargs["reset_required"] is False
    assert [event[0] for event in case.events] == [
        "consume",
        "membership",
        "lock",
        "lock",
        "policy",
        "policy",
        "hash",
        "write",
        "clear",
        "write",
        "clear",
        "revoke",
        "audit",
        "audit",
    ]
    assert receipt.account == case.principal
    assert receipt.completed_at == case.now
    assert receipt.recovery_ref == case.authorization.recovery_ref
    assert receipt.revocation.revoked_session_count == 3
    assert [
        (o.previous_credential_version, o.credential_version) for o in receipt.outcomes
    ] == [(0, 1), (7, 8)]
    assert all(not o.reset_required and o.sessions_revoked for o in receipt.outcomes)
    for call, outcome in zip(
        case.audit.record_event.call_args_list, receipt.outcomes, strict=True
    ):
        entry = call.args[0]
        assert entry.subject == outcome.subject
        assert entry.credential_version == outcome.credential_version
        assert entry.action == entry.reason_code == "credential.reset.completed"
        assert entry.occurred_at == case.now
        assert entry.correlation_ref == receipt.recovery_ref
    for value in (
        receipt,
        receipt.revocation,
        *receipt.outcomes,
        case.owner,
        *case.members,
    ):
        shown = repr(value)
        if (
            case.secret in shown
            or "non-authenticating-synthetic-new-hash" in shown
            or "non-authenticating-synthetic-snapshot" in shown
        ):
            pytest.fail("material escaped a lifecycle representation", pytrace=False)
    assert {field.name for field in fields(receipt)} == {
        "account",
        "recovery_ref",
        "completed_at",
        "outcomes",
        "revocation",
        "schema_version",
    }
    case.generator.assert_not_called()
    case.verification.assert_not_called()
    assert not case.sessions.mock_calls
    assert not case.recovery.mock_calls
    case.audit.record_receipt.assert_not_called()


def test_expiry_equality_is_admitted_with_one_captured_time(account_case):
    case = account_case
    _complete(case, replace(case.authorization, expires_at=case.now))
    case.clock.assert_called_once_with()
    assert case.hasher.call_count == 1


def test_singleton_at_last_incrementable_generation_and_zero_sessions(account_case):
    case = account_case
    member = replace(case.members[0], credential_version=2**63 - 2)
    case.current[member.subject] = member
    case.account.lock_current_credentials.side_effect = None
    case.account.lock_current_credentials.return_value = (member,)
    case.account.revoke_sessions.side_effect = None
    case.account.revoke_sessions.return_value = (
        lifecycle.CredentialAccountSessionRevocationCompletionV1(
            case.principal, "synthetic-zero-session-completion", case.now, 0
        )
    )
    receipt = _complete(case)
    assert len(receipt.outcomes) == 1
    assert receipt.outcomes[0].credential_version == 2**63 - 1
    assert receipt.revocation.revoked_session_count == 0
    assert case.hasher.call_count == 1


def test_replayed_principal_proof_cannot_hash_or_write_again(account_case):
    case = account_case
    case.account.consume.side_effect = [True, False]
    _complete(case)
    with pytest.raises(lifecycle.CredentialAuthorizationError):
        _complete(case)
    assert case.account.consume.call_count == 2
    assert case.account.lock_current_credentials.call_count == 1
    assert case.hasher.call_count == 1
    assert case.store.write_hash.call_count == 2
    assert case.account.revoke_sessions.call_count == 1


@pytest.mark.parametrize("change", ["expired", "future", "missing", "boolean"])
def test_missing_or_outside_window_proof_refused_before_consumption(
    account_case, change
):
    case = account_case
    authorization = (
        replace(case.authorization, expires_at=case.now - timedelta(microseconds=1))
        if change == "expired"
        else replace(case.authorization, proven_at=case.now + timedelta(microseconds=1))
        if change == "future"
        else False
        if change == "boolean"
        else object()
    )
    with pytest.raises(lifecycle.CredentialAuthorizationError):
        _complete(case, authorization)
    case.account.consume.assert_not_called()
    _assert_no_member_writes(case)


@pytest.mark.parametrize("result", [False, None, "truthy"])
def test_spent_or_unproven_actual_port_refusal_never_reaches_membership(
    account_case, result
):
    case = account_case
    case.account.consume.side_effect = None
    case.account.consume.return_value = result
    with pytest.raises(lifecycle.CredentialAuthorizationError):
        _complete(case)
    case.account.consume.assert_called_once_with(case.authorization)
    case.account.lock_current_credentials.assert_not_called()
    _assert_no_member_writes(case)


def test_missing_installed_policy_refuses_without_spending_proof(account_case):
    case = account_case
    case.owner = replace(case.owner, policy=None)
    with pytest.raises(lifecycle.CredentialPolicyViolation):
        _complete(case)
    case.account.consume.assert_not_called()
    _assert_no_member_writes(case)


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "list",
        "duplicate",
        "wrong-application",
        "wrong-kind",
        "wrong-principal",
        "stale",
        "missing",
        "wrong-fresh-subject",
        "overflow",
        "overflow-plus",
        "boolean-version",
    ],
)
def test_complete_cohort_refusals_precede_policy_hash_and_all_writes(
    account_case, fault
):
    case = account_case
    members = case.members
    if fault == "empty":
        members = ()
    elif fault == "list":
        members = list(members)
    elif fault == "duplicate":
        members = (members[0], members[0])
    elif fault.startswith("wrong-") and fault != "wrong-fresh-subject":
        field = {
            "wrong-application": "application",
            "wrong-kind": "principal_kind",
            "wrong-principal": "principal_ref",
        }[fault]
        wrong = replace(members[1].subject, **{field: "another-bound-principal"})
        members = (members[0], replace(members[1], subject=wrong))
    elif fault == "stale":
        case.current[members[1].subject] = replace(members[1], credential_version=8)
    elif fault == "missing":
        del case.current[members[1].subject]
    elif fault == "wrong-fresh-subject":
        case.current[members[1].subject] = replace(
            members[1], subject=members[0].subject
        )
    else:
        version = {
            "overflow": 2**63 - 1,
            "overflow-plus": 2**63,
            "boolean-version": True,
        }[fault]
        members = (members[0], replace(members[1], credential_version=version))
        case.current[members[1].subject] = members[1]
    case.account.lock_current_credentials.side_effect = None
    case.account.lock_current_credentials.return_value = members
    with pytest.raises(lifecycle.CredentialCohortDrift):
        _complete(case)
    case.policy.validate.assert_not_called()
    _assert_no_member_writes(case)


@pytest.mark.parametrize(
    "result", ["none", "boolean", "intent", "wrong-principal", "wrong-time"]
)
def test_revocation_must_be_truthful_typed_synchronous_completion(account_case, result):
    case = account_case
    completion = lifecycle.CredentialAccountSessionRevocationCompletionV1(
        case.principal, "actual-synthetic-completion", case.now, 0
    )
    returned = {
        "none": None,
        "boolean": True,
        "intent": lifecycle.CredentialSessionRevocationIntentV1(
            case.subjects[0], "credential.reset.completed", 1, case.now, "queued-only"
        ),
        "wrong-principal": replace(
            completion,
            account=replace(case.principal, principal_ref="another-tenant/principal"),
        ),
        "wrong-time": replace(completion, completed_at=case.now - timedelta(seconds=1)),
    }[result]
    case.account.revoke_sessions.side_effect = None
    case.account.revoke_sessions.return_value = returned
    with pytest.raises(lifecycle.CredentialEffectNotRecorded):
        _complete(case)
    assert case.store.write_hash.call_count == 2
    case.audit.record_event.assert_not_called()


@pytest.mark.parametrize(
    "stage",
    [
        "consume",
        "membership",
        "fresh-lock",
        "policy-first",
        "policy-second",
        "hash",
        "write-first",
        "write-second",
        "clear-first",
        "clear-second",
        "revoke",
        "audit-first",
        "audit-second",
    ],
)
def test_each_port_or_hash_failure_is_masked_and_requires_caller_rollback(
    account_case, stage
):
    case = account_case
    # Deliberately include synthetic material in the injected port exception;
    # diagnostic text and exception chaining must not disclose it.
    failure = RuntimeError(case.secret + " non-authenticating-synthetic-new-hash")
    if stage == "consume":
        case.account.consume.side_effect = failure
    elif stage == "membership":
        case.account.lock_current_credentials.side_effect = failure
    elif stage == "fresh-lock":
        case.store.lock_for_update.side_effect = [case.members[0], failure]
    elif stage.startswith("policy-"):
        case.policy.validate.side_effect = (
            [failure] if stage.endswith("first") else [None, failure]
        )
    elif stage == "hash":
        case.hasher.side_effect = failure
    elif stage.startswith("write-"):
        case.store.write_hash.side_effect = (
            [failure] if stage.endswith("first") else [None, failure]
        )
    elif stage.startswith("clear-"):
        case.account.clear_failure_state.side_effect = (
            [failure] if stage.endswith("first") else [None, failure]
        )
    elif stage == "revoke":
        case.account.revoke_sessions.side_effect = failure
    else:
        case.audit.record_event.side_effect = (
            [failure] if stage.endswith("first") else [None, failure]
        )
    with pytest.raises(lifecycle.CredentialLifecycleError) as refused:
        _complete(case)
    if case.secret in str(
        refused.value
    ) or "non-authenticating-synthetic-new-hash" in str(refused.value):
        pytest.fail("material escaped lifecycle failure", pytrace=False)
    assert refused.value.__suppress_context__ is True
    assert case.hasher.call_count <= 1
    if stage in {
        "consume",
        "membership",
        "fresh-lock",
        "policy-first",
        "policy-second",
        "hash",
    }:
        case.store.write_hash.assert_not_called()
        case.account.revoke_sessions.assert_not_called()
    if stage not in {"audit-first", "audit-second"}:
        case.audit.record_event.assert_not_called()


def test_policy_refusal_preserves_typed_error_but_masks_product_diagnostic(
    account_case,
):
    case = account_case
    case.policy.validate.side_effect = lifecycle.CredentialPolicyViolation(case.secret)
    with pytest.raises(lifecycle.CredentialPolicyViolation) as refused:
        _complete(case)
    if case.secret in str(refused.value):
        pytest.fail("policy material escaped", pytrace=False)
    assert refused.value.__suppress_context__ is True
    _assert_no_member_writes(case)


@pytest.mark.parametrize("count", [-1, True, 1.5])
def test_revocation_completion_requires_a_nonnegative_integer_count(
    account_case, count
):
    case = account_case
    with pytest.raises(ValueError):
        lifecycle.CredentialAccountSessionRevocationCompletionV1(
            case.principal, "synthetic-completed", case.now, count
        )
