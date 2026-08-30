"""``ReleaseOrdering.v1`` — refusing to deploy backwards by accident.

This facility had **no concept of release ordering at all**. Nothing anywhere
compared the revision about to be deployed with the revision already running,
so a forward deploy and a silent downgrade were the same operation, executed by
the same command, producing the same output.

That is not a hypothetical. A stale checkout, a re-run of an old pipeline, a
descriptor restored from a backup, a `git checkout` that did not move: each
produces a perfectly valid descriptor naming a real image and a real revision,
and the only thing wrong with it is that it is *older than what is there*. The
deployment succeeds, the health checks pass, and the fix that shipped an hour
ago is gone — with no failure anywhere for anyone to read.

## Ancestry is an OBSERVATION, not something this module computes

There is no `git` call here, and there must not be. This facility declares zero
runtime dependencies (ADR-0070) and does not reach the network or the
filesystem to make a decision. Ancestry is supplied as a typed value, exactly
like `ProbeResult`, `ProbeVantage` and `AuthorizationReceipt` — the caller runs
`git merge-base --is-ancestor` and reports what it saw.

What lives here is the part that must be reviewable: **what the measurement
entitles you to do.**

## Unknown and unrelated both refuse, and that is the whole design

The easy version of this guard compares two revisions and refuses when the
candidate is an ancestor. It is worthless, because the interesting cases are
the ones where the comparison could not be made:

- ``UNKNOWN`` — nobody measured, or the measurement failed. An unmeasured
  ancestry is not a forward deploy; it is no information. Treating it as
  permission is how a guard reports success for having checked nothing, which
  is the same failure class as the dead IPv6 rules and the discarded
  docker-proxy PID.
- ``UNRELATED`` — the two revisions share no history. That is a rewritten
  branch, a different repository, or a force-push, and none of them is provably
  forward. It is arguably more alarming than a plain downgrade, because it means
  the running system came from somewhere this descriptor cannot account for.

So the permissive set is small and explicit: ``DESCENDANT`` (strictly forward)
and ``SAME`` (a redeploy, which changes nothing about ordering). Everything
else, including anything added to the enum later, refuses by default — the
check is written as a membership test against the allowed set rather than as a
list of things to reject, so a future ordering value cannot become permitted by
being forgotten about.

## Why the override is here rather than absent

A guard with no escape hatch does not prevent the emergency; it relocates it.
The first genuine "we must go back to last week's build right now" gets handled
by someone bypassing the tool entirely, at speed, under pressure — and the tool
learns nothing and records nothing.

:class:`DowngradeOverride` is therefore a first-class, typed, **single-use**
value: it names the exact revision pair it excuses, the human reason, and the
authorizing decision. It cannot be a boolean or an environment variable,
because those are reusable and unattributable — the two properties that turn an
escape hatch into the normal path. It is consumed on use, so a second downgrade
needs a second decision.
"""

from __future__ import annotations

import dataclasses
import re
from enum import Enum
from typing import Final

from .errors import PreconditionFailed, SpecError

__all__ = [
    "PERMITTED_ORDERINGS",
    "DowngradeOverride",
    "Ordering",
    "ReleaseOrdering",
    "refuse_backwards_deploy",
]

_REVISION = re.compile(r"^[0-9a-f]{40}$")


class Ordering(str, Enum):
    """How the candidate revision relates to the one already running."""

    #: The candidate has the running revision in its history. Forward.
    DESCENDANT = "descendant"
    #: The same revision. A redeploy; ordering is not violated.
    SAME = "same"
    #: The RUNNING revision has the candidate in its history. Backwards.
    ANCESTOR = "ancestor"
    #: No shared history — rewritten branch, force-push, other repository.
    UNRELATED = "unrelated"
    #: Not measured, or the measurement failed. Not a synonym for "fine".
    UNKNOWN = "unknown"
    #: Nothing is deployed yet, so there is nothing to be older than.
    NO_RUNNING_RELEASE = "no_running_release"


#: Written as an allow-list, so an ordering added later refuses until somebody
#: deliberately permits it. A deny-list would let a new value default to
#: permitted by being forgotten.
PERMITTED_ORDERINGS: Final[frozenset[Ordering]] = frozenset(
    {Ordering.DESCENDANT, Ordering.SAME, Ordering.NO_RUNNING_RELEASE}
)


def _revision(value: str, *, field: str) -> str:
    text = str(value).strip().lower()
    if not _REVISION.match(text):
        raise SpecError(
            f"{field} must be a full 40-hex commit, got {value!r}. A short sha "
            "is ambiguous, and an ordering decision made against an ambiguous "
            "revision cannot be re-checked later"
        )
    return text


