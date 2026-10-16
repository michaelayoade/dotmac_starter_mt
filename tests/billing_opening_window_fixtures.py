"""Explicit real-role/window fixtures shared by hosted opening scenarios.

No source evidence, database identity or runtime role is inferred by production.
"""

from __future__ import annotations

# Identifiers are fixed Billing table names and fixture-generated safe role names.
# ruff: noqa: S608
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from dotmac_billing import models
from dotmac_billing import service as s
from dotmac_billing.opening_contracts import OpeningManifestV1
from dotmac_billing.opening_receivable_validation import opening_manifest_fingerprint_v2
from dotmac_billing.opening_validation import opening_manifest_fingerprint
from dotmac_billing.opening_window_contracts import (
    OpeningWindowProvisionVerificationV1,
    OpeningWindowSealVerificationV1,
    OpeningWindowV1,
    ProvisionOpeningWindowV1,
    SealOpeningWindowV1,
)
from dotmac_billing.opening_window_queries import get_opening_window_admissions
from dotmac_kernel.cache import TenantScope
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tests import test_billing_isolation as b


class TrustedWindowVerifier:
    """Fixture-only external population/authority port, never production proof."""

    def verify_provision(self, request):
        window = request.command.window
        return OpeningWindowProvisionVerificationV1(
            "billing.opening.window.provision.v1",
            window.scope,
            window.window_id,
            window.epoch,
            window.manifest_fingerprint,
            window.principal_name,
            window.principal_oid,
            request.command_digest,
            window.provision_authority_ref,
            "fixture:complete-capture",
            "fixture:provision-authority",
        )

    def verify_seal(self, request):
        command = request.command
        return OpeningWindowSealVerificationV1(
            "billing.opening.window.seal.v1",
            command.scope,
            command.window_id,
            command.epoch,
            command.manifest_fingerprint,
            request.command_digest,
            request.population_digest,
            len(command.admissions),
            command.coupled_watermark,
            command.authority_ref,
            "fixture:complete-population",
            "fixture:writer-fence",
            "fixture:seal-authority",
        )


@contextmanager
def _principal(scratch, plane):
    """Real NOLOGIN fixture role, exact named DML; ordinary roles get no membership."""
    role = f"opening_fixture_{uuid4().hex}"
    prefix = "platform_" if plane == "platform" else ""
    tables = models.PLATFORM_TABLES if prefix else models.TENANT_TABLES
    database = make_url(scratch[0]).database
    super_url = b._url_for(b._superuser_url(), database)
    engine = create_engine(super_url)
    writes = {
        "documents",
        "posting_groups",
        "posting_effects",
        "accounting_facts",
        "receivable_position_facts",
        "receivable_position_facts_v2",
        "receivable_exposure_facts_v2",
        "opening_import_receipts",
        "opening_cash_lots",
        "opening_cash_effects",
        "opening_receivable_bases",
        "opening_historical_discharge_claims",
        "cash_source_claims",
        "receivable_source_claims",
        "opening_window_admissions",
        "opening_window_seals",
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS '
                "NOCREATEROLE NOCREATEDB NOREPLICATION"
            )
        )
        connection.execute(
            text(f'GRANT USAGE ON SCHEMA public, mod_billing TO "{role}"')
        )
        for name in tables:
            # Historical-revision canaries run this fixture before later
            # migrations create every current-model table.
            if (
                connection.scalar(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"mod_billing.{name}"},
                )
                is None
            ):
                continue
            privileges = (
                "SELECT, INSERT"
                if name.removeprefix("platform_") in writes
                else "SELECT"
            )
            connection.execute(
                text(f'GRANT {privileges} ON mod_billing.{name} TO "{role}"')
            )
        connection.execute(
            text(
                f"GRANT UPDATE (generation) ON mod_billing.{prefix}opening_windows "
                f'TO "{role}"'
            )
        )
        connection.execute(
            text(
                f'GRANT UPDATE (id) ON mod_billing.{prefix}billing_accounts TO "{role}"'
            )
        )
        for name in ("idempotency_records", "outbox_events"):
            connection.execute(
                text(f'GRANT SELECT, INSERT ON public.{prefix}{name} TO "{role}"')
            )
        oid = connection.scalar(
            text("SELECT oid FROM pg_roles WHERE rolname=:role"), {"role": role}
        )
    try:
        yield role, oid
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{role}"'))
            connection.execute(text(f'DROP ROLE "{role}"'))
        engine.dispose()


@contextmanager
def _restricted_session(scratch, scope, principal, *, isolation="READ COMMITTED"):
    """Privileged test harness selects a real effective restricted principal.

    Production runtime credentials cannot SET ROLE this principal; a separate
    canary exercises those actual ordinary login sessions below.
    """
    database = make_url(scratch[0]).database
    engine = create_engine(
        b._url_for(b._superuser_url(), database), isolation_level=isolation
    )

    @event.listens_for(engine, "connect")
    def initialize(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE "{principal[0]}"')
            if isinstance(scope, TenantScope):
                cursor.execute(
                    "SELECT set_config('app.current_tenant', %s, false)",
                    (str(scope.tenant_id),),
                )
        connection.commit()

    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _provision(scratch, scope, principal, command):
    manifest_digest = (
        opening_manifest_fingerprint(command.manifest)
        if type(command.manifest) is OpeningManifestV1
        else opening_manifest_fingerprint_v2(command.manifest)
    )
    window = OpeningWindowV1(
        scope,
        uuid4(),
        uuid4(),
        1 if type(command.manifest) is OpeningManifestV1 else 2,
        manifest_digest,
        command.manifest,
        principal[0],
        principal[1],
        "fixture:explicit-provision",
        datetime.now(UTC),
    )
    with b._session(scratch[0], scope) as session:
        s.provision_opening_window(
            session,
            scope=scope,
            command=ProvisionOpeningWindowV1(window),
            verifier=TrustedWindowVerifier(),
        )
        session.commit()
    return window


def _seal_command(session, scope, window):
    return SealOpeningWindowV1(
        scope,
        window.window_id,
        window.epoch,
        window.manifest_fingerprint,
        get_opening_window_admissions(session, scope=scope),
        "fixture:coupled-invoice-settlement-allocation-watermark",
        str(uuid4()),
        "fixture:seal-authority",
        datetime.now(UTC),
    )
