"""Dependency contract canaries; executed only by hosted CI."""

# All stateful proofs use the real migrated PostgreSQL application role. They
# deliberately do not use metadata.create_all as a proxy for database integrity.
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from dotmac_kernel.money import Money
from dotmac_tax import (
    ListTaxRuleHistoryV1,
    ListTaxRuleHistoryV2,
    StatutoryReportBoxInput,
    TaxBaseDependencyPolicyV1,
    TaxCommercialSourceV1,
    TaxConflict,
    TaxFact,
    TaxFactSignatureV1,
    TaxFactV2,
    TaxNotFound,
    TaxPriorResultReferenceV1,
    TaxRuleInputV2,
    TaxRuleSupersessionInputV2,
    TaxRuleViolation,
    create_filing_obligation,
    create_statutory_report_definition,
    create_tax_code,
    determine_tax_set,
    determine_tax_set_v2,
    ensure_tax_rule_v2_in_transaction,
    generate_statutory_report,
    get_tax_determination_set_v2,
    get_tax_rule,
    list_tax_rule_history,
    list_tax_rule_history_v2,
    preview_tax_policy_v2,
    supersede_tax_rule_v2,
)
from dotmac_tax.models import (
    TaxDeterminationDependency,
    TaxDeterminationPriorSet,
    TaxDeterminationSet,
    TaxRule,
    TaxRuleDependencyCode,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests import test_tax_released_migration_upgrade as migration_helpers
from tests.unit import test_tax_owner_authoring as owner_helpers
from tests.unit import test_tax_service as helpers

database = migration_helpers.database


def test_dependency_selection_refuses_missing_or_duplicate_code_identity():
    signature = TaxFactSignatureV1(uuid4(), "invoice", "accrual", "output")
    with pytest.raises(ValueError):
        TaxBaseDependencyPolicyV1(signature, "tax_code_ids", ())
    code = uuid4()
    with pytest.raises(ValueError):
        TaxBaseDependencyPolicyV1(signature, "tax_code_ids", (code, code))
    with pytest.raises(ValueError):
        TaxBaseDependencyPolicyV1(signature, "all_prior", (code,))


def test_dependency_signature_is_purpose_specific():
    jurisdiction = uuid4()
    assert TaxFactSignatureV1(
        jurisdiction, "invoice", "accrual", "output"
    ) != TaxFactSignatureV1(jurisdiction, "invoice", "accrual", "withholding")


@pytest.fixture
def dependency_database(database, monkeypatch):
    _, admin_url, app_url = database
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = migration_helpers._config(migration_helpers.CURRENT_TAX)
    # Exercise the real predecessor schema before the additive successor.
    alembic_command.upgrade(config, "tx_0004_rule_supersession")
    engine = create_engine(admin_url)
    with Session(engine) as session:
        first, second = helpers._tenant(session), helpers._tenant(session)
        tenant_ids = first.id, second.id
        session.commit()
    alembic_command.upgrade(config, "tax@head")
    online = create_engine(app_url)
    try:
        yield online, tenant_ids, config
    finally:
        online.dispose()
        engine.dispose()


def _session(engine, tenant_id):
    session = Session(engine, autoflush=False, expire_on_commit=False)
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )
    return session


def _input(code_id, *, side="output", rate="0.1", sequence=100, policy=None):
    return TaxRuleInputV2(
        tax_code_id=code_id,
        version=1,
        effective_from=helpers.NOW.date().replace(month=1, day=1),
        effective_to=None,
        priority=10,
        fact_kind="invoice",
        recognition_basis_code="issued",
        transaction_side=side,
        calculation_method="percentage",
        rate=Decimal(rate),
        fixed_amount=None,
        inclusive=False,
        recoverable_rate=Decimal(0),
        calculation_sequence=sequence,
        calculation_base_code=(
            "source_plus_dependency_tax" if policy else "source_amount"
        ),
        dependency_policy=policy,
    )


