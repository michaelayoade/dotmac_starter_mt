"""Unit coverage for `app.features.auth.service`: email case-normalization
(Task 7 carry-over item) and the single-email-authority login resolution
(Phase 2b.1 Task 3, finding F2).

`Party.email` is looked up case-insensitively (the `parties` table's unique
index is `lower(email)`-based) — `register`/`login` normalize the incoming
address to lowercase before storing/querying it, via
`dotmac_kernel.identity.normalize_email`. Otherwise a user who registered with a
mixed-case address could be locked out by logging in with the lowercase
form (or vice versa).

`login()` no longer reads a credential-local email copy at all (Task 3
dropped the credential table's own email column entirely — see
`UserCredential` in `dotmac_kernel/models.py`, moved there by control-plane
security Task 2, and `app/features/auth/service.py::login`'s
docstring): it resolves the `Party`
by `(tenant, normalize_email(email), party_type=person)` first, then the
credential row by `party_id`. `test_login_null_party_email_rejected` below
pins the documented consequence — a person party with a NULL email cannot
log in with any string, by construction of that query.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.exceptions import ForbiddenError, UnauthorizedError
from dotmac_kernel.models import Party, PartyRole, PartyType, Tenant, UserCredential
from dotmac_kernel.security import hash_password
from dotmac_kernel.settings_models import DomainSetting, SettingDomain, SettingValueType
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.auth import service as auth_service
from app.features.auth.schemas import LoginRequest, RegisterRequest

# Import for the side effect: registers the auth/registration_policy spec
# into the settings resolver registry (register() resolves it).
from app.features.settings import spec as _settings_spec  # noqa: F401

PASSWORD = "correct horse battery staple"


def _open_registration(db: Session, tenant: Tenant) -> None:
    """Set `auth.registration_policy = open` for `tenant` — self-registration
    is policy-CLOSED by default since control-plane security Task 2 (the
    SQLite counterpart of `tests/conftest.py::open_registration`)."""
    db.add(
        DomainSetting(
            tenant_id=tenant.id,
            domain=SettingDomain.auth,
            key="registration_policy",
            value_type=SettingValueType.string,
            value_text="open",
        )
    )
    db.flush()


def test_register_mixed_case_email_then_login_lowercase_succeeds(
    db: Session, tenant_row: Tenant
) -> None:
    _open_registration(db, tenant_row)
    view = auth_service.register(
        db,
        tenant_row,
        RegisterRequest(
            email="MiXeD@Example.COM",
            password=PASSWORD,
            first_name="Mixed",
            last_name="Case",
        ),
    )
    # The stored/returned identity is normalized too.
    assert view.email == "mixed@example.com"

    result = auth_service.login(
        db, tenant_row, LoginRequest(email="mixed@example.com", password=PASSWORD)
    )

    assert result.access_token


def test_register_lowercase_then_login_mixed_case_succeeds(
    db: Session, tenant_row: Tenant
) -> None:
    _open_registration(db, tenant_row)
    auth_service.register(
        db,
        tenant_row,
        RegisterRequest(
            email="lower@example.com",
            password=PASSWORD,
            first_name="Lower",
            last_name="Case",
        ),
    )

    result = auth_service.login(
        db, tenant_row, LoginRequest(email="LOWER@Example.com", password=PASSWORD)
    )

    assert result.access_token


def test_login_wrong_password_still_rejected_after_normalization(
    db: Session, tenant_row: Tenant
) -> None:
    _open_registration(db, tenant_row)
    auth_service.register(
        db,
        tenant_row,
        RegisterRequest(
            email="wrongpw@example.com",
            password=PASSWORD,
            first_name="Wrong",
            last_name="Pw",
        ),
    )

    with pytest.raises(UnauthorizedError):
        auth_service.login(
            db,
            tenant_row,
            LoginRequest(email="WRONGPW@example.com", password="not-the-password"),
        )


def test_login_null_party_email_rejected(db: Session, tenant_row: Tenant) -> None:
    """F2/Task 3's documented consequence: NULLing a person party's email
    (the write path the portal edit form uses, `parties/service.py::
    update_person_party`) disables login for that party outright — the
    login query is `Party.email == normalize_email(email)`, and a NULL
    column matches no string. Pinned here at the unit level (plain query
    logic, no RLS involved) as the cheap counterpart to the Postgres
    canary `tests/test_auth_email_authority.py::
    test_nulled_party_email_disables_login`.
    """
    _open_registration(db, tenant_row)
    view = auth_service.register(
        db,
        tenant_row,
        RegisterRequest(
            email="nullme@example.com",
            password=PASSWORD,
            first_name="Null",
            last_name="Me",
        ),
    )
    party = db.get(Party, view.id)
    assert party is not None
    party.email = None
    db.flush()

    with pytest.raises(UnauthorizedError):
        auth_service.login(
            db, tenant_row, LoginRequest(email="nullme@example.com", password=PASSWORD)
        )


def test_login_organization_party_same_email_rejected(
    db: Session, tenant_row: Tenant
) -> None:
    """Phase 2b.1 Task 3 review (Important #2): proves `login()`'s
    `Party.party_type == PartyType.person` filter is load-bearing, not
    incidental.

    An ORGANIZATION party can never be created by `register()` (it only ever
    builds `party_type=person` rows), so this constructs one directly, gives
    it the same email a login attempt uses, AND — the sharp part — binds a
    real, valid `UserCredential` to that org party's `id`. In production
    `register()` never creates such a row (nothing wires a credential to an
    organization party), but building this exact combination here is what
    makes the case sharp: without the `party_type == person` filter, the
    `Party` lookup would match this org party by email alone, the
    `UserCredential` lookup-by-`party_id` would then find this credential,
    and `verify_password` would succeed — i.e. login would 200 as an
    organization identity. With the filter in place, the `Party` query never
    matches the org party at all, `party` is `None`, the credential lookup is
    skipped, and login 401s, same as any other miss.
    """
    org_party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.organization,
        display_name="Acme Corp",
        email="org-shared@example.com",
    )
    db.add(org_party)
    db.flush()
    db.add(
        UserCredential(
            tenant_id=tenant_row.id,
            party_id=org_party.id,
            password_hash=hash_password(PASSWORD),
        )
    )
    db.flush()

    with pytest.raises(UnauthorizedError):
        auth_service.login(
            db,
            tenant_row,
            LoginRequest(email="org-shared@example.com", password=PASSWORD),
        )


def test_register_closed_policy_raises_forbidden(
    db: Session, tenant_row: Tenant
) -> None:
    """Control-plane security Task 2: with no explicit
    `auth.registration_policy = open` row, registration is CLOSED (the spec
    default) and the service raises `ForbiddenError("registration_closed")`
    before creating anything.
    """
    with pytest.raises(ForbiddenError, match="registration_closed"):
        auth_service.register(
            db,
            tenant_row,
            RegisterRequest(
                email="closed@example.com",
                password=PASSWORD,
                first_name="Closed",
                last_name="Door",
            ),
        )
    # Nothing was created.
    count = db.scalar(
        select(func.count()).select_from(Party).where(Party.tenant_id == tenant_row.id)
    )
    assert count == 0


def test_register_open_policy_grants_no_role(db: Session, tenant_row: Tenant) -> None:
    """Task 2 deleted `_assign_first_user_admin` outright: even the FIRST
    registered user of a tenant under an `open` policy is a PLAIN user —
    no role grant of any kind. Admin/owner accounts are provisioned, not
    registered.
    """
    _open_registration(db, tenant_row)
    view = auth_service.register(
        db,
        tenant_row,
        RegisterRequest(
            email="plain@example.com",
            password=PASSWORD,
            first_name="Plain",
            last_name="User",
        ),
    )
    assert view.email == "plain@example.com"
    grants = db.scalar(
        select(func.count())
        .select_from(PartyRole)
        .where(PartyRole.tenant_id == tenant_row.id)
    )
    assert grants == 0
