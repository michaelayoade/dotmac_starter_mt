"""Hosted synthetic export fixture -> adapter -> migrated PG import and seal.

This proves a nonempty contract path, not a genuine Sub database export,
production evidence qualification, or permission to import production money.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from dotmac_billing import opening_queries as cash_queries
from dotmac_billing import opening_receivable_queries as queries
from dotmac_billing.opening_contracts import OpeningVerificationUnavailable
from dotmac_billing.opening_receivable_validation import (
    opening_account_command_digest_v2,
    opening_manifest_fingerprint_v2,
    preview_opening_account_import_v2,
)
from dotmac_billing.opening_rehearsal import (
    compare_opening_rehearsals,
    report_opening_rehearsal,
)

from tests import test_billing_non_cash_credit as noncash
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_receivable_import as receivables
from tests.unit import test_billing_opening_receivable_contracts as evidence
from tests.unit import test_billing_opening_rehearsal_adapter as fixture

scratch = receivables.scratch


class ExactFixtureVerifier:
    """Accept only the offered synthetic fixture and its complete exact command."""

    def __init__(self, command, original):
        self.command = command
        self.original = original
        self.command_digest = opening_account_command_digest_v2(command)
        self.manifest_fingerprint = opening_manifest_fingerprint_v2(command.manifest)

    def verify_import_v2(self, request):
        if (
            request.command != self.command
            or request.command_digest != self.command_digest
            or request.manifest_fingerprint != self.manifest_fingerprint
            or len(request.command.manifest.receivables) != 1
            or request.command.manifest.receivables[0].original != self.original
            or request.command.manifest.cash_lots
            or request.command.manifest.receivables[0].historical_discharges
        ):
            raise OpeningVerificationUnavailable("fixture evidence does not match")
        return replace(
            evidence.TrustedVerifier().verify_import_v2(request),
            source_binding_proof_ref="fixture-only:exact-source-command",
            cash_lifecycle_proof_ref="fixture-only:empty-cash-population",
            receivable_lifecycle_proof_ref="fixture-only:one-unpaid-invoice",
            documentary_tax_mapping_proof_ref="fixture-only:explicit-original",
            historical_discharge_mapping_proof_ref="fixture-only:no-discharges",
            migration_authority_proof_ref="fixture-only:disposable-ci",
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_nonempty_adapter_fixture_import_replay_report_refusal_and_permanent_seal(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    payload = fixture._fixture("census_one_eligible_v2.json")
    obs, sidecar = fixture.adapter.load_sub_observation(payload)
    original = fixture._explicit_original(payload)
    source_invoice_id = UUID(fixture.INVOICE_1)
    with cash._session(scratch, url, scope) as db:
        account_id = cash._new_account(db, scope)
        target = cash_queries.get_opening_cash_target(
            db, scope=scope, billing_account_id=account_id
        )
        declaration = fixture._assembly().declaration
        partition = replace(
            declaration.partitions[0],
            account_id=account_id,
            expected_position_fingerprint=target.expected_position_fingerprint,
            posting_watermark=target.posting_watermark,
        )
        assembly = fixture.adapter.AssemblyInputs(
            replace(
                declaration,
                scope=scope,
                partitions=(partition,),
                accepted_manual_holds=("fixture:collection-evidence-unverified",),
            )
        )
        runs = []
        for index in range(3):
            observation = replace(
                obs,
                snapshot_id=f"fixture:nonempty-capture:{index}",
                captured_at=obs.captured_at + timedelta(seconds=index),
            )
            run = fixture.adapter.assemble_rehearsal_run(
                observation,
                assembly=assembly,
                sidecar=sidecar,
                original_documents=((source_invoice_id, original),),
            )
            command = run.commands[0]
            verifier = ExactFixtureVerifier(command, original)
            preview = preview_opening_account_import_v2(command, verifier=verifier)
            assert preview.admissible and preview.blockers == ()
            assert preview.receivable_residual_minor == 10000
            assert preview.cash_residual_minor == 0
            run = replace(run, previews=(preview,))
            report = report_opening_rehearsal(run)
            # The source V2 projection cannot attest the supplied full original.
            # Preserve the real report owner's refusal even after qualification
            # of the synthetic importer command.
            assert not report.complete_exact_reconciliation
            assert {block.code for block in report.blockers} == {
                "original_documentary_evidence_unavailable"
            }
            assert report.accepted_manual_holds == assembly.accepted_manual_holds
            assert all(delta.unclassified_delta_minor == 0 for delta in report.deltas)
            runs.append(run)
        comparison = compare_opening_rehearsals(tuple(runs))
        assert {block.code for block in comparison.blockers} == {"run_blocked"}

        # A typed original and matching balance alone still cannot qualify money.
        assert not preview_opening_account_import_v2(command).admissible
        changed = replace(
            command,
            manifest=replace(
                command.manifest,
                receivables=(
                    replace(
                        command.manifest.receivables[0],
                        original=replace(original, documentary_digest="f" * 64),
                    ),
                ),
            ),
        )
        assert not preview_opening_account_import_v2(
            changed, verifier=verifier
        ).admissible
        assert not preview_opening_account_import_v2(
            replace(command, idempotency_key="foreign-command"), verifier=verifier
        ).admissible

        db.commit()  # Provisioning owns a separate transaction, as existing canaries.
        cash._prepare_window(db, scope, command)
        receipt = cash._import_command(
            db, scope=scope, command=command, verifier=verifier, version=2
        )
        db.commit()
        assert receipt.total_receivable.amount == Decimal("100.00")
        assert receipt.total_residual.amount == 0  # cash residual, not unpaid AR
        assert len(receipt.basis_ids) == 1 and receipt.lot_ids == ()
        position = cash._position(db, scope, account_id)
        assert position.collectible_receivable == Decimal("100.00")
        assert position.available_credit == 0
        assert (
            cash._import_command(
                db, scope=scope, command=command, verifier=verifier, version=2
            )
            == receipt
        )
        assert (
            queries.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )
        basis = queries.get_opening_receivable_basis(
            db, scope=scope, basis_id=receipt.basis_ids[0]
        )
        assert basis.source.original == original
        assert not basis.source.collection.automated_collection_allowed
        assert receivables._counts(db, plane, account_id)["OpeningImportReceipt"] == 1
        cash._seal_window(db, scope)
        db.commit()
        assert (
            cash._import_command(
                db, scope=scope, command=command, verifier=verifier, version=2
            )
            == receipt
        )
        fresh = replace(command, idempotency_key="fixture:fresh-after-permanent-seal")
        with pytest.raises(cash.BillingRuleViolation, match="sealed"):
            cash._import_command(
                db,
                scope=scope,
                command=fresh,
                verifier=ExactFixtureVerifier(fresh, original),
                version=2,
            )
        assert cash._position(db, scope, account_id).collectible_receivable == Decimal(
            "100.00"
        )
