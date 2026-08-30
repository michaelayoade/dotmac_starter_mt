"""A forward deploy and a silent downgrade must not be the same operation.

This facility had no concept of release ordering at all. Nothing compared the
revision about to be deployed with the revision already running, so a stale
checkout, a re-run of an old pipeline, or a descriptor restored from a backup
would deploy successfully, pass its health checks, and quietly undo whatever
shipped in between — with no failure anywhere for anyone to read.

## What is actually being tested

Not "ancestors are refused". That is the easy half, and a guard that only does
it is worthless, because the interesting cases are the ones where the
comparison could not be made:

- **UNKNOWN must refuse.** An unmeasured ancestry is not a forward deploy, it
  is no information. Treating it as permission is a check reporting success for
  having checked nothing — the same failure class as the dead IPv6 rules and
  the discarded docker-proxy PID.
- **UNRELATED must refuse.** No shared history means a force-push, a rewritten
  branch or a different repository. Arguably worse than a downgrade: the
  running system came from somewhere the descriptor cannot account for.

The permitted set is an allow-list, so `test_every_ordering_is_classified`
fails when a new enum member is added without a deliberate decision — a
deny-list would let a new value become permitted by being forgotten.
"""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation.ancestry import (
    PERMITTED_ORDERINGS,
    DowngradeOverride,
    Ordering,
    ReleaseOrdering,
    refuse_backwards_deploy,
)
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError

NEW = "a" * 40
OLD = "b" * 40
REASON = "restoring last week's build after the checkout regression"
DECISION = "approvals:decision:5521"


def _ordering(kind: Ordering, *, candidate: str = NEW, running: str = OLD):  # type: ignore[no-untyped-def]
    return ReleaseOrdering(
        candidate_revision=candidate,
        running_revision=running,
        ordering=kind,
        measured_by="git merge-base --is-ancestor",
    )


def _override(candidate: str = NEW, running: str = OLD) -> DowngradeOverride:
    return DowngradeOverride(
        candidate_revision=candidate,
        running_revision=running,
        reason=REASON,
        decision_ref=DECISION,
    )


# ── the permitted set ───────────────────────────────────────────────────────


def test_a_forward_deploy_is_permitted() -> None:
    """The positive control. Without it, refusing everything scores full marks."""
    refuse_backwards_deploy(_ordering(Ordering.DESCENDANT))


def test_a_redeploy_of_the_same_revision_is_permitted() -> None:
    refuse_backwards_deploy(_ordering(Ordering.SAME, candidate=NEW, running=NEW))


def test_a_first_deploy_is_permitted() -> None:
    """Nothing is running, so nothing can be undone."""
    refuse_backwards_deploy(
        ReleaseOrdering(
            candidate_revision=NEW,
            running_revision="",
            ordering=Ordering.NO_RUNNING_RELEASE,
        )
    )


def test_every_ordering_is_classified() -> None:
    """A new enum member must be a deliberate decision, not an omission.

    The allow-list means an unclassified value already refuses; this asserts
    somebody looked at it rather than that the default happened to be safe.
    """
    refusing = set(Ordering) - PERMITTED_ORDERINGS
    assert refusing == {Ordering.ANCESTOR, Ordering.UNRELATED, Ordering.UNKNOWN}


# ── the refusals that matter ────────────────────────────────────────────────


def test_a_backwards_deploy_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="not ahead of the running"):
        refuse_backwards_deploy(_ordering(Ordering.ANCESTOR))


def test_an_unmeasured_ordering_is_refused() -> None:
    """The case a naive implementation gets wrong."""
    with pytest.raises(PreconditionFailed, match="was not established"):
        refuse_backwards_deploy(_ordering(Ordering.UNKNOWN))


def test_an_unrelated_history_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="unrelated"):
        refuse_backwards_deploy(_ordering(Ordering.UNRELATED))


def test_the_unknown_refusal_names_how_it_was_measured() -> None:
    """A refusal a reader cannot act on sends them to the wrong system."""
    with pytest.raises(PreconditionFailed, match="no method recorded"):
        refuse_backwards_deploy(
            ReleaseOrdering(
                candidate_revision=NEW,
                running_revision=OLD,
                ordering=Ordering.UNKNOWN,
            )
        )


# ── the override ────────────────────────────────────────────────────────────


def test_an_override_permits_the_exact_downgrade_it_names() -> None:
    refuse_backwards_deploy(_ordering(Ordering.ANCESTOR), override=_override())


def test_an_override_for_another_revision_pair_is_refused() -> None:
    """An override excuses one downgrade, not downgrading in general."""
    with pytest.raises(PreconditionFailed, match="not for"):
        refuse_backwards_deploy(
            _ordering(Ordering.ANCESTOR),
            override=_override(candidate="c" * 40, running="d" * 40),
        )


def test_an_override_does_not_excuse_an_unmeasured_ordering() -> None:
    """UNKNOWN is not a downgrade — it is an absent measurement.

    Letting an override paper over it would turn "I could not check" into a
    thing you can wave through, which is strictly worse than a downgrade you
    at least had to look at.
    """
    with pytest.raises(PreconditionFailed, match="was not established"):
        refuse_backwards_deploy(_ordering(Ordering.UNKNOWN), override=_override())


def test_an_override_on_a_forward_deploy_is_refused() -> None:
    """Two readings disagree, and that is worth stopping for."""
    with pytest.raises(PreconditionFailed, match="needs none"):
        refuse_backwards_deploy(_ordering(Ordering.DESCENDANT), override=_override())


@pytest.mark.parametrize("reason", ["", "oops", "   ", "fix"])
def test_an_override_needs_a_real_reason(reason: str) -> None:
    with pytest.raises(SpecError, match="real sentence"):
        DowngradeOverride(
            candidate_revision=NEW,
            running_revision=OLD,
            reason=reason,
            decision_ref=DECISION,
        )


def test_an_override_needs_a_decision_reference() -> None:
    """Without one the operator is authorizing themselves."""
    with pytest.raises(SpecError, match="decision_ref"):
        DowngradeOverride(
            candidate_revision=NEW,
            running_revision=OLD,
            reason=REASON,
            decision_ref="",
        )


# ── the measurement must be self-consistent ─────────────────────────────────


def test_a_short_revision_is_refused() -> None:
    with pytest.raises(SpecError, match="40-hex"):
        _ordering(Ordering.DESCENDANT, candidate="abc1234")


def test_same_with_differing_revisions_is_refused() -> None:
    with pytest.raises(SpecError, match="says SAME but"):
        _ordering(Ordering.SAME, candidate=NEW, running=OLD)


def test_a_non_same_ordering_with_identical_revisions_is_refused() -> None:
    with pytest.raises(SpecError, match="that is SAME"):
        _ordering(Ordering.ANCESTOR, candidate=NEW, running=NEW)


def test_no_running_release_must_not_name_one() -> None:
    with pytest.raises(SpecError, match="cannot both be true"):
        ReleaseOrdering(
            candidate_revision=NEW,
            running_revision=OLD,
            ordering=Ordering.NO_RUNNING_RELEASE,
        )
