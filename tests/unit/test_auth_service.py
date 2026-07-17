"""Unit coverage for `app.features.auth.service` email case-normalization
(Task 7 carry-over item).

`Party.email` is looked up case-insensitively (the `parties` table's unique
index is `lower(email)`-based) — `UserCredential.email` must agree with that
semantics, so `register`/`login` normalize the incoming address to lowercase
before storing/querying it. Otherwise a user who registered with a
mixed-case address could be locked out by logging in with the lowercase
form (or vice versa).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import UnauthorizedError
from app.core.models import Tenant
from app.features.auth import service as auth_service
from app.features.auth.schemas import LoginRequest, RegisterRequest

PASSWORD = "correct horse battery staple"


def test_register_mixed_case_email_then_login_lowercase_succeeds(
    db: Session, tenant_row: Tenant
) -> None:
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
