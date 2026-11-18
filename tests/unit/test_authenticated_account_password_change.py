"""Portable contract canaries, not actual cookie/DB/concurrency/rollback proof."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import dotmac_kernel.credential_lifecycle as owner
import pytest

NOW = datetime(2026, 10, 18, tzinfo=UTC)
OLD = "sensitive-current"
NEW = "sensitive-new"
HASH = "sensitive-new-hash"
ACCOUNT = owner.CredentialAccountRef("app", "human", "tenant-bound-person")
A = owner.CredentialSubjectRef("app", "human", ACCOUNT.principal_ref, "a")
B = replace(A, credential_ref="b")


def command(**changes):
    values = {
        "account": ACCOUNT,
        "reason_code": "configured-change",
        "correlation_ref": "corr",
    }
    values.update(changes)
    return owner.CredentialAuthenticatedAccountPasswordChangeV1(**values)


def harness(monkeypatch, selected=B):
    cmd = command()
    evidence = owner.CredentialAuthenticatedAccountPasswordChangeAuthorityV1(
        cmd, "user", ACCOUNT.principal_ref, "actual-session", selected
    )
    originals = (
        owner.CredentialSnapshot(A, "diverged-alias-hash", 3, False, True, True),
        owner.CredentialSnapshot(B, "selected-old-hash", 5, False, True, True),
    )
    rows = {s.subject: s for s in originals}
    trace = []
    account = Mock()
    account.authorize_and_lock.return_value = evidence
    account.lock_current_credentials.side_effect = lambda value: tuple(rows.values())
    account.revalidate_before_change.side_effect = (
        lambda issued, credentials: trace.append("revalidate")
        or (issued is evidence and credentials == originals)
    )
    account.synchronize_account_aliases.side_effect = lambda *args: trace.append(
        "aliases"
    )
    account.clear_failure_state.side_effect = lambda subject: (
        trace.append(("clear", subject))
        or rows.update({subject: replace(rows[subject], is_locked=False)})
    )
    revocation = owner.CredentialAccountSessionRevocationCompletionV1(
        ACCOUNT, "revoked", NOW, 2
    )
    invalidation = owner.CredentialAccountResetTokenInvalidationCompletionV1(
        ACCOUNT, "invalidated", NOW, 3
    )
    account.revoke_sessions.return_value = revocation
    account.invalidate_outstanding_reset_tokens.return_value = invalidation
    account.record_change.return_value = "actual-audit-id"
    store = Mock()
    store.lock_for_update.side_effect = lambda subject: rows.get(subject)

    def write(subject, **kwargs):
        trace.append(("write", subject))
        rows[subject] = replace(rows[subject], **kwargs)

    store.write_hash.side_effect = write
    policy = Mock()
    policy.validate.side_effect = lambda *args: trace.append("policy")
    verifier = Mock(
        side_effect=lambda secret, hashed: trace.append("verify")
        or hashed == "selected-old-hash"
    )
    hasher = Mock(side_effect=lambda secret: trace.append("hash-new") or HASH)
    monkeypatch.setattr(owner, "verify_password", verifier)
    monkeypatch.setattr(owner, "hash_password", hasher)
    rehash = Mock(side_effect=AssertionError("old hash must never upgrade"))
    monkeypatch.setattr(owner, "password_needs_rehash", rehash)
    old_ports = [Mock() for _ in range(3)]
    engine = owner.CredentialLifecycle(
        store, *old_ports, policy, lambda: pytest.fail("generated"), lambda: NOW
    )
    return SimpleNamespace(**locals())


def test_closed_command_shape_and_frozen_exact_fields():
    cmd = command()
    assert [f.name for f in fields(cmd)] == [
        "account",
        "reason_code",
        "correlation_ref",
    ]
    with pytest.raises(FrozenInstanceError):
        cmd.reason_code = "other"
    for name in ("subjects", "roles", "password", "session_ref"):
        with pytest.raises(TypeError):
            owner.CredentialAuthenticatedAccountPasswordChangeV1(
                ACCOUNT, "reason", "corr", **{name: "forbidden"}
            )


@pytest.mark.parametrize("value", [None, True, "", " ", b"bytes"])
def test_command_rejects_bad_references(value):
    with pytest.raises((ValueError, TypeError)):
        owner.CredentialAuthenticatedAccountPasswordChangeV1(ACCOUNT, value, "corr")


def test_success_one_new_shared_hash_all_aliases_and_exact_audit(monkeypatch):
    h = harness(monkeypatch)
    receipt = h.engine.change_authenticated_account_password(
        h.cmd, OLD, NEW, account=h.account
    )
    h.verifier.assert_called_once_with(OLD, "selected-old-hash")
    h.rehash.assert_not_called()
    h.hasher.assert_called_once_with(NEW)
    h.policy.validate.assert_called_once_with(B, NEW)
    assert h.trace[:5] == ["verify", "policy", "revalidate", "aliases", "hash-new"]
    assert len(h.store.write_hash.call_args_list) == 2
    for original in h.originals:
        row = h.rows[original.subject]
        assert row.password_hash == HASH
        assert row.credential_version == original.credential_version + 1
        assert row.reset_required is False and row.is_locked is False
        assert row.is_active is original.is_active
    h.account.revoke_sessions.assert_called_once()
    h.account.invalidate_outstanding_reset_tokens.assert_called_once()
    h.account.clear_context.assert_called_once()
    entry = h.account.record_change.call_args.args[0]
    assert entry.command is h.cmd and entry.authority is h.evidence
    assert entry.action == "credential.password.changed"
    assert receipt.audit_ref == "actual-audit-id"
    assert receipt.revocation is h.revocation and receipt.invalidation is h.invalidation
    for obj in (h.cmd, h.evidence, entry, receipt, h.engine):
        for material in (OLD, NEW, HASH):
            assert material not in repr(obj)
    assert all(port.mock_calls == [] for port in h.old_ports)


def test_diverged_unselected_matching_alias_never_authorizes(monkeypatch):
    h = harness(monkeypatch, selected=A)
    with pytest.raises(
        owner.CredentialAuthenticatedAccountPasswordChangeRefused
    ) as caught:
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    assert caught.value.code == "current_password_invalid"
    h.verifier.assert_called_once_with(OLD, "diverged-alias-hash")
    h.policy.validate.assert_not_called()
    h.hasher.assert_not_called()
    h.account.synchronize_account_aliases.assert_not_called()
    h.store.write_hash.assert_not_called()
    h.account.clear_failure_state.assert_not_called()
    h.account.revoke_sessions.assert_not_called()
    h.account.invalidate_outstanding_reset_tokens.assert_not_called()
    assert tuple(h.rows.values()) == h.originals


@pytest.mark.parametrize("code", ["confirmation_mismatch", "policy_rejected"])
def test_deferred_policy_closed_refusals_after_old_before_effects(monkeypatch, code):
    h = harness(monkeypatch)
    h.policy.validate.side_effect = (
        owner.CredentialAuthenticatedAccountPasswordPolicyRefused(code)
    )
    with pytest.raises(
        owner.CredentialAuthenticatedAccountPasswordChangeRefused
    ) as caught:
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    assert caught.value.code == code
    h.verifier.assert_called_once()
    h.hasher.assert_not_called()
    h.account.synchronize_account_aliases.assert_not_called()
    h.store.write_hash.assert_not_called()
    h.account.clear_context.assert_called_once()


@pytest.mark.parametrize(
    "variant",
    [
        "empty",
        "duplicate",
        "foreign",
        "omitted_selection",
        "copied_command",
        "copied_evidence",
        "omitted_other",
        "overflow",
        "changed_hash",
        "false_recheck",
    ],
)
def test_hostile_membership_evidence_and_drift_fail_before_effects(
    monkeypatch, variant
):
    h = harness(monkeypatch)
    if variant == "empty":
        h.account.lock_current_credentials.side_effect = None
        h.account.lock_current_credentials.return_value = ()
        h.account.authorize_and_lock.return_value = replace(
            h.evidence, verification_subject=None
        )
    elif variant == "duplicate":
        h.account.lock_current_credentials.side_effect = lambda value: (
            h.originals[0],
            h.originals[0],
        )
    elif variant == "foreign":
        h.account.lock_current_credentials.side_effect = lambda value: (
            replace(h.originals[0], subject=replace(A, principal_ref="other-tenant")),
        )
    elif variant == "omitted_selection":
        h.account.lock_current_credentials.side_effect = lambda value: (h.originals[0],)
    elif variant == "copied_command":
        h.account.authorize_and_lock.return_value = replace(
            h.evidence, command=replace(h.cmd)
        )
    elif variant == "copied_evidence":
        h.account.authorize_and_lock.return_value = replace(h.evidence)
    elif variant == "omitted_other":
        h.account.lock_current_credentials.side_effect = lambda value: (h.originals[1],)
    elif variant == "overflow":
        h.rows[B] = replace(h.rows[B], credential_version=(1 << 63) - 1)
    elif variant == "changed_hash":
        h.store.lock_for_update.side_effect = lambda subject: replace(
            h.rows[subject], password_hash="drifted"
        )
    else:
        h.account.revalidate_before_change.return_value = False
        h.account.revalidate_before_change.side_effect = None
    with pytest.raises(owner.CredentialAuthenticatedAccountPasswordChangeError):
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    h.hasher.assert_not_called()
    h.account.synchronize_account_aliases.assert_not_called()
    h.store.write_hash.assert_not_called()
    h.account.clear_context.assert_called_once()


@pytest.mark.parametrize("effect", ["write", "clear", "revoke", "invalidate", "audit"])
def test_downstream_material_error_requires_whole_caller_rollback(monkeypatch, effect):
    from contextlib import contextmanager

    h = harness(monkeypatch)
    methods = {
        "write": h.store.write_hash,
        "clear": h.account.clear_failure_state,
        "revoke": h.account.revoke_sessions,
        "invalidate": h.account.invalidate_outstanding_reset_tokens,
        "audit": h.account.record_change,
    }
    methods[effect].side_effect = RuntimeError(OLD + NEW + HASH)

    @contextmanager
    def fake_caller_root():
        try:
            yield
        except Exception:
            h.rows.clear()
            h.rows.update({row.subject: row for row in h.originals})
            raise

    with (
        pytest.raises(
            owner.CredentialAuthenticatedAccountPasswordChangeError
        ) as caught,
        fake_caller_root(),
    ):
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    assert tuple(h.rows.values()) == h.originals
    assert caught.value.__suppress_context__ is True and caught.value.__cause__ is None
    for material in (OLD, NEW, HASH):
        assert material not in str(caught.value)
    h.account.clear_context.assert_called_once()


@pytest.mark.parametrize("bad", [None, "", " ", True])
def test_actual_audit_id_required(monkeypatch, bad):
    h = harness(monkeypatch)
    h.account.record_change.return_value = bad
    with pytest.raises(owner.CredentialAuthenticatedAccountPasswordChangeError):
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )


def test_unknown_command_subclass_refused_before_authority(monkeypatch):
    class Extended(owner.CredentialAuthenticatedAccountPasswordChangeV1):
        pass

    h = harness(monkeypatch)
    with pytest.raises(owner.CredentialAuthenticatedAccountPasswordChangeError):
        h.engine.change_authenticated_account_password(
            Extended(ACCOUNT, "reason", "corr"), OLD, NEW, account=h.account
        )
    h.account.authorize_and_lock.assert_not_called()


def test_missing_credential_truthful_none_selection_is_typed_refusal(monkeypatch):
    h = harness(monkeypatch)
    h.account.authorize_and_lock.return_value = replace(
        h.evidence, verification_subject=None
    )
    h.account.lock_current_credentials.side_effect = None
    h.account.lock_current_credentials.return_value = ()
    with pytest.raises(
        owner.CredentialAuthenticatedAccountPasswordChangeRefused
    ) as caught:
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    assert caught.value.code == "current_password_invalid"
    h.verifier.assert_not_called()
    h.policy.validate.assert_not_called()
    h.hasher.assert_not_called()
    h.account.synchronize_account_aliases.assert_not_called()


@pytest.mark.parametrize("empty", [False, True])
def test_none_selection_only_valid_for_empty_membership(monkeypatch, empty):
    h = harness(monkeypatch)
    if empty:
        h.account.lock_current_credentials.side_effect = None
        h.account.lock_current_credentials.return_value = ()
    else:
        h.account.authorize_and_lock.return_value = replace(
            h.evidence, verification_subject=None
        )
    with pytest.raises(
        owner.CredentialAuthenticatedAccountPasswordChangeError
    ) as caught:
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    assert type(caught.value) is owner.CredentialAuthenticatedAccountPasswordChangeError
    h.hasher.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"credential_version": 5},
        {"password_hash": "wrong"},
        {"reset_required": True},
        {"is_active": True},
    ],
)
def test_cas_persisted_outcome_must_be_exact(monkeypatch, change):
    h = harness(monkeypatch)
    original = h.store.write_hash.side_effect

    def corrupt(subject, **kwargs):
        original(subject, **kwargs)
        h.rows[subject] = replace(h.rows[subject], **change)

    h.store.write_hash.side_effect = corrupt
    with pytest.raises(owner.CredentialAuthenticatedAccountPasswordChangeError):
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    h.account.record_change.assert_not_called()


@pytest.mark.parametrize("kind", ["revocation", "invalidation"])
@pytest.mark.parametrize(
    "change",
    [
        {"account": replace(ACCOUNT, principal_ref="other-tenant")},
        {"completed_at": NOW.replace(year=2025)},
        {"completion_ref": " "},
    ],
)
def test_completion_binding_revalidated_before_audit(monkeypatch, kind, change):
    h = harness(monkeypatch)
    completion = replace(h.revocation if kind == "revocation" else h.invalidation)
    for name, value in change.items():
        object.__setattr__(completion, name, value)
    method = (
        h.account.revoke_sessions
        if kind == "revocation"
        else h.account.invalidate_outstanding_reset_tokens
    )
    method.return_value = completion
    with pytest.raises(owner.CredentialAuthenticatedAccountPasswordChangeError):
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    h.account.record_change.assert_not_called()


@pytest.mark.parametrize("code", ["arbitrary-sensitive", None, True])
def test_policy_refusal_codes_are_closed(code):
    with pytest.raises((TypeError, ValueError)):
        owner.CredentialAuthenticatedAccountPasswordPolicyRefused(code)
    with pytest.raises((TypeError, ValueError)):
        owner.CredentialAuthenticatedAccountPasswordChangeRefused(code)


def test_hostile_policy_exception_cannot_choose_material_free_expected_code(
    monkeypatch,
):
    h = harness(monkeypatch)
    bad = owner.CredentialAuthenticatedAccountPasswordPolicyRefused("policy_rejected")
    bad.code = OLD + NEW
    h.policy.validate.side_effect = bad
    with pytest.raises(
        owner.CredentialAuthenticatedAccountPasswordChangeError
    ) as caught:
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    assert type(caught.value) is owner.CredentialAuthenticatedAccountPasswordChangeError
    assert OLD not in str(caught.value) and NEW not in repr(caught.value)
    h.hasher.assert_not_called()


def test_private_selection_and_cookie_binding_do_not_come_from_actor_dto(monkeypatch):
    h = harness(monkeypatch)
    h.account.authorize_and_lock.return_value = replace(
        h.evidence, actor_ref="copied-user", session_ref="copied-session"
    )
    with pytest.raises(owner.CredentialAuthenticatedAccountPasswordChangeError):
        h.engine.change_authenticated_account_password(
            h.cmd, OLD, NEW, account=h.account
        )
    h.hasher.assert_not_called()
    h.account.synchronize_account_aliases.assert_not_called()


def test_unordered_membership_reordering_does_not_select_different_old_hash(
    monkeypatch,
):
    h = harness(monkeypatch)
    h.account.lock_current_credentials.side_effect = [
        h.originals,
        tuple(reversed(h.originals)),
    ]
    h.engine.change_authenticated_account_password(h.cmd, OLD, NEW, account=h.account)
    h.verifier.assert_called_once_with(OLD, "selected-old-hash")
    h.hasher.assert_called_once_with(NEW)
