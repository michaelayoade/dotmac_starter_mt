"""R8 migration items: contract, refusals and anchors that need no database."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

import dotmac_billing
import pytest
from dotmac_billing import manifest, service
from dotmac_billing._document_facts import _decode
from dotmac_billing.commands import MIGRATION_ITEM_PERMISSION
from dotmac_billing.contracts import AccountingEffectV1
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.migration_items import (
    ACCOUNTING_FACT_V7_CONTRACT,
    AccountingFactV7,
    MigrationItemFactBlockV1,
    MigrationSourceV1,
    RecordMigrationCreditV1,
    RecordMigrationDebitV1,
    ReverseMigrationItemV1,
    record_migration_credit,
    record_migration_debit,
    reverse_migration_item,
)
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Currency, Money

from tests.unit import test_billing_opening_receivable_contracts as evidence

NGN = Currency("NGN", 2)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
TENANT = TenantScope(UUID(int=1))
VERSIONS = Path(dotmac_billing.__file__).parent / "migrations" / "versions"


def _u(n: int) -> UUID:
    return UUID(int=n)


def _money(value: str) -> Money:
    return Money.of(value, NGN)


def _item(**over) -> MigrationItemFactBlockV1:
    values = dict(  # noqa: C408
        item_id=_u(20),
        window_id=_u(21),
        kind="debit",
        document_id=_u(22),
        legacy_source=MigrationSourceV1(
            "installation:1", "legacy-erp", "inv-1", "LEG-1"
        ),
        reverses_item_id=None,
        actor_ref="holding-release:fixture",
        evidence_digest="a" * 64,
    )
    values.update(over)
    return MigrationItemFactBlockV1(**values)


def _fact(**over) -> AccountingFactV7:
    values = dict(  # noqa: C408
        scope=TENANT,
        source_system="dotmac-billing",
        fact_id=_u(10),
        fact_version=7,
        billing_account_id=_u(11),
        posting_group_id=_u(12),
        source_ref=f"migration-item:{_u(20)}",
        source_authority="internal",
        effect_kind="migration_debit_recorded",
        occurred_at=NOW,
        committed_at=NOW,
        effects=(AccountingEffectV1("receivable", _money("70.00")),),
        item=_item(),
        reverses_fact_id=None,
    )
    values.update(over)
    return AccountingFactV7(**values)


def test_the_permission_is_declared_in_no_default_role():
    # The kernel cannot express an empty default role list, so the code is not a
    # manifest permission at all: no role holds it by default.
    assert MIGRATION_ITEM_PERMISSION == "billing.migration_item.record"
    declared = {p.code: p.default_roles for p in manifest.module.permissions}
    assert MIGRATION_ITEM_PERMISSION not in declared
    assert not any(
        MIGRATION_ITEM_PERMISSION in code or "migration_item" in code
        for code in declared
    )


def test_the_manifest_declares_the_v7_event_and_the_version():
    assert ACCOUNTING_FACT_V7_CONTRACT == "billing.accounting.fact.v7"
    assert "billing.accounting.fact.v7" in manifest.module.outbox_event_types
    assert manifest.module.version == dotmac_billing.__version__ == "0.1.0a15"
    assert "migration_items" in manifest.module.tables
    assert not any(
        t.endswith("migration_items") for t in manifest.module.platform_tables
    )


def test_v7_golden_vector_is_stable_and_round_trips():
    fact = _fact()
    payload = service._jsonable(fact)
    assert fact.fact_version == payload["fact_version"] == 7
    assert payload["effect_kind"] == "migration_debit_recorded"
    assert set(payload["item"]) == {
        "item_id",
        "window_id",
        "kind",
        "document_id",
        "legacy_source",
        "reverses_item_id",
        "actor_ref",
        "evidence_digest",
    }
    assert set(payload["item"]["legacy_source"]) == {
        "installation_id",
        "source_system",
        "key",
        "number",
    }
    assert "tax" not in payload and "revenue" not in payload
    assert fingerprint_of(payload) == (
        "99568add12f7350c78a09a2c7de5a3017aa4c971de059699863fc3dd50b486b6"
    )
    assert _decode(payload, AccountingFactV7) == fact


@pytest.mark.parametrize(
    "changes",
    [
        {"effects": (AccountingEffectV1("receivable", _money("-70.00")),)},
        {"effects": (AccountingEffectV1("available_credit", _money("70.00")),)},
        {"effects": ()},
        {"effect_kind": "migration_credit_applied"},
        {"source_ref": "migration-item:other"},
        {"reverses_fact_id": _u(99)},
        {"source_system": "elsewhere"},
    ],
)
def test_a_debit_fact_refuses_any_inconsistent_shape(changes):
    with pytest.raises(OpeningEvidenceError):
        _fact(**changes)


def test_credit_is_negative_and_reversal_links_its_original():
    credit = _fact(
        effect_kind="migration_credit_applied",
        effects=(AccountingEffectV1("receivable", _money("-5.00")),),
        item=_item(kind="credit"),
    )
    assert credit.effects[0].amount_delta.amount < 0
    reversal = _fact(
        effect_kind="migration_item_reversed",
        effects=(AccountingEffectV1("receivable", _money("5.00")),),
        item=_item(kind="reversal", reverses_item_id=_u(19), evidence_digest=None),
        reverses_fact_id=_u(9),
    )
    assert reversal.reverses_fact_id == _u(9)
    with pytest.raises(OpeningEvidenceError):  # a reversal needs both links
        _fact(
            effect_kind="migration_item_reversed",
            effects=(AccountingEffectV1("receivable", _money("5.00")),),
            item=_item(kind="reversal", reverses_item_id=None, evidence_digest=None),
            reverses_fact_id=_u(9),
        )


def test_commands_reject_blank_text_at_construction():
    with pytest.raises(OpeningEvidenceError):
        MigrationSourceV1("", "legacy-erp", "inv-1")
    with pytest.raises(OpeningEvidenceError):
        ReverseMigrationItemV1(TENANT, uuid4(), "actor", "cmd", " ")


class _Reader:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.asked: list[str] = []

    def holds(self, *, scope, actor_ref, code) -> bool:
        self.asked.append(code)
        return self.allowed


def _code(call) -> str:
    with pytest.raises(BillingRuleViolation) as caught:
        call()
    return caught.value.code.removeprefix("billing.")


def _debit(scope=TENANT, **over):
    cmd = evidence._candidate()
    item = cmd.manifest.receivables[0]
    values = dict(  # noqa: C408
        scope=scope,
        billing_account_id=uuid4(),
        source=MigrationSourceV1("installation:1", "legacy-erp", "inv-late-1"),
        original=item.original,
        amount=_money("70.00"),
        collection=item.collection,
        evidence_ref="archive:late-1",
        evidence_digest="b" * 64,
        actor_ref="holding-release:fixture",
        command_id="cmd-1",
        occurred_at=NOW,
    )
    values.update(over)
    return RecordMigrationDebitV1(**values)


def test_platform_scope_permission_and_digest_refuse_before_any_database_work():
    reader = _Reader(True)
    assert (
        _code(
            lambda: record_migration_debit(
                None,
                scope=PlatformScope(),
                command=_debit(PlatformScope()),
                permissions=reader,
            )
        )
        == "migration_item_platform_unsupported"
    )
    assert (
        _code(lambda: record_migration_debit(None, scope=TENANT, command=_debit()))
        == "migration_item_permission_unavailable"
    )
    denied = _Reader(False)
    assert (
        _code(
            lambda: record_migration_debit(
                None, scope=TENANT, command=_debit(), permissions=denied
            )
        )
        == "migration_item_permission_denied"
    )
    assert denied.asked == [MIGRATION_ITEM_PERMISSION]
    assert (
        _code(
            lambda: record_migration_debit(
                None,
                scope=TENANT,
                command=_debit(evidence_digest="XYZ"),
                permissions=reader,
            )
        )
        == "migration_item_evidence_required"
    )
    credit = RecordMigrationCreditV1(
        PlatformScope(),
        MigrationSourceV1("installation:1", "legacy-erp", "cn-1", "CN-1"),
        _money("10.00"),
        uuid4(),
        _money("5.00"),
        "archive:cn-1",
        "c" * 64,
        "actor",
        "cmd-2",
        NOW,
    )
    assert (
        _code(
            lambda: record_migration_credit(
                None, scope=PlatformScope(), command=credit, permissions=reader
            )
        )
        == "migration_item_platform_unsupported"
    )
    reverse = ReverseMigrationItemV1(PlatformScope(), uuid4(), "actor", "cmd-3", "why")
    assert (
        _code(
            lambda: reverse_migration_item(
                None, scope=PlatformScope(), command=reverse, permissions=reader
            )
        )
        == "migration_item_platform_unsupported"
    )


def _bi21():
    path = VERSIONS / "bi_0021_migration_items.py"
    spec = importlib.util.spec_from_file_location("bi_0021_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_bi8():
    path = VERSIONS / "bi_0008_opening_receivables.py"
    spec = importlib.util.spec_from_file_location("bi_0008_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured: list[str] = []
    with mock.patch.object(module.op, "execute", lambda sql: captured.append(str(sql))):
        module._historical_invoice_room(False)
        module._backing_guards(False)
        module._exposure_admission(False)
    return "\n".join(captured)


def test_each_bi21_rewrite_fragment_occurs_exactly_once_in_the_bi8_tenant_bodies():
    """The migration refuses at upgrade unless this holds; prove it offline."""
    bi8 = _render_bi8()
    bi21 = _bi21()
    assert len(bi21.HUNKS) == 5
    for signature, old, new in bi21.HUNKS:
        assert bi8.count(old) == 1, (signature, old[:60])
        assert new.startswith(old.split("\n")[0][:20]) or old in new


def test_downgrade_is_refused():
    with pytest.raises(RuntimeError, match="no downgrade"):
        _bi21().downgrade()


def test_exposure_due_keeps_a_due_date_only_for_automated_evidence():
    from dotmac_billing.contracts import ServicePeriodEvidenceV1, ServicePeriodStatus
    from dotmac_billing.migration_items import exposure_due
    from dotmac_billing.opening_receivable_contracts import OpeningCollectionEvidenceV1

    basis = evidence._candidate().manifest.receivables[0].collection.due_date_basis
    verified_basis = replace(
        basis,
        status=type(basis.status)("verified"),
        payment_terms_code="net_days",
        payment_terms_version="1",
        issued_at=NOW,
        effective_at=NOW,
        timezone="Africa/Lagos",
        derivation_policy="issued_at_plus_days",
        derivation_version="1",
    )
    period = ServicePeriodEvidenceV1(ServicePeriodStatus.NOT_APPLICABLE)

    def collection(status, subject):
        return OpeningCollectionEvidenceV1(
            status,
            subject,
            None,
            "arrears" if subject else None,
            period,
            NOW,
            verified_basis,
            "dotmac-billing",
            "import",
            "1",
        )

    due, kept = exposure_due(collection("verified", "customer:1"), NOW, verified_basis)
    assert due == NOW and kept == verified_basis
    for unverified in (
        collection("unknown_unverified", None),
        collection("unknown_unverified", "customer:1"),
    ):
        due, downgraded = exposure_due(unverified, NOW, verified_basis)
        assert due is None
        assert downgraded.status.value == "unknown_unverified"


@pytest.mark.parametrize(
    "field,value",
    [
        ("locale", "x" * 41),
        ("timezone", "x" * 81),
        ("document_profile_code", "x" * 121),
        ("document_profile_version", "x" * 121),
        ("document_number", "x" * 256),
    ],
)
def test_the_standalone_validator_agrees_with_the_opening_validator(field, value):
    from dotmac_billing.migration_items import _validate_original
    from dotmac_billing.opening_receivable_validation import (
        preview_opening_account_import_v2,
    )

    cmd = evidence._candidate()
    first, second = cmd.manifest.receivables
    args = {
        "collection": first.collection,
        "amount": _money("70.00"),
        "currency": ("NGN", 2),
        "cutoff": cmd.manifest.capture.economic_cutoff,
    }
    _validate_original(first.original, **args)  # the base case passes both
    assert preview_opening_account_import_v2(
        cmd, verifier=evidence.TrustedVerifier()
    ).admissible
    changed = replace(first, original=replace(first.original, **{field: value}))
    manifest = replace(cmd.manifest, receivables=(changed, second))
    assert not preview_opening_account_import_v2(
        replace(cmd, manifest=manifest), verifier=evidence.TrustedVerifier()
    ).admissible
    with pytest.raises(BillingRuleViolation) as caught:
        _validate_original(changed.original, **args)
    assert caught.value.code.endswith("migration_debit_original_invalid")


def test_verified_but_incomplete_collection_is_refused_by_both_validators():
    from dotmac_billing.migration_items import _validate_original
    from dotmac_billing.opening_receivable_validation import (
        preview_opening_account_import_v2,
    )

    cmd = evidence._candidate()
    first, second = cmd.manifest.receivables
    bad = replace(first.collection, status="verified")
    assert not bad.automated_collection_allowed
    manifest = replace(
        cmd.manifest, receivables=(replace(first, collection=bad), second)
    )
    assert not preview_opening_account_import_v2(
        replace(cmd, manifest=manifest), verifier=evidence.TrustedVerifier()
    ).admissible
    with pytest.raises(BillingRuleViolation) as caught:
        _validate_original(
            first.original,
            collection=bad,
            amount=_money("70.00"),
            currency=("NGN", 2),
            cutoff=cmd.manifest.capture.economic_cutoff,
        )
    assert caught.value.code.endswith("migration_debit_collection_incomplete")
