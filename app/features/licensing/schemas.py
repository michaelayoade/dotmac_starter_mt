"""API schemas for the licence receiver."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ApplyLicenceRequest(BaseModel):
    """The delivered envelope, verbatim (`dotmac-licence-envelope/1` JSON).
    The service verifies it — the route never interprets it."""

    envelope: dict[str, object] = Field(...)


class AcknowledgementOut(BaseModel):
    licence_id: str
    licence_version: int
    digest: str
    status: str
    reason: str | None = None
    deployment_id: str | None = None


class ImportRevocationListRequest(BaseModel):
    """A signed `dotmac-licence-revocation/1` envelope, verbatim."""

    envelope: dict[str, object] = Field(...)


class ImportRevocationListResponse(BaseModel):
    """`revoked_codes` is what actually changed locally — the operator-visible
    consequence of the import, not just that it was accepted."""

    accepted: bool
    list_version: int | None = None
    revoked_licence_ids: list[str]
    revoked_codes: list[str]
    reason: str | None = None


class ApplyLicenceResponse(BaseModel):
    acknowledgement: AcknowledgementOut
    applied: bool
    granted_codes: list[str]
    revoked_codes: list[str]
    validity: str | None = None
    reapplied: bool
