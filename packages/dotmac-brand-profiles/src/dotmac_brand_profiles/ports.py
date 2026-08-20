"""What a caller supplies, and the two things this module refuses to become.

## `ProfileFields` is a typed value, not a `**kwargs` dict

Every field is optional and every field is named. A `dict[str, Any]` would let a
caller write `primary_color` (Sub's spelling) into a module that stores
`primary_hex`, and the value would land in no column at all — silently, because
`setattr` on an ORM row with an unknown name is a Python attribute, not an error.

Naming them also makes the LOCK list checkable: `LOCKABLE_FIELDS` and this
dataclass's fields are asserted equal, so a field added to one and not the other
fails the build rather than becoming unlockable.

## Two things this module is not

**It is not a design system.** `dotmac-ui` owns the tokens, the ramp generation,
the accessibility clamping and the CSS. This module holds two hex values and
hands them over. There is no CSS column, no token map and no colour parser here
(ADR-0006 D8, U1).

**It is not a file store.** `dotmac-files` owns bytes (ADR-0022). The logo,
dark-logo and icon columns hold opaque references this module never dereferences
— it does not know whether a file exists, and it must not, because a brand
profile has to stay readable after a file is purged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# ── Errors ──────────────────────────────────────────────────────────────────


class BrandProfileError(ValueError):
    """Base: this brand profile cannot be written or resolved as asked."""


class ProfileRefusedError(BrandProfileError):
    """The profile is missing, or not in a state this command accepts."""


class UnknownLockedFieldError(BrandProfileError):
    """A lock names a field that does not participate in precedence.

    Fail loudly rather than ignoring it. A lock nothing honours is worse than no
    lock: an operator who pinned `legal_name` and saw the call succeed will
    believe it is pinned, and a reseller will change it anyway.
    """

    def __init__(self, fields: tuple[str, ...]) -> None:
        self.fields = fields
        super().__init__(
            f"cannot lock {', '.join(repr(field) for field in fields)}; only "
            "fields that participate in precedence can be pinned, and a lock "
            "nothing honours is worse than no lock"
        )


class HostBindingRefusedError(BrandProfileError):
    """A host cannot be bound as asked."""


# ── Inbound values ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProfileFields:
    """Everything a caller may set on a brand profile, on either plane.

    All optional: an upsert supplies what changed. `as_dict()` drops the
    unset ones, so `None` means "leave alone" rather than "clear" — which is the
    behaviour an admin form that posts one section needs, and the opposite of
    what a naive `setattr` loop over every attribute would do.
    """

    display_name: str | None = None
    product_name: str | None = None

    #: Legal identity — ADR-0006 § 3 keeps this separate from display brand, and
    #: `IDENTITY_FIELDS` is the set a higher layer typically pins.
    legal_name: str | None = None
    legal_address: dict[str, Any] | None = None

    #: Two colours. Validated by `dotmac_ui.BrandOverride` when the resolved
    #: brand is rendered, not here — a second parser would eventually accept
    #: something the first refuses.
    primary_hex: str | None = None
    accent_hex: str | None = None

    #: Opaque `dotmac-files` references. Never dereferenced (ADR-0022).
    logo_file_ref: str | None = None
    dark_logo_file_ref: str | None = None
    icon_file_ref: str | None = None

    support_email: str | None = None
    support_phone: str | None = None
    support_url: str | None = None

    #: Sender PRESENTATION. Whether the address may send is the Integrator's
    #: connector configuration; whether it should be contacted is
    #: `dotmac_kernel.consent`'s.
    sender_email: str | None = None
    sender_name: str | None = None

    #: An open registered vocabulary (ADR-0008), never an enum: a product names
    #: its own facets.
    enabled_surfaces: list[str] | None = None

    default_locale: str | None = None
    default_timezone: str | None = None

    #: Names a mobile build profile. Holds no build input, no certificate and no
    #: key — native brands are separate SIGNED BUILDS from shared source, and the
    #: signing material lives in the build pipeline's own secret store.
    mobile_build_profile_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The set fields only. `None` means "leave alone", never "clear"."""
        return {
            field: value for field, value in asdict(self).items() if value is not None
        }


__all__ = [
    "BrandProfileError",
    "HostBindingRefusedError",
    "ProfileFields",
    "ProfileRefusedError",
    "UnknownLockedFieldError",
]
