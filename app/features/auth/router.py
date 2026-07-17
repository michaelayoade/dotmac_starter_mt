"""Tenant-scoped auth endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_tenant, require_user_auth
from app.core.models import Party, Tenant
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
    view = auth_flows.register(db, tenant, payload)
    return _current_user_response(view)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> TokenResponse:
    result = auth_flows.login(db, tenant, payload)
    return TokenResponse(access_token=result.access_token, token_type=result.token_type)


@router.get("/me", response_model=CurrentUserResponse)
def me(
    party: Party = Depends(require_user_auth),
    db: Session = Depends(get_db),
) -> CurrentUserResponse:
    view = auth_flows.get_current_user_view(db, party)
    return _current_user_response(view)


def _current_user_response(view: auth_flows.PersonView) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=view.id,
        email=view.email,
        first_name=view.first_name,
        last_name=view.last_name,
        tenant_id=view.tenant_id,
    )
