"""Shared PostgreSQL fixtures for the money-flows Settle and FundedCharge proofs.

A scratch database carrying the kernel, Billing, Payments and money-flows
lineages, plus seeding helpers and the small trusted-assembly wrappers that the
public contracts of ``settle_confirmation`` and the F2 commands describe.
"""

from __future__ import annotations

import calendar
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from dotmac_billing import (
    GetFundingAvailabilityV1,
    InvoiceTaxEvidenceV2,
    RecordAccountTermsV1,
    RecordDefaultTermsV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
    get_effective_account_terms,
    get_funding_availability,
    record_account_terms,
    record_default_terms,
)
from dotmac_billing.errors import BillingError
from dotmac_billing.service import (
    accept_rated_obligation,
    create_billing_account,
    create_draft_document,
    issue_document,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from dotmac_money_flows import (
    FundedChargeOutcomeV1,
    FundedChargePolicyV1,
    FundedChargePorts,
    FundedChargeStepFailed,
    RatedChargeKeyV1,
    RatedChargeV1,
    SettleConfirmationV1,
    SettleConfirmationV2,
    SettledFundsV1,
    SettlementTargetV1,
    SettleOutcomeV1,
    SettlePolicyV1,
    SettlePorts,
    SettleState,
    StartFundedChargeV1,
    get_funded_charge_instance,
    get_settle_instance,
    is_funded_charge_replay_conflict,
    is_settle_replay_conflict,
    on_settlement_settled,
    record_funded_charge_attention,
    record_settle_refusal,
    settle_confirmation,
    start_funded_charge,
)
from dotmac_payments.contracts import (
    ConfirmationSource,
    OpenPaymentIntent,
    PaymentPurpose,
    RecordConfirmation,
)
from dotmac_payments.service import open_payment_intent, record_confirmation
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests import test_billing_isolation as b
from tests import test_billing_tax_dependency_evidence as tax_dependency
from tests import test_billing_tax_determination_evidence as tax_determination

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
ASSEMBLY = ROOT / "alembic/versions"
BILLING = ROOT / "packages/dotmac-billing/src/dotmac_billing/migrations/versions"
PAYMENTS = ROOT / "packages/dotmac-payments/src/dotmac_payments/migrations/versions"
FLOWS = ROOT / "packages/dotmac-money-flows/src/dotmac_money_flows/migrations/versions"
VERSIONS = FLOWS

NOW = b.NOW
COLLECTIONS = (
    ROOT / "packages/dotmac-collections/src/dotmac_collections/migrations/versions"
)
SERVICE_ACCESS = (
    ROOT
    / "packages/dotmac-service-access-policy/src/dotmac_service_access_policy"
    / "migrations/versions"
)

#: Account terms cannot be back-dated against the database clock, so the F2
#: timeline sits in the near future: the default terms take effect half a day
#: before ``BASE``, and every charge falls due at or after it.
BASE = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)


