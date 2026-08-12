"""`ApplicationDescriptor` — what a connected application says about itself.

**This module is the contract's permanent owner** (ADR-0021 §4). It is not
module code awaiting kernel promotion: a descriptor is meaningless outside a
portfolio of connected applications, so it belongs with the domain that defines
it. Only the generic signed-envelope mechanism in that ADR's fourth row is a
kernel-promotion candidate.

A descriptor is what a target application publishes about one of its instances:
where its admin surface is, which audience its API accepts, which tenant inside
it corresponds to the Workspace tenant, and what its role catalogue currently
looks like. The Workspace stores a copy on the binding and compares digests to
notice drift.

## What a descriptor is NOT

It is not an authorization document, and `ApplicationRole` is not a grant.
The role catalogue is the application saying *these roles exist and these of
them may be delegated by a tenant administrator* — a menu, not an order. The
directory never records that anyone holds one of these roles; per ADR-0021 §3
directory visibility is not authorization, and the target application remains
the only writer of its own effective role grants.

`delegable` is the application's own statement about which of its roles a
Workspace administrator may ever request. It exists so that the eventual access
slice has an application-declared allowlist to validate against, rather than the
Workspace inventing one. A role absent from the catalogue, or present but not
delegable, is not requestable — and the decision belongs to the application
because the application is the one that has to live with it.

## Digests

Two, because they answer different questions and a single one would conflate
them:

``role_catalogue_digest``
    Covers the role catalogue alone. This is what a future ``AccessGrantSet``
    binds itself to, so that an allocation authored against one catalogue cannot
    be applied silently against a different one.

``digest``
    Covers the whole descriptor, including the role-catalogue digest. This is
    what the binding stores, and what tells the Workspace that *anything* about
    the application changed — a moved admin URL, a new audience — not only its
    roles.

Both are deterministic over the descriptor's content: same content, same digest,
on any machine and any Python version. Field order is fixed by this module, not
by dataclass declaration order or dict iteration, and roles are sorted by code
before hashing so that two descriptors listing the same roles in different
orders agree.

A digest is content-addressing, not a signature. It proves that two parties are
looking at the same descriptor; it proves nothing about who wrote it. Signed
delivery is the access slice's problem, and is deferred (ADR-0021 §6).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from urllib.parse import urlparse

#: Prefixed onto every canonical payload before hashing, so a digest computed
#: over a role catalogue can never equal one computed over a whole descriptor
#: even if their canonical bytes coincided. The trailing NUL cannot occur in the
#: label, so no payload can complete a different prefix. Changing either value
#: changes every digest and is a compatibility break.
_ROLE_CATALOGUE_DOMAIN = b"dotmac-application-role-catalogue/1\x00"
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
class ApplicationRole:
    """One role a target application declares in its catalogue.

    `code` is the application's own role code, uninterpreted here — the
    directory carries it, never resolves it. `label` is for the Workspace's UI.
    `delegable` is the application's statement about whether a tenant
    administrator may ever request this role for someone; it defaults to False
    so that a catalogue which forgets to think about delegation delegates
    nothing.
    """

    code: str
    label: str
    delegable: bool = False

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise DescriptorError("application role requires a non-empty `code`")
        if not self.label or not self.label.strip():
            raise DescriptorError(
                f"application role {self.code!r} requires a non-empty `label`"
            )


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
    #: The roles the application declares. Empty is legal and means "this
    #: application publishes no delegable roles yet" — not "all roles allowed".
    roles: tuple[ApplicationRole, ...] = field(default_factory=tuple)

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

        if not isinstance(self.roles, tuple):
            raise DescriptorError("`roles` must be a tuple (descriptors are frozen)")
        seen: set[str] = set()
        for role in self.roles:
            if role.code in seen:
                raise DescriptorError(
                    f"duplicate role code in catalogue: {role.code!r}"
                )
            seen.add(role.code)

    # ── Derived content addresses ───────────────────────────────────────────

    @property
    def delegable_role_codes(self) -> frozenset[str]:
        """The codes a tenant administrator may ever request.

        The access slice validates a requested role against this set. It is a
        frozenset rather than a tuple because membership is the only question
        anyone asks of it, and because an ordered type invites someone to treat
        position as precedence.
        """
        return frozenset(role.code for role in self.roles if role.delegable)

    @property
    def role_catalogue_digest(self) -> str:
        """`sha256:<hex>` over the role catalogue alone, order-independent."""
        payload = json.dumps(
            [
                [role.code, role.label, role.delegable]
                for role in sorted(self.roles, key=lambda r: r.code)
            ],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return _digest(_ROLE_CATALOGUE_DOMAIN + payload)

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
                self.role_catalogue_digest,
            ],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return _digest(_DESCRIPTOR_DOMAIN + payload)


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "ApplicationDescriptor",
    "ApplicationRole",
    "DescriptorError",
]
