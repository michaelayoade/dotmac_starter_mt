"""The authenticated principal a facet already established, projected for reuse.

A composed browser facet authenticates once, in the surface dependency the
assembly bound to its authentication profile. Until this module existed the
result was *used and discarded*: the tenant surface dependency resolved a
``Party``, spent it on admission and navigation, and let it fall out of scope,
while the non-tenant dependency bound the principal to ``_principal`` purely to
force the dependency to run. A module-owned browser command that needed to know
who was acting therefore had exactly one option — read the cookie and
authenticate again.

That option is the defect. A second read of the credential is a second
authentication owner wearing a helper's name: it can disagree with the facet
about who the actor is, it can succeed where the facet refused, and it
multiplies the places a token-handling fix must land. Six module surfaces each
inventing their own version of it is the parallel-authority shape this
architecture exists to remove.

So this module is a PROJECTION, never an authenticator. It reads request-scoped
state the facet already wrote and it can do nothing else:

* It never reads a cookie, header, token, session store or database. There is
  no credential-parsing code path here to fall back to, which is what makes the
  "no second authenticator" claim structural rather than a promise.
* An absent principal REFUSES. It does not authenticate one, and it does not
  return ``None`` from the required accessor — a route that expected an actor
  and silently received nothing is how an unattributed mutation gets written.
* A principal from the wrong security plane REFUSES. A tenant-plane identity
  reaching a platform surface is not a weaker principal to be tolerated; the
  two planes have different isolation rules, so accepting one where the other
  is declared is a privilege confusion, not a degradation.

The plane check is deliberately a *declaration* by the caller rather than an
inference from the principal's type. Inferring it would make the guard agree
with whatever it was handed, which is the failure mode where a check exists and
proves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from dotmac_kernel.web_surfaces import BrowserSecurityPlane

#: Attribute on ``request.state`` holding the projection. Private by name: the
#: supported way in is the accessors below, so the shape can change without
#: every module surface reaching for the attribute directly.
_STATE_ATTR: Final[str] = "facet_principal"


class FacetPrincipalError(RuntimeError):
    """The request carries no principal usable for the declared plane."""


class FacetPrincipalUnavailableError(FacetPrincipalError):
    """No facet authenticated a principal for this request.

    This is a COMPOSITION fault, not an authorization outcome: it means the
    route ran outside an authenticating facet surface, or under a public
    profile. It is deliberately not an HTTP 401 — answering "log in" would
    imply the credential was the problem, when the real defect is that the
    route asked for an actor the surface never promised to supply.
    """


class FacetPrincipalPlaneMismatchError(FacetPrincipalError):
    """The authenticated principal belongs to a different security plane."""


@dataclass(frozen=True, slots=True)
class FacetPrincipal:
    """Who the facet authenticated, and on which plane it authenticated them.

    ``subject`` is the principal object the assembly's authentication provider
    returned (a ``Party`` on the tenant plane, a platform admin on the platform
    plane). It is kept as the provider returned it so a caller that needs the
    full record is not forced back to the database; ``subject_id`` is carried
    separately because actor attribution almost always wants only the id, and a
    command that records an id should not have to know the subject's type.
    """

    facet: str
    security_plane: BrowserSecurityPlane
    subject_id: UUID
    subject: Any
    tenant_id: UUID | None = None

    def __post_init__(self) -> None:
        plane = BrowserSecurityPlane(self.security_plane)
        if plane is BrowserSecurityPlane.NONE:
            raise FacetPrincipalError(
                "a facet principal cannot be recorded on the public "
                "(`none`) security plane; a public profile authenticates nobody"
            )
        object.__setattr__(self, "security_plane", plane)
        if self.tenant_id is None and plane is BrowserSecurityPlane.TENANT:
            raise FacetPrincipalError(
                f"tenant-plane principal for facet {self.facet!r} carries no "
                "tenant id; a tenant actor is only meaningful inside a tenant"
            )


def record_facet_principal(request: Any, principal: FacetPrincipal) -> None:
    """Publish the principal the facet just authenticated.

    Called by the kernel's own surface composition at the point authentication
    has ALREADY happened. It is not a public extension point: a module that
    calls this is claiming to have authenticated somebody, which is precisely
    the authority this module refuses to distribute.
    """

    request.state.facet_principal = principal


def facet_principal(request: Any) -> FacetPrincipal | None:
    """The authenticated principal, or ``None`` if the facet established none.

    For the caller that legitimately renders differently for an anonymous
    visitor. A caller that needs an actor must use ``require_facet_principal``
    so the absent case cannot be forgotten.
    """

    value = getattr(request.state, _STATE_ATTR, None)
    return value if isinstance(value, FacetPrincipal) else None


def require_facet_principal(
    request: Any, *, plane: BrowserSecurityPlane
) -> FacetPrincipal:
    """The authenticated principal, refusing absence and the wrong plane.

    ``plane`` is what the CALLER declares it is written for. Passing the plane
    the caller expects — rather than reading whichever plane happens to be
    present — is what lets this refuse a tenant identity on a platform surface
    instead of quietly attributing the action to it.
    """

    expected = BrowserSecurityPlane(plane)
    if expected is BrowserSecurityPlane.NONE:
        raise FacetPrincipalError(
            "cannot require a principal on the public (`none`) security plane"
        )
    value = facet_principal(request)
    if value is None:
        raise FacetPrincipalUnavailableError(
            "this request carries no facet-authenticated principal; the route "
            "is composed outside an authenticating facet surface, or under a "
            "public authentication profile"
        )
    if value.security_plane is not expected:
        raise FacetPrincipalPlaneMismatchError(
            f"facet {value.facet!r} authenticated a "
            f"{value.security_plane.value!r}-plane principal, but this caller "
            f"declares the {expected.value!r} plane; refusing to attribute the "
            "action across a security plane boundary"
        )
    return value


__all__ = [
    "FacetPrincipal",
    "FacetPrincipalError",
    "FacetPrincipalPlaneMismatchError",
    "FacetPrincipalUnavailableError",
    "facet_principal",
    "record_facet_principal",
    "require_facet_principal",
]
