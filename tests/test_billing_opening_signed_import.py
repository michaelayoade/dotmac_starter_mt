"""Hosted PostgreSQL canaries through real signed providers and owner services.

Ephemeral signatures and retained fixture objects prove enforcement only. Window
provision/seal and independent inverse decisions remain explicit test authorities;
these are not production enrollment, original-document custody or cutover proof.
"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from dotmac_billing import service as s
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_commands import ReverseOpeningAccountImportV1
from dotmac_billing.opening_receivable_queries import get_opening_import_receipt_v2
from dotmac_billing.opening_receivable_validation import (
    opening_manifest_fingerprint_v2,
    preview_opening_account_import_v2,
)
from dotmac_billing.opening_window_contracts import ProvisionOpeningWindowV1
from dotmac_billing.opening_window_queries import (
    get_opening_window,
    get_opening_window_admissions,
    get_opening_window_seal,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as b
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_receivable_import as ar
from tests import test_billing_opening_window as window_tests
from tests.billing_opening_signed_fixtures import (
    ImportProofForOtherPurpose,
    SessionWindowReader,
    SignedOpeningFixture,
)
from tests.billing_opening_window_fixtures import (
    TrustedWindowVerifier,
    _principal,
    _provision,
    _restricted_session,
    _seal_command,
)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_signed_import_authority_and_window_reader_never_cross_scope(scratch, plane):
    """First canary: matching caller rehashes cannot move retained authority."""
    with _scenario(scratch, plane) as (scope, _, command, _, session, verifier):
        other_tenant = TenantScope(b._create_tenants(scratch[0], 1)[0])
        foreign_scopes = (
            (other_tenant, PlatformScope()) if plane == "tenant" else (other_tenant,)
        )
        before = _snapshot(session, plane, command.partition.account_id, scope)
        for foreign in foreign_scopes:
            offered = replace(
                command,
                scope=foreign,
                manifest=replace(command.manifest, scope=foreign),
                target=replace(command.target, scope=foreign),
            )
            with pytest.raises(BillingRuleViolation) as refused:
                s.import_opening_account_v2(
                    session, scope=foreign, command=offered, verifier=verifier
                )
            assert refused.value.code == "billing.opening_authority_binding"
            assert (
                _snapshot(session, plane, command.partition.account_id, scope) == before
            )
        reader = SessionWindowReader(session)
        assert (
            reader.current_window(scope=scope).window_id
            == get_opening_window(session, scope=scope).window_id
        )
        if plane == "tenant":
            assert reader.current_window(scope=other_tenant) is None
        foreign_plane = PlatformScope() if plane == "tenant" else other_tenant
        with pytest.raises(DBAPIError):
            reader.current_window(scope=foreign_plane)
        session.rollback()
        assert _snapshot(session, plane, command.partition.account_id, scope) == before


@pytest.fixture
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


@contextmanager
def _scenario(scratch, plane):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = ar._command(session, scope, account)
    with _principal(scratch, plane) as principal:
        window = _provision(scratch, scope, principal, command)
        with _restricted_session(scratch, scope, principal) as session:
            verifier = SignedOpeningFixture(command, window, session)
            yield scope, principal, command, window, session, verifier


def _snapshot(session, plane, account, scope):
    return (
        ar._counts(session, plane, account),
        window_tests._ledger_counts(session, scope),
        get_opening_window_admissions(session, scope=scope),
        get_opening_window_seal(session, scope=scope),
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_real_signed_import_under_restricted_role_and_exact_retry(scratch, plane):
    with _scenario(scratch, plane) as (scope, principal, command, _, session, verifier):
        assert session.scalar(text("SELECT current_user")) == principal[0]
        root = session.get_transaction()
        before = _snapshot(session, plane, command.partition.account_id, scope)
        preview = preview_opening_account_import_v2(command, verifier=verifier)
        assert preview.admissible, preview.blockers
        assert _snapshot(session, plane, command.partition.account_id, scope) == before
        receipt = s.import_opening_account_v2(
            session, scope=scope, command=command, verifier=verifier
        )
        assert session.get_transaction() is root and root.is_active
        assert receipt.total_residual == b._money("60")
        assert receipt.total_receivable == b._money("120")
        assert len(receipt.basis_ids) == 2 and len(receipt.lot_ids) == 2
        assert receipt.manifest_fingerprint == opening_manifest_fingerprint_v2(
            command.manifest
        )
        session.commit()
        after = _snapshot(session, plane, command.partition.account_id, scope)
        assert after != before
        assert len(after[2]) == 1
        assert (
            s.import_opening_account_v2(
                session, scope=scope, command=command, verifier=verifier
            )
            == receipt
        )
        assert _snapshot(session, plane, command.partition.account_id, scope) == after
        assert (
            get_opening_import_receipt_v2(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )
        assert len(verifier.issued_grants) == 1  # preview/retry never consume or mint.
        assert verifier.windows.session is session


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_real_signed_exact_replay_after_seal_and_fresh_new_admission_refusal(
    scratch, plane
):
    with _scenario(scratch, plane) as (scope, _, command, window, session, verifier):
        receipt = s.import_opening_account_v2(
            session, scope=scope, command=command, verifier=verifier
        )
        session.commit()
        seal = s.seal_opening_window(
            session,
            scope=scope,
            command=_seal_command(session, scope, window),
            verifier=TrustedWindowVerifier(),
        )
        session.commit()
        before = _snapshot(session, plane, command.partition.account_id, scope)
        assert verifier.verify_import_v2(verifier.request(command)) is not None
        assert (
            s.import_opening_account_v2(
                session, scope=scope, command=command, verifier=verifier
            )
            == receipt
        )
        new_command = replace(command, idempotency_key=str(uuid4()))
        verifier.issue_exact_import_grant(new_command)
        # Prove the new request has real current authority before admission refuses it.
        assert verifier.verify_import_v2(verifier.request(new_command)) is not None
        with pytest.raises(BillingRuleViolation) as refused:
            s.import_opening_account_v2(
                session, scope=scope, command=new_command, verifier=verifier
            )
        assert refused.value.code == "billing.opening_window_sealed"
        assert _snapshot(session, plane, command.partition.account_id, scope) == before
        assert get_opening_window_seal(session, scope=scope) == seal


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("revoked", ["source", "grant"])
def test_preview_then_revocation_has_no_financial_admission_or_retry_effects(
    scratch, plane, revoked
):
    with _scenario(scratch, plane) as (scope, _, command, _, session, verifier):
        before = _snapshot(session, plane, command.partition.account_id, scope)
        preview = preview_opening_account_import_v2(command, verifier=verifier)
        assert preview.admissible, preview.blockers
        assert _snapshot(session, plane, command.partition.account_id, scope) == before
        verifier.ports.revocation = replace(
            verifier.ports.revocation,
            revoked_key_ids=("source",) if revoked == "source" else (),
            revoked_grant_ids=(verifier.ports.grant_payload.grant_id,)
            if revoked == "grant"
            else (),
        )
        with pytest.raises(BillingRuleViolation) as refused:
            s.import_opening_account_v2(
                session, scope=scope, command=command, verifier=verifier
            )
        assert refused.value.code == "billing.opening_authority_binding"
        assert _snapshot(session, plane, command.partition.account_id, scope) == before
        session.commit()
        assert _snapshot(session, plane, command.partition.account_id, scope) == before


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("invalid", ["epoch", "source", "expired"])
def test_signed_retry_rechecks_epoch_source_and_expiry_without_effects(
    scratch, plane, invalid
):
    with _scenario(scratch, plane) as (scope, _, command, _, session, verifier):
        receipt = s.import_opening_account_v2(
            session, scope=scope, command=command, verifier=verifier
        )
        session.commit()
        before = _snapshot(session, plane, command.partition.account_id, scope)
        if invalid == "epoch":
            verifier.issue_exact_import_grant(command, epoch=uuid4())
        elif invalid == "source":
            ports = verifier.ports
            ports.source_payload = replace(
                ports.source_payload,
                terms=replace(
                    ports.source_payload.terms,
                    expires_at=ports.now_value + timedelta(minutes=40),
                ),
            )
            # Correct signature over a different retained attestation.
            ports.sign_source()
        else:
            ports = verifier.ports
            # Refresh source beyond the grant window, then issue the exact
            # current-source grant. Only grant validity expires on this retry.
            ports.source_payload = replace(
                ports.source_payload,
                terms=replace(
                    ports.source_payload.terms,
                    expires_at=ports.now_value + timedelta(minutes=45),
                ),
            )
            ports.sign_source()
            verifier.issue_exact_import_grant(command)
            ports.now_value = ports.grant_payload.terms.expires_at
            ports.revocation = replace(ports.revocation, checked_at=ports.now_value)
            assert (
                verifier.source.load_source(
                    scope=scope, capture=command.manifest.capture
                )
                is not None
            )
        with pytest.raises(BillingRuleViolation) as refused:
            s.import_opening_account_v2(
                session, scope=scope, command=command, verifier=verifier
            )
        assert refused.value.code == "billing.opening_authority_binding"
        assert _snapshot(session, plane, command.partition.account_id, scope) == before
        assert (
            get_opening_import_receipt_v2(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_signed_import_proof_cannot_provision_seal_or_reverse(scratch, plane):
    with _scenario(scratch, plane) as (scope, _, command, window, session, verifier):
        offered = ImportProofForOtherPurpose(verifier, command)
        # A real valid import result, offered to another owner, must be rejected.
        assert offered.import_proof() is not None
        before = _snapshot(session, plane, command.partition.account_id, scope)
        with pytest.raises(BillingRuleViolation) as refused:
            s.provision_opening_window(
                session,
                scope=scope,
                command=ProvisionOpeningWindowV1(replace(window, window_id=uuid4())),
                verifier=offered,
            )
        assert refused.value.code == "billing.opening_authority_binding"
        assert _snapshot(session, plane, command.partition.account_id, scope) == before
        receipt = s.import_opening_account_v2(
            session, scope=scope, command=command, verifier=verifier
        )
        session.commit()
        before = _snapshot(session, plane, command.partition.account_id, scope)
        seal_command = _seal_command(session, scope, window)
        with pytest.raises(BillingRuleViolation) as refused:
            s.seal_opening_window(
                session, scope=scope, command=seal_command, verifier=offered
            )
        assert refused.value.code == "billing.opening_authority_binding"
        inverse_command = ReverseOpeningAccountImportV1(
            scope,
            receipt.receipt_id,
            "fixture:separate-inverse-authority",
            str(uuid4()),
            command.occurred_at,
        )
        with pytest.raises(BillingRuleViolation) as refused:
            s.reverse_opening_account_import_v2(
                session, scope=scope, command=inverse_command, verifier=offered
            )
        assert refused.value.code == "billing.opening_authority_binding"
        with pytest.raises(BillingRuleViolation) as refused:
            s.reverse_opening_account_import_v2(
                session, scope=scope, command=inverse_command, verifier=verifier
            )
        assert refused.value.code == "billing.opening_authority_binding"
        assert _snapshot(session, plane, command.partition.account_id, scope) == before
        # Independent exact-request inverse approval is a separate fixture port;
        # demonstrating its reachable admit proves the delegate is not deny-all.
        request = verifier.inverse.requests[-1]
        verifier.inverse.approve_exact(request)
        s.seal_opening_window(
            session, scope=scope, command=seal_command, verifier=TrustedWindowVerifier()
        )
        session.commit()
        inverse = s.reverse_opening_account_import_v2(
            session, scope=scope, command=inverse_command, verifier=verifier
        )
        assert inverse.receipt_kind == "unwind"
        assert inverse.original_receipt_id == receipt.receipt_id
        assert verifier.inverse.requests[-1] == request
        assert (
            verifier.inverse.returned
            is verifier.inverse.approved[request.command_digest]
        )
        session.commit()
