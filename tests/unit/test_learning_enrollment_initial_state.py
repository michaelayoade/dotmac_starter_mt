"""Required new-state policy seams; authored for hosted execution only."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party
from dotmac_learning import (
    AccountPresenceSelection,
    ActiveAccountSelection,
    BulkEnrollCommand,
    BulkEnrollmentInitialState,
    EnsureInvitationEnrollmentCommand,
    InvitationEnrollmentInitialState,
    ParticipationForbidden,
    RosterAuthority,
    roster,
)
from sqlalchemy import literal, select, text


@pytest.mark.parametrize(
    "factory,fields",
    [
        (BulkEnrollmentInitialState, ("role_in_cohort", "status")),
        (InvitationEnrollmentInitialState, ("status",)),
    ],
)
def test_required_frozen_exact_strings_and_character_bounds(factory, fields):
    with pytest.raises(TypeError):
        factory()
    values = {name: "" for name in fields}
    state = factory(**values)
    with pytest.raises(FrozenInstanceError):
        state.status = "active"

    class StringSubclass(str):
        pass

    for name in fields:
        for value in ("", " \t ", "business-value", "界" * 20):
            assert getattr(factory(**{**values, name: value}), name) == value
        with pytest.raises(ValueError, match=name):
            factory(**{**values, name: "界" * 21})
        for value in (None, True, 1, [], {}, b"x", StringSubclass("active")):
            with pytest.raises(TypeError, match=name):
                factory(**{**values, name: value})


@pytest.mark.parametrize("kind", ["bulk", "invitation"])
def test_exact_type_and_tamper_revalidation_before_any_database_handoff(
    monkeypatch, kind
):
    tenant = uuid4()
    authority = RosterAuthority(tenant, True)
    if kind == "bulk":
        factory = BulkEnrollmentInitialState
        command = BulkEnrollCommand(
            tenant_id=tenant, cohort_id=uuid4(), emails=(), pacing_default_open=True
        )
        owner = roster.bulk_enroll
        values = {"role_in_cohort": "student", "status": "active"}
        kwargs = {
            "authority": authority,
            "accounts": AccountPresenceSelection(tenant, select(literal(uuid4()))),
        }
    else:
        factory = InvitationEnrollmentInitialState
        command = EnsureInvitationEnrollmentCommand(
            tenant_id=tenant,
            cohort_id=uuid4(),
            person_id=uuid4(),
            global_role="learner",
            pacing_default_open=True,
        )
        owner = roster.ensure_invitation_enrollment
        values = {"status": "active"}
        kwargs = {"authority": authority, "role_policy": object()}

    def forbidden(db):
        pytest.fail("invalid snapshot reached transaction owner")

    monkeypatch.setattr(roster, "_transaction", forbidden)
    with pytest.raises(TypeError):
        owner(None, command, **kwargs)
    with pytest.raises(TypeError):
        owner(None, command, factory(**values), **kwargs)

    class Bypass(factory):
        def __post_init__(self):
            pass

    for malformed in (None, {}, Bypass(**values)):
        with pytest.raises(TypeError, match="initial_state"):
            owner(None, command, initial_state=malformed, **kwargs)

    class StringSubclass(str):
        pass

    for field in values:
        for value, error in [
            (None, TypeError),
            (StringSubclass("active"), TypeError),
            ("界" * 21, ValueError),
        ]:
            state = factory(**values)
            object.__setattr__(state, field, value)
            with pytest.raises(error, match=field):
                owner(None, command, initial_state=state, **kwargs)


def test_presence_is_required_distinct_and_tenant_bound_before_db(monkeypatch):
    tenant = uuid4()
    command = BulkEnrollCommand(
        tenant_id=tenant, cohort_id=uuid4(), emails=(), pacing_default_open=True
    )
    kwargs = {
        "authority": RosterAuthority(tenant, True),
        "initial_state": BulkEnrollmentInitialState("student", "active"),
    }

    def forbidden(db):
        pytest.fail("invalid presence reached transaction owner")

    monkeypatch.setattr(roster, "_transaction", forbidden)
    with pytest.raises(TypeError):
        roster.bulk_enroll(None, command, **kwargs)
    with pytest.raises(TypeError, match="accounts"):
        roster.bulk_enroll(
            None,
            command,
            accounts=ActiveAccountSelection(tenant, select(literal(uuid4()))),
            **kwargs,
        )
    with pytest.raises(ParticipationForbidden, match="presence selection"):
        roster.bulk_enroll(
            None,
            command,
            accounts=AccountPresenceSelection(uuid4(), select(literal(uuid4()))),
            **kwargs,
        )


@pytest.mark.parametrize(
    "relation,error,detail",
    [
        ([uuid4()], TypeError, "SQLAlchemy Select"),
        ((uuid4(),), TypeError, "SQLAlchemy Select"),
        (None, TypeError, "SQLAlchemy Select"),
        ({"person_ids": uuid4()}, TypeError, "SQLAlchemy Select"),
        (text("SELECT id FROM public.parties"), TypeError, "SQLAlchemy Select"),
        (select(), ValueError, "exactly one column"),
        (select(Party.id, Party.tenant_id), ValueError, "exactly one column"),
        (select(Party.id, text("tenant_id")), ValueError, "exactly one column"),
        (select(literal("not-a-uuid")), TypeError, "Uuid column"),
        (select(literal(1)), TypeError, "Uuid column"),
    ],
)
def test_presence_constructor_and_tamper_revalidation_refuse_before_db(
    monkeypatch, relation, error, detail
):
    tenant = uuid4()
    with pytest.raises(error, match=detail):
        AccountPresenceSelection(tenant, relation)

    def forbidden(db):
        pytest.fail("malformed presence relation reached transaction owner")

    monkeypatch.setattr(roster, "_transaction", forbidden)
    accounts = AccountPresenceSelection(tenant, select(Party.id))
    object.__setattr__(accounts, "person_ids", relation)
    with pytest.raises(error, match=detail):
        roster.bulk_enroll(
            None,
            BulkEnrollCommand(
                tenant_id=tenant, cohort_id=uuid4(), emails=(), pacing_default_open=True
            ),
            authority=RosterAuthority(tenant, True),
            accounts=accounts,
            initial_state=BulkEnrollmentInitialState("student", "active"),
        )


def test_presence_subclass_wrapper_cannot_skip_validation_or_reach_db(monkeypatch):
    class Bypass(AccountPresenceSelection):
        def __post_init__(self):
            pass

    def forbidden(db):
        pytest.fail("subclass wrapper reached transaction owner")

    monkeypatch.setattr(roster, "_transaction", forbidden)
    tenant = uuid4()
    for relation in (select(Party.id), [uuid4()]):
        with pytest.raises(TypeError, match="accounts"):
            roster.bulk_enroll(
                None,
                BulkEnrollCommand(
                    tenant_id=tenant,
                    cohort_id=uuid4(),
                    emails=(),
                    pacing_default_open=True,
                ),
                authority=RosterAuthority(tenant, True),
                accounts=Bypass(tenant, relation),
                initial_state=BulkEnrollmentInitialState("student", "active"),
            )


def test_presence_tampered_tenant_refuses_before_db(monkeypatch):
    def forbidden(db):
        pytest.fail("tampered tenant reached transaction owner")

    monkeypatch.setattr(roster, "_transaction", forbidden)
    tenant = uuid4()
    accounts = AccountPresenceSelection(tenant, select(Party.id))
    object.__setattr__(accounts, "tenant_id", uuid4())
    with pytest.raises(ParticipationForbidden, match="presence selection"):
        roster.bulk_enroll(
            None,
            BulkEnrollCommand(
                tenant_id=tenant, cohort_id=uuid4(), emails=(), pacing_default_open=True
            ),
            authority=RosterAuthority(tenant, True),
            accounts=accounts,
            initial_state=BulkEnrollmentInitialState("student", "active"),
        )


def test_canonical_uuid_select_is_accepted_before_transaction_handoff(monkeypatch):
    class ReachedTransaction(Exception):
        pass

    marker = object()

    def reached(db):
        assert db is marker
        raise ReachedTransaction

    monkeypatch.setattr(roster, "_transaction", reached)
    tenant = uuid4()
    relation = select(Party.id).where(Party.tenant_id == tenant)
    accounts = AccountPresenceSelection(tenant, relation)
    assert accounts.person_ids is relation
    with pytest.raises(ReachedTransaction):
        roster.bulk_enroll(
            marker,
            BulkEnrollCommand(
                tenant_id=tenant, cohort_id=uuid4(), emails=(), pacing_default_open=True
            ),
            authority=RosterAuthority(tenant, True),
            accounts=accounts,
            initial_state=BulkEnrollmentInitialState("student", "active"),
        )