def _fixture(session, tenant_id, *, selected=False, zero=False):
    _, jurisdiction, first = helpers._masters(session, tenant_id)
    second = create_tax_code(
        session,
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        code="LEVY",
        name="Levy",
        tax_kind_code="custom",
    )
    target = create_tax_code(
        session,
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        code="WHT",
        name="Withholding",
        tax_kind_code="custom",
    )
    first_rule = ensure_tax_rule_v2_in_transaction(
        session,
        tenant_id=tenant_id,
        command=_input(first.id, rate="0" if zero else "0.1"),
    )
    ensure_tax_rule_v2_in_transaction(
        session,
        tenant_id=tenant_id,
        command=_input(second.id, rate="0.2", sequence=200),
    )
    policy = TaxBaseDependencyPolicyV1(
        TaxFactSignatureV1(jurisdiction.id, "invoice", "issued", "output"),
        "tax_code_ids" if selected else "all_prior",
        (first.id,) if selected else (),
    )
    target_input = _input(target.id, side="withholding", rate="0.05", policy=policy)
    target_rule = ensure_tax_rule_v2_in_transaction(
        session,
        tenant_id=tenant_id,
        command=target_input,
    )
    ordinary = TaxFact(
        jurisdiction.id,
        helpers.NOW.date(),
        "invoice",
        "issued",
        "output",
        Money.of("100", helpers.NGN),
        "invoice:A:output",
        "1",
        "invoice:A",
        counterparty_ref="party:A",
        supply_ref="line:A",
        place_ref="place:A",
    )
    commercial = TaxCommercialSourceV1("invoice:A", "1")
    source = TaxFactV2(ordinary, commercial)
    prior = determine_tax_set_v2(
        session,
        tenant_id=tenant_id,
        fact=source,
        determined_at=helpers.NOW,
    )
    reference = TaxPriorResultReferenceV1(
        prior.id,
        prior.source_ref,
        prior.source_version,
        prior.source_fingerprint,
        prior.result_fingerprint,
    )
    dependent = TaxFactV2(
        replace(ordinary, transaction_side="withholding", source_ref="invoice:A:wht"),
        commercial,
        (reference,),
    )
    return source, prior, dependent, first_rule, target_rule, target_input


def test_cross_tenant_prior_reference_is_not_found(dependency_database):
    engine, (first, second), _ = dependency_database
    with _session(engine, first) as session:
        _, _, fact, _, _, _ = _fixture(session, first)
        session.commit()
    with _session(engine, second) as session:
        _, jurisdiction, _ = helpers._masters(session, second)
        foreign = replace(
            fact, fact=replace(fact.fact, jurisdiction_id=jurisdiction.id)
        )
        # Even a policy-free V2 attempt must resolve every supplied reference.
        with pytest.raises(TaxNotFound):
            determine_tax_set_v2(
                session, tenant_id=second, fact=foreign, determined_at=helpers.NOW
            )
        assert (
            session.scalar(select(func.count()).select_from(TaxDeterminationSet)) == 0
        )


@pytest.mark.parametrize("selected,expected", [(False, "6.50"), (True, "5.50")])
def test_policy_selects_all_or_exact_codes_without_readding_prior_tax(
    dependency_database,
    selected,
    expected,
):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        with owner_helpers.participant_guard(session):
            _, prior, fact, _, _, _ = _fixture(session, tenant_id, selected=selected)
            preview = preview_tax_policy_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )
            result = determine_tax_set_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )
        assert (
            prior.source_amount
            == prior.net_amount
            == result.source_amount
            == result.net_amount
            == Money.of("100", helpers.NGN)
        )
        assert prior.tax_amount == Money.of("30", helpers.NGN)
        assert (
            result.tax_amount == preview.tax_amount == Money.of(expected, helpers.NGN)
        )
        assert result.gross_amount == result.source_amount + result.tax_amount
        assert result.components[0].base_amount == Money.of(
            "110" if selected else "130", helpers.NGN
        )
        assert len(result.components[0].dependency_components) == (1 if selected else 2)
        assert result.components[0].selected_dependency_policy is not None
        assert result.result_fingerprint.startswith("rv2:")
        assert (
            determine_tax_set_v2(
                session,
                tenant_id=tenant_id,
                fact=fact,
                determined_at=helpers.NOW + timedelta(days=1),
            )
            == result
        )
        session.commit()
    with _session(engine, tenant_id) as session:
        assert (
            determine_tax_set_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )
            == result
        )


def test_explicit_zero_is_evidence_and_missing_code_is_refused(dependency_database):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, _ = _fixture(session, tenant_id, selected=True, zero=True)
        result = determine_tax_set_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        )
        assert result.tax_amount == Money.of("5", helpers.NGN)
        assert result.components[0].dependency_components[0].tax_amount.is_zero
        with pytest.raises(TaxRuleViolation, match="exactly one"):
            determine_tax_set_v2(
                session,
                tenant_id=tenant_id,
                fact=replace(
                    fact,
                    fact=replace(fact.fact, source_ref="no-prior"),
                    prior_results=(),
                ),
                determined_at=helpers.NOW,
            )


@pytest.mark.parametrize(
    "change",
    ["source", "amount", "date", "party", "supply", "place", "fingerprint", "seal"],
)
def test_dependency_refuses_changed_commercial_fact_or_expected_seal(
    dependency_database, change
):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, _ = _fixture(session, tenant_id)
        changes = {
            "amount": {"base_amount": Money.of("101", helpers.NGN)},
            "date": {"occurred_on": fact.fact.occurred_on + timedelta(days=1)},
            "party": {"counterparty_ref": "party:B"},
            "supply": {"supply_ref": "line:B"},
            "place": {"place_ref": "place:B"},
        }
        if change in changes:
            fact = replace(fact, fact=replace(fact.fact, **changes[change]))
        elif change == "source":
            fact = replace(
                fact, commercial_source=TaxCommercialSourceV1("invoice:B", "1")
            )
        else:
            ref = fact.prior_results[0]
            ref = replace(
                ref,
                **(
                    {"source_fingerprint": "0" * 64}
                    if change == "fingerprint"
                    else {"result_fingerprint": "rv2:" + "0" * 64}
                ),
            )
            fact = replace(fact, prior_results=(ref,))
        with pytest.raises(TaxConflict):
            determine_tax_set_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )


