"""Manifest-owned open registry of product-neutral fulfillment participants."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dotmac_kernel.modules import AnyManifest


class DuplicateParticipantError(ValueError):
    """Two installed modules claimed one participant code."""


class UndeclaredParticipantError(KeyError):
    """A saga step named a participant no installed module declares."""


class ParticipantRegistry:
    """Immutable owner map derived from installed module manifests."""

    __slots__ = ("_owner_by_code",)

    def __init__(self, declarations: Iterable[tuple[str, str]]) -> None:
        owner_by_code: dict[str, str] = {}
        for owner, raw_code in declarations:
            code = raw_code.strip()
            if not code:
                raise ValueError("provisioning participant code must not be blank")
            existing = owner_by_code.get(code)
            if existing is not None and existing != owner:
                raise DuplicateParticipantError(
                    f"provisioning participant {code!r} declared by both "
                    f"{existing!r} and {owner!r}"
                )
            owner_by_code[code] = owner
        self._owner_by_code = owner_by_code

    @classmethod
    def from_manifests(cls, manifests: Iterable[AnyManifest]) -> ParticipantRegistry:
        declarations: list[tuple[str, str]] = []
        for manifest in manifests:
            owner = getattr(manifest, "code", None) or manifest.name
            declarations.extend(
                (owner, code)
                for code in getattr(manifest, "provisioning_participants", ())
            )
        return cls(declarations)

    def require(self, code: str) -> None:
        if code not in self._owner_by_code:
            raise UndeclaredParticipantError(
                f"provisioning participant {code!r} is not declared by any "
                "installed module"
            )

    def owner(self, code: str) -> str | None:
        return self._owner_by_code.get(code)

    def codes(self) -> frozenset[str]:
        return frozenset(self._owner_by_code)


__all__ = [
    "DuplicateParticipantError",
    "ParticipantRegistry",
    "UndeclaredParticipantError",
]
