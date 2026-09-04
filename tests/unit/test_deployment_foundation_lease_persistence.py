"""Lease persistence: the terminal release lives BESIDE the lease, in one store.

## Why one store rather than two

A release written anywhere else is a second ledger, and the destroy gate would
then consult one record while the lease lived in another — which is exactly how a
swapped lease goes unnoticed. `release_path` is derived from `_lease_path` rather
than composed independently, so the two records cannot drift apart.

## The gate is only meaningful once a release can exist

`require_release_before_destruction` has refused unconditionally since it was
written, because nothing could produce a release. This is the other half: with a
writer, every refusal below becomes reachable, and the two that matter most are
the ones proving the axes COMPOSE.

## Two questions, two answers

"May this host be destroyed?" and "may a next lease take it as it stands?" are
different, and a host may legitimately answer yes to the first and no to the
second — `destroy_only` is exactly that. So reuse has its own gate, and it is
where `failed` and `outcome_unknown` cleanup refuse.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.lease import (
    HostLease,
    release_path,
    write_lease,
)
from dotmac_deployment_foundation.lease_release import (
    RELEASE_DUPLICATE,
    RELEASE_FOREIGN,
    RELEASE_MISSING,
    RELEASE_NOT_DESTROYABLE,
    CleanupDisposition,
    HostClosure,
    HostLeaseReleaseV1,
    ReleasingPrincipal,
    TerminalOutcome,
    TerminalRefusal,
    lease_digest,
    load_release,
    require_release_for_destruction,
    require_release_for_reuse,
    write_release,
)

TARGET = "10.120.120.54"
SLOT = "dotmacproxmox/102"
RUN = "33854964978"
REHEARSAL = "33860000001"
PRINCIPAL = "repo:michaelayoade/dotmac_starter_mt:ref:refs/heads/main"
FINGERPRINT = "sha256:" + "a" * 64
LIVE = datetime(2026, 9, 4, 3, 0, tzinfo=UTC)
AFTER = datetime(2026, 9, 4, 7, 0, tzinfo=UTC)


def _lease(**over) -> HostLease:
    kwargs = {
        "target": TARGET,
        "holder": "deployment-foundation-rehearsal",
        "authorization_run_id": RUN,
        "starts_at": "2026-09-04T00:00:00Z",
        "expires_at": "2026-09-04T06:00:00Z",
        "compose_project_prefix": "rehearsal-",
        "controller_identity_fingerprint": FINGERPRINT,
        "workload_principal": PRINCIPAL,
    }
    kwargs.update(over)
    return HostLease(**kwargs)


def _release(lease: HostLease | None = None, **over) -> HostLeaseReleaseV1:
    lease = lease or _lease()
    kwargs = {
        "lease_digest": lease_digest(lease),
        "vm_slot": SLOT,
        "vm_installation_id": "",
        "candidate_version": "0.4.0a1",
        "source_revision": "0" * 40,
        "authorization_run_id": RUN,
        "rehearsal_run_id": REHEARSAL,
        "outcome": TerminalOutcome(receipt_digest="sha256:" + "b" * 64),
        "released_at": "2026-09-04T05:00:00Z",
        "released_by": ReleasingPrincipal(
            kind="github_actions_workload",
            subject=lease.workload_principal,
            run_binding=REHEARSAL,
        ),
        "host_mutation_evidence": lease.controller_identity_fingerprint,
        "closure": HostClosure.REUSABLE,
        "cleanup": CleanupDisposition.PURGED,
    }
    kwargs.update(over)
    return HostLeaseReleaseV1(**kwargs)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    write_lease(_lease(), directory=tmp_path, now=LIVE)
    return tmp_path


def _destroy(store: Path, **kw):
    params = {
        "directory": store,
        "now": AFTER,
        "vm_slot": SLOT,
        "candidate_version": "0.4.0a1",
    }
    params.update(kw)
    return require_release_for_destruction(TARGET, **params)


# ── one store, beside the lease ────────────────────────────────────────────


def test_the_release_lives_beside_the_lease(store: Path) -> None:
    """Not a second ledger. Derived from the lease's own path so the two cannot
    come to live in different places."""
    written = write_release(_release(), target=TARGET, directory=store)
    assert written == release_path(TARGET, directory=store)
    assert written.parent == (store / f"{TARGET}.json").parent
    assert {p.name for p in store.iterdir()} == {
        f"{TARGET}.json",
        f"{TARGET}.release.json",
    }


def test_the_release_round_trips_through_the_store(store: Path) -> None:
    original = _release()
    write_release(original, target=TARGET, directory=store)
    assert load_release(TARGET, directory=store) == original


def test_no_release_is_None_and_not_an_error(store: Path) -> None:
    """A host with no release is the ordinary state of one being worked on, and
    the state a crash leaves. The gate decides what that means — "no release
    exists" and "this release does not authorize" must not share an answer."""
    assert load_release(TARGET, directory=store) is None


def test_a_second_release_of_one_lease_is_refused(store: Path) -> None:
    write_release(_release(), target=TARGET, directory=store)
    with pytest.raises(PreconditionFailed) as exc:
        write_release(_release(), target=TARGET, directory=store)
    assert exc.value.code == RELEASE_DUPLICATE


# ── the required refusals ──────────────────────────────────────────────────


def test_missing_release_refuses_destruction(store: Path) -> None:
    with pytest.raises(PreconditionFailed) as exc:
        _destroy(store)
    assert exc.value.code == RELEASE_MISSING