def test_missing_code_ambiguous_prior_and_duplicate_reference_are_refused(
    dependency_database,
):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        source, _, fact, _, _, target_input = _fixture(session, tenant_id)
        with pytest.raises(ValueError, match="duplicate"):
            replace(fact, prior_results=(fact.prior_results[0],) * 2)
        another = determine_tax_set_v2(
            session,
            tenant_id=tenant_id,
            fact=replace(
                source, fact=replace(source.fact, source_ref="another-output")
            ),
            determined_at=helpers.NOW,
        )
        ref = TaxPriorResultReferenceV1(
            another.id,
            another.source_ref,
            another.source_version,
            another.source_fingerprint,
            another.result_fingerprint,
        )
        with pytest.raises(TaxRuleViolation, match="exactly one"):
            determine_tax_set_v2(
                session,
                tenant_id=tenant_id,
                fact=replace(fact, prior_results=(*fact.prior_results, ref)),
                determined_at=helpers.NOW,
            )
        # The WHT code exists in the same jurisdiction but is absent from prior output.
        policy = replace(
            target_input.dependency_policy,
            selection="tax_code_ids",
            tax_code_ids=(target_input.tax_code_id,),
        )
        ensure_tax_rule_v2_in_transaction(
            session,
            tenant_id=tenant_id,
            command=replace(
                target_input, version=2, priority=20, dependency_policy=policy
            ),
        )
        with pytest.raises(TaxRuleViolation, match="missing"):
            determine_tax_set_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )


def test_v1_reads_and_writer_refuse_dependency_policy(dependency_database):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, target_rule, target_input = _fixture(session, tenant_id)
        with pytest.raises(TaxConflict, match="V2"):
            get_tax_rule(session, tenant_id=tenant_id, rule_id=target_rule.rule_id)
        with pytest.raises(TaxConflict, match="V2"):
            list_tax_rule_history(
                session,
                tenant_id=tenant_id,
                request=ListTaxRuleHistoryV1(tax_code_id=target_input.tax_code_id),
            )
        page = list_tax_rule_history_v2(
            session,
            tenant_id=tenant_id,
            request=ListTaxRuleHistoryV2(tax_code_id=target_input.tax_code_id),
        )
        assert page.items[0].rule.dependency_policy == target_input.dependency_policy
        with pytest.raises(TaxRuleViolation, match="V2|v2"):
            determine_tax_set(
                session, tenant_id=tenant_id, fact=fact.fact, determined_at=helpers.NOW
            )


def test_replay_survives_supersession_and_report_counts_own_tax_once(
    dependency_database,
):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, target_rule, target_input = _fixture(session, tenant_id)
        original = determine_tax_set_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        )
        replacement = replace(
            target_input,
            version=2,
            effective_from=fact.fact.occurred_on,
            rate=Decimal("0.1"),
        )
        change = TaxRuleSupersessionInputV2(
            target_rule.rule_id,
            1,
            replacement,
            replacement.effective_from,
            uuid4(),
            "editor",
            "rate adjustment",
            "approval:A",
            "policy:A",
            "1",
        )
        supersession = supersede_tax_rule_v2(
            session, tenant_id=tenant_id, command=change
        )
        assert (
            supersede_tax_rule_v2(session, tenant_id=tenant_id, command=change)
            == supersession
        )
        assert (
            determine_tax_set_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )
            == original
        )
        assert preview_tax_policy_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        ).tax_amount == Money.of("13", helpers.NGN)
        definition = create_statutory_report_definition(
            session,
            tenant_id=tenant_id,
            jurisdiction_id=fact.fact.jurisdiction_id,
            code="WHT",
            name="WHT",
            currency=helpers.NGN,
            payable_box_code="tax",
            boxes=(
                StatutoryReportBoxInput(
                    "tax",
                    "Own withholding",
                    1,
                    target_input.tax_code_id,
                    "tax_amount",
                    Decimal(1),
                ),
            ),
        )
        obligation = create_filing_obligation(
            session,
            tenant_id=tenant_id,
            definition_id=definition.id,
            obligation_ref="obligation:A",
            period_start=fact.fact.occurred_on,
            period_end=fact.fact.occurred_on,
            due_on=fact.fact.occurred_on,
            taxpayer_ref="taxpayer:A",
        )
        report = generate_statutory_report(
            session,
            tenant_id=tenant_id,
            obligation_id=obligation.id,
            generated_by_id=uuid4(),
            generated_at=helpers.NOW,
        )
        assert report.total_payable == Decimal("6.5")


