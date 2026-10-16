"""Hosted migrated-PostgreSQL canaries for public rated invoice authoring."""

import threading
from dataclasses import FrozenInstanceError, fields, replace
from uuid import uuid4

import pytest
from dotmac_billing import (
    GetInvoiceDocumentFactV1,
    IssuedRatedInvoiceV1,
    IssueRatedInvoiceV1,
    get_invoice_document_fact,
    issue_rated_invoice,
    models,
    service,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.service import create_billing_account
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.idempotency_models import (
    IdempotencyRecord,
    PlatformIdempotencyRecord,
)
from dotmac_kernel.messaging.models import OutboxEvent, PlatformOutboxEvent
from dotmac_kernel.money import Currency, Money
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from dotmac_numbering import (
    SeriesConfiguration,
    allocate,
    configure_series,
)
from dotmac_numbering import (
    models as numbering_models,
)
from sqlalchemy import func, select, text

from tests import test_billing_isolation as billing


@pytest.fixture(scope="module")
def scratch():
    # Extend the existing real migrated Billing scratch database with Numbering
    # through its public lineage, never metadata.create_all or handwritten DDL.
    from alembic import command
    from alembic.config import Config

    planes = (ModulePlane.TENANT, ModulePlane.PLATFORM)
    with billing._scratch_database(planes) as urls:
        config = Config(str(billing.REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(billing.REPO_ROOT / "alembic"))
        numbering_versions = (
            billing.REPO_ROOT
            / "packages/dotmac-numbering/src/dotmac_numbering/migrations/versions"
        )
        config.set_main_option(
            "version_locations",
            f"{billing.KERNEL_VERSIONS} {billing.ASSEMBLY_VERSIONS} "
            f"{billing.BILLING_VERSIONS} {numbering_versions}",
        )
        config.attributes["module_plane_selections"] = tuple(
            ModulePlaneSelection(module=name, planes=planes)
            for name in ("billing", "numbering")
        )
        command.upgrade(config, "heads")
        yield urls


class RealNumbering:
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    def allocate(self, db, **kwargs):
        self.calls += 1
        result = allocate(db, **kwargs)
        if self.fail:
            raise ValueError("injected failure after number allocation")
        return result.formatted_number


def _configure_numbering(session, scope, command):
    configure_series(
        session,
        scope=scope,
        configuration=SeriesConfiguration(
            series_code=command.series_code,
            prefix="PUBLIC",
            min_digits=6,
        ),
    )


class Sources:
    def require_obligation_source(self, code):
        if code != "recurring_charge_occurrence":
            raise ValueError("undeclared obligation source")
        return "subscriptions"


class Numbering(billing._Numbering):
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    def allocate(self, *args, **kwargs):
        self.calls += 1
        if self.fail:
            raise ValueError("numbering unavailable")
        return super().allocate(*args, **kwargs)


def _command(account_id, scope):
    template = billing._draft_command(uuid4())
    snapshots = {
        field.name: getattr(template, field.name)
        for field in fields(template)
        if field.name not in {"obligation_id", "quantity", "due_date_basis"}
    }
    issue = billing._issue_command(uuid4())
    return IssueRatedInvoiceV1(
        obligation=billing._obligation_command(
            account_id, scope=scope, source_fact_id=str(uuid4())
        ),
        **snapshots,
        series_code=issue.series_code,
        reference_date=issue.reference_date,
        due_at=issue.due_at,
        due_date_basis=issue.due_date_basis,
        actor_ref=issue.actor_ref,
        correlation_id="public-rated-invoice",
    )


def _issue(session, scope, command, numbering=None):
    return issue_rated_invoice(
        session,
        scope=scope,
        command=command,
        obligation_sources=Sources(),
        numbering=numbering or Numbering(),
    )


def test_public_authoring_cross_scope_canary(scratch):
    admin_url, tenant_url, platform_url = scratch
    left, right = billing._create_tenants(admin_url)
    left_scope = TenantScope(left)
    with billing._session(tenant_url, left_scope) as session:
        account = create_billing_account(
            session,
            scope=left_scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        account_id = account.id
        session.commit()
    command = _command(account_id, left_scope)
    for url, routed, offered in (
        (tenant_url, TenantScope(right), command),
        (platform_url, PlatformScope(), command),
        (
            tenant_url,
            TenantScope(right),
            replace(
                command,
                obligation=replace(command.obligation, scope=TenantScope(right)),
            ),
        ),
        (
            platform_url,
            PlatformScope(),
            replace(
                command, obligation=replace(command.obligation, scope=PlatformScope())
            ),
        ),
    ):
        with billing._session(url, routed) as session:
            numbering = Numbering()
            with pytest.raises(BillingRuleViolation):
                _issue(session, routed, offered, numbering)
            assert numbering.calls == 0
    with billing._session(tenant_url, TenantScope(right)) as session:
        assert (
            session.execute(
                select(models.BillingAccount.id).where(
                    models.BillingAccount.id == account_id
                )
            ).first()
            is None
        )
        session.execute(text("RESET app.current_tenant"))
        assert session.execute(select(models.BillingAccount.id)).first() is None


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_exact_replay_stable_ids_and_changed_replay_refusal(scratch, plane):
    scope = (
        TenantScope(billing._create_tenants(scratch[0], 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = _command(account.id, scope)
        numbering = Numbering()
        result = _issue(session, scope, command, numbering)
        assert isinstance(result, IssuedRatedInvoiceV1)
        assert result.scope == scope
        assert result.billing_account_id == account.id
        assert result.line_number == result.issued_fact_version == 1
        assert not hasattr(result, "_sa_instance_state")
        with pytest.raises(FrozenInstanceError):
            result.line_number = 2
        session.commit()
    with billing._session(url, scope) as session:
        assert _issue(session, scope, command, numbering) == result
        for changed in (
            replace(command, description="changed"),
            replace(command, series_code="changed"),
            replace(command, correlation_id="changed"),
            replace(
                command, obligation=replace(command.obligation, subject_ref="changed")
            ),
            replace(
                command,
                seller_snapshot=replace(command.seller_snapshot, legal_name="changed"),
            ),
        ):
            with pytest.raises(IdempotencyConflict):
                _issue(session, scope, changed, numbering)
        fact = get_invoice_document_fact(
            session,
            scope=scope,
            request=GetInvoiceDocumentFactV1(
                scope, result.invoice_id, result.issued_fact_version
            ),
        ).fact
        assert fact.tax_snapshots == command.obligation.tax_snapshots
        assert fact.fx_snapshot == command.obligation.fx_snapshot
        assert fact.seller_snapshot == command.seller_snapshot
        assert fact.customer_snapshot == command.customer_snapshot
        assert fact.party_tax_identities == command.party_tax_identities
        assert fact.lines[0].quantity == 1
        assert fact.grand_total == command.obligation.total_amount
        assert numbering.calls == 1
        prefix = "" if plane == "tenant" else "Platform"
        line_model = getattr(models, prefix + "DocumentLine")
        line = session.execute(
            select(line_model).where(line_model.id == result.line_id)
        ).scalar_one()
        assert (
            line.document_id,
            line.obligation_id,
            line.billing_account_id,
            line.line_number,
        ) == (
            result.invoice_id,
            result.obligation_id,
            result.billing_account_id,
            result.line_number,
        )
        group_model = getattr(models, prefix + "PostingGroup")
        accounting_model = getattr(models, prefix + "AccountingFact")
        assert (
            session.scalar(
                select(func.count())
                .select_from(accounting_model)
                .join(group_model, accounting_model.posting_group_id == group_model.id)
                .where(group_model.billing_account_id == result.billing_account_id)
            )
            == 1
        )
        for model_name, column, identity in (
            ("RatedObligation", "id", result.obligation_id),
            ("BillingDocument", "id", result.invoice_id),
            ("DocumentLine", "document_id", result.invoice_id),
            ("PostingGroup", "billing_account_id", result.billing_account_id),
            ("InvoiceDocumentFact", "document_id", result.invoice_id),
        ):
            model = getattr(models, prefix + model_name)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(getattr(model, column) == identity)
                )
                == 1
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("failure", ["numbering", "after_fact"])
def test_failure_rolls_back_only_authoring_operation(
    scratch, plane, failure, monkeypatch
):
    scope = (
        TenantScope(billing._create_tenants(scratch[0], 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = replace(_command(account.id, scope), series_code=f"inv_{uuid4().hex}")
        _configure_numbering(session, scope, command)
        root = session.get_transaction()
        prefix = "" if plane == "tenant" else "Platform"
        watched = [
            getattr(models, prefix + name)
            for name in (
                "RatedObligation",
                "BillingDocument",
                "DocumentLine",
                "PostingGroup",
                "InvoiceDocumentFact",
                "AccountingFact",
                "AppliedTaxSnapshot",
                "AppliedFxSnapshot",
            )
        ]
        watched.extend(
            getattr(numbering_models, prefix + name)
            for name in (
                "AllocationReceipt",
                "SeriesCounter",
            )
        )
        watched.extend(
            (
                IdempotencyRecord if plane == "tenant" else PlatformIdempotencyRecord,
                OutboxEvent if plane == "tenant" else PlatformOutboxEvent,
            )
        )
        before = [
            session.scalar(select(func.count()).select_from(model)) for model in watched
        ]
        original = service._document_fact
        if failure == "after_fact":

            def fail_after_fact(*args, **kwargs):
                original(*args, **kwargs)
                raise ValueError("injected issuance failure")

            monkeypatch.setattr(service, "_document_fact", fail_after_fact)
        with pytest.raises(ValueError):
            _issue(session, scope, command, RealNumbering(fail=failure == "numbering"))
        assert session.get_transaction() is root and root.is_active
        assert [
            session.scalar(select(func.count()).select_from(model)) for model in watched
        ] == before
        assert session.get(type(account), account.id) is not None
        if failure == "after_fact":
            monkeypatch.setattr(service, "_document_fact", original)
        result = _issue(session, scope, command, RealNumbering())
        assert result.invoice_id is not None
        receipt_model = getattr(numbering_models, prefix + "AllocationReceipt")
        receipt = session.execute(
            select(receipt_model).where(
                receipt_model.idempotency_key == f"billing:{result.invoice_id}"
            )
        ).scalar_one()
        assert receipt.allocated_value == 1
        session.commit()


def test_undeclared_source_and_unverified_due_basis_refuse_before_acceptance(scratch):
    scope = TenantScope(billing._create_tenants(scratch[0], 1)[0])
    with billing._session(scratch[1], scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = _command(account.id, scope)
        with pytest.raises(ValueError, match="undeclared"):
            _issue(
                session,
                scope,
                replace(
                    command,
                    obligation=replace(command.obligation, source_kind="untrusted"),
                ),
            )
        with pytest.raises(BillingRuleViolation, match="verified"):
            _issue(
                session,
                scope,
                replace(
                    command,
                    due_date_basis=command.due_date_basis.unknown_unverified(
                        source_authority="sub", evidence_ref="unknown"
                    ),
                ),
            )
        assert (
            session.scalar(select(func.count()).select_from(models.RatedObligation))
            == 0
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("fault", ["missing", "currency", "precision", "sum"])
def test_invalid_applied_tax_evidence_refuses_atomically(scratch, plane, fault):
    scope = (
        TenantScope(billing._create_tenants(scratch[0], 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        valid = _command(account.id, scope)
        tax = valid.obligation.tax_snapshots[0]
        if fault == "missing":
            snapshots = ()
        elif fault == "sum":
            snapshots = (replace(tax, tax_amount=billing._money("19")),)
        else:
            identity = Currency("USD", 2) if fault == "currency" else Currency("NGN", 3)
            snapshots = (
                replace(
                    tax,
                    taxable_basis=Money.of("100", identity),
                    tax_amount=Money.of("20", identity),
                ),
            )
        invalid = replace(
            valid, obligation=replace(valid.obligation, tax_snapshots=snapshots)
        )
        prefix = "" if plane == "tenant" else "Platform"
        watched = [
            getattr(models, prefix + name)
            for name in (
                "RatedObligation",
                "BillingDocument",
                "DocumentLine",
                "PostingGroup",
                "InvoiceDocumentFact",
                "AccountingFact",
                "AppliedTaxSnapshot",
                "AppliedFxSnapshot",
            )
        ]
        watched.extend(
            (
                IdempotencyRecord if plane == "tenant" else PlatformIdempotencyRecord,
                OutboxEvent if plane == "tenant" else PlatformOutboxEvent,
            )
        )
        before = [
            session.scalar(select(func.count()).select_from(model)) for model in watched
        ]
        root = session.get_transaction()
        numbering = Numbering()
        expected = (
            "tax_snapshot_total_mismatch"
            if fault in {"missing", "sum"}
            else "tax_money_identity_mismatch"
        )
        with pytest.raises(BillingRuleViolation) as error:
            _issue(session, scope, invalid, numbering)
        assert error.value.code == f"billing.{expected}"
        assert numbering.calls == 0
        assert session.get_transaction() is root and root.is_active
        assert [
            session.scalar(select(func.count()).select_from(model)) for model in watched
        ] == before
        assert session.get(type(account), account.id) is not None
        result = _issue(session, scope, valid, numbering)
        assert _issue(session, scope, valid, numbering) == result
        assert numbering.calls == 1
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("fault", ["currency", "precision"])
def test_rated_money_must_match_account_identity(scratch, plane, fault):
    scope = (
        TenantScope(billing._create_tenants(scratch[0], 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = _command(account.id, scope)
        identity = Currency("USD", 2) if fault == "currency" else Currency("NGN", 3)
        invalid = replace(
            command,
            obligation=replace(
                command.obligation,
                pre_tax_amount=Money.of("100", identity),
                tax_amount=Money.of("20", identity),
                total_amount=Money.of("120", identity),
            ),
        )
        numbering = Numbering()
        with pytest.raises(BillingRuleViolation) as error:
            _issue(session, scope, invalid, numbering)
        assert error.value.code == "billing.money_identity_mismatch"
        assert numbering.calls == 0
        prefix = "" if plane == "tenant" else "Platform"
        model = getattr(models, prefix + "RatedObligation")
        assert (
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.billing_account_id == account.id)
            )
            == 0
        )
        assert _issue(session, scope, command).billing_account_id == account.id


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_source_fact_version_preserves_existing_natural_identity(scratch, plane):
    scope = (
        TenantScope(billing._create_tenants(scratch[0], 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = _command(account.id, scope)
        original = _issue(session, scope, command)
        revised = replace(
            command, obligation=replace(command.obligation, source_fact_version="2")
        )
        successor = _issue(session, scope, revised)
        assert successor.obligation_id != original.obligation_id
        assert successor.invoice_id != original.invoice_id
        assert _issue(session, scope, command) == original
        assert _issue(session, scope, revised) == successor
        # New source versions are new identities, not invoice corrections.
        # A caller must route correction of a documented obligation to its owner.
        corrected = replace(
            command,
            obligation=replace(
                command.obligation,
                source_fact_version="3",
                supersedes_obligation_id=original.obligation_id,
            ),
        )
        with pytest.raises(BillingConflict) as error:
            _issue(session, scope, corrected)
        assert error.value.code == "billing.obligation_already_documented"


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_successful_authoring_remains_subject_to_caller_root_rollback(scratch, plane):
    scope = (
        TenantScope(billing._create_tenants(scratch[0], 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = replace(_command(account.id, scope), series_code=f"inv_{uuid4().hex}")
        _configure_numbering(session, scope, command)
        session.commit()
        prefix = "" if plane == "tenant" else "Platform"
        watched = [
            getattr(models, prefix + name)
            for name in (
                "RatedObligation",
                "BillingDocument",
                "DocumentLine",
                "PostingGroup",
                "InvoiceDocumentFact",
                "AccountingFact",
            )
        ]
        watched.extend(
            getattr(numbering_models, prefix + name)
            for name in (
                "AllocationReceipt",
                "SeriesCounter",
            )
        )
        watched.extend(
            (
                IdempotencyRecord if plane == "tenant" else PlatformIdempotencyRecord,
                OutboxEvent if plane == "tenant" else PlatformOutboxEvent,
            )
        )
        before = [
            session.scalar(select(func.count()).select_from(model)) for model in watched
        ]
        original = _issue(session, scope, command, RealNumbering())
        session.rollback()  # The caller, not the Billing service, owns the root.
    with billing._session(url, scope) as session:
        assert [
            session.scalar(select(func.count()).select_from(model)) for model in watched
        ] == before
        replacement = _issue(session, scope, command, RealNumbering())
        assert replacement.invoice_id != original.invoice_id
        receipt_model = getattr(numbering_models, prefix + "AllocationReceipt")
        receipt = session.execute(
            select(receipt_model).where(
                receipt_model.idempotency_key == f"billing:{replacement.invoice_id}"
            )
        ).scalar_one()
        assert receipt.allocated_value == 1
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_concurrent_public_retries_allocate_one_number_and_effect(scratch, plane):
    scope = (
        TenantScope(billing._create_tenants(scratch[0], 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )
    url = scratch[1] if plane == "tenant" else scratch[2]
    with billing._session(url, scope) as session:
        account = create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        command = replace(_command(account.id, scope), series_code=f"inv_{uuid4().hex}")
        _configure_numbering(session, scope, command)
        session.commit()
    barrier = threading.Barrier(2)
    results, errors = [], []
    numbering = RealNumbering()

    def issue_and_commit():
        try:
            with billing._session(url, scope) as session:
                barrier.wait(timeout=10)
                result = _issue(session, scope, command, numbering)
                session.commit()
                results.append(result)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=issue_and_commit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2 and results[0] == results[1]
    assert numbering.calls == 1
    with billing._session(url, scope) as session:
        prefix = "" if plane == "tenant" else "Platform"
        for name, field, value in (
            ("BillingDocument", "id", results[0].invoice_id),
            ("DocumentLine", "document_id", results[0].invoice_id),
            ("InvoiceDocumentFact", "document_id", results[0].invoice_id),
            ("PostingGroup", "billing_account_id", results[0].billing_account_id),
        ):
            model = getattr(models, prefix + name)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(getattr(model, field) == value)
                )
                == 1
            )
        receipt_model = getattr(numbering_models, prefix + "AllocationReceipt")
        assert (
            session.scalar(
                select(func.count())
                .select_from(receipt_model)
                .where(
                    receipt_model.idempotency_key == f"billing:{results[0].invoice_id}"
                )
            )
            == 1
        )