@contextmanager
def scratch_database(
    *, with_access_policy: bool = False, with_collections: bool = False
) -> Iterator[tuple[str, str]]:
    """Yield ``(admin_url, app_url)`` for a migrated scratch database.

    ``with_access_policy`` adds the Service Access Policy lineage, so a proof can
    show that no F2 call writes its tables. ``with_collections`` adds the
    Collections lineage (tenant plane) for the GAP-4 assembly conformance proof.
    """
    superuser = b._superuser_url()
    name = f"mflowsx_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(b._url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin_url = b._url_for(superuser, name, user="app_admin")
    previous = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        locations = f"{KERNEL} {ASSEMBLY} {BILLING} {PAYMENTS} {FLOWS}"
        modules = ["billing", "payments", "money_flows"]
        if with_access_policy:
            locations += f" {SERVICE_ACCESS}"
            modules.append("service_access_policy")
        if with_collections:
            locations += f" {COLLECTIONS}"
            modules.append("collections")
        config.set_main_option("version_locations", locations)
        config.attributes["module_plane_selections"] = tuple(
            ModulePlaneSelection(module=module, planes=(ModulePlane.TENANT,))
            for module in modules
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, b._url_for(superuser, name, user="app_user")
    finally:
        if previous is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


# --------------------------------------------------------------------------
# Kernel + money-flows lineage only (the table proofs; no owner schemas)
# --------------------------------------------------------------------------
def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, host = prefix.partition("://")
        prefix = f"{scheme}://{user}@{host.rpartition('@')[2]}"
    return f"{prefix}/{database}"


@pytest.fixture
def migrated() -> Iterator[tuple[str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("PostgreSQL URL required")
    name = f"mflows_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin = _url(superuser, name, "app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
        os.environ["MIGRATION_DATABASE_URL"] = admin
        command.upgrade(cfg, "heads")
        yield admin, _url(superuser, name, "app_user")
    finally:
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _tenant(admin_url: str) -> uuid.UUID:
    tenant = uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO public.tenants (id, slug, name) VALUES (:id, :s, :s)"),
            {"id": tenant, "s": f"mf-{tenant}"},
        )
    engine.dispose()
    return tenant


def _as_tenant(connection, tenant) -> None:
    connection.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant)}
    )


def new_scope(scratch: tuple[str, str]) -> TenantScope:
    return TenantScope(b._create_tenants(scratch[0], 1)[0])


def session(scratch: tuple[str, str], scope: TenantScope):
    return b._session(scratch[1], scope)


# --------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------
class RecordingTargets:
    """Resolver that records exactly what it was asked."""

    def __init__(self, mapping: dict[str, SettlementTargetV1] | None = None) -> None:
        self.mapping = mapping if mapping is not None else {}
        self.calls: list[tuple[str | None, str]] = []

    def resolve(self, db, *, scope, target_reference, purpose):
        self.calls.append((target_reference, purpose))
        return self.mapping.get(target_reference)


class FixedPolicy:
    def __init__(self, policy: SettlePolicyV1) -> None:
        self.policy = policy
        self.reads = 0

    def read(self, db, *, scope):
        self.reads += 1
        return self.policy


class BarrierPolicy(FixedPolicy):
    """Policy reader that holds every caller until all have reached it.

    ``read`` runs after the replay check, so passing the barrier proves every
    caller found no existing instance before any of them wrote one.
    """

    def __init__(self, policy: SettlePolicyV1, barrier) -> None:
        super().__init__(policy)
        self.barrier = barrier

    def read(self, db, *, scope):
        policy = super().read(db, scope=scope)
        self.barrier.wait(timeout=30)
        return policy


def make_ports(
    targets: RecordingTargets, policy: SettlePolicyV1 | None = None
) -> SettlePorts:
    return SettlePorts(
        targets=targets, policy=FixedPolicy(policy or SettlePolicyV1(version="p1"))
    )


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
def seed_account(db, scope, *, currency: str = "NGN"):
    return create_billing_account(
        db,
        scope=scope,
        external_account_ref=f"customer-{uuid4()}",
        currency=currency,
        minor_units=2,
    )


