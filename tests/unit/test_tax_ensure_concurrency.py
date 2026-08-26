"""Concurrency canaries for tax-policy idempotent ensure seams."""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import cast
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.money import Currency
from dotmac_tax import service as tax_service
from dotmac_tax.contracts import (
    TaxAuthorityInput,
    TaxJurisdictionInput,
    TaxRuleInput,
    TaxSubjectClassificationInput,
)
from dotmac_tax.models import (
    TaxAuthority,
    TaxCode,
    TaxJurisdiction,
    TaxRule,
    TaxSubjectClassification,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = ROOT / "packages/dotmac-tax/src/dotmac_tax/service.py"
NGN = Currency("NGN", 2)
NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


class _NestedSavepoint(AbstractContextManager[None]):
    def __init__(self, session: _RaceSession) -> None:
        self.session = session

    def __enter__(self) -> None:
        assert not self.session.in_savepoint
        self.session.in_savepoint = True
        self.session.savepoint_entries += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.session.in_savepoint = False
        return False


class _RaceSession:
    """Small Session protocol fake that makes the protected flush lose a race."""

    def __init__(self, *, before: list[object | None], after: list[object | None]):
        self._before: Iterator[object | None] = iter(before)
        self._after: Iterator[object | None] = iter(after)
        self.race_lost = False
        self.in_savepoint = False
        self.savepoint_entries = 0
        self.outer_transaction_marker = "tenant-scope-still-installed"

    def begin_nested(self) -> _NestedSavepoint:
        return _NestedSavepoint(self)

    def scalar(self, statement: object) -> object | None:
        del statement
        return next(self._after if self.race_lost else self._before)

    def add(self, instance: object) -> None:
        del instance
        assert self.in_savepoint, "the raced mutation escaped its SAVEPOINT"

    def flush(self) -> None:
        assert self.in_savepoint, "the raced flush escaped its SAVEPOINT"
        self.race_lost = True
        raise IntegrityError("INSERT", {}, RuntimeError("simulated unique race"))


def _as_session(session: _RaceSession) -> Session:
    return cast(Session, session)


def _authority(tenant_id: UUID, *, name: str = "Federal authority") -> TaxAuthority:
    return TaxAuthority(
        id=uuid4(),
        tenant_id=tenant_id,
        code="FIRS",
        name=name,
        authority_level_code="federal",
        status="active",
    )


def _jurisdiction(
    tenant_id: UUID,
    authority_id: UUID,
    *,
    name: str = "Nigeria federal",
) -> TaxJurisdiction:
    return TaxJurisdiction(
        id=uuid4(),
        tenant_id=tenant_id,
        authority_id=authority_id,
        code="NG-FED",
        name=name,
        country_code="NG",
        subdivision_code=None,
        currency_code="NGN",
        minor_units=2,
        status="active",
    )


def _code(
    tenant_id: UUID,
    jurisdiction_id: UUID,
    *,
    name: str = "Value added tax",
) -> TaxCode:
    return TaxCode(
        id=uuid4(),
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction_id,
        code="VAT",
        name=name,
        tax_kind_code="consumption",
        description="Standard VAT",
        status="active",
    )


def _rule_command(tax_code_id: UUID) -> TaxRuleInput:
    return TaxRuleInput(
        tax_code_id=tax_code_id,
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        priority=10,
        fact_kind="taxable-sale",
        recognition_basis_code="invoice",
        transaction_side="output",
        calculation_method="percentage",
        rate=Decimal("0.075"),
        fixed_amount=None,
        inclusive=False,
        recoverable_rate=Decimal("0"),
    )


def _rule(
    tenant_id: UUID,
    command: TaxRuleInput,
    *,
    priority: int | None = None,
) -> TaxRule:
    return TaxRule(
        id=uuid4(),
        tenant_id=tenant_id,
        tax_code_id=command.tax_code_id,
        version=command.version,
        effective_from=command.effective_from,
        effective_to=command.effective_to,
        priority=command.priority if priority is None else priority,
        fact_kind=command.fact_kind,
        recognition_basis_code=command.recognition_basis_code,
        transaction_side=command.transaction_side,
        calculation_method=command.calculation_method,
        rate=command.rate,
        fixed_amount=None,
        inclusive=command.inclusive,
        recoverable_rate=command.recoverable_rate,
        party_category=None,
        supply_category=None,
        place_code=None,
        treatment_code=command.treatment_code,
        calculation_sequence=command.calculation_sequence,
        calculation_base_code=command.calculation_base_code,
        published_at=NOW,
    )


def _classification_command(tax_code_id: UUID) -> TaxSubjectClassificationInput:
    return TaxSubjectClassificationInput(
        tax_code_id=tax_code_id,
        subject_kind="party",
        subject_ref="customer:42",
        category_code="standard",
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        basis_code="approved-profile",
        evidence_ref="approval:42",
        published_by_ref="principal:reviewer",
        source_ref="erp:customer-tax-profile:42",
        source_version="cv1:profile",
    )


def _classification(
    tenant_id: UUID,
    command: TaxSubjectClassificationInput,
    *,
    source_ref: str | None = None,
) -> TaxSubjectClassification:
    normalized_source_ref = source_ref or command.source_ref
    fingerprint = tax_service._classification_fingerprint(
        command,
        subject_ref=command.subject_ref,
        category_code=command.category_code,
        basis_code=command.basis_code,
        evidence_ref=command.evidence_ref,
        published_by_ref=command.published_by_ref,
        source_ref=normalized_source_ref,
        source_version=command.source_version,
    )
    return TaxSubjectClassification(
        id=uuid4(),
        tenant_id=tenant_id,
        tax_code_id=command.tax_code_id,
        subject_kind=command.subject_kind,
        subject_ref=command.subject_ref,
        category_code=command.category_code,
        version=command.version,
        effective_from=command.effective_from,
        effective_to=command.effective_to,
        basis_code=command.basis_code,
        evidence_ref=command.evidence_ref,
        published_by_ref=command.published_by_ref,
        source_ref=normalized_source_ref,
        source_version=command.source_version,
        source_fingerprint=fingerprint,
        published_at=NOW,
    )


def _assert_outer_transaction_survived(session: _RaceSession) -> None:
    assert session.savepoint_entries == 1
    assert not session.in_savepoint
    assert session.outer_transaction_marker == "tenant-scope-still-installed"


def test_identical_races_replay_all_five_tax_policy_identities() -> None:
    tenant_id = uuid4()
    authority_command = TaxAuthorityInput(" FIRS ", " Federal authority ", " federal ")
    authority = _authority(tenant_id)
    authority_race = _RaceSession(before=[None, None], after=[authority])
    authority_result = tax_service.ensure_tax_authority(
        _as_session(authority_race), tenant_id=tenant_id, command=authority_command
    )
    assert authority_result.authority_id == authority.id
    _assert_outer_transaction_survived(authority_race)

    jurisdiction_command = TaxJurisdictionInput(
        authority_id=authority.id,
        code=" NG-FED ",
        name=" Nigeria federal ",
        country_code="ng",
        currency=NGN,
    )
    jurisdiction = _jurisdiction(tenant_id, authority.id)
    jurisdiction_race = _RaceSession(before=[None, authority], after=[jurisdiction])
    jurisdiction_result = tax_service.ensure_tax_jurisdiction(
        _as_session(jurisdiction_race),
        tenant_id=tenant_id,
        command=jurisdiction_command,
    )
    assert jurisdiction_result.jurisdiction_id == jurisdiction.id
    _assert_outer_transaction_survived(jurisdiction_race)

    tax_code = _code(tenant_id, jurisdiction.id)
    code_race = _RaceSession(before=[None, jurisdiction], after=[tax_code])
    code_result = tax_service.ensure_tax_code(
        _as_session(code_race),
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        code=" VAT ",
        name=" Value added tax ",
        tax_kind_code=" consumption ",
        description=" Standard VAT ",
    )
    assert code_result.tax_code_id == tax_code.id
    _assert_outer_transaction_survived(code_race)

    rule_command = _rule_command(tax_code.id)
    rule = _rule(tenant_id, rule_command)
    rule_race = _RaceSession(
        before=[None, tax_code, jurisdiction, None],
        after=[rule, tax_code, jurisdiction],
    )
    rule_result = tax_service.ensure_tax_rule(
        _as_session(rule_race), tenant_id=tenant_id, command=rule_command
    )
    assert rule_result.rule_id == rule.id
    _assert_outer_transaction_survived(rule_race)

    classification_command = _classification_command(tax_code.id)
    classification = _classification(tenant_id, classification_command)
    classification_race = _RaceSession(
        before=[None, None, tax_code, None, None],
        after=[classification, classification],
    )
    classification_result = tax_service.ensure_tax_subject_classification(
        _as_session(classification_race),
        tenant_id=tenant_id,
        command=classification_command,
    )
    assert classification_result.classification_id == classification.id
    _assert_outer_transaction_survived(classification_race)


def test_race_winners_with_drift_fail_closed_for_each_tax_identity() -> None:
    tenant_id = uuid4()
    authority_command = TaxAuthorityInput("FIRS", "Federal authority", "federal")
    authority = _authority(tenant_id)
    jurisdiction_command = TaxJurisdictionInput(
        authority_id=authority.id,
        code="NG-FED",
        name="Nigeria federal",
        country_code="NG",
        currency=NGN,
    )
    jurisdiction = _jurisdiction(tenant_id, authority.id)
    tax_code = _code(tenant_id, jurisdiction.id)
    rule_command = _rule_command(tax_code.id)

    races: tuple[tuple[_RaceSession, Callable[[Session], object]], ...] = (
        (
            _RaceSession(
                before=[None, None], after=[_authority(tenant_id, name="Drift")]
            ),
            lambda db: tax_service.ensure_tax_authority(
                db, tenant_id=tenant_id, command=authority_command
            ),
        ),
        (
            _RaceSession(
                before=[None, authority],
                after=[_jurisdiction(tenant_id, authority.id, name="Drift")],
            ),
            lambda db: tax_service.ensure_tax_jurisdiction(
                db, tenant_id=tenant_id, command=jurisdiction_command
            ),
        ),
        (
            _RaceSession(
                before=[None, jurisdiction],
                after=[_code(tenant_id, jurisdiction.id, name="Drift")],
            ),
            lambda db: tax_service.ensure_tax_code(
                db,
                tenant_id=tenant_id,
                jurisdiction_id=jurisdiction.id,
                code="VAT",
                name="Value added tax",
                tax_kind_code="consumption",
                description="Standard VAT",
            ),
        ),
        (
            _RaceSession(
                before=[None, tax_code, jurisdiction, None],
                after=[
                    _rule(tenant_id, rule_command, priority=99),
                    tax_code,
                    jurisdiction,
                ],
            ),
            lambda db: tax_service.ensure_tax_rule(
                db, tenant_id=tenant_id, command=rule_command
            ),
        ),
    )
    for race, call in races:
        with pytest.raises(tax_service.TaxConflict):
            call(_as_session(race))
        _assert_outer_transaction_survived(race)


def test_repeated_inner_checks_reselect_an_identical_authority_or_rule() -> None:
    """Authority/rule helpers may observe the race before reaching INSERT."""

    tenant_id = uuid4()
    authority_command = TaxAuthorityInput("FIRS", "Federal authority", "federal")
    authority = _authority(tenant_id)
    authority_race = _RaceSession(
        before=[None, authority, authority],
        after=[],
    )
    authority_result = tax_service.ensure_tax_authority(
        _as_session(authority_race), tenant_id=tenant_id, command=authority_command
    )
    assert authority_result.authority_id == authority.id
    _assert_outer_transaction_survived(authority_race)

    jurisdiction = _jurisdiction(tenant_id, authority.id)
    tax_code = _code(tenant_id, jurisdiction.id)
    command = _rule_command(tax_code.id)
    rule = _rule(tenant_id, command)
    rule_race = _RaceSession(
        before=[None, tax_code, jurisdiction, rule, rule, tax_code, jurisdiction],
        after=[],
    )
    rule_result = tax_service.ensure_tax_rule(
        _as_session(rule_race), tenant_id=tenant_id, command=command
    )
    assert rule_result.rule_id == rule.id
    _assert_outer_transaction_survived(rule_race)


def test_classification_race_checks_source_and_subject_version_identities() -> None:
    tenant_id = uuid4()
    tax_code = _code(tenant_id, uuid4())
    command = _classification_command(tax_code.id)
    source_winner = _classification(
        tenant_id,
        command,
        source_ref=command.source_ref,
    )
    version_winner = _classification(
        tenant_id,
        command,
        source_ref="erp:another-source:42",
    )

    source_only_race = _RaceSession(
        before=[None, None, tax_code, None, None],
        after=[source_winner, None],
    )
    with pytest.raises(tax_service.TaxConflict):
        tax_service.ensure_tax_subject_classification(
            _as_session(source_only_race), tenant_id=tenant_id, command=command
        )
    _assert_outer_transaction_survived(source_only_race)

    version_only_race = _RaceSession(
        before=[None, None, tax_code, None, None],
        after=[None, version_winner],
    )
    with pytest.raises(tax_service.TaxConflict):
        tax_service.ensure_tax_subject_classification(
            _as_session(version_only_race), tenant_id=tenant_id, command=command
        )
    _assert_outer_transaction_survived(version_only_race)


def test_classification_reselects_winner_seen_by_inner_version_check() -> None:
    """The source query can miss while max(version) already sees the winner."""

    tenant_id = uuid4()
    tax_code = _code(tenant_id, uuid4())
    command = _classification_command(tax_code.id)
    winner = _classification(tenant_id, command)
    race = _RaceSession(
        before=[None, None, tax_code, None, command.version, winner, winner],
        after=[],
    )

    result = tax_service.ensure_tax_subject_classification(
        _as_session(race), tenant_id=tenant_id, command=command
    )

    assert result.classification_id == winner.id
    _assert_outer_transaction_survived(race)


def test_classification_preserves_unrelated_inner_tax_conflict() -> None:
    tenant_id = uuid4()
    tax_code = _code(tenant_id, uuid4())
    command = _classification_command(tax_code.id)
    race = _RaceSession(
        before=[None, None, tax_code, None, 5, None, None],
        after=[],
    )

    with pytest.raises(
        tax_service.TaxConflict,
        match="next tax classification version must be 6",
    ):
        tax_service.ensure_tax_subject_classification(
            _as_session(race), tenant_id=tenant_id, command=command
        )
    _assert_outer_transaction_survived(race)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_all_ensure_mutations_are_inside_lazy_conflict_savepoints() -> None:
    """AST proves the five race handlers cannot regress into text-only coverage."""

    tree = ast.parse(SERVICE_PATH.read_text())
    targets = {
        "ensure_tax_authority": "create_tax_authority",
        "ensure_tax_jurisdiction": "create_tax_jurisdiction",
        "ensure_tax_code": "create_tax_code",
        "ensure_tax_rule": "publish_tax_rule",
        "ensure_tax_subject_classification": "publish_tax_subject_classification",
    }
    for ensure_name, mutation_name in targets.items():
        function = _function(tree, ensure_name)
        lazy_imports = [
            node
            for node in function.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "dotmac_kernel.db"
            and any(alias.name == "conflict_savepoint" for alias in node.names)
        ]
        assert len(lazy_imports) == 1

        race_try = next(node for node in function.body if isinstance(node, ast.Try))
        savepoints = [
            node
            for node in race_try.body
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and _call_name(item.context_expr) == "conflict_savepoint"
                for item in node.items
            )
        ]
        assert len(savepoints) == 1
        assert any(
            _call_name(node) == mutation_name
            for node in ast.walk(savepoints[0])
            if isinstance(node, ast.Call)
        )
        handlers = [
            handler
            for handler in race_try.handlers
            if (
                isinstance(handler.type, ast.Name)
                and handler.type.id == "IntegrityError"
            )
            or (
                isinstance(handler.type, ast.Tuple)
                and any(
                    isinstance(item, ast.Name) and item.id == "IntegrityError"
                    for item in handler.type.elts
                )
            )
        ]
        assert len(handlers) == 1
        assert any(
            _call_name(node) == "scalar"
            for node in ast.walk(handlers[0])
            if isinstance(node, ast.Call)
        )
        assert not any(
            _call_name(node) == "rollback"
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
        )

    classification = _function(tree, "ensure_tax_subject_classification")
    race_try = next(node for node in classification.body if isinstance(node, ast.Try))
    integrity_handler = next(
        handler
        for handler in race_try.handlers
        if (isinstance(handler.type, ast.Name) and handler.type.id == "IntegrityError")
        or (
            isinstance(handler.type, ast.Tuple)
            and any(
                isinstance(item, ast.Name) and item.id == "IntegrityError"
                for item in handler.type.elts
            )
        )
    )
    assert isinstance(integrity_handler.type, ast.Tuple)
    assert {
        item.id for item in integrity_handler.type.elts if isinstance(item, ast.Name)
    } == {"IntegrityError", "TaxConflict"}
    assert (
        sum(
            _call_name(node) == "scalar"
            for node in ast.walk(integrity_handler)
            if isinstance(node, ast.Call)
        )
        == 2
    )
