"""Opening recovery preserves the refusal and the caller's transaction boundary."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from dotmac_billing import service as s
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict, IdempotentOutcome
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, SessionTransaction


@pytest.fixture
def recovery(monkeypatch):
    db = Mock(spec=Session)
    root = Mock(spec=SessionTransaction, is_active=True)
    enclosing = Mock(spec=SessionTransaction)
    db.get_transaction.return_value = root
    db.get_nested_transaction.return_value = enclosing
    db.info = {}
    ledger = Mock()
    monkeypatch.setattr(s, "_idempotent", ledger)
    return db, root, enclosing, ledger


@pytest.mark.parametrize("existing", [True, False])
def test_initial_ledger_result_does_not_require_new_admission(recovery, existing):
    db, _, _, ledger = recovery
    operation = Mock()
    replay = IdempotentOutcome("opening.operation", "key", {"id": "historical"}, True)
    refusal = BillingRuleViolation("opening_window_sealed", "lookup failed")
    if existing:
        ledger.return_value = replay
    else:
        ledger.side_effect = refusal
    arguments = {
        "scope": PlatformScope(),
        "operation_scope": "opening.operation",
        "key": "key",
        "fingerprint": "fingerprint",
        "operation": operation,
    }
    if existing:
        assert s._idempotent_opening_admission(db, **arguments) is replay
    else:
        with pytest.raises(BillingRuleViolation) as caught:
            s._idempotent_opening_admission(db, **arguments)
        assert caught.value is refusal
    ledger.assert_called_once()
    operation.assert_not_called()


@pytest.mark.parametrize("scope", [TenantScope(uuid4()), PlatformScope()])
@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (BillingConflict, "opening_target_changed"),
        (BillingRuleViolation, "opening_target_not_pristine"),
        (BillingRuleViolation, "opening_window_sealed"),
        (BillingRuleViolation, "opening_window_manifest"),
    ],
)
@pytest.mark.parametrize("winner", ["absent", "exact", "changed"])
def test_recovery_only_rechecks_the_original_ledger_identity(
    recovery, scope, error_type, code, winner
):
    db, _, _, ledger = recovery
    refusal = error_type(code, "original refusal", evidence="preserve")
    financial_operation = Mock(side_effect=refusal)
    replay = IdempotentOutcome("opening.operation", "same-key", {"id": "winner"}, True)
    conflict = IdempotencyConflict("changed fingerprint")

    def lookup(session, **kwargs):
        if ledger.call_count == 1 or winner == "absent":
            return kwargs["operation"](session)
        if winner == "changed":
            raise conflict
        return replay

    ledger.side_effect = lookup
    arguments = {
        "scope": scope,
        "operation_scope": "opening.operation",
        "key": "same-key",
        "fingerprint": "unchanged-fingerprint",
    }
    if winner == "exact":
        assert (
            s._idempotent_opening_admission(
                db, **arguments, operation=financial_operation
            )
            is replay
        )
    else:
        expected = conflict if winner == "changed" else refusal
        with pytest.raises(type(expected)) as caught:
            s._idempotent_opening_admission(
                db, **arguments, operation=financial_operation
            )
        assert caught.value is expected
    assert ledger.call_count == 2
    for call in ledger.call_args_list:
        assert call.args == (db,)
        assert {k: v for k, v in call.kwargs.items() if k != "operation"} == arguments
    financial_operation.assert_called_once_with(db)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("damage", ["root", "nested", "inactive", "rollback_latch"])
def test_failed_transaction_recovery_never_attempts_a_second_lookup(recovery, damage):
    db, root, _, ledger = recovery
    refusal = BillingRuleViolation("opening_window_sealed", "original refusal")

    def admission(session):
        if damage == "root":
            session.get_transaction.return_value = Mock(spec=SessionTransaction)
        elif damage == "nested":
            session.get_nested_transaction.return_value = Mock(spec=SessionTransaction)
        elif damage == "inactive":
            root.is_active = False
        else:
            # Kernel's durable exact-root latch survives savepoint unwind.
            session.info["_dotmac_kernel_conflict_savepoint_failed_root"] = root
        raise refusal

    ledger.side_effect = lambda session, **kwargs: kwargs["operation"](session)
    with pytest.raises(BillingRuleViolation) as caught:
        s._idempotent_opening_admission(
            db,
            scope=PlatformScope(),
            operation_scope="opening.operation",
            key="key",
            fingerprint="fingerprint",
            operation=admission,
        )
    assert caught.value is refusal
    ledger.assert_called_once()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        DBAPIError("statement", {}, RuntimeError("serialization failure")),
        BillingRuleViolation("opening_window_population", "wrong population"),
        BillingConflict("opening_authority_binding", "wrong authority"),
    ],
)
def test_database_and_unrelated_domain_errors_are_never_recovered(recovery, error):
    db, _, _, ledger = recovery
    operation = Mock(side_effect=error)
    ledger.side_effect = lambda session, **kwargs: kwargs["operation"](session)
    with pytest.raises(type(error)) as caught:
        s._idempotent_opening_admission(
            db,
            scope=PlatformScope(),
            operation_scope="opening.operation",
            key="key",
            fingerprint="fingerprint",
            operation=operation,
        )
    assert caught.value is error
    ledger.assert_called_once()
    operation.assert_called_once_with(db)