def seed_invoice(
    db,
    scope,
    account,
    *,
    service_ref: str = "service:example",
    net: str | None = None,
    due_at=None,
    issued_at=None,
):
    """An issued invoice; ``net`` (VAT 20% on top) and ``due_at`` are optional.

    The default is a 120.00 invoice due on the fixture date. ``issued_at`` moves
    the issue instant (GAP-3 reads account terms at it); ``due_at`` then
    defaults to 30 days after it.
    """
    command = replace(
        b._obligation_command(
            account.id, scope=scope, source_fact_id=f"occurrence-{uuid4()}"
        ),
        service_ref=service_ref,
    )
    if net is not None:
        cent = Decimal("0.01")
        pre = b._money(net)
        tax = b._money(str((Decimal(net) * Decimal("0.20")).quantize(cent)))
        total = b._money(str((Decimal(net) * Decimal("1.20")).quantize(cent)))
        (snapshot,) = command.tax_snapshots
        command = replace(
            command,
            pre_tax_amount=pre,
            tax_amount=tax,
            total_amount=total,
            tax_snapshots=(replace(snapshot, taxable_basis=pre, tax_amount=tax),),
        )
    obligation = accept_rated_obligation(
        db,
        scope=scope,
        command=command,
        accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
    )
    draft = create_draft_document(
        db, scope=scope, command=b._draft_command(obligation.id)
    )
    issue = b._issue_command(draft.id)
    if issued_at is not None:
        issue = replace(
            issue,
            due_at=due_at or issued_at + timedelta(days=30),
            due_date_basis=replace(
                issue.due_date_basis, issued_at=issued_at, effective_at=issued_at
            ),
        )
    elif due_at is not None:
        issue = replace(issue, due_at=due_at)
    return issue_document(db, scope=scope, command=issue, numbering=b.NUMBERING)


@dataclass(frozen=True)
class Payment:
    intent_reference: str
    intent_id: UUID
    confirmation_id: UUID
    external_reference: str
    target_reference: str | None

    def command(self, **overrides) -> SettleConfirmationV1:
        values = {
            "confirmation_id": self.confirmation_id,
            "intent_reference": self.intent_reference,
        }
        values.update(overrides)
        return SettleConfirmationV1(**values)

    def command_v2(self, **overrides) -> SettleConfirmationV2:
        values = {
            "confirmation_id": self.confirmation_id,
            "intent_reference": self.intent_reference,
        }
        values.update(overrides)
        return SettleConfirmationV2(**values)


def seed_payment(
    db,
    scope,
    *,
    purpose: PaymentPurpose,
    amount: str,
    target_reference: str | None,
    source: ConfirmationSource = ConfirmationSource.PROVIDER_CALLBACK,
    external_reference: str | None = None,
    observed_at: datetime | None = None,
) -> Payment:
    observed = observed_at or NOW
    reference = f"intent-{uuid4()}"
    intent = open_payment_intent(
        db,
        scope=scope,
        command=OpenPaymentIntent(
            payer_reference="payer:example",
            purpose=purpose,
            requested=b._money(amount),
            reference=reference,
            provider_type="testpay",
            channel="web",
            target_reference=target_reference,
            opened_at=NOW,
            expires_at=max(NOW, observed) + timedelta(hours=1),
        ),
    )
    confirmation = record_confirmation(
        db,
        scope=scope,
        command=RecordConfirmation(
            intent_id=intent.id,
            source=source,
            external_reference=external_reference or f"ext-{uuid4()}",
            confirmed=b._money(amount),
            observed_at=observed,
        ),
    )
    db.flush()
    return Payment(
        intent_reference=reference,
        intent_id=intent.id,
        confirmation_id=confirmation.id,
        external_reference=confirmation.external_reference,
        target_reference=target_reference,
    )


# --------------------------------------------------------------------------
# The trusted-assembly wrapper (the documented contract, nothing more)
# --------------------------------------------------------------------------
def assembly_settle(
    scratch: tuple[str, str],
    scope: TenantScope,
    command: SettleConfirmationV1,
    ports: SettlePorts,
) -> SettleOutcomeV1:
    """Own the transaction around ``settle_confirmation`` as the contract says.

    * success: commit;
    * uniqueness violation on the confirmation: replay of the winner;
    * owner exception: roll back, then record the refusal in a NEW transaction.
    """
    code: str
    try:
        with session(scratch, scope) as db:
            outcome = settle_confirmation(db, scope=scope, command=command, ports=ports)
            db.commit()
            return outcome
    except IntegrityError as exc:
        if not is_settle_replay_conflict(exc):
            raise
        with session(scratch, scope) as db:
            view = get_settle_instance(
                db, scope=scope, confirmation_id=command.confirmation_id
            )
        assert view is not None
        return SettleOutcomeV1(
            instance_id=view.id,
            state=view.state,
            reason_code=view.reason_code,
            version=view.version,
            settlement_id=view.settlement_id,
            billing_account_id=view.billing_account_id,
            replayed=True,
        )
    except BillingError as exc:
        code = exc.code
    with session(scratch, scope) as db:
        outcome = record_settle_refusal(
            db, scope=scope, command=command, ports=ports, refusal_code=code
        )
        db.commit()
        return outcome


