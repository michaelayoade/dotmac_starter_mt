"""`dotmac_kernel.external_identity` — bind a verified external subject, and
refuse everything else.

The behaviours worth pinning are mostly REFUSALS, because this seam's whole
value is what it declines to do: it does not provision, does not match on
email, does not read external roles, and does not accept a subject on the
strength of its issuer alone.

SQLite/in-memory, so tenancy correctness is NOT tested here — the cross-tenant
canary is `tests/test_external_identity_isolation.py`, on real Postgres with
RLS. What these tests cover is the service logic above the database.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.exceptions import ConflictError, NotFoundError
from dotmac_kernel.external_identity import (
    ResolvedExternalIdentity,
    bind_external_identity,
    disable_external_identity_binding,
    finalize_external_login,
    record_external_authentication,
    resolve_external_identity,
)
from dotmac_kernel.models import (
    ExternalIdentityBinding,
    Party,
    PartyOrganization,
    PartyPerson,
    PartyType,
    Tenant,
)
from sqlalchemy.orm import Session

PROVIDER = "corp-idp"
ISSUER = "https://idp.example.com"
SUBJECT = "8f14e45f-ea9c-4f2a-9f1b-2c3d4e5f6a7b"
BOUND_BY = "admin@example.com"
REASON = "Verified against HR record #4412 during onboarding."


def _person(db: Session, tenant: Tenant, email: str) -> Party:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=email,
        email=email,
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Test", last_name="User"))
    db.flush()
    return party


def _organization(db: Session, tenant: Tenant, name: str) -> Party:
    party = Party(
        tenant_id=tenant.id, party_type=PartyType.organization, display_name=name
    )
    db.add(party)
    db.flush()
    db.add(PartyOrganization(party_id=party.id, legal_name=name))
    db.flush()
    return party


def _bind(db: Session, tenant: Tenant, party: Party, **over) -> ExternalIdentityBinding:
    kwargs = {
        "provider_binding": PROVIDER,
        "issuer": ISSUER,
        "subject": SUBJECT,
        "bound_by": BOUND_BY,
        "reason": REASON,
    }
    kwargs.update(over)
    return bind_external_identity(db, tenant=tenant, party=party, **kwargs)


# ── Resolution ──────────────────────────────────────────────────────────────


def test_a_bound_subject_resolves_to_its_party(db: Session, tenant_row: Tenant) -> None:
    party = _person(db, tenant_row, "person@example.com")
    _bind(db, tenant_row, party)

    resolved = resolve_external_identity(
        db,
        tenant=tenant_row,
        provider_binding=PROVIDER,
        issuer=ISSUER,
        subject=SUBJECT,
    )
    assert isinstance(resolved, ResolvedExternalIdentity)
    assert resolved.party.id == party.id


def test_an_unbound_subject_resolves_to_none_and_provisions_nothing(
    db: Session, tenant_row: Tenant
) -> None:
    """The anti-provisioning property. An unknown subject is not an error and
    is certainly not an invitation to create an account."""
    before = db.query(Party).count()

    assert (
        resolve_external_identity(
            db,
            tenant=tenant_row,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject="nobody-has-ever-seen-this",
        )
        is None
    )
    assert db.query(Party).count() == before
    assert db.query(ExternalIdentityBinding).count() == 0


def test_a_matching_email_does_not_link_an_unbound_subject(
    db: Session, tenant_row: Tenant
) -> None:
    """Email-based linking is account takeover with a convenience argument: an
    attacker who controls an IdP account claiming a known address would inherit
    that person. A party with the very email the provider would report still
    does not resolve without a binding."""
    _person(db, tenant_row, "victim@example.com")

    assert (
        resolve_external_identity(
            db,
            tenant=tenant_row,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject="victim@example.com",
        )
        is None
    )


def test_the_issuer_alone_does_not_authenticate_a_subject(
    db: Session, tenant_row: Tenant
) -> None:
    """The trust-direction property. A binding made for one configured provider
    must not be satisfied by a DIFFERENT registration presenting the same
    issuer/subject pair — the provider registration is the trusted half, and it
    is the half that did not arrive inside the token."""
    party = _person(db, tenant_row, "person@example.com")
    _bind(db, tenant_row, party)

    assert (
        resolve_external_identity(
            db,
            tenant=tenant_row,
            provider_binding="some-other-registration",
            issuer=ISSUER,
            subject=SUBJECT,
        )
        is None
    )


def test_a_disabled_binding_does_not_resolve(db: Session, tenant_row: Tenant) -> None:
    party = _person(db, tenant_row, "person@example.com")
    binding = _bind(db, tenant_row, party)
    disable_external_identity_binding(db, tenant=tenant_row, binding_id=binding.id)

    assert (
        resolve_external_identity(
            db,
            tenant=tenant_row,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject=SUBJECT,
        )
        is None
    )


def test_a_deactivated_party_stops_resolving_without_touching_the_binding(
    db: Session, tenant_row: Tenant
) -> None:
    """Re-checked every time, not trusted from bind time: a binding made a year
    ago says nothing about whether the account may still log in today."""
    party = _person(db, tenant_row, "leaver@example.com")
    _bind(db, tenant_row, party)
    party.is_active = False
    db.flush()

    assert (
        resolve_external_identity(
            db,
            tenant=tenant_row,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject=SUBJECT,
        )
        is None
    )
    # The binding itself is untouched — deactivating a person is not unbinding.
    assert db.query(ExternalIdentityBinding).one().is_active is True


def test_resolution_is_whitespace_insensitive_at_the_edges(
    db: Session, tenant_row: Tenant
) -> None:
    party = _person(db, tenant_row, "person@example.com")
    _bind(db, tenant_row, party)

    resolved = resolve_external_identity(
        db,
        tenant=tenant_row,
        provider_binding=f"  {PROVIDER} ",
        issuer=f" {ISSUER}",
        subject=f"{SUBJECT}  ",
    )
    assert resolved is not None and resolved.party.id == party.id


# ── Binding ─────────────────────────────────────────────────────────────────


def test_binding_records_who_and_why(db: Session, tenant_row: Tenant) -> None:
    party = _person(db, tenant_row, "person@example.com")
    binding = _bind(db, tenant_row, party)

    assert binding.bound_by == BOUND_BY
    assert binding.bind_reason == REASON
    assert binding.bound_at is not None
    assert binding.last_authenticated_at is None


@pytest.mark.parametrize(
    "blank_field",
    ["provider_binding", "issuer", "subject", "bound_by", "reason"],
)
def test_a_blank_field_is_refused(
    db: Session, tenant_row: Tenant, blank_field: str
) -> None:
    """A NOT NULL that permits `''` is not a constraint. Evidence fields matter
    most here: a binding with an empty reason is an unexplained grant of a
    login, which is precisely what the column exists to prevent."""
    party = _person(db, tenant_row, "person@example.com")
    with pytest.raises(ValueError):
        _bind(db, tenant_row, party, **{blank_field: "   "})


def test_an_oversized_field_is_refused_rather_than_silently_truncated(
    db: Session, tenant_row: Tenant
) -> None:
    """SQLite would store an over-length string happily and Postgres would
    raise at flush; neither is a good answer for a value that decides identity."""
    party = _person(db, tenant_row, "person@example.com")
    with pytest.raises(ValueError):
        _bind(db, tenant_row, party, subject="x" * 256)


def test_rebinding_the_same_party_reactivates_and_re_evidences(
    db: Session, tenant_row: Tenant
) -> None:
    """Idempotent for the same party — and this is the only way to restore a
    disabled binding, since the row keeps occupying its unique keys."""
    party = _person(db, tenant_row, "person@example.com")
    first = _bind(db, tenant_row, party)
    disable_external_identity_binding(db, tenant=tenant_row, binding_id=first.id)

    again = _bind(
        db, tenant_row, party, bound_by="other@example.com", reason="Restored"
    )

    assert again.id == first.id
    assert again.is_active is True
    assert again.bound_by == "other@example.com"
    assert again.bind_reason == "Restored"
    assert db.query(ExternalIdentityBinding).count() == 1


def test_rebinding_a_subject_to_a_different_party_is_refused(
    db: Session, tenant_row: Tenant
) -> None:
    """Silently repointing would transfer an account to another person."""
    first = _person(db, tenant_row, "first@example.com")
    second = _person(db, tenant_row, "second@example.com")
    _bind(db, tenant_row, first)

    with pytest.raises(ConflictError):
        _bind(db, tenant_row, second)


def test_a_party_may_not_hold_two_identities_at_one_provider(
    db: Session, tenant_row: Tenant
) -> None:
    party = _person(db, tenant_row, "person@example.com")
    _bind(db, tenant_row, party)

    with pytest.raises(ConflictError):
        _bind(db, tenant_row, party, subject="a-second-subject")


def test_a_party_may_hold_one_identity_per_provider(
    db: Session, tenant_row: Tenant
) -> None:
    """The reason the discriminator is the BINDING and not a mechanism code:
    two configured providers are two registrations, and a party may be known to
    both."""
    party = _person(db, tenant_row, "person@example.com")
    _bind(db, tenant_row, party)
    second = _bind(
        db, tenant_row, party, provider_binding="partner-idp", subject="other-subject"
    )

    assert second.provider_binding == "partner-idp"
    assert db.query(ExternalIdentityBinding).count() == 2


def test_an_organization_party_can_never_hold_an_external_identity(
    db: Session, tenant_row: Tenant
) -> None:
    """Same defence-in-depth `authenticate_request` applies: an organization has
    no one to authenticate."""
    org = _organization(db, tenant_row, "Acme Ltd")
    with pytest.raises(ConflictError):
        _bind(db, tenant_row, org)


# ── Disable / authentication stamp ──────────────────────────────────────────


def test_disabling_keeps_the_row_and_its_evidence(
    db: Session, tenant_row: Tenant
) -> None:
    party = _person(db, tenant_row, "person@example.com")
    binding = _bind(db, tenant_row, party)

    disabled = disable_external_identity_binding(
        db, tenant=tenant_row, binding_id=binding.id
    )

    assert disabled.is_active is False
    assert disabled.bound_by == BOUND_BY
    assert disabled.bind_reason == REASON
    assert db.query(ExternalIdentityBinding).count() == 1


def test_disabling_does_not_release_the_subject_for_another_party(
    db: Session, tenant_row: Tenant
) -> None:
    """The property the conflict message must not misstate.

    A disabled row keeps occupying the unique key, so "disable it first" is NOT
    a route to handing an external identity to somebody else. That is the safe
    default — silently freeing the tuple would make disabling a step on the path
    to takeover — and reassignment has to be a deliberate delete instead.
    """
    first = _person(db, tenant_row, "first@example.com")
    second = _person(db, tenant_row, "second@example.com")
    binding = _bind(db, tenant_row, first)
    disable_external_identity_binding(db, tenant=tenant_row, binding_id=binding.id)

    with pytest.raises(ConflictError):
        _bind(db, tenant_row, second)

    # And it is still recoverable for the ORIGINAL party.
    again = _bind(db, tenant_row, first)
    assert again.id == binding.id and again.is_active is True


def test_disabling_an_unknown_binding_raises_not_found(
    db: Session, tenant_row: Tenant
) -> None:
    from uuid import uuid4

    with pytest.raises(NotFoundError):
        disable_external_identity_binding(db, tenant=tenant_row, binding_id=uuid4())


def test_resolution_does_not_stamp_the_authentication_time(
    db: Session, tenant_row: Tenant
) -> None:
    """Resolution is a READ. A caller that resolves a party and then refuses the
    login for its own reasons must not leave evidence saying it authenticated."""
    party = _person(db, tenant_row, "person@example.com")
    binding = _bind(db, tenant_row, party)

    resolved = resolve_external_identity(
        db,
        tenant=tenant_row,
        provider_binding=PROVIDER,
        issuer=ISSUER,
        subject=SUBJECT,
    )
    db.refresh(binding)
    assert binding.last_authenticated_at is None

    # The whole reason resolution returns a typed result: the caller stamps the
    # binding it was just authorised by, without a second lookup that could
    # return a different row.
    assert resolved is not None
    assert resolved.binding_id == binding.id
    record_external_authentication(
        db, tenant=tenant_row, binding_id=resolved.binding_id
    )
    db.refresh(binding)
    assert binding.last_authenticated_at is not None


# ── Login finalization: the decision and the stamp are one step ─────────────


def test_finalizing_returns_the_party_and_stamps_in_the_same_call(
    db: Session, tenant_row: Tenant
) -> None:
    """The whole point: a caller never holds a decision it has not recorded.

    `resolve_external_identity` deliberately leaves the stamp to somebody else,
    which is what created the gap between deciding a login and issuing the
    session for it. Finalization does both, so there is no moment in which the
    decision exists un-recorded and un-locked.
    """
    party = _person(db, tenant_row, "person@example.com")
    binding = _bind(db, tenant_row, party)

    finalized = finalize_external_login(
        db,
        tenant=tenant_row,
        provider_binding=PROVIDER,
        issuer=ISSUER,
        subject=SUBJECT,
    )

    assert isinstance(finalized, ResolvedExternalIdentity)
    assert finalized.party.id == party.id
    assert finalized.binding_id == binding.id
    db.refresh(binding)
    assert binding.last_authenticated_at is not None


def test_finalizing_a_disabled_binding_refuses_and_records_nothing(
    db: Session, tenant_row: Tenant
) -> None:
    """A refusal must leave no evidence of an authentication that never
    happened — the stamp is the only signal that a binding is still live, and a
    refused login writing one would make a revoked identity look exercised."""
    party = _person(db, tenant_row, "person@example.com")
    binding = _bind(db, tenant_row, party)
    disable_external_identity_binding(db, tenant=tenant_row, binding_id=binding.id)

    assert (
        finalize_external_login(
            db,
            tenant=tenant_row,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject=SUBJECT,
        )
        is None
    )
    db.refresh(binding)
    assert binding.last_authenticated_at is None


def test_finalizing_an_unbound_subject_refuses_and_provisions_nothing(
    db: Session, tenant_row: Tenant
) -> None:
    """The login path inherits every refusal the read path has. A locked
    decision is still a decision to say no."""
    before = db.query(Party).count()

    assert (
        finalize_external_login(
            db,
            tenant=tenant_row,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject="nobody-has-ever-seen-this",
        )
        is None
    )
    assert db.query(Party).count() == before
    assert db.query(ExternalIdentityBinding).count() == 0


def test_finalizing_refuses_a_deactivated_party_without_stamping(
    db: Session, tenant_row: Tenant
) -> None:
    """The party is re-read inside the call, not trusted from bind time — a
    leaver whose binding nobody remembered to disable still cannot log in."""
    party = _person(db, tenant_row, "leaver@example.com")
    binding = _bind(db, tenant_row, party)
    party.is_active = False
    db.flush()

    assert (
        finalize_external_login(
            db,
            tenant=tenant_row,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject=SUBJECT,
        )
        is None
    )
    db.refresh(binding)
    assert binding.last_authenticated_at is None
    assert binding.is_active is True  # refusing a login is not unbinding


def test_finalizing_refuses_a_party_that_became_an_organization(
    db: Session, tenant_row: Tenant
) -> None:
    """Defence in depth against a row `bind_external_identity` would refuse to
    create today. The check is re-run at login rather than assumed from the
    bind, because the bind happened at some other time under some other code."""
    party = _person(db, tenant_row, "person@example.com")
    binding = _bind(db, tenant_row, party)
    party.party_type = PartyType.organization
    db.flush()

    assert (
        finalize_external_login(
            db,
            tenant=tenant_row,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject=SUBJECT,
        )
        is None
    )
    db.refresh(binding)
    assert binding.last_authenticated_at is None


def test_finalizing_keeps_the_trust_direction(db: Session, tenant_row: Tenant) -> None:
    """Locking changed the concurrency story and nothing else: a binding made
    for one configured provider is still not satisfied by a different
    registration presenting the same issuer/subject pair."""
    party = _person(db, tenant_row, "person@example.com")
    _bind(db, tenant_row, party)

    assert (
        finalize_external_login(
            db,
            tenant=tenant_row,
            provider_binding="some-other-registration",
            issuer=ISSUER,
            subject=SUBJECT,
        )
        is None
    )


def test_finalizing_is_whitespace_insensitive_at_the_edges(
    db: Session, tenant_row: Tenant
) -> None:
    party = _person(db, tenant_row, "person@example.com")
    _bind(db, tenant_row, party)

    finalized = finalize_external_login(
        db,
        tenant=tenant_row,
        provider_binding=f"  {PROVIDER} ",
        issuer=f" {ISSUER}",
        subject=f"{SUBJECT}  ",
    )
    assert finalized is not None and finalized.party.id == party.id


def test_both_entry_points_answer_with_the_same_shape(
    db: Session, tenant_row: Tenant
) -> None:
    """Migrating a caller off the racy pair must be one identifier, not a
    rewrite — otherwise the safe call is the expensive one and the racy one
    stays where it is."""
    party = _person(db, tenant_row, "person@example.com")
    binding = _bind(db, tenant_row, party)
    arguments = {
        "tenant": tenant_row,
        "provider_binding": PROVIDER,
        "issuer": ISSUER,
        "subject": SUBJECT,
    }

    read = resolve_external_identity(db, **arguments)
    finalized = finalize_external_login(db, **arguments)

    assert read is not None and finalized is not None
    assert (read.party.id, read.binding_id) == (finalized.party.id, binding.id)


def test_the_locking_read_locks_and_refuses_a_cached_row(
    db: Session, tenant_row: Tenant
) -> None:
    """Source-level, because the unit lane physically cannot observe either
    half (see the next test for the proof of that).

    Two things have to be true together and each is silently useless alone.
    `with_for_update` takes the row lock that serializes against the disable
    path. `populate_existing` is what makes the value tested under that lock the
    value in the database: without it a session that had already loaded the
    binding gets the identity map's copy, so the lock would be taken and then a
    pre-disable `is_active` examined — a lock guarding a stale attribute, which
    is worse than no lock because it reads as correct.
    """
    import inspect

    source = inspect.getsource(finalize_external_login)
    assert "with_for_update()" in source, "the login path stopped locking the row"
    # The CALL SITE, not the phrase. Counting `populate_existing=True` counts
    # the docstring that explains the technique too — the assertion said 2 and
    # the file has 3, of which one is prose. A source-text pin that counts its
    # own documentation fails the moment the documentation is good.
    assert source.count(".execution_options(populate_existing=True)") == 2, (
        "both the binding and the party must be re-read from the database, "
        "never served from the session's identity map"
    )


def test_the_unit_lane_cannot_see_the_lock() -> None:
    """The sensitivity disclosure that justifies the Postgres canary.

    SQLAlchemy's SQLite compiler drops `FOR UPDATE` entirely — sqlite has no
    such clause — so every test in this file would pass identically against a
    `finalize_external_login` that never locked anything. Asserting that here,
    rather than leaving it as folklore, is what stops a future reader concluding
    the unit lane covers the race. It does not; that is
    `tests/test_external_identity_login_race.py`.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql, sqlite

    statement = select(ExternalIdentityBinding).with_for_update()

    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in str(statement.compile(dialect=sqlite.dialect()))


# ── The module holds no protocol and no external authorization ──────────────


def test_the_module_reaches_no_network_and_reads_no_external_roles() -> None:
    """Source-level, in the spirit of ERP's `test_identity_protocol_boundary`.

    An import of an HTTP client here would mean the local binding seam had
    started doing protocol work; a mention of external role/group claims would
    mean authorization had begun leaking in from the provider. Both are the
    failures this module exists to make structurally impossible.
    """
    import pathlib

    import dotmac_kernel.external_identity as mod

    source = pathlib.Path(mod.__file__).read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # Strip the module docstring, which discusses these words on purpose.
    body = code.split('"""', 2)[-1]

    for forbidden in ("httpx", "requests", "urllib", "jwt", "jwks", "socket"):
        assert forbidden not in body, f"{forbidden!r} reached the binding seam"
    for forbidden in ("roles", "groups", "scopes", "entitlement"):
        assert forbidden not in body, (
            f"{forbidden!r} appears in the binding seam — external authorization "
            "must never be read, stored or mapped here"
        )
