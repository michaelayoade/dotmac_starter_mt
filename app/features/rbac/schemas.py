"""Pydantic schemas for the remaining audit endpoint.

Moved here from `router.py` (inline models) so `service.py` can take concrete
payload types instead of `Any`. Field names, validation, and response shapes
are unchanged from the inline versions.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AuditEventRead(BaseModel):
    id: UUID
    actor_party_id: UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict[str, object]
    created_at: datetime
    model_config = {"from_attributes": True}