def test_sealed_membership_tampering_and_populated_downgrade_refused(
    dependency_database,
):
    engine, (tenant_id, _), config = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, _ = _fixture(session, tenant_id)
        result = determine_tax_set_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        )
        session.commit()
    with _session(engine, tenant_id) as session:
        with pytest.raises(DBAPIError, match="sealed|commercial"):
            session.execute(
                text(
                    "INSERT INTO mod_tax.tax_determination_prior_sets "
                    "SELECT tenant_id, determination_set_id, prior_set_id, "
                    "source_ref, source_version, source_fingerprint, "
                    "result_fingerprint "
                    "FROM mod_tax.tax_determination_prior_sets "
                    "WHERE tenant_id = :tenant AND determination_set_id = :id"
                ),
                {"tenant": tenant_id, "id": result.id},
            )
    with _session(engine, tenant_id) as session:
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(
                text(
                    "UPDATE mod_tax.tax_determination_dependencies "
                    "SET tax_amount = tax_amount + 1 WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant_id},
            )
    with _session(engine, tenant_id) as session:
        with pytest.raises(DBAPIError, match="snapshot|sealed"):
            session.execute(
                text("""
                INSERT INTO mod_tax.tax_determination_dependencies
                SELECT * FROM mod_tax.tax_determination_dependencies
                WHERE tenant_id = :tenant
            """),
                {"tenant": tenant_id},
            )
    with _session(engine, tenant_id) as session:
        with pytest.raises(DBAPIError, match="published"):
            session.execute(
                text("""
                INSERT INTO mod_tax.tax_rule_dependency_codes
                    (tenant_id,rule_id,tax_code_id)
                SELECT tenant_id,rule_id,tax_code_id FROM mod_tax.tax_determinations
                WHERE tenant_id = :tenant AND determination_set_id = :set
            """),
                {"tenant": tenant_id, "set": result.id},
            )
    with pytest.raises(DBAPIError, match="populated V2"):
        alembic_command.downgrade(config, "tx_0004_rule_supersession")


def test_v1_parity_and_replay_are_unchanged_with_v2_source_available(
    dependency_database,
):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        source, prior, _, _, _, _ = _fixture(session, tenant_id)
        ordinary = replace(source.fact, source_ref="legacy-contract:output")
        v1 = determine_tax_set(
            session, tenant_id=tenant_id, fact=ordinary, determined_at=helpers.NOW
        )
        assert v1.result_fingerprint.startswith("rv1:")
        assert v1.source_amount == prior.source_amount
        assert v1.tax_amount == prior.tax_amount
        assert v1.gross_amount == prior.gross_amount
        assert [(part.base_amount, part.tax_amount) for part in v1.components] == [
            (part.base_amount, part.tax_amount) for part in prior.components
        ]
        assert (
            determine_tax_set(
                session,
                tenant_id=tenant_id,
                fact=ordinary,
                determined_at=helpers.NOW + timedelta(days=1),
            )
            == v1
        )
        # V1 cannot be laundered into a commercial source by referring to it as V2.
        with pytest.raises(ValueError, match="V2"):
            TaxPriorResultReferenceV1(
                v1.id,
                v1.source_ref,
                v1.source_version,
                v1.source_fingerprint,
                v1.result_fingerprint,
            )


def test_whole_owner_rollback_removes_new_policy_and_dependency_evidence(
    dependency_database,
):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, _ = _fixture(session, tenant_id)
        result = determine_tax_set_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        )
        assert result.tax_amount == Money.of("6.5", helpers.NGN)
        session.rollback()
    with _session(engine, tenant_id) as session:
        for table in (
            TaxRule,
            TaxRuleDependencyCode,
            TaxDeterminationSet,
            TaxDeterminationPriorSet,
            TaxDeterminationDependency,
        ):
            assert session.scalar(select(func.count()).select_from(table)) == 0


