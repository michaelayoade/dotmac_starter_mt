"""Open, owner-declared vocabularies for Party context facts.

Role, relationship, membership, and contact-channel codes belong to adopting
products.  They are therefore plain strings in the database and must be
declared through this registry before a service accepts them.  Lifecycle states
remain closed module vocabulary in :mod:`dotmac_party.contracts`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar

Normalizer = Callable[[str], str]
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")


class PartyVocabularyError(ValueError):
    """A declaration is malformed, duplicated, or absent."""


def _validate(code: str, description: str, owner: str) -> None:
    if not _CODE.fullmatch(code):
        raise PartyVocabularyError(
            f"vocabulary code {code!r} must match {_CODE.pattern}"
        )
    if not description.strip():
        raise PartyVocabularyError(f"vocabulary code {code!r} needs a description")
    if not owner.strip():
        raise PartyVocabularyError(f"vocabulary code {code!r} needs an owner")


@dataclass(frozen=True, slots=True)
class RoleTypeSpec:
    code: str
    description: str
    owner: str
    key_required: bool = False

    def __post_init__(self) -> None:
        _validate(self.code, self.description, self.owner)


@dataclass(frozen=True, slots=True)
class RelationshipTypeSpec:
    code: str
    description: str
    owner: str
    subject_role_types: frozenset[str]
    object_role_types: frozenset[str]
    key_required: bool = False

    def __post_init__(self) -> None:
        _validate(self.code, self.description, self.owner)
        if not self.subject_role_types or not self.object_role_types:
            raise PartyVocabularyError(
                f"relationship type {self.code!r} must declare both endpoint "
                "role-type sets"
            )
        for endpoint in self.subject_role_types | self.object_role_types:
            if not _CODE.fullmatch(endpoint):
                raise PartyVocabularyError(
                    f"relationship type {self.code!r} has invalid endpoint "
                    f"role type {endpoint!r}"
                )


@dataclass(frozen=True, slots=True)
class MembershipTypeSpec:
    code: str
    description: str
    owner: str
    access_scope_keys: frozenset[str] = frozenset()
    key_required: bool = False

    def __post_init__(self) -> None:
        _validate(self.code, self.description, self.owner)
        for key in self.access_scope_keys:
            if not _CODE.fullmatch(key):
                raise PartyVocabularyError(
                    f"membership type {self.code!r} has invalid access-scope "
                    f"key {key!r}"
                )

    def normalize_access_scope(self, value: Mapping[str, object]) -> dict[str, object]:
        unknown = set(value) - self.access_scope_keys
        if unknown:
            raise PartyVocabularyError(
                f"membership type {self.code!r} received undeclared "
                f"access-scope keys: {sorted(unknown)}"
            )
        return dict(value)


@dataclass(frozen=True, slots=True)
class ContactChannelSpec:
    code: str
    description: str
    owner: str
    normalizer: Normalizer
    requires_provider_identity: bool = False

    def __post_init__(self) -> None:
        _validate(self.code, self.description, self.owner)
        if not callable(self.normalizer):
            raise PartyVocabularyError(
                f"contact channel {self.code!r} needs a callable normalizer"
            )


_Spec = TypeVar(
    "_Spec", RoleTypeSpec, RelationshipTypeSpec, MembershipTypeSpec, ContactChannelSpec
)


class _Declarations(Generic[_Spec]):
    def __init__(self, kind: str, specs: Iterable[_Spec]) -> None:
        values: dict[str, _Spec] = {}
        for spec in specs:
            if spec.code in values:
                raise PartyVocabularyError(
                    f"{kind} {spec.code!r} is declared more than once"
                )
            values[spec.code] = spec
        self.kind = kind
        self.values: Mapping[str, _Spec] = MappingProxyType(values)

    def require(self, code: str) -> _Spec:
        normalized = code.strip().lower()
        spec = self.values.get(normalized)
        if spec is None:
            known = ", ".join(sorted(self.values)) or "none"
            raise PartyVocabularyError(
                f"{self.kind} {normalized!r} is not declared; declared: {known}"
            )
        return spec


class PartyVocabularyRegistry:
    """The complete Party vocabulary selected by one product assembly."""

    def __init__(
        self,
        *,
        role_types: Iterable[RoleTypeSpec],
        relationship_types: Iterable[RelationshipTypeSpec],
        membership_types: Iterable[MembershipTypeSpec],
        contact_channels: Iterable[ContactChannelSpec],
    ) -> None:
        self._roles = _Declarations("role type", role_types)
        self._relationships = _Declarations("relationship type", relationship_types)
        self._memberships = _Declarations("membership type", membership_types)
        self._channels = _Declarations("contact channel", contact_channels)
        declared_roles = frozenset(self._roles.values)
        for relationship in self._relationships.values.values():
            unknown = (
                relationship.subject_role_types | relationship.object_role_types
            ) - declared_roles
            if unknown:
                raise PartyVocabularyError(
                    f"relationship type {relationship.code!r} references "
                    f"undeclared role types: {sorted(unknown)}"
                )

    def role_type(self, code: str) -> RoleTypeSpec:
        return self._roles.require(code)

    def relationship_type(self, code: str) -> RelationshipTypeSpec:
        return self._relationships.require(code)

    def membership_type(self, code: str) -> MembershipTypeSpec:
        return self._memberships.require(code)

    def contact_channel(self, code: str) -> ContactChannelSpec:
        return self._channels.require(code)


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or "@" not in normalized:
        raise PartyVocabularyError("email contact value is invalid")
    return normalized


def normalize_phone(value: str) -> str:
    raw = value.strip()
    prefix = "+" if raw.startswith("+") else ""
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) < 7:
        raise PartyVocabularyError("phone contact value is invalid")
    return f"{prefix}{digits}"


def normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise PartyVocabularyError("contact value must not be empty")
    return normalized


__all__ = [
    "ContactChannelSpec",
    "MembershipTypeSpec",
    "PartyVocabularyError",
    "PartyVocabularyRegistry",
    "RelationshipTypeSpec",
    "RoleTypeSpec",
    "normalize_email",
    "normalize_phone",
    "normalize_text",
]
