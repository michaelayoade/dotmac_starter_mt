"""Pure site and release-readiness lifecycle decisions."""

from __future__ import annotations

import enum


class TransitionError(ValueError):
    """A site-owned lifecycle transition is inadmissible."""


class SiteState(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class SiteRevisionState(enum.StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RETIRED = "retired"


_SITE_TRANSITIONS: dict[SiteState, frozenset[SiteState]] = {
    SiteState.ACTIVE: frozenset({SiteState.ARCHIVED}),
    SiteState.ARCHIVED: frozenset(),
}

_REVISION_TRANSITIONS: dict[SiteRevisionState, frozenset[SiteRevisionState]] = {
    SiteRevisionState.DRAFT: frozenset({SiteRevisionState.READY}),
    SiteRevisionState.READY: frozenset({SiteRevisionState.RETIRED}),
    SiteRevisionState.RETIRED: frozenset(),
}


def _check_transition(
    *,
    subject: str,
    current: enum.StrEnum,
    desired: enum.StrEnum,
    allowed: frozenset[enum.StrEnum],
) -> None:
    if current == desired:
        return
    if desired not in allowed:
        choices = ", ".join(value.value for value in sorted(allowed))
        raise TransitionError(
            f"{subject} in {current.value!r} cannot move to {desired.value!r}; "
            f"allowed: {choices or 'no later state'}"
        )


def check_site_transition(current: SiteState, desired: SiteState) -> None:
    _check_transition(
        subject="site",
        current=current,
        desired=desired,
        allowed=_SITE_TRANSITIONS[current],
    )


def check_revision_transition(
    current: SiteRevisionState, desired: SiteRevisionState
) -> None:
    _check_transition(
        subject="site revision",
        current=current,
        desired=desired,
        allowed=_REVISION_TRANSITIONS[current],
    )


__all__ = [
    "SiteRevisionState",
    "SiteState",
    "TransitionError",
    "check_revision_transition",
    "check_site_transition",
]