def test_database_refuses_old_a6_source_only_component_under_dependency_rule(
    dependency_database,
):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, target_rule, _ = _fixture(session, tenant_id)
        result = determine_tax_set_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        )
        session.commit()
    with _session(engine, tenant_id) as session:
        set_id = uuid4()
        session.execute(
            text("""
            INSERT INTO mod_tax.tax_determination_sets
                (id,tenant_id,jurisdiction_id,occurred_on,fact_kind,
                 recognition_basis_code,transaction_side,
                 source_amount,net_amount,tax_amount,gross_amount,currency_code,minor_units,
                 source_ref,source_version,source_fingerprint,result_seal_state,evidence_ref,
                 counterparty_ref,supply_ref,place_ref,determined_at)
            SELECT :id,tenant_id,jurisdiction_id,occurred_on,fact_kind,
                 recognition_basis_code,transaction_side,
                 source_amount,source_amount,5,source_amount+5,currency_code,minor_units,
                 'a6-old-writer','1',source_fingerprint,'building',evidence_ref,
                 counterparty_ref,supply_ref,place_ref,determined_at
            FROM mod_tax.tax_determination_sets
            WHERE tenant_id = :tenant AND id = :existing
        """),
            {"id": set_id, "tenant": tenant_id, "existing": result.id},
        )
        with pytest.raises(DBAPIError, match="V2"):
            session.execute(
                text("""
                INSERT INTO mod_tax.tax_determinations
                    (id,tenant_id,determination_set_id,component_sequence,jurisdiction_id,tax_code_id,
                     rule_id,rule_version,occurred_on,fact_kind,
                 recognition_basis_code,transaction_side,
                     treatment_code,calculation_base_code,inclusive,base_amount,tax_amount,
                     recoverable_amount,non_recoverable_amount,currency_code,minor_units,
                     source_ref,source_version,source_fingerprint,evidence_ref,
                     counterparty_ref,determined_at)
                SELECT :id,tenant_id,:set,component_sequence,
                     jurisdiction_id,tax_code_id,
                     rule_id,rule_version,occurred_on,fact_kind,
                 recognition_basis_code,transaction_side,
                     treatment_code,calculation_base_code,inclusive,100,5,0,5,
                     currency_code,minor_units,
                     'a6-old-writer','1',source_fingerprint,evidence_ref,
                     counterparty_ref,determined_at
                FROM mod_tax.tax_determinations
                WHERE tenant_id = :tenant AND rule_id = :rule
            """),
                {
                    "id": uuid4(),
                    "set": set_id,
                    "tenant": tenant_id,
                    "rule": target_rule.rule_id,
                },
            )


def test_real_rls_catalog_and_snapshot_tenant_fk_boundary(dependency_database):
    engine, (tenant_id, other), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, _ = _fixture(session, tenant_id, selected=True)
        result = determine_tax_set_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        )
        session.commit()
    with _session(engine, other) as session:
        for table in (
            TaxRuleDependencyCode,
            TaxDeterminationPriorSet,
            TaxDeterminationDependency,
        ):
            catalog = session.execute(
                text("""
                SELECT relrowsecurity,relforcerowsecurity FROM pg_class
                WHERE oid = CAST(:table AS regclass)
            """),
                {"table": f"mod_tax.{table.__tablename__}"},
            ).one()
            assert catalog == (True, True)
            assert session.scalar(select(func.count()).select_from(table)) == 0
        with pytest.raises(DBAPIError):
            session.execute(
                text("""
                INSERT INTO mod_tax.tax_determination_prior_sets
                    (tenant_id,determination_set_id,prior_set_id,source_ref,source_version,source_fingerprint,result_fingerprint)
                VALUES (:tenant,:set,:prior,'invoice:A:output','1',:source,:result)
            """),
                {
                    "tenant": other,
                    "set": uuid4(),
                    "prior": result.id,
                    "source": "0" * 64,
                    "result": "rv2:" + "0" * 64,
                },
            )


def test_competing_source_writers_require_whole_owner_retry(dependency_database):
    from threading import Barrier, Thread

    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError

    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, _ = _fixture(session, tenant_id)
        session.commit()
    rendezvous = Barrier(2)
    outcomes = []

    def both_read_absent(_connection, _cursor, statement, parameters, _context, _many):
        if (
            "tax_determination_sets.source_ref =" in statement
            and isinstance(parameters, dict)
            and fact.fact.source_ref in parameters.values()
        ):
            rendezvous.wait(timeout=15)

    def author():
        try:
            with _session(engine, tenant_id) as session:
                result = determine_tax_set_v2(
                    session,
                    tenant_id=tenant_id,
                    fact=fact,
                    determined_at=helpers.NOW,
                )
                session.commit()
                outcomes.append(result)
        except IntegrityError as exc:
            outcomes.append(exc)

    event.listen(engine, "after_cursor_execute", both_read_absent)
    threads = [Thread(target=author), Thread(target=author)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert all(not thread.is_alive() for thread in threads)
    finally:
        event.remove(engine, "after_cursor_execute", both_read_absent)
    assert len(outcomes) == 2
    winners = [result for result in outcomes if not isinstance(result, Exception)]
    conflicts = [result for result in outcomes if isinstance(result, IntegrityError)]
    assert len(winners) == len(conflicts) == 1
    with _session(engine, tenant_id) as session:
        retry = determine_tax_set_v2(
            session,
            tenant_id=tenant_id,
            fact=fact,
            determined_at=helpers.NOW,
        )
        assert retry == winners[0]
        assert (
            session.scalar(select(func.count()).select_from(TaxDeterminationSet)) == 2
        )


def test_currency_recognition_and_unsealed_or_tampered_prior_are_refused(
    dependency_database,
):
    from dotmac_kernel.money import Currency
    from sqlalchemy.orm.attributes import set_committed_value

    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, prior, fact, _, _, _ = _fixture(session, tenant_id)
        with pytest.raises(TaxRuleViolation, match="currency"):
            determine_tax_set_v2(
                session,
                tenant_id=tenant_id,
                fact=replace(
                    fact,
                    fact=replace(
                        fact.fact, base_amount=Money.of("100", Currency("USD", 2))
                    ),
                ),
                determined_at=helpers.NOW,
            )
        with pytest.raises(TaxConflict, match="commercial"):
            determine_tax_set_v2(
                session,
                tenant_id=tenant_id,
                fact=replace(
                    fact, fact=replace(fact.fact, recognition_basis_code="settled")
                ),
                determined_at=helpers.NOW,
            )
        row = session.scalar(
            select(TaxDeterminationSet).where(
                TaxDeterminationSet.tenant_id == tenant_id,
                TaxDeterminationSet.id == prior.id,
            )
        )
        set_committed_value(row, "result_seal_state", "building")
        with pytest.raises(TaxConflict, match="sealed"):
            determine_tax_set_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )
        set_committed_value(row, "result_seal_state", "sealed")
        set_committed_value(row, "commercial_source_ref", "tampered")
        with pytest.raises(TaxConflict, match="fingerprint"):
            determine_tax_set_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )


