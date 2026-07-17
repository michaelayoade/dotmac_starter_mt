"""Tenant-scoped auth endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_tenant, require_user_auth
from app.core.models import Person, Tenant
from app.features.auth import service as auth_flows
from app.features.auth.schemas import (
    CurrentUserResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(
    prefix="/auth", tags=["auth"], dependencies=[Depends(require_tenant)]
)


@router.post(
    "/register", response_model=CurrentUserResponse, status_code=status.HTTP_201_CREATED
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> CurrentUserResponse:
    person = auth_flows.register(db, tenant, payload)
    return _current_user_response(person)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> TokenResponse:
    result = auth_flows.login(db, tenant, payload)
    return TokenResponse(access_token=result.access_token, token_type=result.token_type)


@router.get("/me", response_model=CurrentUserResponse)
def me(person: Person = Depends(require_user_auth)) -> CurrentUserResponse:
    return _current_user_response(person)


def _current_user_response(person: Person) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=person.id,
        email=person.email,
        first_name=person.first_name,
        last_name=person.last_name,
        tenant_id=person.tenant_id,
    )