def test_expired_without_release_refuses_destruction(store: Path) -> None:
    """Expiry is not release. This is the shape a crash leaves, and the one case
    where nobody can be asked what happened."""
    with pytest.raises(PreconditionFailed) as exc:
        _destroy(store, now=AFTER)
    assert exc.value.code == RELEASE_MISSING


def test_a_swapped_lease_refuses(store: Path) -> None:
    """The release names the exact lease by CONTENT, so a release written for a
    different lease on this target does not discharge it."""
    other = _lease(compose_project_prefix="other-")
    write_release(_release(other), target=TARGET, directory=store)
    with pytest.raises(PreconditionFailed) as exc:
        _destroy(store)
    assert exc.value.code == RELEASE_FOREIGN


def test_a_wrong_workload_principal_refuses(store: Path) -> None:
    """`released_by` must equal the principal bound into THIS lease."""
    write_release(
        _release(
            released_by=ReleasingPrincipal(
                kind="github_actions_workload",
                subject="repo:michaelayoade/other:ref:refs/heads/main",
                run_binding=REHEARSAL,
            )
        ),
        target=TARGET,
        directory=store,
    )
    with pytest.raises(PreconditionFailed) as exc:
        _destroy(store)
    assert exc.value.code == RELEASE_FOREIGN


def test_a_wrong_candidate_refuses(store: Path) -> None:
    write_release(_release(candidate_version="0.3.0a6"), target=TARGET, directory=store)
    with pytest.raises(PreconditionFailed) as exc:
        _destroy(store)
    assert exc.value.code == RELEASE_FOREIGN


def test_a_wrong_vm_identity_refuses(store: Path) -> None:
    """An address can be re-pointed by the very restoration this authorises; the
    thing that gets wiped is a slot."""
    write_release(_release(), target=TARGET, directory=store)
    with pytest.raises(PreconditionFailed) as exc:
        _destroy(store, vm_slot="dotmacproxmox/107")
    assert exc.value.code == RELEASE_FOREIGN


def test_cleanup_unknown_can_never_be_reused(store: Path) -> None:
    """What the lease created is either still there or nobody can say it is not,
    and a next lease inheriting that host as clean is the same failure either
    way."""
    write_release(
        _release(
            cleanup=CleanupDisposition.OUTCOME_UNKNOWN,
            closure=HostClosure.DESTROY_ONLY,
        ),
        target=TARGET,
        directory=store,
    )
    with pytest.raises(PreconditionFailed) as exc:
        require_release_for_reuse(TARGET, directory=store, now=AFTER)
    assert exc.value.code == RELEASE_NOT_DESTROYABLE


def test_a_clean_release_permits_both(store: Path) -> None:
    """The positive control. Without it every refusal above could come from a
    gate that refuses everything."""
    write_release(_release(), target=TARGET, directory=store)
    assert _destroy(store)
    assert require_release_for_reuse(TARGET, directory=store, now=AFTER)


# ── V1 can neither authorize nor release ──────────────────────────────────


def test_a_v1_lease_cannot_be_loaded_so_it_can_neither_authorize_nor_release(
    tmp_path: Path,
) -> None:
    """It names no workload principal, so nothing it says can be bound to a
    releasing party. Refusing to LOAD is what makes both impossible rather than
    one of them checked."""
    document = _lease().as_document()
    document["schema"] = "HostLease.v1"
    document.pop("workload_principal")
    (tmp_path / f"{TARGET}.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SpecError):
        _destroy(tmp_path)
    with pytest.raises(SpecError):
        require_release_for_reuse(TARGET, directory=tmp_path, now=AFTER)


# ── the two axes COMPOSE, proved concretely ───────────────────────────────


def test_an_untouched_refusal_releases_only_when_cleanup_permits(
    store: Path,
) -> None:
    """`precondition_unfit` is unrestricted BY REFUSAL — nothing was attempted,
    the host is untouched. It may still be restricted BY CLEANUP.

    So the same refusal reaches `reusable` with clean cleanup and cannot reach it
    with unknown cleanup. That is the intersection doing work: neither axis alone
    produces both answers.
    """
    untouched = TerminalOutcome(refusal=TerminalRefusal.PRECONDITION_UNFIT)
    assert _release(
        outcome=untouched,
        cleanup=CleanupDisposition.NOT_ATTEMPTED,
        closure=HostClosure.REUSABLE,
    )
    with pytest.raises(SpecError):
        _release(
            outcome=untouched,
            cleanup=CleanupDisposition.OUTCOME_UNKNOWN,
            closure=HostClosure.REUSABLE,
        )


def test_a_partially_mutated_host_can_never_become_reusable(store: Path) -> None:
    """Site 150's case: the seeder placed a foreign rule, failed, and partially
    unwound, so the machine is in a state nobody has certified.

    Restricted by the REFUSAL axis whatever the cleanup says — including a
    cleanup that claims success, because a purge claimed after a failed
    provocation is a claim about a host whose state was already uncertified.
    """
    mutated = TerminalOutcome(refusal=TerminalRefusal.PROVOCATION_UNESTABLISHED)
    for cleanup in CleanupDisposition:
        with pytest.raises(SpecError):
            _release(outcome=mutated, cleanup=cleanup, closure=HostClosure.REUSABLE)
    # And the permitted closures remain reachable, so this is a restriction
    # rather than a wall.
    assert _release(
        outcome=mutated,
        cleanup=CleanupDisposition.PURGED,
        closure=HostClosure.DESTROY_ONLY,
    )
