"""Pure commands and refusals for customer accounts."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from uuid import UUID


class CustomerError(Exception):
    """Base refusal."""


class NotFound(CustomerError):
    """A scoped account does not exist."""


class Conflict(CustomerError):
    """An identity or lifecycle rule conflicts."""


class AccountStatus(enum.StrEnum):
    PROSPECT = "PROSPECT"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class PartyReferenceRole(enum.StrEnum):
    ACCOUNT_HOLDER = "ACCOUNT_HOLDER"
    BILLING_CONTACT = "BILLING_CONTACT"
    SERVICE_USER = "SERVICE_USER"


@dataclass(frozen=True, slots=True)
class CreateCustomerAccount:
    account_number: str
    display_name: str
    status: AccountStatus = AccountStatus.PROSPECT


@dataclass(frozen=True, slots=True)
class SetCustomerProfile:
    account_id: UUID
    segment: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class LinkPartyReference:
    account_id: UUID
    party_system: str
    party_reference: str
    role: PartyReferenceRole


__all__ = [
    "AccountStatus",
    "Conflict",
    "CreateCustomerAccount",
    "CustomerError",
    "LinkPartyReference",
    "NotFound",
    "PartyReferenceRole",
    "SetCustomerProfile",
]
