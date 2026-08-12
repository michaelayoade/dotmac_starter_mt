"""`ApplicationDescriptor` — what a connected application says about itself.

**This module is the contract's permanent owner** (ADR-0021 §4). It is not
module code awaiting kernel promotion: a descriptor is meaningless outside a
portfolio of connected applications, so it belongs with the domain that defines
it. Only the generic signed-envelope mechanism in that ADR's fourth row is a
kernel-promotion candidate.

A descriptor is what a target application publishes about one of its instances:
where its admin surface is, which audience its API accepts, which tenant inside
it corresponds to the Workspace tenant. The Workspace stores a copy on the
binding and compares digests to notice drift.

## What a descriptor is NOT

It is not an authorization document. It says where an application instance is
and which tenant inside it corresponds to the Workspace tenant. It says nothing
about who may enter — per ADR-0021 §3 directory visibility is not authorization,
and the target application remains the only writer of its own effective role
grants.

## No role catalogue in 0.1.0a1

An earlier draft carried `ApplicationRole`, a `delegable` flag, a
`delegable_role_codes` set and a separate `role_catalogue_digest`, so that a
future `AccessGrantSet` could bind itself to the exact catalogue it was authored
against.

All of it is deferred, because **nothing consumes it**. The access module that
would is deferred by ADR-0021 §5, and the Workspace launcher never reads a role.
Shipping it now would be a published contract — with a database column and a
digest other planes could start depending on — designed against zero consumers,
which is the failure ADR-0008 records against declarations with no reader and
ADR-0017 records against facilities with no adopter.

It returns with the access slice, designed against that slice's real needs
rather than guessed ahead of them.

## The digest

One, over the whole descriptor. It tells the Workspace that *anything* about the
application changed — a moved admin URL, a new audience. It is deterministic
over the descriptor's content: same content, same digest, on any machine and any
Python version, with field order fixed by this module rather than by dataclass
declaration order or dict iteration.

A digest is content-addressing, not a signature. It proves that two parties are
looking at the same descriptor; it proves nothing about who wrote it. Signed
delivery is the access slice's problem, and is deferred (ADR-0021 §6).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlparse

#: Prefixed onto the canonical payload before hashing. Domain separation is kept
#: even with a single digest, so that a later second digest kind (the deferred
#: role catalogue) cannot collide with this one, and so a digest of these bytes
#: computed for any other purpose is not this digest. The trailing NUL cannot
#: occur in the label, so no payload can complete a different prefix. Changing
#: the value changes every digest and is a compatibility break.
_DESCRIPTOR_DOMAIN = b"dotmac-application-descriptor/1\x00"

#: Schemes an admin URL may use. `http` is permitted because a development or
#: air-gapped deployment legitimately runs without TLS; a deployment profile
#: that requires `https` enforces it at ITS layer, where the environment is
#: known. Rejecting it here would make the contract untestable offline.
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


class DescriptorError(ValueError):
    """A descriptor is structurally invalid. Fails closed at construction: an
    invalid descriptor must never reach a binding, because the binding's digest
    would then attest to nonsense."""


@dataclass(frozen=True, slots=True)
class ApplicationDescriptor:
    """What one instance of a connected application publishes about itself.

    Every field is a fact about the APPLICATION. Nothing here is a fact about a
    person, and nothing here is a permission.
    """

    #: The logical application, e.g. `"sub"`. Stable across instances — two
    #: deployments of Sub share this and differ in `instance_ref`.
    application_code: str
    #: Which deployment/instance this descriptor describes. Opaque to the
    #: directory; meaningful to whoever operates the fleet.
    instance_ref: str
    #: The tenant identifier INSIDE the target application that corresponds to
    #: the Workspace tenant holding the binding. The directory never assumes the
    #: two identifiers are equal — they are issued by different planes.
    local_tenant_ref: str
    #: Absolute URL of the application's admin surface. What a launcher tile
    #: links to; following it grants nothing (ADR-0021 §3).
    admin_url: str
    #: The audience value the application's API expects in a token presented to
    #: it. Carried for the access slice; unused by the directory itself.
    api_audience: str
    #: Monotonic within an (application_code, instance_ref). Lets a receiver
    #: reject an older descriptor without comparing digests it may not hold.
    descriptor_version: int

    def __post_init__(self) -> None:
        for name in (
            "application_code",
            "instance_ref",
            "local_tenant_ref",
            "api_audience",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DescriptorError(f"descriptor requires a non-empty `{name}`")

        if not isinstance(self.descriptor_version, int) or isinstance(
            self.descriptor_version, bool
        ):
            raise DescriptorError("`descriptor_version` must be an int")
        if self.descriptor_version < 1:
            raise DescriptorError(
                f"`descriptor_version` must be >= 1, got {self.descriptor_version}"
            )

        parsed = urlparse(self.admin_url)
        if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
            raise DescriptorError(
                f"`admin_url` must be an absolute http(s) URL, got "
                f"{self.admin_url!r}"
            )

    # ── Derived content address ─────────────────────────────────────────────

    @property
    def digest(self) -> str:
        """`sha256:<hex>` over the whole descriptor.

        The field list is written out explicitly rather than derived from the
        dataclass, so that adding a field is a deliberate decision about whether
        it belongs in the identity — a digest that silently changes shape when
        someone appends an attribute is a digest nobody can compare across
        versions.
        """
        payload = json.dumps(
            [
                self.application_code,
                self.instance_ref,
                self.local_tenant_ref,
                self.admin_url,
                self.api_audience,
                self.descriptor_version,
            ],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return _digest(_DESCRIPTOR_DOMAIN + payload)


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "ApplicationDescriptor",
    "DescriptorError",
]
