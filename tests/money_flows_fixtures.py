"""Shared PostgreSQL fixtures for the money-flows Settle proofs.

A scratch database carrying the kernel, Billing, Payments and money-flows
lineages, plus seeding helpers and the small trusted-assembly wrapper that the
public contract of ``settle_confirmation`` describes.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from dotmac_billing.errors import BillingError
from dotmac_billing.service import (
    accept_rated_obligation,
    create_billing_account,
    create_draft_document,
    issue_document,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from dotmac_money_flows import (
    SettleConfirmationV1,
    SettlementTargetV1,
    SettleOutcomeV1,
    SettlePolicyV1,
    SettlePorts,
    SettleState,
    get_settle_instance,
    is_settle_replay_conflict,
    record_settle_refusal,
    settle_confirmation,
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

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
ASSEMBLY = ROOT / "alembic/versions"
BILLING = ROOT / "packages/dotmac-billing/src/dotmac_billing/migrations/versions"
PAYMENTS = ROOT / "packages/dotmac-payments/src/dotmac_payments/migrations/versions"
FLOWS = ROOT / "packages/dotmac-money-flows/src/dotmac_money_flows/migrations/versions"

NOW = b.NOW


@contextmanager
def scratch_database() -> Iterator[tuple[str, str]]:
    """Yield ``(admin_url, app_url)`` for a migrated scratch database."""
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
        config.set_main_option(
            "version_locations",
            f"{KERNEL} {ASSEMBLY} {BILLING} {PAYMENTS} {FLOWS}",
        )
        config.attributes["module_plane_selections"] = tuple(
            ModulePlaneSelection(module=module, planes=(ModulePlane.TENANT,))
            for module in ("billing", "payments", "money_flows")
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


def seed_invoice(db, scope, account, *, service_ref: str = "service:example"):
    command = replace(
        b._obligation_command(
            account.id, scope=scope, source_fact_id=f"occurrence-{uuid4()}"
        ),
        service_ref=service_ref,
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
    return issue_document(
        db, scope=scope, command=b._issue_command(draft.id), numbering=b.NUMBERING
    )


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
            "occurred_at": NOW,
        }
        values.update(overrides)
        return SettleConfirmationV1(**values)


def seed_payment(
    db,
    scope,
    *,
    purpose: PaymentPurpose,
    amount: str,
    target_reference: str | None,
    source: ConfirmationSource = ConfirmationSource.PROVIDER_CALLBACK,
    external_reference: str | None = None,
) -> Payment:
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
            expires_at=NOW + timedelta(hours=1),
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
            observed_at=NOW,
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
