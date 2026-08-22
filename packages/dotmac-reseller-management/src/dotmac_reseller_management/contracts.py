"""Typed commands for the provider-neutral reseller owner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

_AUTHORITY_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")
ACCOUNT_STATUSES = frozenset({"active", "suspended", "retired"})


class ContractError(ValueError):
    """A reseller command is malformed."""


def required(name: str, value: str, limit: int = 255) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ContractError(f"{name} is required and must be at most {limit} chars")
    return normalized


@dataclass(frozen=True, slots=True)
class CreateResellerAccount:
    code: str
    name: str
    party_role_ref: str
    parent_account_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", required("code", self.code, 80).upper())
        object.__setattr__(self, "name", required("name", self.name, 200))
        object.__setattr__(
            self, "party_role_ref", required("party_role_ref", self.party_role_ref)
        )


@dataclass(frozen=True, slots=True)
class PublishAuthority:
    account_id: UUID
    authority_codes: tuple[str, ...]
    evidence_ref: str

    def __post_init__(self) -> None:
        codes = tuple(sorted({code.strip().lower() for code in self.authority_codes}))
        invalid = [code for code in codes if not _AUTHORITY_CODE.fullmatch(code)]
        if invalid:
            raise ContractError(
                "authority_codes must be stable lower-case capability identifiers"
            )
        object.__setattr__(self, "authority_codes", codes)
        object.__setattr__(
            self, "evidence_ref", required("evidence_ref", self.evidence_ref)
        )


@dataclass(frozen=True, slots=True)
class SetParent:
    account_id: UUID
    parent_account_id: UUID | None
    evidence_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_ref", required("evidence_ref", self.evidence_ref)
        )


@dataclass(frozen=True, slots=True)
class BindMember:
    account_id: UUID
    member_ref: str
    evidence_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "member_ref", required("member_ref", self.member_ref))
        object.__setattr__(
            self, "evidence_ref", required("evidence_ref", self.evidence_ref)
        )


@dataclass(frozen=True, slots=True)
class BindCustomerAccount:
    account_id: UUID
    customer_account_ref: str
    evidence_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "customer_account_ref",
            required("customer_account_ref", self.customer_account_ref),
        )
        object.__setattr__(
            self, "evidence_ref", required("evidence_ref", self.evidence_ref)
        )


@dataclass(frozen=True, slots=True)
class ChangeStatus:
    account_id: UUID
    target_status: str
    evidence_ref: str

    def __post_init__(self) -> None:
        target = self.target_status.strip().lower()
        if target not in ACCOUNT_STATUSES:
            raise ContractError("target_status must be active, suspended, or retired")
        object.__setattr__(self, "target_status", target)
        object.__setattr__(
            self, "evidence_ref", required("evidence_ref", self.evidence_ref)
        )


__all__ = [
    "ACCOUNT_STATUSES",
    "BindCustomerAccount",
    "BindMember",
    "ChangeStatus",
    "ContractError",
    "CreateResellerAccount",
    "PublishAuthority",
    "SetParent",
]