@dataclasses.dataclass(frozen=True, slots=True)
class DowngradeOverride:
    """A single, attributable excuse for one specific backwards deploy.

    Deliberately not a boolean and not an environment variable. Both are
    reusable and neither records who decided or why, which is what turns an
    escape hatch into the normal path.
    """

    #: The exact pair this excuses. An override issued for one downgrade must
    #: not silently cover a different one that happens later the same day.
    candidate_revision: str
    running_revision: str
    #: Free text, required. A downgrade with no stated reason is the thing this
    #: type exists to stop being routine.
    reason: str
    #: The decision this rests on, in the same spirit as
    #: `AuthorizationReceipt.decision_ref` — a human authorized it somewhere.
    decision_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_revision",
            _revision(self.candidate_revision, field="DowngradeOverride.candidate"),
        )
        object.__setattr__(
            self,
            "running_revision",
            _revision(self.running_revision, field="DowngradeOverride.running"),
        )
        if len(str(self.reason).strip()) < 12:
            raise SpecError(
                "DowngradeOverride.reason must be a real sentence. A downgrade "
                "that nobody had to explain is a downgrade nobody will "
                "remember authorizing"
            )
        if not str(self.decision_ref).strip():
            raise SpecError(
                "DowngradeOverride.decision_ref is empty. An override with no "
                "decision behind it is the operator authorizing themselves"
            )

    def covers(self, ordering: ReleaseOrdering) -> bool:
        return (
            self.candidate_revision == ordering.candidate_revision
            and self.running_revision == ordering.running_revision
        )


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseOrdering:
    """A measured relationship between the candidate and the running release."""

    candidate_revision: str
    #: Empty only when `ordering` is `NO_RUNNING_RELEASE`.
    running_revision: str
    ordering: Ordering
    #: How the caller established this — `git merge-base --is-ancestor`, a
    #: registry query, whatever. Recorded so a reader can judge the claim
    #: rather than take it.
    measured_by: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_revision",
            _revision(self.candidate_revision, field="ReleaseOrdering.candidate"),
        )
        if self.ordering is Ordering.NO_RUNNING_RELEASE:
            if str(self.running_revision).strip():
                raise SpecError(
                    "ReleaseOrdering says there is no running release but "
                    "names one. Those cannot both be true"
                )
            return
        object.__setattr__(
            self,
            "running_revision",
            _revision(self.running_revision, field="ReleaseOrdering.running"),
        )
        if (
            self.ordering is Ordering.SAME
            and self.candidate_revision != self.running_revision
        ):
            raise SpecError(
                "ReleaseOrdering says SAME but the two revisions differ. A "
                "self-inconsistent measurement is not evidence"
            )
        if (
            self.ordering is not Ordering.SAME
            and self.candidate_revision == self.running_revision
        ):
            raise SpecError(
                f"ReleaseOrdering says {self.ordering.value} but both "
                "revisions are identical; that is SAME"
            )


def refuse_backwards_deploy(
    ordering: ReleaseOrdering, *, override: DowngradeOverride | None = None
) -> None:
    """Permit a forward deploy; refuse everything that is not provably forward.

    Returns ``None`` and raises on refusal, rather than returning a boolean, so
    a caller cannot proceed by forgetting to look at the result.
    """
    if ordering.ordering in PERMITTED_ORDERINGS:
        if override is not None:
            raise PreconditionFailed(
                f"a DowngradeOverride was supplied for a "
                f"{ordering.ordering.value} deploy, which needs none. Either "
                "the ordering measurement or the override is about a different "
                "deployment, and both readings are worth stopping for"
            )
        return

    if ordering.ordering is Ordering.UNKNOWN:
        raise PreconditionFailed(
            f"the ordering between {ordering.candidate_revision} and the "
            f"running {ordering.running_revision} was not established "
            f"({ordering.measured_by or 'no method recorded'}). An unmeasured "
            "ancestry is not a forward deploy — it is no information, and "
            "treating it as permission is how a check reports success for "
            "having checked nothing"
        )

    if override is None:
        raise PreconditionFailed(
            f"refusing a {ordering.ordering.value} deploy: candidate "
            f"{ordering.candidate_revision} is not ahead of the running "
            f"{ordering.running_revision}. Deploying it would silently undo "
            "whatever shipped in between, with no failure for anyone to read. "
            "If this is deliberate, supply a DowngradeOverride naming this "
            "exact revision pair, a reason, and the decision behind it"
        )
    if not override.covers(ordering):
        raise PreconditionFailed(
            f"the override is for {override.candidate_revision} over "
            f"{override.running_revision}, not for "
            f"{ordering.candidate_revision} over {ordering.running_revision}. "
            "An override excuses one downgrade, not downgrading in general"
        )
