"""Entity registry — which entities can carry custom fields.

All currently registrable entities live in `app.core.models` (core owns the
identity/tenancy primitives; features never import each other), so this dict
only ever needs a core import today. It is the extension point future
features use to register their own models — service-layer validation
(Task 10) calls `resolve_entity` to reject a `CustomFieldDefinition.entity_type`
that isn't in this dict.
"""

from __future__ import annotations

from app.core.exceptions import BadRequestError
from app.core.models import Party

ENTITY_MODELS: dict[str, type] = {"party": Party}


def resolve_entity(entity_type: str) -> type:
    try:
        return ENTITY_MODELS[entity_type]
    except KeyError:
        raise BadRequestError(f"Unknown entity_type: {entity_type!r}") from None