def test_inclusive_and_combined_external_bases_are_explicitly_refused(
    dependency_database,
):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, target_input = _fixture(session, tenant_id)
        with pytest.raises(TaxRuleViolation, match="inclusive"):
            ensure_tax_rule_v2_in_transaction(
                session,
                tenant_id=tenant_id,
                command=replace(target_input, version=2, inclusive=True),
            )
        extra = create_tax_code(
            session,
            tenant_id=tenant_id,
            jurisdiction_id=fact.fact.jurisdiction_id,
            code="LOCAL-COMPOUND",
            name="Local compound",
            tax_kind_code="custom",
        )
        ensure_tax_rule_v2_in_transaction(
            session,
            tenant_id=tenant_id,
            command=replace(
                _input(extra.id, side="withholding", sequence=200),
                calculation_base_code="source_plus_prior_tax",
            ),
        )
        with pytest.raises(TaxRuleViolation, match="external dependency"):
            determine_tax_set_v2(
                session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
            )


def test_predecessor_can_itself_have_an_ordered_compound_component(dependency_database):
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        source, _, fact, _, _, _ = _fixture(session, tenant_id)
        extra = create_tax_code(
            session,
            tenant_id=tenant_id,
            jurisdiction_id=fact.fact.jurisdiction_id,
            code="PRIOR-COMPOUND",
            name="Prior compound",
            tax_kind_code="custom",
        )
        ensure_tax_rule_v2_in_transaction(
            session,
            tenant_id=tenant_id,
            command=replace(
                _input(extra.id, sequence=300),
                calculation_base_code="source_plus_prior_tax",
            ),
        )
        prior = determine_tax_set_v2(
            session,
            tenant_id=tenant_id,
            fact=replace(
                source, fact=replace(source.fact, source_ref="invoice:A:compound")
            ),
            determined_at=helpers.NOW,
        )
        assert prior.tax_amount == Money.of("43", helpers.NGN)
        reference = TaxPriorResultReferenceV1(
            prior.id,
            prior.source_ref,
            prior.source_version,
            prior.source_fingerprint,
            prior.result_fingerprint,
        )
        result = determine_tax_set_v2(
            session,
            tenant_id=tenant_id,
            fact=replace(fact, prior_results=(reference,)),
            determined_at=helpers.NOW,
        )
        assert result.components[0].base_amount == Money.of("143", helpers.NGN)
        assert result.tax_amount == Money.of("7.15", helpers.NGN)


def test_prior_from_another_authority_is_refused(dependency_database):
    from dotmac_tax import (
        TaxAuthorityInput,
        TaxJurisdictionInput,
        create_tax_authority,
        create_tax_jurisdiction,
    )

    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, _ = _fixture(session, tenant_id)
        authority = create_tax_authority(
            session,
            tenant_id=tenant_id,
            command=TaxAuthorityInput("OTHER-AUTHORITY", "Other authority"),
        )
        jurisdiction = create_tax_jurisdiction(
            session,
            tenant_id=tenant_id,
            command=TaxJurisdictionInput(
                authority.id,
                "OTHER-JURISDICTION",
                "Other jurisdiction",
                "NG",
                helpers.NGN,
            ),
        )
        with pytest.raises(TaxConflict, match="authority"):
            determine_tax_set_v2(
                session,
                tenant_id=tenant_id,
                fact=replace(
                    fact, fact=replace(fact.fact, jurisdiction_id=jurisdiction.id)
                ),
                determined_at=helpers.NOW,
            )