def assert_state(outcome: SettleOutcomeV1, state: SettleState, reason=None) -> None:
    assert outcome.state is state, outcome
    assert outcome.reason_code == reason, outcome


# --------------------------------------------------------------------------
# F2 FundedCharge: terms, the charge source, ports and the assembly wrappers
# --------------------------------------------------------------------------
def seed_default_terms(
    db, scope, *, contract_type: str = "prepaid", period: str = "monthly"
) -> None:
    """The tenant default terms, effective half a day before ``BASE``."""
    record_default_terms(
        db,
        command=RecordDefaultTermsV1(
            scope=scope,
            effective_from=BASE - timedelta(hours=12),
            contract_type=contract_type,
            period=period,
            postpaid_billing_timing="period_end",
            postpaid_grace=timedelta(days=3),
            actor_ref="fixture",
            reason="funded charge fixture",
        ),
    )


def seed_customer_terms(
    db,
    scope,
    account_id: UUID,
    *,
    contract_type: str,
    effective_from: datetime,
    period: str | None = None,
) -> None:
    record_account_terms(
        db,
        command=RecordAccountTermsV1(
            scope=scope,
            billing_account_id=account_id,
            effective_from=effective_from,
            actor_ref="fixture",
            reason="funded charge fixture",
            contract_type=contract_type,
            period=period,
        ),
    )


# The period rule and the prorated amount, written independently of the flows
# verifier (calendar arithmetic by hand), so the tests are not circular.
LAGOS = "Africa/Lagos"


def add_months_local(moment: datetime, months: int, zone: str = LAGOS) -> datetime:
    """``moment`` plus calendar months at the same local wall clock, clamped."""
    local = moment.astimezone(ZoneInfo(zone))
    index = local.year * 12 + local.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day, fold=0).astimezone(UTC)


def end_for_terms(moment: datetime, period: str, zone: str = LAGOS) -> datetime:
    """The end of the period starting at ``moment`` under a Billing terms period."""
    if period == "daily":
        local = moment.astimezone(ZoneInfo(zone))
        return (local + timedelta(days=1)).replace(fold=0).astimezone(UTC)
    months = {"monthly": 1, "quarterly": 3, "annually": 12}[period]
    return add_months_local(moment, months, zone)


def local_days(start: datetime, end: datetime, zone: str = LAGOS) -> int:
    z = ZoneInfo(zone)
    return (end.astimezone(z).date() - start.astimezone(z).date()).days


def prorated_amount(
    total: str, issue_at: datetime, start: datetime, end: datetime, zone: str = LAGOS
) -> str:
    """Full price x used days / days in the enclosing period, half-up once."""
    value = (
        Decimal(total) * local_days(issue_at, end, zone) / local_days(start, end, zone)
    )
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class ObligationSources:
    """The assembly's registry of declared obligation sources (one kind)."""

    def require_obligation_source(self, code: str) -> str:
        if code != "recurring_charge_occurrence":
            raise ValueError("undeclared obligation source")
        return "subscriptions"


