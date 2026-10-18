"""Hosted-CI contract canaries; mock ports prove no actual DB authority."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import dotmac_kernel.credential_lifecycle as owner
import pytest

NOW = datetime(2026, 10, 18, tzinfo=UTC)
SECRET = "supplied-sensitive-material"
HASH = "stored-sensitive-hash"
TARGET = owner.CredentialSubjectRef("app", "human", "opaque:target", "credential")
PRIOR = replace(TARGET, principal_ref="opaque:prior")
MAX_VERSION = (1 << 63) - 1


def command(previous=None, version=4, **changes):
    values = {
        "target_subject": TARGET,
        "tenant_ref": "opaque-tenant",
        "purpose_ref": "configured-purpose",
        "intended_role_refs": ("a-role", "z-role"),
        "reason_code": "configured-reason",
        "correlation_ref": "correlation",
    }
    values.update(changes)
    if previous is None:
        return owner.CredentialCreateAbsentV1(**values)
    return owner.CredentialReplaceSelectedV1(
        **values,
        expected_previous_subject=previous,
        expected_credential_version=version,
    )


def harness(monkeypatch, cmd, current=None):
    evidence = owner.CredentialAssignmentAuthorityEvidenceV1(
        cmd, "service", "actual-runtime-actor", "current-authority"
    )
    trace = []
    authority = Mock()
    authority.authorize_and_lock.side_effect = lambda value: (
        trace.append("authority") or evidence
    )
    store = Mock()
    store.lock_current.side_effect = lambda value: trace.append("lock") or current
    version = 0 if current is None else current.credential_version + 1
    persisted = owner.CredentialSnapshot(TARGET, HASH, version, reset_required=False)
    store.create_absent.side_effect = lambda *args, **kwargs: (
        trace.append("create") or persisted
    )
    store.replace_selected.side_effect = lambda *args, **kwargs: (
        trace.append("replace") or persisted
    )
    policy = Mock()
    policy.validate.side_effect = lambda *args: trace.append("policy")
    hasher = Mock(side_effect=lambda value: trace.append("hash") or HASH)
    monkeypatch.setattr(owner, "hash_password", hasher)
    audit = Mock()
    audit.record_assignment.side_effect = (
        lambda entry: trace.append("audit") or "audit-id"
    )
    old = [Mock() for _ in range(4)]
    engine = owner.CredentialLifecycle(
        *old,
        policy,
        lambda: pytest.fail("assignment generated material"),
        lambda: NOW,
        authority,
        store,
        audit,
    )
    return SimpleNamespace(
        engine=engine,
        authority=authority,
        evidence=evidence,
        store=store,
        policy=policy,
        hasher=hasher,
        audit=audit,
        old=old,
        trace=trace,
        persisted=persisted,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"target_subject": "subject"},
        {"tenant_ref": None},
        {"purpose_ref": " "},
        {"reason_code": 1},
        {"correlation_ref": ""},
        {"intended_role_refs": []},
        {"intended_role_refs": ()},
        {"intended_role_refs": ("z", "a")},
        {"intended_role_refs": ("a", "a")},
        {"intended_role_refs": ("a", 3)},
    ],
)
def test_closed_command_rejects_malformed_input(changes):
    with pytest.raises((TypeError, ValueError)):
        command(**changes)


@pytest.mark.parametrize("version", [True, False, -1, 1.0, "1", MAX_VERSION + 1])
def test_replace_rejects_non_integer_and_out_of_bounds_versions(version):
    with pytest.raises((TypeError, ValueError)):
        command(PRIOR, version)


@pytest.mark.parametrize(
    "changes",
    [
        {"application": "other"},
        {"principal_kind": "other"},
        {"credential_ref": "other"},
    ],
)
def test_replace_refuses_cross_envelope(changes):
    with pytest.raises((TypeError, ValueError)):
        command(replace(PRIOR, **changes))


def test_commands_frozen_and_duplicate_roles_not_canonicalized():
    cmd = command()
    with pytest.raises(FrozenInstanceError):
        cmd.tenant_ref = "other"
    assert cmd.intended_role_refs == ("a-role", "z-role")


@pytest.mark.parametrize("previous", [None, TARGET, PRIOR])
def test_assignment_one_policy_one_hash_and_exact_evidence(monkeypatch, previous):
    cmd = command(previous)
    current = None if previous is None else owner.CredentialSnapshot(previous, "old", 4)
    h = harness(monkeypatch, cmd, current)
    receipt = h.engine.assign_authorized_password(cmd, SECRET)
    effect = "create" if previous is None else "replace"
    assert h.trace == ["authority", "lock", "policy", "hash", effect, "audit"]
    h.policy.validate.assert_called_once_with(TARGET, SECRET)
    h.hasher.assert_called_once_with(SECRET)
    method = h.store.create_absent if previous is None else h.store.replace_selected
    assert method.call_args.kwargs["reset_required"] is False
    assert method.call_args.kwargs["password_hash"] == HASH
    if previous is not None:
        assert method.call_args.kwargs["credential_version"] == 5
    entry = h.audit.record_assignment.call_args.args[0]
    assert entry.command is cmd
    assert entry.authority is h.evidence
    assert entry.action == "credential.password.assigned"
    assert [f.name for f in fields(entry)] == [
        "command",
        "authority",
        "previous_credential_version",
        "credential_version",
        "occurred_at",
    ]
    assert receipt.authority is h.evidence
    assert receipt.audit_ref == "audit-id"
    assert receipt.reset_required is False
    assert receipt.previous_credential_version == (None if previous is None else 4)
    for value in (cmd, h.evidence, entry, receipt, h.engine):
        for rendered in (repr(value), str(value)):
            assert SECRET not in rendered and HASH not in rendered
    for port in h.old:
        assert port.mock_calls == []


def test_old_positional_constructor_and_missing_ports_refuse(monkeypatch):
    cmd = command()
    h = harness(monkeypatch, cmd)
    old = owner.CredentialLifecycle(*h.old, h.policy, lambda: "unused", lambda: NOW)
    assert (
        old.assignment_authority is old.assignment_store is old.assignment_audit is None
    )
    with pytest.raises(owner.CredentialAssignmentError):
        old.assign_authorized_password(cmd, SECRET)
    for name in (
        "assignment_authority",
        "assignment_store",
        "assignment_audit",
        "policy",
    ):
        with pytest.raises(owner.CredentialAssignmentError):
            replace(h.engine, **{name: None}).assign_authorized_password(cmd, SECRET)
    h.authority.authorize_and_lock.assert_not_called()


@pytest.mark.parametrize(
    "current",
    [
        owner.CredentialSnapshot(TARGET, "old", 0),
        owner.CredentialSnapshot(PRIOR, "old", 4),
    ],
)
def test_create_replay_refuses_before_policy(monkeypatch, current):
    cmd = command()
    h = harness(monkeypatch, cmd, current)
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)
    h.policy.validate.assert_not_called()
    h.hasher.assert_not_called()


@pytest.mark.parametrize(
    "current",
    [
        None,
        owner.CredentialSnapshot(TARGET, "old", 4),
        owner.CredentialSnapshot(PRIOR, "old", 3),
        owner.CredentialSnapshot(PRIOR, "old", True),
        owner.CredentialSnapshot(PRIOR, "old", MAX_VERSION + 1),
    ],
)
def test_replace_exact_prior_and_version_required(monkeypatch, current):
    cmd = command(PRIOR)
    h = harness(monkeypatch, cmd, current)
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)
    h.policy.validate.assert_not_called()


def test_overflow_refuses_without_policy_or_hash(monkeypatch):
    cmd = command(PRIOR, MAX_VERSION)
    h = harness(monkeypatch, cmd, owner.CredentialSnapshot(PRIOR, "old", MAX_VERSION))
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)
    h.policy.validate.assert_not_called()
    h.hasher.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"subject": PRIOR},
        {"credential_version": True},
        {"credential_version": MAX_VERSION + 1},
        {"password_hash": "wrong"},
        {"reset_required": True},
        {"reset_required": 0},
    ],
)
def test_store_outcome_refusal_precedes_audit(monkeypatch, change):
    cmd = command(PRIOR)
    h = harness(monkeypatch, cmd, owner.CredentialSnapshot(PRIOR, "old", 4))
    h.store.replace_selected.side_effect = None
    h.store.replace_selected.return_value = replace(h.persisted, **change)
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)
    h.audit.record_assignment.assert_not_called()


@pytest.mark.parametrize("audit_ref", [None, "", " ", 1, False])
def test_real_nonempty_audit_reference_required(monkeypatch, audit_ref):
    cmd = command()
    h = harness(monkeypatch, cmd)
    h.audit.record_assignment.side_effect = None
    h.audit.record_assignment.return_value = audit_ref
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)


@pytest.mark.parametrize(
    "step", ["authority", "lock", "policy", "hash", "create", "audit"]
)
@pytest.mark.parametrize("after_effect", [False, True])
def test_failures_mask_material_and_require_caller_rollback(
    monkeypatch, step, after_effect
):
    cmd = command()
    h = harness(monkeypatch, cmd)
    effects = []
    methods = {
        "authority": h.authority.authorize_and_lock,
        "lock": h.store.lock_current,
        "policy": h.policy.validate,
        "hash": h.hasher,
        "create": h.store.create_absent,
        "audit": h.audit.record_assignment,
    }

    def fail(*args, **kwargs):
        if after_effect:
            effects.append(step)
        raise ValueError(SECRET + HASH)

    methods[step].side_effect = fail
    with pytest.raises(owner.CredentialAssignmentError) as caught:
        h.engine.assign_authorized_password(cmd, SECRET)
    assert SECRET not in str(caught.value) and HASH not in repr(caught.value)
    assert caught.value.__suppress_context__ is True
    assert caught.value.__cause__ is None
    assert effects == ([step] if after_effect else [])
    # The engine cannot undo product port effects: the caller's root/savepoint must.
    assert all(port.mock_calls == [] for port in h.old)


@pytest.mark.parametrize("kind", ["database", "", None, True])
def test_actor_vocabulary_closed(kind):
    with pytest.raises((TypeError, ValueError)):
        owner.CredentialAssignmentAuthorityEvidenceV1(
            command(), kind, "actor", "authority"
        )


def test_exact_command_identity_required_from_authority(monkeypatch):
    cmd = command()
    h = harness(monkeypatch, cmd)
    h.authority.authorize_and_lock.side_effect = None
    h.authority.authorize_and_lock.return_value = replace(
        h.evidence, command=replace(cmd)
    )
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)
    h.store.lock_current.assert_not_called()


def test_closed_engine_command_and_secret_types(monkeypatch):
    cmd = command()
    h = harness(monkeypatch, cmd)
    for bad_cmd, bad_secret in ((object(), SECRET), (cmd, None), (cmd, b"bytes")):
        with pytest.raises(owner.CredentialAssignmentError):
            h.engine.assign_authorized_password(bad_cmd, bad_secret)
    h.authority.authorize_and_lock.assert_not_called()


@pytest.mark.parametrize("version", [0, 17, MAX_VERSION])
def test_create_accepts_actual_store_version_without_initial_version_policy(
    monkeypatch, version
):
    cmd = command()
    h = harness(monkeypatch, cmd)
    h.store.create_absent.side_effect = None
    h.store.create_absent.return_value = replace(
        h.persisted, credential_version=version
    )
    assert (
        h.engine.assign_authorized_password(cmd, SECRET).credential_version == version
    )


@pytest.mark.parametrize("version", [True, MAX_VERSION + 1])
def test_create_rejects_malformed_store_versions(monkeypatch, version):
    cmd = command()
    h = harness(monkeypatch, cmd)
    h.store.create_absent.side_effect = None
    h.store.create_absent.return_value = replace(
        h.persisted, credential_version=version
    )
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)
    h.audit.record_assignment.assert_not_called()


def test_negative_store_version_refused_even_if_snapshot_is_corrupted(monkeypatch):
    cmd = command()
    h = harness(monkeypatch, cmd)
    invalid = replace(h.persisted)
    object.__setattr__(invalid, "credential_version", -1)
    h.store.create_absent.side_effect = None
    h.store.create_absent.return_value = invalid
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)
    h.audit.record_assignment.assert_not_called()


@pytest.mark.parametrize("after_effect", [False, True])
def test_replacement_effect_failure_is_masked(monkeypatch, after_effect):
    cmd = command(PRIOR)
    h = harness(monkeypatch, cmd, owner.CredentialSnapshot(PRIOR, "old", 4))
    effects = []

    def fail(*args, **kwargs):
        if after_effect:
            effects.append("written")
        raise RuntimeError(SECRET + HASH)

    h.store.replace_selected.side_effect = fail
    with pytest.raises(owner.CredentialAssignmentError) as caught:
        h.engine.assign_authorized_password(cmd, SECRET)
    assert SECRET not in str(caught.value) and HASH not in str(caught.value)
    assert caught.value.__suppress_context__ is True
    assert effects == (["written"] if after_effect else [])
    h.audit.record_assignment.assert_not_called()


@pytest.mark.parametrize("timestamp", [None, "time", datetime(2026, 10, 18)])
def test_audit_requires_aware_datetime_and_masks_clock_failure(monkeypatch, timestamp):
    cmd = command()
    h = harness(monkeypatch, cmd)
    engine = replace(h.engine, clock=lambda: timestamp)
    with pytest.raises(owner.CredentialAssignmentError):
        engine.assign_authorized_password(cmd, SECRET)
    h.audit.record_assignment.assert_not_called()


@pytest.mark.parametrize("kind", ["user", "service", "api_key", "system"])
def test_all_canonical_actor_kinds_without_invented_defaults(kind):
    evidence = owner.CredentialAssignmentAuthorityEvidenceV1(
        command(), kind, "actor", "ref"
    )
    assert evidence.actor_kind == kind


def test_audit_action_fixed_and_no_material_fields(monkeypatch):
    cmd = command()
    h = harness(monkeypatch, cmd)
    h.engine.assign_authorized_password(cmd, SECRET)
    entry = h.audit.record_assignment.call_args.args[0]
    with pytest.raises((TypeError, FrozenInstanceError)):
        replace(entry, action="other")
    for bad_entry in (
        {"previous_credential_version": 0},
        {"credential_version": True},
        {"occurred_at": datetime(2026, 10, 18)},
        {"authority": replace(h.evidence, command=replace(cmd))},
    ):
        with pytest.raises((TypeError, ValueError)):
            replace(entry, **bad_entry)


@pytest.mark.parametrize("evidence", [None, True, object()])
def test_invalid_authority_evidence_cannot_reach_store(monkeypatch, evidence):
    cmd = command()
    h = harness(monkeypatch, cmd)
    h.authority.authorize_and_lock.side_effect = None
    h.authority.authorize_and_lock.return_value = evidence
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(cmd, SECRET)
    h.store.lock_current.assert_not_called()


def test_policy_secret_bearing_rejection_is_not_exposed(monkeypatch):
    cmd = command()
    h = harness(monkeypatch, cmd)
    h.policy.validate.side_effect = owner.CredentialPolicyViolation(SECRET)
    with pytest.raises(owner.CredentialAssignmentError) as caught:
        h.engine.assign_authorized_password(cmd, SECRET)
    assert SECRET not in str(caught.value)
    h.hasher.assert_not_called()
    h.audit.record_assignment.assert_not_called()


def test_subclass_command_refused_by_closed_engine(monkeypatch):
    class ExtendedCommand(owner.CredentialCreateAbsentV1):
        pass

    cmd = command()
    h = harness(monkeypatch, cmd)
    extended = ExtendedCommand(**{f.name: getattr(cmd, f.name) for f in fields(cmd)})
    with pytest.raises(owner.CredentialAssignmentError):
        h.engine.assign_authorized_password(extended, SECRET)
    h.authority.authorize_and_lock.assert_not_called()