def test_late_dependency_membership_waits_for_parent_lock_then_refuses(
    dependency_database,
):
    """A late child writer must acquire the same row lock as parent sealing."""
    from threading import Event, Thread
    from time import monotonic

    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, _, fact, _, _, _ = _fixture(session, tenant_id)
        result = determine_tax_set_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        )
        session.commit()
    ready = Event()
    finished = Event()
    child_pid = []
    outcomes = []

    def append_late():
        with _session(engine, tenant_id) as writer:
            child_pid.append(
                writer.execute(text("SELECT pg_backend_pid()")).scalar_one()
            )
            ready.set()
            try:
                writer.execute(
                    text("""
                    INSERT INTO mod_tax.tax_determination_dependencies
                    SELECT * FROM mod_tax.tax_determination_dependencies
                    WHERE tenant_id = :tenant AND determination_id = :component
                """),
                    {
                        "tenant": tenant_id,
                        "component": result.components[0].determination_id,
                    },
                )
            except DBAPIError as exc:
                outcomes.append(str(exc))
            finally:
                finished.set()

    with _session(engine, tenant_id) as publisher:
        publisher_pid = publisher.execute(text("SELECT pg_backend_pid()")).scalar_one()
        publisher.execute(
            text("""
            SELECT id FROM mod_tax.tax_determination_sets
            WHERE tenant_id = :tenant AND id = :id FOR NO KEY UPDATE
        """),
            {"tenant": tenant_id, "id": result.id},
        )
        thread = Thread(target=append_late)
        thread.start()
        try:
            assert ready.wait(timeout=10)
            deadline = monotonic() + 10
            blocked = False
            while monotonic() < deadline and not finished.is_set():
                blockers = publisher.execute(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": child_pid[0]}
                ).scalar_one()
                if publisher_pid in blockers:
                    blocked = True
                    break
                finished.wait(timeout=0.02)
            assert blocked, "child membership skipped the parent's seal lock"
            assert not finished.is_set()
        finally:
            publisher.commit()
            thread.join(timeout=15)
        assert not thread.is_alive()
    assert finished.is_set()
    assert len(outcomes) == 1
    assert "snapshot" in outcomes[0] or "sealed" in outcomes[0]


def _sql_clone_building_set(session, tenant_id, original_id):
    identity = uuid4()
    session.execute(
        text("""
        INSERT INTO mod_tax.tax_determination_sets
        SELECT (jsonb_populate_record(NULL::mod_tax.tax_determination_sets,
            to_jsonb(original) || jsonb_build_object('id',CAST(:id AS text),
                'source_ref',CAST(:source AS text), 'result_seal_state','building',
                'result_fingerprint',NULL))).*
        FROM mod_tax.tax_determination_sets original
        WHERE tenant_id = :tenant AND id = :original
    """),
        {
            "tenant": tenant_id,
            "original": original_id,
            "id": identity,
            "source": f"sql:{identity}",
        },
    )
    return identity


@pytest.mark.parametrize("selection", ["all_prior", "tax_code_ids"])
def test_app_role_cannot_construct_duplicate_prior_code_at_another_sequence(
    dependency_database, selection
):
    """The released set-code unique also prevents the ambiguity reaching sealing."""
    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, prior, _, _, _, _ = _fixture(
            session, tenant_id, selected=selection == "tax_code_ids"
        )
        session.commit()
    with _session(engine, tenant_id) as session:
        identity = _sql_clone_building_set(session, tenant_id, prior.id)
        first = prior.components[0]
        for sequence in (100, 200):
            arguments = {
                "tenant": tenant_id,
                "original": first.determination_id,
                "id": uuid4(),
                "set": identity,
                "sequence": sequence,
            }
            statement = text("""
                INSERT INTO mod_tax.tax_determinations
                SELECT (jsonb_populate_record(NULL::mod_tax.tax_determinations,
                    to_jsonb(original) || jsonb_build_object('id',CAST(:id AS text),
                        'determination_set_id',CAST(:set AS text),
                        'component_sequence',CAST(:sequence AS integer)))).*
                FROM mod_tax.tax_determinations original
                WHERE tenant_id = :tenant AND id = :original
            """)
            if sequence == 100:
                session.execute(statement, arguments)
            else:
                with pytest.raises(DBAPIError, match="uq_tax_determinations_set_code"):
                    session.execute(statement, arguments)


