"""Opening evidence is complete, immutable and never itself spendable money."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from dotmac_billing.opening_contracts import (
    OpeningCaptureV1,
    OpeningCashConfirmationV1,
    OpeningCashLotV1,
    OpeningConsumptionV1,
    OpeningDispositionV1,
    OpeningEvidenceError,
    OpeningLaneControlsV1,
    OpeningManifestV1,
    OpeningPartitionV1,
    OpeningPopulationCountsV1,
    OpeningPreviewRequestV1,
    OpeningSourceIdentityV1,
    OpeningTargetV1,
    OpeningVerificationUnavailable,
    OpeningVerificationV1,
)
from dotmac_billing.opening_validation import (
    opening_manifest_fingerprint,
    preview_opening_account_import,
)
from dotmac_kernel.cache import PlatformScope, TenantScope

NOW = datetime(2026, 10, 13, tzinfo=UTC)


def _candidate():
    identity = OpeningSourceIdentityV1("payment", "payment:1")
    capture = OpeningCaptureV1(
        "sub",
        "repo-sha",
        "installation:1",
        ("schema:1",),
        "snapshot:1",
        NOW,
        NOW,
        NOW,
        "repeatable_read",
        "population:v1",
        "source-authority:1",
        "mapping:v1",
        "cash-mapping:v1",
    )
    partition = OpeningPartitionV1("source-account:1", UUID(int=1), "NGN", 2)
    lot = OpeningCashLotV1(
        "lot:1",
        identity,
        partition,
        "sub",
        "payment:1",
        OpeningCashConfirmationV1(
            "payments.confirmation",
            "confirmation:1",
            "v1",
            "confirmation-digest",
            "provider_settled",
            100,
            "NGN",
            2,
            NOW,
            NOW,
        ),
        100,
        60,
        (
            OpeningConsumptionV1(
                "allocation:1",
                "allocation",
                40,
                NOW,
                NOW,
                "invoice:1",
                "ledger:1",
                None,
            ),
        ),
    )
    disposition = OpeningDispositionV1(identity, partition, "eligible", "cash lot", 60)
    controls = OpeningLaneControlsV1(partition, 0, 60, 0, 0, 0, 0, 0, 0, 0, 0)
    manifest = OpeningManifestV1(
        PlatformScope(),
        capture,
        (identity,),
        (disposition,),
        OpeningPopulationCountsV1(1, 1, 0, 0),
        (controls,),
        (lot,),
    )
    target = OpeningTargetV1(
        PlatformScope(), UUID(int=1), "NGN", 2, "target-fingerprint", "posting:0"
    )
    request = OpeningPreviewRequestV1(manifest, partition, target, "authority-proof:1")
    return request


class _Verifier:
    def verify(self, request, fingerprint):
        return OpeningVerificationV1(
            "billing.opening.preview.v1",
            request.manifest.scope,
            fingerprint,
            request.partition,
            request.target,
            request.migration_authority_ref,
            request.manifest.capture.source_authority_ref,
            "source-proof:1",
            "cash-proof:1",
            "authority-proof:1",
        )


def _codes(request, verifier=None):
    result = preview_opening_account_import(request, verifier=verifier)
    assert result.blocker_count == sum(item.count for item in result.blockers)
    return {item.code for item in result.blockers}


def test_cash_preflight_needs_actual_verifier_and_confers_no_money():
    request = _candidate()
    assert "verifier_unavailable" in _codes(request)
    result = preview_opening_account_import(request, verifier=_Verifier())
    assert result.admissible
    assert result.blocker_count == 0
    assert result.operation == "billing.opening.preview.v1"
    assert result.cash_residual_minor == 60
    assert not hasattr(result, "settlement_id")


def test_frozen_and_exact_minor_units():
    request = _candidate()
    with pytest.raises(FrozenInstanceError):
        request.partition.currency = "USD"
    for amount in (True, 1.0, Decimal("NaN"), Decimal("Infinity"), "100"):
        with pytest.raises(OpeningEvidenceError):
            replace(request.manifest.cash_lots[0], gross_minor=amount)
    with pytest.raises(OpeningEvidenceError):
        replace(request.manifest, cash_lots=list(request.manifest.cash_lots))
    with pytest.raises(OpeningEvidenceError):
        preview_opening_account_import({})


def test_provenance_and_order_are_bound_even_when_balances_equal():
    request = _candidate()
    lot = request.manifest.cash_lots[0]
    consumption = lot.consumptions[0]
    pair = (
        replace(consumption, reference="allocation:2", amount_minor=20),
        replace(consumption, amount_minor=20),
    )
    manifest = replace(request.manifest, cash_lots=(replace(lot, consumptions=pair),))
    changed = replace(manifest, cash_lots=(replace(lot, consumptions=pair[::-1]),))
    assert opening_manifest_fingerprint(manifest) != opening_manifest_fingerprint(
        changed
    )
    assert opening_manifest_fingerprint(manifest) == opening_manifest_fingerprint(
        manifest
    )
    assert opening_manifest_fingerprint(
        request.manifest
    ) != opening_manifest_fingerprint(
        replace(
            request.manifest,
            capture=replace(request.manifest.capture, snapshot_id="snapshot:2"),
        )
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("confirmation", None, "cash_confirmation_incomplete"),
        ("gross_minor", -1, "cash_negative"),
        ("residual_minor", 61, "cash_equation_mismatch"),
    ],
)
def test_cash_lifecycle_refusals(field, value, code):
    request = _candidate()
    manifest = replace(
        request.manifest,
        cash_lots=(replace(request.manifest.cash_lots[0], **{field: value}),),
    )
    assert code in _codes(replace(request, manifest=manifest), _Verifier())


def test_unresolved_consumption_and_duplicate_shared_source_are_counted():
    request = _candidate()
    lot = request.manifest.cash_lots[0]
    unresolved = replace(lot.consumptions[0], kind="proportional", ledger_ref=None)
    manifest = replace(
        request.manifest,
        cash_lots=(
            replace(lot, consumptions=(unresolved,)),
            replace(lot, lot_id="lot:2"),
        ),
    )
    codes = _codes(replace(request, manifest=manifest), _Verifier())
    assert "cash_source_duplicate" in codes
    assert "cash_consumption_unresolved" in codes


def test_complete_population_dispositions_controls_and_partition_membership():
    request = _candidate()
    manifest = replace(
        request.manifest, dispositions=(), counts=OpeningPopulationCountsV1(2, 1, 0, 0)
    )
    codes = _codes(replace(request, manifest=manifest), _Verifier())
    assert "population_counts_mismatch" in codes
    assert "population_disposition_mismatch" in codes
    other = replace(request.partition, source_account_key="source-account:2")
    assert "partition_missing" in _codes(replace(request, partition=other), _Verifier())
    assert "unsupported_lane" in _codes(
        replace(
            request,
            manifest=replace(
                request.manifest,
                controls=(
                    replace(
                        request.manifest.controls[0], unapplied_customer_credit_minor=1
                    ),
                ),
            ),
        ),
        _Verifier(),
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", TenantScope(UUID(int=2))),
        ("manifest_fingerprint", "other"),
        ("operation", "billing.opening.import.v1"),
        ("migration_authority_ref", "other"),
        ("partition", replace(_candidate().partition, currency="USD")),
        ("target", replace(_candidate().target, account_id=UUID(int=2))),
        ("source_authority_ref", "other"),
    ],
)
def test_verifier_must_bind_the_exact_request(field, value):
    class WrongVerifier(_Verifier):
        def verify(self, request, fingerprint):
            return replace(super().verify(request, fingerprint), **{field: value})

    assert "verification_binding_mismatch" in _codes(_candidate(), WrongVerifier())


def test_unknown_verifier_result_is_a_typed_refusal():
    class BadVerifier:
        def verify(self, request, fingerprint):
            return True

    assert "verification_unsupported" in _codes(_candidate(), BadVerifier())


@pytest.mark.parametrize(
    "lane",
    [
        "collectible_receivable_minor",
        "available_non_cash_credit_minor",
        "unapplied_customer_credit_minor",
        "prepaid_funding_reserved_minor",
        "prepaid_funding_consumed_minor",
        "written_off_total_minor",
        "refunded_total_minor",
        "adjustment_total_minor",
        "open_ar_minor",
    ],
)
def test_every_other_selected_lane_is_refused_without_netting(lane):
    request = _candidate()
    controls = replace(request.manifest.controls[0], **{lane: -10})
    result = preview_opening_account_import(
        replace(request, manifest=replace(request.manifest, controls=(controls,))),
        verifier=_Verifier(),
    )
    assert not result.admissible
    assert "unsupported_lane" in {item.code for item in result.blockers}


def test_excluded_nonzero_and_omitted_other_partition_are_not_silent():
    request = _candidate()
    disposition = replace(request.manifest.dispositions[0], disposition="excluded")
    manifest = replace(
        request.manifest,
        dispositions=(disposition,),
        counts=OpeningPopulationCountsV1(1, 0, 1, 0),
    )
    assert "excluded_nonzero_residue" in _codes(
        replace(request, manifest=manifest), _Verifier()
    )
    disposition = replace(
        disposition,
        residual_minor=0,
        partition=replace(disposition.partition, source_account_key="other"),
    )
    manifest = replace(manifest, dispositions=(disposition,))
    assert "partition_membership_missing" in _codes(
        replace(request, manifest=manifest), _Verifier()
    )


def test_confirmation_must_match_cash_and_currency_and_cutoffs():
    request = _candidate()
    lot = request.manifest.cash_lots[0]
    for confirmation in (
        replace(lot.confirmation, cash_amount_minor=101),
        replace(lot.confirmation, currency="USD"),
        replace(lot.confirmation, precision=3),
    ):
        manifest = replace(
            request.manifest, cash_lots=(replace(lot, confirmation=confirmation),)
        )
        assert "cash_confirmation_binding_mismatch" in _codes(
            replace(request, manifest=manifest), _Verifier()
        )
    confirmation = replace(
        lot.confirmation, recorded_at=datetime(2026, 10, 14, tzinfo=UTC)
    )
    manifest = replace(
        request.manifest, cash_lots=(replace(lot, confirmation=confirmation),)
    )
    assert "source_after_cutoff" in _codes(
        replace(request, manifest=manifest), _Verifier()
    )


def test_bounded_verifier_unavailability_is_counted_programmer_failure_propagates():
    class UnavailableVerifier:
        def verify(self, request, fingerprint):
            raise OpeningVerificationUnavailable("unsupported qualification")

    class BrokenVerifier:
        def verify(self, request, fingerprint):
            raise RuntimeError("programmer failure")

    assert "verification_unavailable" in _codes(_candidate(), UnavailableVerifier())
    with pytest.raises(RuntimeError, match="programmer failure"):
        preview_opening_account_import(_candidate(), verifier=BrokenVerifier())


def test_scope_and_target_mismatches_are_refused_before_any_funding_exists():
    request = _candidate()
    target = replace(request.target, scope=TenantScope(UUID(int=3)))
    assert "target_binding_mismatch" in _codes(
        replace(request, target=target), _Verifier()
    )


def _prohibited_imports(source):
    import ast

    prohibited = {
        "sqlalchemy",
        "models",
        "service",
        "db",
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "transactions",
    }
    violations = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [f"{node.module or ''}.{alias.name}" for alias in node.names]
        else:
            continue
        violations.extend(
            module for module in modules if prohibited.intersection(module.split("."))
        )
    return tuple(violations)


def test_preflight_has_no_financial_or_io_imports():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for name in ("opening_contracts.py", "opening_validation.py"):
        path = root / "packages/dotmac-billing/src/dotmac_billing" / name
        source = path.read_text()
        assert not _prohibited_imports(source)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "eval", "exec", "__import__"}


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy",
        "import requests as network",
        "import socket",
        "import dotmac_billing.service as financial",
        "import dotmac_kernel.db",
        "from sqlalchemy import select",
        "from requests import get",
        "from socket import socket",
        "from dotmac_billing.service import accept_settlement",
        "from dotmac_billing import models",
        "from dotmac_kernel.transactions import transaction",
    ],
)
def test_import_guard_detects_both_import_forms(source):
    assert _prohibited_imports(source)


def test_import_guard_allows_legitimate_standard_library_imports():
    source = "import datetime as clock\nfrom collections import Counter\n"
    assert not _prohibited_imports(source)


@pytest.mark.parametrize("reverses_ref", [None, "missing", "allocation:1"])
def test_every_ambiguous_reversal_is_refused_even_with_a_resolved_link(reverses_ref):
    request = _candidate()
    lot = request.manifest.cash_lots[0]
    reversal = OpeningConsumptionV1(
        "reversal:1",
        "reversal",
        40,
        NOW,
        NOW,
        "invoice:1",
        "ledger:2",
        reverses_ref,
    )
    # This arithmetic would wrongly admit an allocation inverse as a deduction.
    lot = replace(lot, residual_minor=20, consumptions=(*lot.consumptions, reversal))
    manifest = replace(
        request.manifest,
        cash_lots=(lot,),
        dispositions=(replace(request.manifest.dispositions[0], residual_minor=20),),
        controls=(
            replace(request.manifest.controls[0], available_cash_credit_minor=20),
        ),
    )
    result = preview_opening_account_import(
        replace(request, manifest=manifest), verifier=_Verifier()
    )
    assert not result.admissible
    assert "cash_reversal_unsupported" in {item.code for item in result.blockers}
    assert manifest.cash_lots[0].consumptions[-1] == reversal


def test_oversized_integer_is_a_structural_typed_refusal_without_coercion():
    lot = _candidate().manifest.cash_lots[0]
    for amount in (1 << 4096, -(1 << 4096), 1 << 20000):
        with pytest.raises(OpeningEvidenceError, match="integer too large"):
            replace(lot, gross_minor=amount)


def test_same_instant_with_different_offset_has_same_semantic_fingerprint():
    manifest = _candidate().manifest
    offset_instant = NOW.astimezone(timezone(timedelta(hours=1)))
    capture = replace(
        manifest.capture,
        captured_at=offset_instant,
        economic_cutoff=offset_instant,
        recorded_cutoff=offset_instant,
    )
    lot = manifest.cash_lots[0]
    confirmation = replace(
        lot.confirmation, economic_at=offset_instant, recorded_at=offset_instant
    )
    consumption = replace(
        lot.consumptions[0], economic_at=offset_instant, recorded_at=offset_instant
    )
    same = replace(
        manifest,
        capture=capture,
        cash_lots=(
            replace(lot, confirmation=confirmation, consumptions=(consumption,)),
        ),
    )
    assert opening_manifest_fingerprint(manifest) == opening_manifest_fingerprint(same)
    assert same.capture.captured_at.utcoffset() == timedelta(hours=1)


@pytest.mark.parametrize("kind", ["allocation", "refund", "reservation"])
def test_inverse_provenance_cannot_be_relabelled_as_an_allowed_deduction(kind):
    request = _candidate()
    lot = request.manifest.cash_lots[0]
    disguised_inverse = OpeningConsumptionV1(
        "inverse:1",
        kind,
        40,
        NOW,
        NOW,
        "invoice:1",
        "ledger:2",
        "allocation:1",
    )
    lot = replace(
        lot, residual_minor=20, consumptions=(*lot.consumptions, disguised_inverse)
    )
    manifest = replace(
        request.manifest,
        cash_lots=(lot,),
        dispositions=(replace(request.manifest.dispositions[0], residual_minor=20),),
        controls=(
            replace(request.manifest.controls[0], available_cash_credit_minor=20),
        ),
    )
    result = preview_opening_account_import(
        replace(request, manifest=manifest), verifier=_Verifier()
    )
    codes = {item.code for item in result.blockers}
    assert "cash_equation_mismatch" not in codes
    assert "cash_control_mismatch" not in codes
    assert "source_residual_mismatch" not in codes
    assert "cash_reversal_unsupported" in codes
    assert not result.admissible
    assert manifest.cash_lots[0].consumptions[-1] == disguised_inverse
