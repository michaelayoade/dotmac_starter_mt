"""The persistence service exposes one coherent, flush-only money path."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_billing import service

MODULE_ROOT = (
    Path(__file__).resolve().parents[2] / "packages/dotmac-billing/src/dotmac_billing"
)


def test_the_coherent_financial_path_exists() -> None:
    for operation in (
        "create_billing_account",
        "accept_rated_obligation",
        "create_draft_document",
        "issue_document",
        "void_document",
        "issue_credit_note",
        "accept_settlement",
        "allocate_settlement",
        "deallocate_settlement",
        "reallocate_settlement",
        "refund_settlement",
        "reverse_posting_group",
        "rebuild_receivable_position",
        "record_document_artifact",
    ):
        assert callable(getattr(service, operation))


def test_services_mutate_and_flush_but_never_own_transactions() -> None:
    source = inspect.getsource(service).lower()
    assert "db.flush(" in source
    assert "db.commit(" not in source
    assert "db.rollback(" not in source
    assert "session(" not in source
    assert "sessionmaker(" not in source


def test_no_direct_balance_assignment_or_float_arithmetic() -> None:
    tree = ast.parse(inspect.getsource(service))
    forbidden_targets = {"balance", "current_balance", "balance_due", "amount_paid"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
            assert not names & forbidden_targets
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "float"


def test_pending_or_unverified_evidence_is_not_an_accepted_constant() -> None:
    source = inspect.getsource(service).lower()
    for forbidden in (
        "pending_checkout",
        "uploaded_proof",
        "ui_approval",
        "unverified_provider_acknowledgement",
    ):
        assert forbidden not in source