class FakeChargeSource:
    """A charge source over plain dicts; it records every call it receives.

    A charge totalling 107.50 carries real Tax V2 evidence (100.00 + 7.50);
    any other total is a zero-tax charge. ``mutators`` rewrite the command the
    source returns, to prove F2 refuses a command that is not the charge's own.
    """

    def __init__(self) -> None:
        self.charges: dict[RatedChargeKeyV1, RatedChargeV1] = {}
        self.evidence: dict[RatedChargeKeyV1, tuple] = {}
        self.mutators: dict[RatedChargeKeyV1, object] = {}
        self.reads: list[RatedChargeKeyV1] = []
        self.issues: list[tuple[RatedChargeKeyV1, datetime]] = []
        #: The billing time zone the source puts on its commands (the policy's).
        self.timezone = LAGOS

    @property
    def zone(self) -> str:
        """The zone the source counts periods in (an unknown policy zone falls back)."""
        try:
            ZoneInfo(self.timezone)
        except Exception:
            return LAGOS
        return self.timezone

    def add(
        self,
        scope: TenantScope,
        account_id: UUID,
        fact: str,
        *,
        total: str = "107.50",
        due_at: datetime = BASE,
        service_ref: str | None = "service:one",
        period_starts_at: datetime | None = None,
        period_ends_at: datetime | None = None,
    ) -> RatedChargeKeyV1:
        key = RatedChargeKeyV1(
            "subscriptions", "recurring_charge_occurrence", fact, "1"
        )
        self.charges[key] = RatedChargeV1(
            key=key,
            billing_account_id=account_id,
            service_ref=service_ref,
            total=b._money(total),
            due_at=due_at,
            period_starts_at=period_starts_at,
            period_ends_at=period_ends_at,
        )
        if total == "107.50":
            self.evidence[key] = (
                InvoiceTaxEvidenceV2(
                    "invoice_tax",
                    tax_dependency._v2(tax_determination._evidence(scope)),
                ),
            )
        else:
            self.evidence[key] = ()
        return key

    def withdraw(self, key: RatedChargeKeyV1) -> None:
        self.charges.pop(key)

    def read(self, db, *, scope, key):
        self.reads.append(key)
        return self.charges.get(key)

    def issue_command(self, db, *, scope, key, issue_at):
        self.issues.append((key, issue_at))
        charge = self.charges[key]
        if charge.period_ends_at is None or charge.period_starts_at is None:
            # An ordinary period: the Billing terms period at ``issue_at``.
            terms = get_effective_account_terms(
                db, scope=scope, account_id=charge.billing_account_id, at=issue_at
            )
            period_end = end_for_terms(issue_at, terms.period.value, self.zone)
            amount = charge.total.amount
        else:
            # A prorated charge: co-terminous with the enclosing period.
            assert not self.evidence[key], "prorated fixtures use zero-tax totals"
            period_end = charge.period_ends_at
            amount = Decimal(
                prorated_amount(
                    str(charge.total.amount),
                    issue_at,
                    charge.period_starts_at,
                    period_end,
                    self.zone,
                )
            )
        command = tax_dependency._command(
            charge.billing_account_id, scope, self.evidence[key]
        )
        obligation = replace(
            command.obligation,
            contract_line_ref=f"line:{key.source_fact_id}",
            source_system=key.source_system,
            source_kind=key.source_kind,
            source_fact_id=key.source_fact_id,
            source_fact_version=key.source_fact_version,
            service_ref=charge.service_ref,
            service_period=ServicePeriodEvidenceV1(
                ServicePeriodStatus.VERIFIED, issue_at, period_end
            ),
            collection_timing="advance",
        )
        if not self.evidence[key]:
            due = b._money(str(amount))
            obligation = replace(
                obligation,
                pre_tax_amount=due,
                tax_amount=b._money("0"),
                total_amount=due,
            )
        command = replace(
            command,
            timezone=self.timezone,
            obligation=obligation,
            reference_date=issue_at.date(),
            due_at=issue_at,
            correlation_id=f"funded-charge-{key.source_fact_id}",
        )
        mutate = self.mutators.get(key)
        return command if mutate is None else mutate(command)


class CountingFundedPolicy:
    """Policy reader that counts reads, optionally holding callers at a barrier."""

    def __init__(self, policy: FundedChargePolicyV1, barrier=None) -> None:
        self.policy = policy
        self.barrier = barrier
        self.reads = 0

    def read(self, db, *, scope):
        self.reads += 1
        if self.barrier is not None:
            self.barrier.wait(timeout=30)
        return self.policy


