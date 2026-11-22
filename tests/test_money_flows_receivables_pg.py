"""Hosted PostgreSQL proofs for GAP-4's X1 reader and the assembly chain.

The reader runs over real Billing (a13) postpaid and prepaid invoices. The chain
test composes Billing, the X1 reader, Collections, a fake Sub-thin fan-out to
Service Access Policy and the GAP-3 balance pass: the due wake, the hold wake, one
event per change of ``held``, the balance pass before the hold, then payment.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from dotmac_billing import (
    RecordAccountTermsV1,
    get_document_exposure,
    record_account_terms,
)
from dotmac_collections import (
    AssessCollectionExposureV1,
    AssessmentBlocked,
    CaseAssessed,
    CollectionCaseService,
    CollectionHoldService,
    CollectionPolicyService,
    CreateCollectionPolicyV1,
    FakeCollectionsTimer,
    HoldLifted,
    LiftCollectionHoldV1,
    PlaceCollectionHoldV1,
    PolicyPublicationV1,
    PolicyStepDraftV1,
    PolicyVersionDraftV1,
    PositionReadOk,
    PositionUnavailable,
    ProcessCollectionAccessHoldDueV1,
    TimerTriggerV1,
    TriggerProvenanceV1,
    get_collection_case,
    get_collection_subject_state,
)
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_money_flows import (
    BillingReceivablesReader,
    InvoiceIssuedV1,
    apply_balance_on_invoice_issued,
    subject_ref_for,
)
from dotmac_service_access_policy import (
    AccessSignal,
    DesiredAccess,
    RecordAccessInputV2,
    ResolveDesiredAccess,
    record_access_input_v2,
    resolve_desired_access,
)
from sqlalchemy import select

from tests.money_flows_fixtures import (
    BASE,
    RecordingTargets,
    deposit,
    make_ports,
    new_scope,
    scratch_database,
    seed_account,
    seed_default_terms,
    seed_invoice,
    session,
)

ISSUED = BASE + timedelta(hours=1)
DUE = ISSUED + timedelta(days=30)
DEFAULT_GRACE = timedelta(days=3)  # the fixture's tenant default
ACCESS_EVENT = "collections.subject.access_hold.v1"
OWNER = "dotmac-billing"


@pytest.fixture(scope="module")
def scratch():
    with scratch_database(with_access_policy=True, with_collections=True) as urls:
        yield urls


def _world(scratch, *, contract="postpaid", balance=None):
    scope = new_scope(scratch)
    with session(scratch, scope) as db:
        seed_default_terms(db, scope, contract_type=contract)
        account = seed_account(db, scope)
        db.commit()
        account_id = account.id
    if balance is not None:
        deposit(scratch, scope, account_id, balance)
    with session(scratch, scope) as db:
        invoice = seed_invoice(
            db,
            scope,
            SimpleNamespace(id=account_id),
            net="100.00",
            issued_at=ISSUED,
        )
        db.commit()
        return scope, account_id, invoice.id


def _read(scratch, scope, document_id, at=ISSUED):
    with session(scratch, scope) as db:
        return BillingReceivablesReader(db).read(
            scope=scope,
            source_owner=OWNER,
            exposure_ref=f"document:{document_id}",
            as_of=at,
        )


# --- the reader over real Billing ------------------------------------------


def test_postpaid_invoice_maps_with_the_hold_instant_from_the_default_grace(
    scratch,
) -> None:
    scope, account_id, document_id = _world(scratch)
    result = _read(scratch, scope, document_id)
    assert isinstance(result, PositionReadOk)
    observation = result.position
    with session(scratch, scope) as db:
        exposure = get_document_exposure(db, scope=scope, document_id=document_id)
    assert observation.subject_ref == subject_ref_for(account_id)
    assert observation.subject_ref == f"billing-account:{account_id}"
    assert observation.source_owner == OWNER
    assert observation.exposure_ref == f"document:{document_id}"
    assert observation.due_date_status == "verified"
    assert observation.due_at == DUE
    assert observation.access_hold_from == DUE + DEFAULT_GRACE
    assert observation.collectible_receivable == exposure.collectible_receivable
    assert observation.state_fingerprint == exposure.state_fingerprint
    assert _read(scratch, scope, document_id) == result  # deterministic replay


def test_the_account_override_beats_the_default_and_a_later_change_cannot_move_it(
    scratch,
) -> None:
    scope, account_id, document_id = _world(scratch)

    def record(grace: timedelta, effective_from: datetime) -> None:
        with session(scratch, scope) as db:
            record_account_terms(
                db,
                command=RecordAccountTermsV1(
                    scope=scope,
                    billing_account_id=account_id,
                    effective_from=effective_from,
                    actor_ref="fixture",
                    reason="grace override",
                    postpaid_grace=grace,
                ),
            )
            db.commit()

    record(timedelta(days=60), ISSUED)
    first = _read(scratch, scope, document_id)
    assert isinstance(first, PositionReadOk)
    assert first.position.access_hold_from == DUE + timedelta(days=60)
    # A version that takes effect after the due date is not read at the due date.
    record(timedelta(days=90), DUE + timedelta(days=1))
    again = _read(scratch, scope, document_id)
    assert isinstance(again, PositionReadOk)
    assert again.position.access_hold_from == DUE + timedelta(days=60)


def test_a_prepaid_account_is_refused_as_not_postpaid(scratch) -> None:
    scope, _, document_id = _world(scratch, contract="prepaid")
    assert _read(scratch, scope, document_id) == PositionUnavailable(
        "not_postpaid", None
    )


# --- the assembly chain ------------------------------------------------------


def _publish_policy(scratch, scope) -> UUID:
    code = f"postpaid_{uuid4().hex}"
    version_id = uuid4()
    with session(scratch, scope) as db:
        CollectionPolicyService.create(
            db,
            CreateCollectionPolicyV1(
                policy_id=uuid4(),
                scope=scope,
                policy_code=code,
                description="GAP-4 assembly chain",
            ),
        )
        steps = tuple(
            PolicyStepDraftV1(
                code=f"notice_{days}",
                ordinal=ordinal,
                offset=timedelta(days=days),
                offset_anchor="exposure_at",
                request_kind="notice",
                action_code=None,
                purpose_code="overdue_notice",
                effect_scope=None,
                receipt_required=False,
                retry_offsets=(),
            )
            for ordinal, days in ((1, 7), (2, 30))
        )
        CollectionPolicyService.publish(
            db,
            scope=scope,
            draft=PolicyVersionDraftV1(
                policy_code=code,
                reason_code="invoice_overdue",
                collection_timing="arrears",
                grace=None,
                steps=steps,
            ),
            publication=PolicyPublicationV1(
                policy_version_id=version_id,
                version=1,
                effective_from=ISSUED - timedelta(days=1),
                actor_ref="service:chain",
                reason="GAP-4 chain",
                published_at=ISSUED,
            ),
        )
        db.commit()
    return version_id


def _trigger_of(handle) -> TimerTriggerV1:
    return TimerTriggerV1(
        handle.timer_id,
        handle.identity,
        handle.generation,
        handle.due_at,
        handle.output_event_type,
        handle.expected_source_version,
    )


class _Chain:
    """What the assembly does, one transaction per step."""

    def __init__(self, scratch, scope, document_id) -> None:
        self.scratch = scratch
        self.scope = scope
        self.document_id = document_id
        self.timer = FakeCollectionsTimer()
        self.policy = _publish_policy(scratch, scope)
        self.ref = f"document:{document_id}"
        self.sequence = 0
        self.delivered: set[int] = set()  # the outbox delivers each event once

    def _command(self, observation) -> AssessCollectionExposureV1:
        self.sequence += 1
        return AssessCollectionExposureV1(
            command_id=uuid4(),
            idempotency_key=f"chain:{self.document_id}:{self.sequence}",
            correlation_id=uuid4(),
            causal_event_id=f"billing:{self.sequence}",
            scope=self.scope,
            source_owner=OWNER,
            exposure_ref=self.ref,
            subject_ref=observation.subject_ref,
            service_ref=observation.service_ref,
            collection_timing=observation.collection_timing,
            reason_code=observation.reason_code,
            trigger=TriggerProvenanceV1(
                kind="receivable_changed",
                trigger_id=f"t{self.sequence}",
                triggered_at=ISSUED,
            ),
        )

    def assess(self, at: datetime, wake=None):
        observation = _read(self.scratch, self.scope, self.document_id).position
        with session(self.scratch, self.scope) as db:
            result = CollectionCaseService.assess(
                db,
                command=self._command(observation),
                policy_version_id=self.policy,
                reader=BillingReceivablesReader(db),
                timer=self.timer,
                assessed_at=at,
                exposure_wake=wake,
            )
            db.commit()
        return result

    def wake(self, purpose: str, entity: str):
        identity = next(
            i
            for i in self.timer.identities
            if i.purpose == purpose and i.entity_id == entity
        )
        with session(self.scratch, self.scope) as db:
            handle = self.timer.current(db, identity)
        assert handle is not None
        return handle

    def balance_pass(self, case_id: UUID):
        """The assembly's step before the hold wake: its own transaction.

        The invoice comes from the public case read, not from fixture knowledge.
        """

        with session(self.scratch, self.scope) as db:
            view = get_collection_case(db, scope=self.scope, case_id=case_id)
        prefix, _, identity = view.exposure_ref.partition(":")
        assert (view.source_owner, prefix) == (OWNER, "document")
        with session(self.scratch, self.scope) as db:
            outcome = apply_balance_on_invoice_issued(
                db,
                scope=self.scope,
                request=InvoiceIssuedV1(UUID(identity)),
                ports=make_ports(RecordingTargets()),
            )
            db.commit()
        return outcome

    def hold_wake(self, case_id: UUID, at: datetime):
        handle = self.wake("access_hold", str(case_id))
        trigger = TimerTriggerV1(
            handle.timer_id,
            handle.identity,
            handle.generation,
            handle.due_at,
            handle.output_event_type,
            handle.expected_source_version,
        )
        with session(self.scratch, self.scope) as db:
            result = CollectionCaseService.process_access_hold_due(
                db,
                command=ProcessCollectionAccessHoldDueV1(
                    scope=self.scope, trigger=trigger, processed_at=at
                ),
                reader=BillingReceivablesReader(db),
                timer=self.timer,
            )
            db.commit()
        return result

    def events(self) -> list[OutboxEvent]:
        with session(self.scratch, self.scope) as db:
            return [
                e
                for e in db.scalars(select(OutboxEvent)).all()
                if e.event_type == ACCESS_EVENT
            ]

    def fan_out(self, service_ref: str) -> DesiredAccess:
        """Fake Sub-thin: write COLLECTIONS_HOLD (V2) from every event, then decide."""

        with session(self.scratch, self.scope) as db:
            for event in self.events_in(db):
                payload = event.payload
                if payload["source_version"] in self.delivered:
                    continue
                self.delivered.add(payload["source_version"])
                record_access_input_v2(
                    db,
                    scope=self.scope,
                    command=RecordAccessInputV2(
                        service_reference=service_ref,
                        signal=AccessSignal.COLLECTIONS_HOLD,
                        active=payload["active"],
                        source_reference=payload["source_reference"],
                        observed_at=datetime.fromisoformat(payload["observed_at"]),
                        source_version=payload["source_version"],
                        policy_version=payload["policy_version"],
                    ),
                )
            decision = resolve_desired_access(
                db,
                scope=self.scope,
                command=ResolveDesiredAccess(service_ref, DUE + timedelta(days=100)),
            )
            db.commit()
            return decision.desired_access

    @staticmethod
    def events_in(db) -> list[OutboxEvent]:
        return sorted(
            (
                e
                for e in db.scalars(select(OutboxEvent)).all()
                if e.event_type == ACCESS_EVENT
            ),
            key=lambda e: e.payload["source_version"],
        )


def test_chain_not_due_then_due_then_hold_then_payment_lifts(scratch) -> None:
    scope, account_id, document_id = _world(scratch)
    chain = _Chain(scratch, scope, document_id)
    service_ref = "service:example"

    blocked = chain.assess(ISSUED + timedelta(hours=1))
    assert blocked == AssessmentBlocked("receivable_not_due", None)
    due_wake = chain.wake("due", f"{OWNER}:{chain.ref}")
    assert due_wake.due_at == DUE

    opened = chain.assess(DUE, wake=_trigger_of(due_wake))
    assert isinstance(opened, CaseAssessed) and opened.opened
    with session(scratch, scope) as db:  # the fired wake is consumed, none scheduled
        assert chain.timer.current(db, due_wake.identity) is None
    hold_wake = chain.wake("access_hold", str(opened.case_id))
    assert hold_wake.due_at == DUE + DEFAULT_GRACE
    assert chain.events() == []

    # No balance: the pass is a quiet no-op and the hold wake holds the account.
    assert chain.balance_pass(opened.case_id).applied_document_ids == ()
    evaluated = chain.hold_wake(opened.case_id, DUE + DEFAULT_GRACE)
    assert evaluated.subject_state == "held"
    assert len(chain.events()) == 1
    assert chain.fan_out(service_ref) is DesiredAccess.DENY
    with session(scratch, scope) as db:
        state = get_collection_subject_state(
            db, scope=scope, subject_ref=subject_ref_for(account_id)
        )
    assert state.state == "held"

    # Payment: the deposit pays the invoice (F1), Billing re-emits, assess resolves.
    deposit(scratch, scope, account_id, "500.00")
    resolved = chain.assess(DUE + timedelta(days=5))
    assert isinstance(resolved, CaseAssessed) and resolved.lifecycle == "resolved"
    events = chain.events()
    assert sorted(e.payload["active"] for e in events) == [False, True]
    assert chain.fan_out(service_ref) is DesiredAccess.ALLOW


def test_chain_balance_pass_before_the_hold_prevents_the_hold(scratch) -> None:
    scope, account_id, document_id = _world(scratch, balance="500.00")
    chain = _Chain(scratch, scope, document_id)
    opened = chain.assess(DUE)
    assert isinstance(opened, CaseAssessed)
    assert chain.balance_pass(opened.case_id).applied_document_ids == (document_id,)
    evaluated = chain.hold_wake(opened.case_id, DUE + DEFAULT_GRACE)
    assert evaluated.lifecycle == "resolved"
    assert evaluated.subject_state == "clear"
    assert chain.events() == []
    assert chain.fan_out("service:example") is DesiredAccess.ALLOW


def test_chain_a_collection_hold_restores_service_and_a_lift_cuts_it_again(
    scratch,
) -> None:
    scope, account_id, document_id = _world(scratch)
    chain = _Chain(scratch, scope, document_id)
    opened = chain.assess(DUE)
    chain.hold_wake(opened.case_id, DUE + DEFAULT_GRACE)
    assert chain.fan_out("service:example") is DesiredAccess.DENY
    subject = subject_ref_for(account_id)
    with session(scratch, scope) as db:
        CollectionHoldService.place(
            db,
            PlaceCollectionHoldV1(
                command_id=uuid4(),
                idempotency_key=f"chain-place:{document_id}",
                scope=scope,
                subject_ref=subject,
                external_ref="payment_arrangement:chain",
                reason_code="arrangement_approved",
                evidence_ref="arrangement:chain",
                actor_ref="system:sub-thin",
                placed_at=DUE + DEFAULT_GRACE + timedelta(hours=1),
            ),
        )
        db.commit()
    assert chain.fan_out("service:example") is DesiredAccess.ALLOW
    assert [e.payload["active"] for e in chain.events()].count(False) == 1

    # Lifting the hold cuts the still-overdue account again (the hold stays engaged).
    with session(scratch, scope) as db:
        lifted = CollectionHoldService.lift(
            db,
            LiftCollectionHoldV1(
                command_id=uuid4(),
                idempotency_key=f"chain-lift:{document_id}",
                scope=scope,
                subject_ref=subject,
                external_ref="payment_arrangement:chain",
                reason_code="arrangement_ended",
                evidence_ref="arrangement:chain",
                actor_ref="system:sub-thin",
                lifted_at=DUE + DEFAULT_GRACE + timedelta(hours=2),
            ),
            chain.timer,
        )
        db.commit()
    assert isinstance(lifted, HoldLifted)
    assert chain.fan_out("service:example") is DesiredAccess.DENY
    assert [e.payload["active"] for e in chain.events()] == [True, False, True]


def test_chain_late_opened_case_balance_pass_runs_before_the_hold_engages(
    scratch,
) -> None:
    scope, account_id, document_id = _world(scratch, balance="500.00")
    chain = _Chain(scratch, scope, document_id)
    late = DUE + timedelta(days=30)  # long past due + grace
    opened = chain.assess(late)
    assert isinstance(opened, CaseAssessed) and opened.opened
    # Past its instant but not engaged: nothing is cut yet, and the wake is due now.
    assert chain.events() == []
    assert chain.wake("access_hold", str(opened.case_id)).due_at == late
    assert chain.fan_out("service:example") is DesiredAccess.ALLOW
    # The balance pass pays the invoice, then the wake finds it closed: never held.
    assert chain.balance_pass(opened.case_id).applied_document_ids == (document_id,)
    evaluated = chain.hold_wake(opened.case_id, late)
    assert (evaluated.lifecycle, evaluated.subject_state) == ("resolved", "clear")
    assert chain.events() == []


def test_chain_late_opened_case_without_a_balance_is_held_by_the_wake(
    scratch,
) -> None:
    scope, account_id, document_id = _world(scratch)
    chain = _Chain(scratch, scope, document_id)
    late = DUE + timedelta(days=30)
    opened = chain.assess(late)
    assert chain.events() == []
    assert chain.balance_pass(opened.case_id).applied_document_ids == ()
    assert chain.hold_wake(opened.case_id, late).subject_state == "held"
    assert len(chain.events()) == 1
    assert chain.fan_out("service:example") is DesiredAccess.DENY


def test_an_item_issued_before_the_flip_reads_terms_at_the_floor(scratch) -> None:
    scope = new_scope(scratch)
    before_flip = BASE - timedelta(days=3)  # the fixture's terms start at BASE - 12h
    with session(scratch, scope) as db:
        seed_default_terms(db, scope, contract_type="postpaid")
        account = seed_account(db, scope)
        invoice = seed_invoice(
            db,
            scope,
            SimpleNamespace(id=account.id),
            net="100.00",
            issued_at=before_flip,
        )
        db.commit()
        document_id = invoice.id

    def read(floor):
        with session(scratch, scope) as db:
            return BillingReceivablesReader(db, terms_floor_at=floor).read(
                scope=scope,
                source_owner=OWNER,
                exposure_ref=f"document:{document_id}",
                as_of=before_flip,
            )

    # No floor, or a floor still before any terms: blocked, not a poison message.
    assert read(None) == PositionUnavailable("account_terms_missing", None)
    assert read(before_flip) == PositionUnavailable("account_terms_missing", None)
    # With the flip as the floor the item is chased on the terms in force then.
    result = read(BASE)
    assert isinstance(result, PositionReadOk)
    due = before_flip + timedelta(days=30)
    assert result.position.due_at == due
    assert result.position.access_hold_from == due + DEFAULT_GRACE