def test_app_role_seal_explicitly_requires_every_selected_code(dependency_database):
    from dotmac_tax._dependencies import policy_payload

    engine, (tenant_id, _), _ = dependency_database
    with _session(engine, tenant_id) as session:
        _, prior, fact, _, _, original_input = _fixture(session, tenant_id)
        original = determine_tax_set_v2(
            session, tenant_id=tenant_id, fact=fact, determined_at=helpers.NOW
        )
        # A exists; B is a tenant code but is absent from the output purpose.
        policy = replace(
            original_input.dependency_policy,
            selection="tax_code_ids",
            tax_code_ids=(prior.components[0].tax_code_id, original_input.tax_code_id),
        )
        rule = ensure_tax_rule_v2_in_transaction(
            session,
            tenant_id=tenant_id,
            command=replace(
                original_input, version=2, priority=20, dependency_policy=policy
            ),
        )
        session.commit()
    with _session(engine, tenant_id) as session:
        identity = _sql_clone_building_set(session, tenant_id, original.id)
        session.execute(
            text("""
            INSERT INTO mod_tax.tax_determination_prior_sets
            SELECT tenant_id,:set,prior_set_id,source_ref,source_version,
                source_fingerprint,result_fingerprint
            FROM mod_tax.tax_determination_prior_sets
            WHERE tenant_id = :tenant AND determination_set_id = :original
        """),
            {"tenant": tenant_id, "set": identity, "original": original.id},
        )
        component_id = uuid4()
        session.execute(
            text("""
            INSERT INTO mod_tax.tax_determinations
            SELECT (jsonb_populate_record(NULL::mod_tax.tax_determinations,
                to_jsonb(original) || jsonb_build_object('id',CAST(:id AS text),
                    'determination_set_id',CAST(:set AS text),
                    'rule_id',CAST(:rule AS text),'rule_version',2,
                    'selected_dependency_policy',CAST(:policy AS jsonb)))).*
            FROM mod_tax.tax_determinations original
            WHERE tenant_id = :tenant AND id = :original
        """),
            {
                "tenant": tenant_id,
                "id": component_id,
                "set": identity,
                "rule": rule.rule_id,
                "policy": json.dumps(policy_payload(policy)),
                "original": original.components[0].determination_id,
            },
        )
        session.execute(
            text("""
            INSERT INTO mod_tax.tax_determination_dependencies
            SELECT tenant_id,:component,prior_determination_id,prior_set_id,tax_code_id,
                rule_id,rule_version,component_sequence,tax_amount,currency_code,minor_units
            FROM mod_tax.tax_determination_dependencies
            WHERE tenant_id = :tenant AND determination_id = :original
                AND tax_code_id = :code
        """),
            {
                "tenant": tenant_id,
                "component": component_id,
                "original": original.components[0].determination_id,
                "code": prior.components[0].tax_code_id,
            },
        )
        with pytest.raises(DBAPIError, match="selected a missing prior tax code"):
            session.execute(
                text("""
                UPDATE mod_tax.tax_determination_sets SET result_seal_state = 'sealed',
                    result_fingerprint = :fingerprint
                WHERE tenant_id = :tenant AND id = :id
            """),
                {"tenant": tenant_id, "id": identity, "fingerprint": "rv2:" + "0" * 64},
            )


def test_public_v2_result_getter_is_scoped_historical_and_read_only(
    dependency_database,
):
    engine, (tenant_id, other_tenant), _ = dependency_database
    with _session(engine, tenant_id) as session:
        source, prior, _, _, _, _ = _fixture(session, tenant_id)
        legacy = determine_tax_set(
            session,
            tenant_id=tenant_id,
            fact=replace(source.fact, source_ref="legacy:read"),
            determined_at=helpers.NOW,
        )
        session.commit()
    with _session(engine, tenant_id) as session:
        with owner_helpers.participant_guard(session):
            assert (
                get_tax_determination_set_v2(
                    session, tenant_id=tenant_id, determination_set_id=prior.id
                )
                == prior
            )
        for identity in (uuid4(), legacy.id):
            with pytest.raises(TaxNotFound):
                get_tax_determination_set_v2(
                    session, tenant_id=tenant_id, determination_set_id=identity
                )
    for field, changed in (
        ("result_fingerprint", "rv2:" + "0" * 64),
        ("source_fingerprint", "0" * 64),
        ("result_seal_state", "building"),
    ):
        with _session(engine, tenant_id) as session:
            # Autoflush is enabled deliberately: the getter must suppress it.
            row = session.get(TaxDeterminationSet, prior.id)
            session.autoflush = True
            setattr(row, field, changed)
            with owner_helpers.participant_guard(session), pytest.raises(TaxConflict):
                get_tax_determination_set_v2(
                    session, tenant_id=tenant_id, determination_set_id=prior.id
                )
            session.rollback()
    with _session(engine, tenant_id) as session:
        assert (
            get_tax_determination_set_v2(
                session, tenant_id=tenant_id, determination_set_id=prior.id
            )
            == prior
        )
    with _session(engine, other_tenant) as session, pytest.raises(TaxNotFound):
        get_tax_determination_set_v2(
            session, tenant_id=other_tenant, determination_set_id=prior.id
        )