def make_funded_ports(
    source: FakeChargeSource,
    *,
    cap: int = 20,
    barrier=None,
    proration: str = "actual_calendar_days",
    timezone: str = LAGOS,
) -> FundedChargePorts:
    source.timezone = timezone
    return FundedChargePorts(
        charges=source,
        policy=CountingFundedPolicy(
            FundedChargePolicyV1("fp1", cap, proration, timezone), barrier
        ),
        obligation_sources=ObligationSources(),
        numbering=b.NUMBERING,
    )


def deposit(
    scratch: tuple[str, str],
    scope: TenantScope,
    account_id: UUID,
    amount: str,
    *,
    observed_at: datetime | None = None,
) -> SettleOutcomeV1:
    """Land cash on an account as available credit through F1 Settle."""
    reference = f"acct:{account_id}"
    with session(scratch, scope) as db:
        payment = seed_payment(
            db,
            scope,
            purpose=PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT,
            amount=amount,
            target_reference=reference,
            observed_at=observed_at,
        )
        db.commit()
    targets = RecordingTargets({reference: SettlementTargetV1(account_id)})
    outcome = assembly_settle(scratch, scope, payment.command(), make_ports(targets))
    assert outcome.state is SettleState.SETTLED, outcome
    return outcome


def unrestricted(scratch, scope: TenantScope, account_id: UUID) -> Decimal:
    with session(scratch, scope) as db:
        return get_funding_availability(
            db, scope=scope, request=GetFundingAvailabilityV1(scope, account_id)
        ).unrestricted.amount


def _step_code(error: BaseException) -> str:
    return error.code if isinstance(error, BillingError) else "idempotency_conflict"


def assembly_start(
    scratch: tuple[str, str],
    scope: TenantScope,
    key: RatedChargeKeyV1,
    ports: FundedChargePorts,
) -> FundedChargeOutcomeV1:
    """Own the transaction around ``start_funded_charge`` as the contract says.

    * success: commit;
    * uniqueness violation on the charge: replay of the winner;
    * owner exception: roll back, then record attention in a NEW transaction.
    """
    try:
        with session(scratch, scope) as db:
            outcome = start_funded_charge(
                db, scope=scope, command=StartFundedChargeV1(key), ports=ports
            )
            db.commit()
            return outcome
    except IntegrityError as exc:
        if not is_funded_charge_replay_conflict(exc):
            raise
        with session(scratch, scope) as db:
            view = get_funded_charge_instance(db, scope=scope, key=key)
        assert view is not None
        return FundedChargeOutcomeV1(
            view.id,
            view.state,
            view.reason_code,
            view.version,
            view.billing_account_id,
            view.document_id,
            True,
        )
    except (BillingError, IdempotencyConflict) as exc:
        code = _step_code(exc)
    with session(scratch, scope) as db:
        outcome = record_funded_charge_attention(
            db, scope=scope, key=key, code=code, ports=ports
        )
        db.commit()
        return outcome


def assembly_settled(
    scratch: tuple[str, str],
    scope: TenantScope,
    event: SettledFundsV1,
    ports: FundedChargePorts,
) -> tuple[FundedChargeOutcomeV1, ...]:
    """Run ``on_settlement_settled`` as the transport does.

    A step failure rolls everything back, records attention for the failed row
    (re-enqueueing the continuation) in a NEW transaction and returns normally.
    """
    try:
        with session(scratch, scope) as db:
            outcomes = on_settlement_settled(db, scope=scope, event=event, ports=ports)
            db.commit()
            return outcomes
    except FundedChargeStepFailed as exc:
        failure = exc
    with session(scratch, scope) as db:
        record_funded_charge_attention(
            db,
            scope=scope,
            instance_id=failure.instance_id,
            code=failure.code,
            funding_settlement_id=event.settlement_id,
            ports=ports,
        )
        db.commit()
    return ()
