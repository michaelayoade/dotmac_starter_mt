#!/usr/bin/env python3
"""Lane 3, driven by the CONTROLLER — snapshot, apply, observe, probe, roll back.

Until now Lane 3 was a fixture and a prose table. `scripts/exposure-rehearsal/`
held a descriptor and some recorded bytes that no test and no workflow ever
consumed, and the sixteen gate items lived in a hand-maintained markdown table.
That is not evidence, and its own header proved it: on 2026-08-29 it read
"14 of 16 CLOSED" while the rows beneath recorded four `partial` and one `n/a`.

This runner is the fix. It ORIGINATES every host MUTATION through the library —
`ExposureTransaction` over `ComposeHostExposureEffects` — rather than shelling
out beside it. That distinction is the whole point of the lane: a human running
the same eight commands proves the operator can do it, not that the code can.

## MUTATION, not "every action" — and the narrowing is structural

The invariant said "every action" until 2026-09-04. It was narrowed because it
could not be satisfied as written, and a rule that cannot be satisfied is not
upheld by leaving it written.

The reason a change must originate through the library is that a change to a
host has to be authorized, recorded and rollback-able. **An observation changes
nothing, so it needs none of those properties** — and the observation this lane
depends on most cannot be taken through the library at all. The far-end source
address is what the TARGET saw of a connection from the vantage, and that is
definitionally not the near end's to make: a vantage cannot certify where it
egresses from. It comes from a different host, structurally outside the
transaction's reach.

So: every mutation goes through the library, observation need not, and the split
is enforceable rather than stated — the collector path does not mutate. The
seeded foreign rules and the inert-chain provocation ARE mutations, which is
exactly why they are named acts in `lane3_provocation` on the transaction side
rather than something a collector does on the way past. If those two blurred,
this narrowing would become the hole its critics would expect.

## Every input is required, and the run refuses without it

    --foundation-revision   the exact protected-main commit under test
    --foundation-artifact   digest read from the candidate receipt after the
                            downloaded wheel was verified
    --authorization-run     Platform CP authorization run id
    --authorization-doc     the signed authorization document
    --controller-identity   fingerprint of the dedicated controller key
    --target                the leased host
    --probe-evidence        the external vantage's measurements
    --descriptor            the exact rehearsal fixture

There is no default for any of them and no `--skip`. A rehearsal missing one of
these is not a partial rehearsal, it is a different activity — and the receipt
this emits is only meaningful because none of its bindings can be absent.

## What it CANNOT do, deliberately

It cannot grant its own lease (`lease.load_lease` refuses a record that names no
authorization run), it cannot mint an authorization (Foundation must never do
that — `provenance` and `rehearsal` both refuse), and it cannot mark an item
`executed_passed` that it did not execute: every status is set from a measured
outcome in the phase that produced it, and `build_receipt` refuses a receipt
missing any of the sixteen.

## It CLOSES the lease, and that is the other half of the contract

`load_lease` has always refused to BEGIN without a lease record. Nothing wrote
the END, so the only evidence a shared host was finished with was a process
being gone and a timestamp having passed — two inferences, neither a record.
This runner writes `HostLeaseRelease.v1` on every terminal outcome it can name:
a receipt, or one of the closed vocabulary's refusal members.

Five rules shape that write, and each of them is a way a host could otherwise be
wiped on an inference:

* **absence means HELD.** The write happens on a typed terminal outcome and
  nothing else — no `except Exception` anywhere near it. A crash leaves no
  record, and `expired_held` is the standing that produces.
* **a cleanup failure never rewrites a verdict.** `disarm_apply_failure` and
  `withdraw_foreign_rules` run after the receipt is fixed and discard their
  results by design. They are watched through the runner they already call, so
  each act's outcome reaches `cleanup` and the host's closure instead.
* **`released_by` is the authenticated workload**, read from the identity token
  the Actions runtime mints — never the CLI, never a person, never the
  controller key fingerprint, which is a separate fact in its own field —
  `controller_identity_fingerprint`, the same name and the same type the lease
  already uses for it. If it cannot be proven, nothing is written and the lease
  stays held.
* **a refusal under the lease is named, not dropped.** A `StepFailed` from a
  compose apply on the host used to have no member in the closed vocabulary at
  all, so a failed apply left the host mutated with no record and no closure —
  reachable on any run where the apply fails. `classify_refusal` answers on TWO
  facts, because there are three cases and one boolean has two values: with no
  exact lease in hand nothing is written and an expired lease stays
  `expired_held`; with the lease in hand a refusal this lane proved before host
  contact is `precondition_unfit`; and with the lease in hand any other refusal
  is `host_state_uncertified` EVEN IF nothing was mutated, because owning the
  host and being unable to say what state it is in is not the same claim as
  having attempted nothing.
* **create-only, atomically — and owned by the package.** A second release of
  one lease is a replay or two runs each believing they finished the same work,
  and neither overwrites the first. This runner does NOT publish: `lease.py`
  owns the store, so the record goes in through `lease_release.write_release`,
  whose `os.link` publish makes creating the name and failing on a taken name
  one syscall. A writer here would be a second answer to one question, and the
  destroy gate would read whichever one happened to run.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import dataclasses
import functools
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Final

# This file's OWN directory, so `lane3_provocation` resolves however this module
# was loaded. Running `python scripts/exposure_rehearsal_runner.py` puts
# `scripts/` on `sys.path[0]` for free; loading it through `importlib` from a
# path — which `tests/unit/test_lane3_proxy_recreation_gate.py` does, to reach
# `judge_proxy_recreation` without a host — does not, and the sibling import
# then fails at collection time rather than at run time.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import lane3_inside_vantage as inside_vantage
from dotmac_deployment_foundation.controller_identity import (
    ControllerSshFingerprintV1,
)
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.engine.run import CommandResult
from dotmac_deployment_foundation.errors import (
    DeploymentFoundationError,
    PreconditionFailed,
    SpecError,
)
from dotmac_deployment_foundation.exposure import (
    ExposureTransaction,
    ObservedProxy,
    ownership_comment,
    refuse_non_recreating_apply,
)
from dotmac_deployment_foundation.lease import HostLease, load_lease
from dotmac_deployment_foundation.lease_release import (
    CleanupDisposition,
    HostClosure,
    HostLeaseReleaseV1,
    ReleasingPrincipal,
    TerminalOutcome,
    TerminalRefusal,
    lease_digest,
    write_release,
)
from dotmac_deployment_foundation.policy import build_firewall_plan
from dotmac_deployment_foundation.providers.exposure_host import (
    ComposeHostExposureEffects,
)
from dotmac_deployment_foundation.rehearsal import (
    RequirementResult,
    RequirementStatus,
    build_receipt,
    render_status_document,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from dotmac_deployment_foundation.vantage import (
    VantageQualification,
    qualify_vantage,
)
from lane3_provocation import (
    SeededRule,
    disarm_apply_failure,
    inside_source_set,
    observed_foreign,
    private_port,
    provoke_apply_failure,
    seed_foreign_rules,
    withdraw_foreign_rules,
)

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2

PASSED = RequirementStatus.EXECUTED_PASSED
FAILED = RequirementStatus.EXECUTED_FAILED
BLOCKED = RequirementStatus.BLOCKED


# ── the terminal refusals, as TYPES and POSITIONS rather than line numbers ──
#
# `HostLeaseRelease.v1` needs to know WHICH refusal ended a run, and the
# vocabulary that answers it (`TerminalRefusal`) is closed and owned by
# Foundation. The mapping from this lane's refusals onto that vocabulary has to
# survive the file being edited, so it is carried by the exception CLASS and not
# by a site's position: three independent people derived the site list by
# grepping and got three different wrong answers, and a line number is exactly
# the thing a later commit moves.
#
# Every class below is a `DeploymentFoundationError`, so `main`'s handler and
# every existing caller behave as they did. What changes is that the handler can
# now say which member of the closed vocabulary the refusal belongs to instead
# of matching prose.
#
# Two of them are raised by `lane3_provocation` and one region by
# `lane3_inside_vantage`, files this change does not touch. They are translated
# at their CALL SITE below, which discriminates them exactly where the type
# cannot: `ProvocationError` covers both "the descriptor declares no private
# port" (a precondition) and "the foreign rule could not be seeded" (a mutation
# that failed), and those two demand opposite operator actions.
#
# The type is not the WHOLE answer, and it cannot be. A refusal raised below this
# lane carries none of these classes, and its own type does not discriminate it:
# `PreconditionFailed` is documented as "nothing has changed" and is raised by
# `ExposureTransaction.run` after the apply, and `SpecError` is raised both by
# `ProductDeploymentSpec.load` before host contact and by `build_receipt` after
# the whole transaction. `classify_refusal` therefore answers by TYPE first, and
# then by whether an exact lease was in hand at all — a refusal below this lane
# is `host_state_uncertified` whenever the run owned the host, mutated or not,
# and no member at all when it did not.


class ResultRecordedTwice(DeploymentFoundationError):
    """The runner's own record contradicted itself. -> `receipt_inconsistent`."""


class EvidenceUnreadable(DeploymentFoundationError):
    """Evidence existed and could not be read. -> `evidence_unreadable`."""


class EvidenceIncomplete(DeploymentFoundationError):
    """Evidence was read and a required fact was absent. -> `evidence_incomplete`."""


class ProbeRefused(DeploymentFoundationError):
    """A probe refused to run. -> `probe_refused`."""


class PreconditionUnfit(DeploymentFoundationError):
    """An input is unusable and the host was never touched. -> `precondition_unfit`.

    The second half of that sentence is load-bearing, and it is why the checks
    that raise this were moved ahead of the first mutation — see `run`.
    """


class HostStateUncertified(DeploymentFoundationError):
    """Nobody has certified what state the host is in. -> `host_state_uncertified`.

    Raised by name where this lane knows it: the seeder failed to place the
    foreign rule, having already changed the chain part way.

    It is ALSO the member `classify_refusal` reaches for any refusal raised
    below this lane while an exact lease was in hand — whether or not a mutation
    was attempted, because owning the host and being unable to characterise it is
    the condition this member names. See there.
    """


#: Refusal class -> the closed vocabulary member. This table answers by TYPE and
#: it is not the whole answer: a `DeploymentFoundationError` raised below this
#: lane has no class here, and `classify_refusal` then answers POSITIONALLY.
_REFUSAL_BY_TYPE: Final[
    tuple[tuple[type[DeploymentFoundationError], TerminalRefusal], ...]
] = (
    (ResultRecordedTwice, TerminalRefusal.RECEIPT_INCONSISTENT),
    (EvidenceUnreadable, TerminalRefusal.EVIDENCE_UNREADABLE),
    (EvidenceIncomplete, TerminalRefusal.EVIDENCE_INCOMPLETE),
    (ProbeRefused, TerminalRefusal.PROBE_REFUSED),
    (PreconditionUnfit, TerminalRefusal.PRECONDITION_UNFIT),
    (HostStateUncertified, TerminalRefusal.HOST_STATE_UNCERTIFIED),
)


def classify_refusal(
    exc: BaseException, *, lease_in_hand: bool, host_mutated: bool
) -> TerminalRefusal | None:
    """The vocabulary member for this refusal — or none, and then no release.

    ## THREE cases, so TWO facts. One boolean could not carry them.

    This answered on `host_mutated` alone until 2026-09-04, and a single boolean
    has two values. The third case therefore had to borrow one of the other two's
    answers, and the one it borrowed was the answer that matters most: a generic
    failure that left nobody able to say what state the host is in, arriving
    before anything had been mutated, came back as `None` — no release, and a
    silence indistinguishable from a run that never started.

    The ruling names three cases and each takes a different answer:

    * **no exact `HostLease.v2` in hand** — a descriptor that will not parse, a
      lease that is missing, expired, or issued for another authorization run.
      `None`, and **no release is written at all**. The host keeps the standing
      it already had: an expired lease stays `EXPIRED_HELD`, which is
      `HostStanding`'s answer for a holder nobody can ask anything of, and it is
      not this function's to overwrite. Checked FIRST and without consulting the
      exception, because with no lease there is nothing to discharge and no
      digest to name a release by — `build_release` refuses on exactly that
      sentence, and answering a member here would only route it there.
    * **the lease in hand, and an invocation defect this lane PROVED before host
      contact or mutation** -> `PRECONDITION_UNFIT`. Proved, not assumed: the
      refusal carries one of this module's own classes from `_REFUSAL_BY_TYPE`,
      raised by a check `run` asks ahead of its first mutation.
    * **the lease in hand, and a generic failure that prevented host state from
      being established** -> `HOST_STATE_UNCERTIFIED`, **even with
      `host_mutated=False`.** This is the answer that reads wrong and is right.
      Holding the lease means this run OWNED the host; a refusal it cannot name
      means it could not establish what state that host is in. *"Nothing was
      attempted"* and *"nobody can say"* are different claims, and only the
      second is one this run can defend — so the closure is bounded to
      inspection or destruction by `lease_release._PERMITTED_CLOSURES` rather
      than advertising a machine nobody characterised as generally reusable.

    ## What `host_mutated` decides now that it is no longer the discriminator

    It is the CHECK on the second case's premise. `PRECONDITION_UNFIT`'s pole is
    *untouched and safely releasable*. Every check that raises `PreconditionUnfit`
    sits ahead of the first mutation in `run` today — and that is an arrangement
    of lines, which is precisely the thing a later commit moves. Three of these
    checks once drifted behind an applied compose stack and two rewritten filter
    chains, and a release written from there would have told a destroyer the host
    was untouched when it was not.

    So a `PreconditionUnfit` that arrives with mutation attempted does not keep
    its member: it degrades to `HOST_STATE_UNCERTIFIED`. The error is then in the
    direction that asks for an inspection nobody needed, never the one that
    advertises an unexamined host as clean.

    The other named members are deliberately unaffected. `EVIDENCE_UNREADABLE`
    and `PROBE_REFUSED` are raised after the apply BY DESIGN, and their poles
    assert nothing about the host being untouched. `PRECONDITION_UNFIT` is the
    only member in the closed vocabulary whose meaning is a claim about the
    machine, so it is the only one a mutation can falsify.

    ## The modality stays conservative

    `host_mutated` is set by `run` immediately before `transaction.run()`, so a
    `LockUnavailableError` from the lock that transaction takes BEFORE its first
    effect degrades a precondition too. That is the member's own modality — *may*
    have begun, not *did*.

    ## One consequence, stated rather than left to be discovered

    `run` parses `--controller-identity` before it loads the lease, so a
    malformed fingerprint refuses with `PreconditionUnfit` while no lease is in
    hand and therefore records NO member. `TerminalRefusal`'s table still maps
    that refusal to `PRECONDITION_UNFIT` and the mapping is right; what changed
    is that no release can carry it from that site. Moving the parse behind
    `load_lease` would make it recordable and is NOT done here — it is a change
    to what `run` establishes and in which order, and it is named in
    `TerminalRefusal`'s docstring as open rather than decided in passing.
    """
    if not lease_in_hand:
        return None
    for kind, member in _REFUSAL_BY_TYPE:
        if isinstance(exc, kind):
            if member is TerminalRefusal.PRECONDITION_UNFIT and host_mutated:
                return TerminalRefusal.HOST_STATE_UNCERTIFIED
            return member
    return TerminalRefusal.HOST_STATE_UNCERTIFIED


@contextlib.contextmanager
def refusal_of(kind: type[DeploymentFoundationError]) -> Iterator[None]:
    """Translate a refusal raised in a file this lane does not own.

    Used only where the raising module cannot discriminate its own sites and
    this one can — the call site is the discriminator, and it is named at the
    call rather than inferred from a message.
    """
    try:
        yield
    except kind:
        raise
    except DeploymentFoundationError as exc:
        raise kind(str(exc)) from exc


class Results:
    """Collects one outcome per item, refusing a second write for the same one.

    A rerun that overwrote an earlier failure with a later pass would be the
    quietest possible way to launder a red run, so the collector refuses rather
    than the reviewer having to notice.
    """

    def __init__(self) -> None:
        self._rows: dict[str, RequirementResult] = {}

    def record(
        self,
        code: str,
        status: RequirementStatus,
        detail: str,
        *evidence: str,
    ) -> None:
        if code in self._rows:
            raise ResultRecordedTwice(
                f"item {code!r} was recorded twice. Overwriting an outcome is "
                "how a failure becomes a pass without anyone deciding to"
            )
        self._rows[code] = RequirementResult(
            code=code, status=status, detail=detail, evidence=tuple(evidence)
        )

    def all(self) -> list[RequirementResult]:
        return list(self._rows.values())


def _ssh_runner(target: str, identity: str):
    """Every host command goes through the dedicated controller identity.

    Not the shared key. `authorized_keys` on the rehearsal target holds two keys
    every agent authenticates as, so a run under one of them cannot be
    attributed to the controller — which is the difference between a
    procedurally and an evidentially controller-driven rehearsal.
    """

    def run(argv, *, timeout=60, env=None, capture=True) -> CommandResult:
        remote = shlex.join(list(argv))
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-i",
            identity,
            target,
            remote,
        ]
        completed = subprocess.run(
            command, capture_output=capture, text=True, timeout=timeout, check=False
        )
        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    return run


def _load_probe_evidence(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceUnreadable(
            f"the probe evidence at {path} could not be read ({exc}). The "
            "external half cannot be assumed"
        ) from exc


def _qualify(evidence: dict) -> VantageQualification:
    vantage = evidence.get("vantage", {})
    return qualify_vantage(
        VantageQualification(
            address_v4=str(vantage.get("address_v4", "")),
            address_v6=str(vantage.get("address_v6", "")),
            public_interface=str(vantage.get("public_interface", "")),
            interfaces={
                str(name): tuple(str(a) for a in addrs)
                for name, addrs in (vantage.get("interfaces") or {}).items()
            },
            link_kinds=tuple(str(k) for k in vantage.get("link_kinds", ())),
            routes_to_target={
                str(f): str(i) for f, i in (vantage.get("routes") or {}).items()
            },
            private_paths_unreachable={
                str(t): bool(v)
                for t, v in (vantage.get("private_paths_unreachable") or {}).items()
            },
            credential_markers={
                str(m): bool(v)
                for m, v in (vantage.get("credential_markers") or {}).items()
            },
            observed_source_v4=str(vantage.get("observed_source_v4", "")),
            observed_source_v6=str(vantage.get("observed_source_v6", "")),
        )
    )


def _probe(evidence: dict, key: str) -> dict:
    probe = (evidence.get("probes") or {}).get(key)
    if not isinstance(probe, dict):
        raise EvidenceIncomplete(
            f"the probe evidence carries no {key!r} result. An unmeasured probe "
            "is not a passing one"
        )
    return probe


def _observed_from(args: argparse.Namespace, *, jump_key: str) -> dict[str, str]:
    """The far end's report of a vantage's source address, per family.

    The same script the qualify phase uses, with the INSIDE vantage's jump key.
    Reading it again rather than reusing the outside value is the point: these
    are different vantages, and one of the two addresses is SLAAC-derived, so it
    is requalified every run rather than remembered.
    """
    script = (
        pathlib.Path(__file__).resolve().parent
        / "exposure-rehearsal"
        / "observe_far_end.sh"
    )
    argv = [
        str(script),
        args.inside_vantage,
        args.target,
        args.observer_user,
        args.observer_key,
        jump_key,
    ]
    completed = subprocess.run(
        argv, capture_output=True, text=True, timeout=args.timeout, check=False
    )
    if completed.returncode != 0:
        raise EvidenceUnreadable(
            f"could not read the inside vantage's observed source addresses: "
            f"{completed.stderr.strip() or 'no stderr'}"
        )
    try:
        return {str(k): str(v) for k, v in json.loads(completed.stdout).items()}
    except ValueError as exc:
        raise EvidenceUnreadable(
            f"the far-end observation emitted no readable JSON ({exc})"
        ) from exc


def _collect_probe_phase(args: argparse.Namespace) -> dict:
    """Run the collector's `probe` phase and return what it measured.

    Refuses rather than degrades. An unparseable or absent probe document would
    otherwise leave `evidence["probes"]` empty, every item would fail for a
    reason that looks like an exposure defect, and the real cause — that the
    collection never ran — would be invisible in the receipt.
    """
    script = (
        pathlib.Path(__file__).resolve().parent
        / "exposure-rehearsal"
        / "collect_probe_evidence.sh"
    )
    argv = [str(script), "probe", args.probe_host, args.target]
    completed = subprocess.run(
        argv, capture_output=True, text=True, timeout=args.timeout, check=False
    )
    if completed.returncode != 0:
        raise ProbeRefused(
            f"the probe phase refused ({shlex.join(argv)}): "
            f"{completed.stderr.strip() or 'no stderr'}"
        )
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise EvidenceUnreadable(
            f"the probe phase emitted no readable JSON ({exc}). An unmeasured "
            "probe is not a passing one"
        ) from exc


def judge_proxy_recreation(
    before: Sequence[ObservedProxy], after: Sequence[ObservedProxy]
) -> tuple[RequirementStatus, str]:
    """Gate item 5 — "the `docker-proxy` PID is NEW" — as a pure decision.

    A surviving pid means the container was never recreated, so the apply
    proved nothing about the binding: the socket that answered afterwards is
    the same socket that answered before, and a wrong port mapping would look
    exactly as healthy.

    Extracted from :func:`run` so it can be exercised without a leased host, an
    SSH identity or a qualified vantage. That is not tidiness. This item was
    DEAD until recently — `ObservedProxy` discarded the pid entirely, so it
    could only ever be closed by a human reading `ps` — and the capture was
    fixed without the decision built on it ever being observed working. Lane 3
    cannot currently run (no issuer, no registered runner), so a unit test is
    the only thing that can establish this gate bites at all.

    Four outcomes, and the first is the one that is easy to get wrong:

    - a listing with NO pid column is ``BLOCKED``, never a pass. Comparing
      `None` against `None` and calling the result "new" is how a check reports
      success for having measured nothing;
    - no proxy at all is a failure — there is nothing publishing the port;
    - any surviving pid is a failure, named;
    - otherwise every pid is new, and the detail records both sets so the
      receipt shows what was compared rather than asserting a conclusion.
    """
    unknown = [proxy for proxy in after if proxy.pid is None]
    before_pids = {proxy.pid for proxy in before if proxy.pid is not None}
    after_pids = {proxy.pid for proxy in after if proxy.pid is not None}
    survivors = sorted(before_pids & after_pids)

    if unknown:
        return BLOCKED, (
            f"{len(unknown)} docker-proxy line(s) carried no pid, so 'the pid is "
            "new' cannot be established from this listing"
        )
    if not after_pids:
        return FAILED, "no docker-proxy process was observed"
    if survivors:
        return FAILED, (
            f"docker-proxy pid(s) {survivors} SURVIVED the apply — the container "
            "was not recreated, so the apply proved nothing about the binding"
        )
    return PASSED, (
        f"every docker-proxy pid is new ({sorted(after_pids)}); none survived "
        f"from the snapshot ({sorted(before_pids)})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The terminal record
#
# `HostLeaseRelease.v1` is Foundation's schema and this is its only writer.
# Nothing anywhere wrote one before, so `load_lease` refused to BEGIN without a
# record while the END of every run was an inference: a process was gone and a
# timestamp had passed. The lane it matters to is destruction — `expired_held`
# exists precisely so a crashed run's timeout cannot authorise a wipe — and that
# shapes every rule below.
#
# ## Absence means HELD
#
# So the write happens on a TYPED terminal outcome and on nothing else. There is
# no `except Exception` around it: a refusal this lane can name produces a
# refusal-release, and a crash — SIGKILL, the runner dying, a bug with no member
# in the closed vocabulary — produces no record and leaves the lease held. Those
# two must not collapse, because the collapse is how a killed run comes to
# authorise a destroy.
#
# ## A cleanup failure may not rewrite a recorded verdict
#
# `withdraw_foreign_rules` and `disarm_apply_failure` run after the verdict is
# recorded and ignore their failures deliberately, so their outcomes existed and
# were discarded. They are captured here — through the runner the effects
# already call, so nothing in `lane3_provocation` or the provider changes — and
# a failure among them lands in `cleanup`, never in the receipt.
# ─────────────────────────────────────────────────────────────────────────────

#: The OIDC audience the releasing principal is minted for. A knob with a
#: documented default rather than a literal, like every other env-specific value.
DEFAULT_PRINCIPAL_AUDIENCE: Final = "dotmac-lane3-release"

#: Worst-wins, for combining several acts into the schema's single field.
#: `NOT_ATTEMPTED` outranks `PURGED` on purpose: an act that never ran must not
#: be summarised as one that succeeded, and the schema calls `NOT_ATTEMPTED`
#: "a loud one for a run that should have".
_CLEANUP_RANK: Final[tuple[CleanupDisposition, ...]] = (
    CleanupDisposition.PURGED,
    CleanupDisposition.RETAINED_FOR_INSPECTION,
    CleanupDisposition.NOT_ATTEMPTED,
    CleanupDisposition.OUTCOME_UNKNOWN,
    CleanupDisposition.FAILED,
)

#: Proposed in this order; the TYPE decides which survives. The two constraints
#: — what the refusal permits and what the cleanup outcome permits — are
#: composed by intersection inside `HostLeaseReleaseV1`, and asking there rather
#: than re-deriving the tables here is what keeps one owner for the rule. A
#: second copy would drift, and the direction it drifts is always permissive.
_CLOSURE_PREFERENCE: Final[tuple[HostClosure, ...]] = (
    HostClosure.REUSABLE,
    HostClosure.INSPECTION_REQUIRED,
    HostClosure.DESTROY_ONLY,
)


class PrincipalUnprovable(Exception):
    """The authenticated workload could not be proven, so nothing is released.

    Deliberately NOT a `DeploymentFoundationError`: it must never be mistaken
    for one of this lane's own refusals and classified as one.
    """


class ReleaseNotWritable(Exception):
    """A truthful release cannot be built or stored, so none is written."""


@dataclasses.dataclass(frozen=True, slots=True)
class Captured:
    """One command a cleanup act issued, and what it answered."""

    argv: tuple[str, ...]
    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def as_document(self) -> dict[str, object]:
        return {
            "command": shlex.join(self.argv),
            "exit_code": self.exit_code,
            "output": self.output,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class CleanupAct:
    """What became of ONE thing the lease created."""

    name: str
    disposition: CleanupDisposition
    detail: str
    commands: tuple[Captured, ...] = ()

    def as_document(self) -> dict[str, object]:
        return {
            "act": self.name,
            "disposition": self.disposition.value,
            "detail": self.detail,
            "commands": [c.as_document() for c in self.commands],
        }


class CapturingRunner:
    """The controller runner, with a window that keeps what a cleanup discarded.

    `_delete_owned` passes `allow_failure=True` and `withdraw_foreign_rules` is
    best-effort by design, so both of them throw their results away. Wrapping
    the runner both of them ultimately call is what lets this lane see those
    results without changing either — and without a second SSH seam, which would
    be a second writer to the host.
    """

    def __init__(self, inner: Callable[..., CommandResult]) -> None:
        self._inner = inner
        self._window: list[Captured] | None = None

    @contextlib.contextmanager
    def watching(self) -> Iterator[list[Captured]]:
        previous: list[Captured] | None = self._window
        window: list[Captured] = []
        self._window = window
        try:
            yield window
        finally:
            self._window = previous

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: int = 60,
        env: dict[str, str] | None = None,
        capture: bool = True,
    ) -> CommandResult:
        result = self._inner(list(argv), timeout=timeout, env=env, capture=capture)
        if self._window is not None:
            self._window.append(
                Captured(
                    argv=tuple(str(a) for a in argv),
                    exit_code=result.exit_code,
                    output=(result.stderr or result.stdout or "").strip()[:200],
                )
            )
        return result


def disposition_of(name: str, observed: Sequence[Captured]) -> CleanupAct:
    """Read a completed act's outcome off the commands it actually issued.

    Three answers, and the third is the one that is easy to lose. An act that
    ran and issued nothing observable is `outcome_unknown`, never `purged` and
    never `not_attempted`: "we tried and do not know" is a different fact from
    both, and the borrowed answer is always the one that looks safer.
    """
    failures = [c for c in observed if not c.ok]
    if failures:
        return CleanupAct(
            name,
            CleanupDisposition.FAILED,
            f"{len(failures)} of {len(observed)} command(s) failed; something "
            f"the lease created is still there: {shlex.join(failures[0].argv)} "
            f"-> {failures[0].exit_code} {failures[0].output}",
            tuple(observed),
        )
    if not observed:
        return CleanupAct(
            name,
            CleanupDisposition.OUTCOME_UNKNOWN,
            "the act ran and issued no observable command, so nothing certifies "
            "what became of what it was to remove",
            (),
        )
    return CleanupAct(
        name,
        CleanupDisposition.PURGED,
        f"{len(observed)} command(s), all successful",
        tuple(observed),
    )


def attempt_cleanup(
    name: str, controller: CapturingRunner, act: Callable[[], None]
) -> CleanupAct:
    """Run one cleanup act and record its outcome without letting it escape.

    The verdict is already in the receipt by the time this runs. A cleanup
    failure is a fact about the HOST, and it belongs in `cleanup` and in the
    closure the host is released into — not in an item's status.
    """
    with controller.watching() as observed:
        try:
            act()
        except DeploymentFoundationError as exc:
            return CleanupAct(
                name,
                CleanupDisposition.FAILED,
                f"the act refused after {len(observed)} command(s): {exc}"[:400],
                tuple(observed),
            )
        except Exception as exc:
            return CleanupAct(
                name,
                CleanupDisposition.OUTCOME_UNKNOWN,
                f"the act raised {type(exc).__name__} after {len(observed)} "
                f"command(s), so whether it finished is not known: {exc}"[:400],
                tuple(observed),
            )
    return disposition_of(name, observed)


@dataclasses.dataclass
class TerminalContext:
    """Everything a terminal record binds, accumulated as the run establishes it.

    Every field starts at the value that claims nothing. A refusal at the first
    line therefore produces either a truthful record or none, never a confident
    one built from defaults.
    """

    lease: HostLease | None = None
    #: The controller key, PARSED. Held here rather than re-read off `args` at
    #: the end, for the same reason the lease is: the release names what this run
    #: actually mutated the host with, and a value that could not be established
    #: as a fingerprint never gets that far — `run` parses it before it reads the
    #: descriptor, so every later refusal already has it.
    controller_identity: ControllerSshFingerprintV1 | None = None
    #: A mutation was ATTEMPTED. Not "succeeded" — the attempt is what makes
    #: `precondition_unfit`'s "the host was never touched" false, which is the
    #: one thing `classify_refusal` still consults it for: a precondition refusal
    #: arriving past this point degrades to `host_state_uncertified` rather than
    #: keeping a member that asserts an untouched machine.
    host_mutated: bool = False
    #: The inert rule was offered to the chain, so a disarm has something to do
    #: even if the arming call itself failed part way.
    arm_attempted: bool = False
    vm_installation_id: str = ""
    receipt_digest: str = ""
    acts: list[CleanupAct] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)

    @property
    def lease_in_hand(self) -> bool:
        """An EXACT, live `HostLease.v2` covering THIS authorization run was taken.

        Derived from `lease` rather than tracked as its own flag, and the
        derivation is only sound because of where `run` assigns it: `load_lease`
        refuses a missing record, a non-V2 record and an unparseable one, and
        `covers` refuses an expired window or a foreign authorization run —
        BOTH before `ctx.lease` is set. So this is false for every case the
        ruling groups under "no exact V2 lease acquired", and there is no state
        in which a rejected lease is held here.

        Without it there is nothing to discharge, so `classify_refusal` names no
        member and no release is written. A separate boolean set beside the
        assignment would be a second answer to the same question, free to drift.
        """
        return self.lease is not None

    def record_cleanup(self, act: CleanupAct) -> None:
        self.acts.append(act)

    def cleanup_disposition(self) -> CleanupDisposition:
        if not self.acts:
            return CleanupDisposition.NOT_ATTEMPTED
        return max((a.disposition for a in self.acts), key=_CLEANUP_RANK.index)


def _decode_workload_claims(token: str) -> dict[str, object]:
    """The claims a workload identity token carries.

    The signature is NOT verified here and this is the limit of what the record
    establishes: the token was minted by the request endpoint the Actions
    runtime injected, using the request secret it injected with it. That is why
    the endpoint is read from the environment and never accepted as an argument
    — a subject handed in on a command line is exactly what `released_by` may
    not be.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise PrincipalUnprovable(
            "the workload identity token is not a three-part JWT, so no subject "
            "can be read from it"
        )
    payload = parts[1]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(raw.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise PrincipalUnprovable(
            f"the workload identity token's claims could not be read ({exc})"
        ) from exc
    if not isinstance(claims, dict):
        raise PrincipalUnprovable("the workload identity token carries no claims")
    return claims


def prove_principal(*, audience: str, timeout: int) -> tuple[ReleasingPrincipal, str]:
    """The authenticated workload, and the run it is bound to, from ONE token.

    Both come out of the same signed document on purpose. `released_by` must be
    bound to the run that produced it, and deriving the subject from one source
    and the run id from another (`GITHUB_RUN_ID`, say) would let a mismatched
    pair look like a derivation.

    Never the CLI, never a display name, and never the controller/SSH key
    fingerprint — that key mutated the host and is retained as its own separate
    evidence. If this cannot be proven the caller writes nothing and the lease
    stays held, which is the designed outcome and not an error to route around.
    """
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    if not url or not request_token:
        raise PrincipalUnprovable(
            "no workload identity endpoint is present in this environment "
            "(ACTIONS_ID_TOKEN_REQUEST_URL / _TOKEN are injected by the runtime, "
            "and the job must request `id-token: write`). Without it the "
            "releasing principal cannot be proven, so nothing is released"
        )
    if not url.lower().startswith("https://"):
        raise PrincipalUnprovable(
            f"the workload identity endpoint {url!r} is not https, and a "
            "principal is not read off a plaintext channel"
        )
    endpoint = f"{url}&audience={urllib.parse.quote(audience)}"
    request = urllib.request.Request(  # noqa: S310 - scheme checked immediately above
        endpoint, headers={"Authorization": f"Bearer {request_token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise PrincipalUnprovable(
            f"the workload identity endpoint did not answer ({exc})"
        ) from exc
    token = str((body or {}).get("value", "")) if isinstance(body, dict) else ""
    if not token:
        raise PrincipalUnprovable("the workload identity endpoint returned no token")
    claims = _decode_workload_claims(token)
    subject = str(claims.get("sub", "")).strip()
    repository = str(claims.get("repository", "")).strip()
    run_id = str(claims.get("run_id", "")).strip()
    attempt = str(claims.get("run_attempt", "")).strip()
    if not subject:
        raise PrincipalUnprovable("the workload identity token names no subject")
    if not repository or not run_id:
        raise PrincipalUnprovable(
            "the workload identity token names no repository/run, so the "
            "principal cannot be bound to the run that produced it"
        )
    rehearsal_run_id = f"github-actions:{repository}:{run_id}"
    if attempt:
        rehearsal_run_id = f"{rehearsal_run_id}:{attempt}"
    try:
        principal = ReleasingPrincipal(
            kind="github_actions_workload",
            subject=subject,
            run_binding=rehearsal_run_id,
        )
    except SpecError as exc:
        raise PrincipalUnprovable(
            f"the proven subject {subject!r} is not a shape "
            f"`ReleasingPrincipal` accepts ({exc})"
        ) from exc
    return principal, rehearsal_run_id


def build_release(
    ctx: TerminalContext, args: argparse.Namespace, outcome: TerminalOutcome
) -> HostLeaseReleaseV1:
    """Assemble the record, or refuse and leave the lease held."""
    lease = ctx.lease
    if lease is None:
        raise ReleaseNotWritable(
            "no lease was in hand when the run ended, so there is nothing to "
            "release and no digest to name it by"
        )
    principal, rehearsal_run_id = prove_principal(
        audience=args.principal_audience, timeout=args.timeout
    )
    if principal.subject != str(lease.workload_principal):
        raise ReleaseNotWritable(
            f"the proven principal {principal.subject!r} is not the workload "
            f"{lease.workload_principal!r} this lease was issued to. Writing it "
            "anyway would make the host read as RELEASED while the destroy gate "
            "refuses it as foreign — the worst of both answers"
        )
    controller_identity = ctx.controller_identity
    if controller_identity is None:
        raise ReleaseNotWritable(
            "the controller identity was never established as a key "
            "fingerprint, so nothing can record what mutated the host"
        )
    # Compared as DECODED DIGESTS. A well-formed fingerprint of the WRONG key is
    # refused HERE as well as at the destroy gate: parsing proves shape, and
    # this is where identity is proven on the writing side.
    if controller_identity != lease.controller_identity_fingerprint:
        raise ReleaseNotWritable(
            f"the host was mutated with {controller_identity} and the "
            f"lease names {lease.controller_identity_fingerprint}. Same "
            "reason: a record the destroy gate refuses as foreign would still "
            "flip this host's standing to RELEASED"
        )
    cleanup = ctx.cleanup_disposition()
    common = {
        "lease_digest": lease_digest(lease),
        "vm_slot": args.vm_slot,
        "vm_installation_id": ctx.vm_installation_id,
        "candidate_version": args.candidate_version,
        "source_revision": args.foundation_revision,
        "authorization_run_id": args.authorization_run,
        "rehearsal_run_id": rehearsal_run_id,
        "outcome": outcome,
        "released_at": datetime.now(UTC).isoformat(),
        "released_by": principal,
        "controller_identity_fingerprint": controller_identity,
        "cleanup": cleanup,
    }
    # Every field EXCEPT the closure, checked once against the pole every
    # constrained set contains. Without this probe a malformed `vm_slot` would
    # come back as "no closure was permitted", which is a true sentence about
    # the wrong thing.
    try:
        HostLeaseReleaseV1(closure=HostClosure.DESTROY_ONLY, **common)
    except SpecError as exc:
        raise ReleaseNotWritable(f"the release does not construct: {exc}") from exc
    rejected: list[str] = []
    for closure in _CLOSURE_PREFERENCE:
        try:
            return HostLeaseReleaseV1(closure=closure, **common)
        except SpecError as exc:
            rejected.append(f"{closure.value} ({exc})")
    raise ReleaseNotWritable(
        "no closure was permitted for this release: " + "; ".join(rejected)
    )


def record_terminal(
    ctx: TerminalContext,
    args: argparse.Namespace,
    outcome: TerminalOutcome | None,
) -> str:
    """Write the terminal record, and the evidence of the attempt either way.

    ``outcome`` is ``None`` when the run ended on a refusal with no member in
    the closed vocabulary. That is not a receipt and not a refusal this lane can
    name, so no release is written and the lease stays held — but the ATTEMPT is
    still recorded, because a run that leaves nothing behind is
    indistinguishable from one that never happened.

    Never raises. A record that failed to be written must not become a second,
    different verdict on top of the one already reached.
    """
    digest = ""
    written = ""
    try:
        if outcome is None:
            raise ReleaseNotWritable(
                "the refusal has no member in the closed terminal vocabulary"
            )
        release = build_release(ctx, args, outcome)
    except (ReleaseNotWritable, PrincipalUnprovable) as exc:
        ctx.notes.append(f"NO RELEASE WRITTEN: {exc}")
    else:
        try:
            # `lease.py` owns this store — it owns `load_lease` and derives the
            # release path beside it — so the record goes in through the
            # package's writer rather than one of this runner's own. A second
            # publisher would be two answers to "how does a release reach the
            # store", and the destroy gate would then be reading whichever
            # answer happened to run.
            #
            # `PreconditionFailed` is caught BY NAME because `write_release`
            # documents it as its contract: the `FileExistsError` from the
            # atomic publish is a store primitive's signal, and the meaning of a
            # duplicate release belongs to the module that owns releases. A
            # handler that caught only `OSError` would let it escape a function
            # that promises never to raise — and on the receipt path it would
            # unwind into `main`, be classified as unnameable, and report a
            # green rehearsal as a refusal.
            stored = write_release(
                release, target=args.target, directory=args.lease_dir
            )
        except (PreconditionFailed, ReleaseNotWritable, OSError) as exc:
            ctx.notes.append(f"NO RELEASE WRITTEN: {exc}")
        else:
            digest, written = release.digest(), str(stored)
            ctx.notes.append(f"released {written} ({digest})")
            # The store is the ledger; this is a COPY of what it holds, taken
            # after the write succeeded, so the `if: always()` artifact step has
            # something to upload from a workspace it can reach. Never a
            # parallel write path: if the store refused, there is nothing to
            # copy, and that absence is the correct outcome rather than a gap to
            # paper over.
            if args.release_out:
                try:
                    shutil.copyfile(stored, pathlib.Path(args.release_out))
                except OSError as exc:
                    ctx.notes.append(
                        f"the stored release could not be copied for upload: {exc}"
                    )

    evidence = {
        "document": "lane3-terminal-evidence",
        "version": 1,
        "target": str(args.target),
        "authorization_run_id": str(args.authorization_run),
        "outcome": (
            outcome.as_document()
            if outcome is not None
            else {"receipt_digest": "", "refusal": ""}
        ),
        # BOTH facts the classifier answers on, so a reader of this sidecar can
        # tell the three terminal cases apart without re-deriving them from the
        # notes. An absent refusal with `lease_in_hand: false` is "no exact V2
        # lease was ever taken"; with `lease_in_hand: true` it is a release that
        # could be named and could not be written, and the notes say why.
        "lease_in_hand": ctx.lease_in_hand,
        "host_mutation_attempted": ctx.host_mutated,
        "cleanup": ctx.cleanup_disposition().value,
        "cleanup_acts": [act.as_document() for act in ctx.acts],
        "release_digest": digest,
        "release_path": written,
        "notes": list(ctx.notes),
    }
    try:
        out = pathlib.Path(args.terminal_evidence_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        partial = out.parent / f".{out.name}.{os.getpid()}.partial"
        partial.write_text(
            json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(partial, out)
    except OSError as exc:
        print(f"terminal evidence not written: {exc}", file=sys.stderr)
    for note in ctx.notes:
        print(note, file=sys.stderr)
    print(f"lease_release={digest or 'NOT WRITTEN — the lease stays HELD'}")
    return digest


def inside_probe_harness() -> pathlib.Path:
    """The inside-vantage probe script, named in ONE place.

    Two callers now ask for it — the probe itself, and the precondition check
    that runs long before it — and a second literal path would be a second
    answer to "which harness is this run about".
    """
    return (
        pathlib.Path(__file__).resolve().parent
        / "exposure-rehearsal"
        / "probe_inside_vantage.sh"
    )


def require_inside_probe_harness(
    args: argparse.Namespace, *, target_v6: str, port: int
) -> None:
    """Ask, BEFORE the host is touched, whether the inside probe can be invoked.

    `probe_inside_vantage.sh` wraps its `ssh` in `|| true` and its `case` falls
    through to `unknown`, so every way the vantage can be unavailable is
    reported as DATA. It exits non-zero only on its `${...:?}` guards: a missing
    positional argument, or `LANE3_JUMP_KEY` unset. Those are facts about the
    INVOCATION, decidable here, with the host untouched — which is the state
    `precondition_unfit` claims and the state the run is actually in at this
    point.

    Note that the script reads `LANE3_JUMP_KEY` from the AMBIENT environment
    rather than from `--inside-jump-key`, which is the runner's separate
    argument for `observe_far_end.sh`. The environment variable is checked here
    because it is the one the script actually reads.
    """
    script = inside_probe_harness()
    if not script.is_file():
        raise PreconditionUnfit(
            f"the inside-vantage probe harness is not at {script}. Items 12 and "
            "16 have no measurement without it"
        )
    if not os.access(script, os.X_OK):
        raise PreconditionUnfit(
            f"the inside-vantage probe harness at {script} is not executable"
        )
    missing = [
        name
        for name, value in (
            ("--inside-vantage", args.inside_vantage),
            ("--target", args.target),
            ("the vantage's `target_v6`", target_v6),
        )
        if not str(value).strip()
    ]
    if missing:
        raise PreconditionUnfit(
            f"the inside-vantage probe would be invoked with empty {missing}, "
            "which its usage guards refuse"
        )
    if port <= 0:
        raise PreconditionUnfit(
            f"the descriptor's private port resolved to {port}, which the "
            "inside-vantage probe cannot be invoked with"
        )
    if not os.environ.get("LANE3_JUMP_KEY", "").strip():
        raise PreconditionUnfit(
            "`LANE3_JUMP_KEY` is unset, so the inside-vantage probe refuses "
            "before opening a single connection. Nothing is attempted and "
            "nothing is measured; the repair is configuring the jump identity, "
            "never inspecting the target"
        )


def read_installation_id(controller: CapturingRunner) -> str:
    """The guest's machine-id, or `""` — a STATED "not recorded".

    An observation, taken while the controller channel is known good, so a
    release can bind the INSTALLATION as well as the slot: a slot re-provisioned
    between release and destroy is the one case the slot alone cannot catch. A
    value that could not be read is stated as absent rather than invented.
    """
    try:
        result = controller(["cat", "/etc/machine-id"], timeout=30)
    except Exception:
        return ""
    if not result.ok:
        return ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else ""


def run_cleanup(
    ctx: TerminalContext,
    controller: CapturingRunner,
    effects: ComposeHostExposureEffects,
    seeded: Sequence[SeededRule],
) -> None:
    """Undo what the provocation created, recording each act SEPARATELY.

    Separately because they are separate facts about the host: the inert IPv6
    rule and each family's foreign seed fail independently, and one combined
    answer would let a failure hide behind two successes. `HostLeaseRelease.v1`
    carries a single `cleanup` field, so they are combined worst-wins for it and
    kept per act in the terminal evidence written beside it.
    """
    if ctx.arm_attempted:
        ctx.record_cleanup(
            attempt_cleanup(
                "provocation_disarm",
                controller,
                lambda: disarm_apply_failure(effects),
            )
        )
    else:
        ctx.record_cleanup(
            CleanupAct(
                "provocation_disarm",
                CleanupDisposition.NOT_ATTEMPTED,
                "no inert rule was ever offered to the chain, so there is "
                "nothing to take back out",
            )
        )
    for rule in reversed(tuple(seeded)):
        ctx.record_cleanup(
            attempt_cleanup(
                f"foreign_rule_withdrawal:{rule.family}",
                controller,
                functools.partial(withdraw_foreign_rules, controller, (rule,)),
            )
        )
    if not seeded:
        ctx.record_cleanup(
            CleanupAct(
                "foreign_rule_withdrawal",
                CleanupDisposition.NOT_ATTEMPTED,
                "no foreign seed survived to be withdrawn",
            )
        )


def run(args: argparse.Namespace, ctx: TerminalContext) -> int:
    started = datetime.now(UTC).isoformat()
    results = Results()

    # FIRST, before the descriptor is even opened. `--controller-identity` is an
    # OpenSSH fingerprint — `SHA256:` and 43 characters of base64, as
    # `ssh-keygen -lf` emits — and whether the value IS one is decidable from the
    # argument alone. Asking here means a malformed identity refuses as
    # `precondition_unfit` with the host genuinely untouched, and means every
    # later refusal already holds the parsed value, so a run that ends between
    # here and the first mutation can still write a truthful release.
    with refusal_of(PreconditionUnfit):
        controller_identity = ControllerSshFingerprintV1.parse(
            args.controller_identity, field="--controller-identity"
        )
    ctx.controller_identity = controller_identity

    descriptor = pathlib.Path(args.descriptor)
    fixture_bytes = descriptor.read_bytes()
    spec = ProductDeploymentSpec.load(str(descriptor))
    descriptor_digest = spec.to_canonical_document().sha256_digest()

    # ── the lease, which cannot be self-granted ─────────────────────────────
    lease = load_lease(args.target, directory=args.lease_dir)
    lease.covers(now=datetime.now(UTC), authorization_run_id=args.authorization_run)
    # The terminal record names the lease BY CONTENT, so it is held from here on
    # rather than re-read at the end: a release must discharge the lease this run
    # actually executed under, not whichever record is on the target later.
    ctx.lease = lease

    # This block used to derive a Compose project from the authorization run and
    # check the lease owned it. Both halves were inert and the comment above them
    # was false, so all three are gone.
    #
    # `owns_project(p)` is `p.startswith(self.compose_project_prefix)` and the
    # name was built by concatenating that prefix, so the check was true by
    # construction and could never fire — its own `pragma: no cover` said so.
    # Worse, the derived name never reached the effects:
    # `ComposeHostExposureEffects.apply_compose` passes `--project-name
    # self._spec.product`, so Docker labels every object
    # `com.docker.compose.project=lane3_exposure`. The comment claimed a deletion
    # set "scoped by construction"; that property did not hold, and the derived
    # name's only other use was as item 1's evidence pointer — a checkable,
    # wrong pointer inside a PASSING item, which is worse than an absent one for
    # the same reason `RequirementResult` refuses an empty detail.
    #
    # The real question the dead check was gesturing at is still open and is NOT
    # answered here: does the lease's prefix cover `spec.product`, the name
    # Docker actually uses? That cannot be decided from this source — the prefix
    # lives in the lease record, which Platform CP owns — so replacing the dead
    # check with `lease.owns_project(spec.product)` would introduce a refusal
    # that might reject every run, untested. Named rather than guessed.

    # ── the external vantage, qualified BEFORE its refusals are believed ────
    evidence = _load_probe_evidence(pathlib.Path(args.probe_evidence))
    _qualify(evidence)

    # ── every PRECONDITION, asked before the host is touched ────────────────
    #
    # `private_port`, `inside_source_set` and the inside-probe harness used to
    # be reached only after `transaction.run()` had applied the compose stack
    # and rewritten both filter chains. All three refuse with
    # `precondition_unfit`, whose recorded meaning is "nothing attempted,
    # nothing mutated ... the repair is fixing an input, never inspecting a
    # machine". Asked where they used to be asked, that sentence was FALSE about
    # the machine, and a release carrying it would have told a destroyer the
    # host was untouched when it was not.
    #
    # They are pure questions about the descriptor and the invocation, so asking
    # them here changes nothing for a fit input and makes the answer true for an
    # unfit one.
    with refusal_of(PreconditionUnfit):
        private = private_port(spec)
        accepted_source_set = inside_source_set(spec)
        target_v6 = str((evidence.get("vantage") or {}).get("target_v6", args.target))
        require_inside_probe_harness(args, target_v6=target_v6, port=private)

    owner = ownership_comment(spec.product)
    # ONE seam onto the host, wrapped so that what a best-effort cleanup throws
    # away is still seen. A second `_ssh_runner` would be a second writer.
    controller = CapturingRunner(_ssh_runner(args.target, args.controller_key))
    effects = ComposeHostExposureEffects(
        spec,
        deploy_dir=args.deploy_dir,
        runner=controller,
        timeout_seconds=args.timeout,
    )
    ctx.vm_installation_id = read_installation_id(controller)

    # ── item 3, before anything mutates ─────────────────────────────────────
    refused = False
    try:
        refuse_non_recreating_apply(["restart"])
    except DeploymentFoundationError:
        refused = True
    results.record(
        "non_recreating_refused",
        PASSED if refused else FAILED,
        "`docker compose restart` refused by the real controller path"
        if refused
        else "a non-recreating apply was NOT refused",
        "refuse_non_recreating_apply(['restart'])",
    )

    # ── items 1, 2, 4, 5, 6, 10: the transaction itself ─────────────────────
    snapshot = effects.observe()
    results.record(
        "pre_change_snapshot",
        PASSED,
        f"{len(snapshot.sockets)} sockets, {len(snapshot.chains)} chains captured "
        "before mutation",
        "ExposureTransaction.snapshot",
    )
    transaction = ExposureTransaction(
        spec=spec, effects=effects, lock_directory=args.lock_dir
    )
    # From here the host is mutated, whatever happens next. `precondition_unfit`
    # may not be claimed past this line, and this flag is what a reader of the
    # terminal evidence checks rather than inferring it from the refusal.
    ctx.host_mutated = True
    report = transaction.run()
    results.record(
        "apply_under_lock",
        PASSED if report.ok else FAILED,
        f"applied and verified under the {spec.product} deployment lock",
        # The project Docker was actually given, read from the same attribute
        # `apply_compose` passes to `--project-name`. It used to name a derived
        # value the effects never saw.
        f"project={spec.product}",
    )

    observed = effects.observe()
    sockets = {(s.address, s.port) for s in observed.sockets}
    # S104 is about BINDING to all interfaces. This is the opposite: the
    # wildcard addresses are what the lane exists to prove ABSENT, so the
    # literal here is a refusal predicate rather than a bind.
    wildcards = ("0.0.0.0", "::", "*")  # noqa: S104
    wildcard = [p for a, p in sockets if a in wildcards]
    results.record(
        "socket_reobservation",
        PASSED if not wildcard else FAILED,
        f"{len(sockets)} sockets re-observed; wildcard binds: {wildcard or 'none'}",
        "ss -tlnp",
    )
    results.record(
        "none_emits_no_socket",
        PASSED if not any(p == 18444 for _a, p in sockets) else FAILED,
        'the exposure = "none" port emits no socket',
        "ss -tlnp",
    )
    proxy_status, proxy_detail = judge_proxy_recreation(
        snapshot.proxies, observed.proxies
    )
    results.record("proxy_reobservation", proxy_status, proxy_detail, "ps -eo pid,args")

    planned = build_firewall_plan(spec)
    landed = []
    for rule in planned:
        chain = observed.chain(rule.family, rule.chain)
        landed.append(bool(chain and chain.rules_for(rule.host_port)))
    terminal_drop = any(rule.action == "DROP" and rule.terminal for rule in planned)
    firewall_ok = bool(planned) and all(landed) and terminal_drop
    results.record(
        "firewall_reobservation",
        PASSED if firewall_ok else FAILED,
        f"{len(planned)} derived rules; landed={sum(landed)}/{len(planned)}; "
        f"terminal DROP={terminal_drop}",
        "iptables-save",
        "ip6tables-save",
    )

    v6_docker_user = observed.chain("ipv6", "DOCKER-USER")
    results.record(
        "inert_v6_chain",
        PASSED if v6_docker_user is not None else BLOCKED,
        "ip6tables DOCKER-USER captured; a v6 rule there is inert because the "
        "chain is jumped only from FORWARD while a v6 publish terminates on INPUT"
        if v6_docker_user is not None
        else "the ip6tables DOCKER-USER chain could not be read",
        "ip6tables -L DOCKER-USER -v -n",
    )

    # ── items 13-16: the external half, measured from the qualified vantage ─
    # ── the probe phase, taken while the stack is UP ────────────────────────
    #
    # The qualify phase ran before the controller touched anything, so the
    # vantage was enumerated rather than trusted. These probes could not run
    # then: a negative measured before the apply is an accurate measurement of
    # the wrong instant, and item 13 requires the negative to be measured
    # against a RUNNING service. So they are taken here — after the apply,
    # before any teardown — and `service_running` is read from the target's own
    # socket table at that moment rather than asserted.
    #
    # Shelling out to the collector is observation, not mutation, and the
    # invariant this runner states is about mutations. See the module docstring
    # for why that narrowing is structural rather than convenient.
    probe_phase = _collect_probe_phase(args)
    evidence["probes"] = probe_phase.get("probes", {})
    if "closed_port_behaviour" in probe_phase:
        evidence["closed_port_behaviour"] = probe_phase["closed_port_behaviour"]

    external = (
        ("external_positive_v6", "positive_v6", True, "tcp/22 over IPv6, THIS target"),
        (
            "external_negative_v6",
            "negative_v6",
            False,
            "the loopback-bound v6 socket, service RUNNING",
        ),
        ("external_v4", "v4_pair", False, "IPv4 negative with its tcp/22 control"),
    )
    for code, key, want_reachable, note in external:
        probe = _probe(evidence, key)
        reachable = bool(probe.get("reachable"))
        control = bool(probe.get("positive_control_fired", True))
        # ABSENCE FAILS. `probe.get("service_running", True)` read an evidence
        # file that simply omitted the key as a running service — an unmeasured
        # negative reading as an enforced one, which is the defect item 13
        # exists to catch arriving through the reader instead of the collector.
        if "service_running" not in probe:
            raise EvidenceIncomplete(
                f"the {key!r} probe carries no `service_running`. A negative "
                "probe against a port where nothing is listening measures an "
                "absent service, not an enforced exposure, and an unmeasured "
                "one must never read as a pass"
            )
        running = bool(probe["service_running"])
        ok = reachable == want_reachable and control and running
        results.record(
            code,
            PASSED if ok else FAILED,
            f"{note}: reachable={reachable} (wanted {want_reachable}), "
            f"positive control fired={control}, service running={running}",
            f"probe:{key}",
        )

    behaviour = evidence.get("closed_port_behaviour")
    results.record(
        "closed_port_behaviour",
        PASSED if behaviour in ("reset", "drop") else FAILED,
        f"target closed-port behaviour recorded as {behaviour!r}",
        "workstation probe",
    )
    # ── items 12 and 16: measured from a vantage INSIDE the source set ─────
    #
    # Both used to be literals emitted by a collector that is outside the set by
    # construction. They are now taken through a restricted jump whose key runs
    # no command on the vantage, so the TCP connection originates inside while
    # nothing executes there.
    #
    # `lane3_inside_vantage.collect` raises a bare `DeploymentFoundationError`
    # from two sites — the harness exiting non-zero, and unreadable JSON — and
    # nothing outside that function can tell them apart. Matching the message is
    # the guesswork every stable code exists to avoid, so the whole region is
    # recorded as `evidence_unreadable`, which is what that member's own
    # definition already covers: "Includes a reader subprocess exiting non-zero:
    # to a destroy decision that is the same fact as malformed output."
    #
    # The mapping table files the non-zero-exit site under `precondition_unfit`
    # instead. That cannot be honoured HERE: `precondition_unfit` asserts the
    # host was never touched, and this call is reached long after the apply.
    # That site's own preconditions are asked above, before any mutation, where
    # the sentence is true.
    with refusal_of(EvidenceUnreadable):
        inside_probe = inside_vantage.collect(
            str(inside_probe_harness()),
            jump=args.inside_vantage,
            target_v4=args.target,
            target_v6=target_v6,
            port=private,
            timeout=args.timeout,
        )
    inside_observed = _observed_from(args, jump_key=args.inside_jump_key)
    seen = inside_vantage.vantages(inside_probe, inside_observed)
    control_ok = inside_vantage.control_is_meaningful(inside_probe)

    # Item 16. ONE receipt row, because `build_receipt` enforces a closed set of
    # sixteen codes and splitting one would be refused — but the SOURCE MODEL is
    # per family, and the detail carries both. Michael's "model inside/outside
    # IPv4 and IPv6 independently" is about which addresses are inside which
    # set, not about the receipt's vocabulary.
    #
    # PASSED requires BOTH families to have reached. A pass on one would be a
    # v4 result wearing a dual-stack claim, and this vantage is exactly the case
    # where that goes wrong: its v4 is on the target's segment and its v6 is not.
    both_reached = all(
        vantage.outcome is inside_vantage.InsideOutcome.REACHED for vantage in seen
    )
    observed_all = all(vantage.observed_source for vantage in seen)
    per_family = "; ".join(
        f"{vantage.family}: outcome={vantage.outcome}, observed as "
        f"{vantage.observed_source or 'NOTHING'} ({vantage.cidr})"
        for vantage in seen
    )
    control_note = (
        "jump-scope control prohibited as expected"
        if control_ok
        else "jump-scope control DID NOT FIRE — a permitopen that refused "
        "everything would look identical to one correctly scoped"
    )
    results.record(
        "private_from_source",
        PASSED if both_reached and observed_all and control_ok else FAILED,
        f"the private port from inside its source set, per family. {per_family}. "
        f"{control_note}",
        *(f"inside:{vantage.family}:{vantage.cidr}" for vantage in seen),
        "requalified-every-run",
    )

    # Item 12, and the branch it fires on is checked rather than assumed.
    endpoint = f"{spec.product}:{private}"
    verdicts = [
        inside_vantage.refusal_fired_for_the_right_reason(
            vantage,
            endpoint_token=endpoint,
            accepted_source_sets=(accepted_source_set,),
        )
        for vantage in seen
    ]
    refused_ok = all(ok for ok, _ in verdicts) and control_ok
    results.record(
        "privileged_vantage_refused",
        PASSED if refused_ok else FAILED,
        "accept_public_exposure_evidence refused a real probe from inside an "
        "accepted source set, on the privileged-vantage branch rather than the "
        "membership one: "
        + "; ".join(
            f"{v.family}={detail}"
            for v, (_, detail) in zip(seen, verdicts, strict=False)
        ),
        f"accepted_source_set:{accepted_source_set}",
    )

    # ── item 8: a SECOND transaction, provoked into a real rollback ─────────
    #
    # Two transactions, deliberately. The first one above is CLEAN and its
    # evidence is what items 1-7 and 9-16 rest on; provoking it would have made
    # every one of them the record of a failed run. This one exists only to
    # execute item 8, and it is the only item that reads it.
    #
    # Nothing here calls `_rollback`. `ExposureTransaction.run()` re-observes,
    # runs `verify_exposure`, and rolls back ITSELF when the report refuses —
    # so the rollback is the system's response to a failure it met, which is
    # the whole distinction item 8 turns on.
    # The seeding goes through the SAME capturing runner the effects use, so the
    # seeder's own unwind — which issues its deletes and ignores whether they
    # worked — is observed rather than assumed.
    seeded: tuple[SeededRule, ...] = ()
    try:
        with controller.watching() as seeding:
            try:
                with refusal_of(HostStateUncertified):
                    seeded = seed_foreign_rules(controller)
            except HostStateUncertified:
                ctx.record_cleanup(
                    disposition_of(
                        "foreign_seed_unwind",
                        [c for c in seeding if "-D" in c.argv],
                    )
                )
                raise

        provoked = ExposureTransaction(spec=spec, effects=effects)
        # Set BEFORE the call: `replace_rules` deletes our owned rules and then
        # inserts, so an arm that fails part way has still changed the chain and
        # still has something to take back out.
        ctx.arm_attempted = True
        armed = provoke_apply_failure(effects, port=private)
        provoked_snapshot = effects.observe()
        seeded_before = observed_foreign(provoked_snapshot, owner=owner)
        refusal = ""
        try:
            provoked.run()
        except DeploymentFoundationError as encountered:
            refusal = str(encountered)

        restored = effects.observe()
        foreign_after = observed_foreign(restored, owner=owner)
        lost = sorted(seeded_before - foreign_after)

        # Four conditions, and the third is the one that stops this passing
        # vacuously. `foreign rules lost: none` reads identically whether five
        # rules were preserved or none existed, so the snapshot is required to
        # have been NON-EMPTY and the count is rendered rather than the word.
        rolled_back = provoked.rolled_back
        preserved = not lost
        non_vacuous = bool(seeded_before)
        provoked_ok = bool(rolled_back) and preserved and non_vacuous and bool(refusal)
        met = refusal[:160] if refusal else "NONE — the run was never provoked"
        results.record(
            "provoked_rollback",
            PASSED if provoked_ok else FAILED,
            (
                "induced an ip6tables DOCKER-USER rule for the descriptor's private "
                f"port {armed.host_port} — a chain that can never fire for IPv6, so "
                "the apply path has no authority to clear it. Met at "
                "`verify_exposure`, which refused and rolled back on its own: "
                f"rolled_back={rolled_back}; refusal={met}; compared against "
                f"{len(seeded_before)} foreign rule(s) across "
                f"{len(seeded)} seeded famil(ies); lost: {lost or 'none'}"
            ),
            "ExposureTransaction.run -> verify_exposure -> _rollback",
            f"provocation:{armed.chain}/{armed.family}/{armed.host_port}",
            *(f"seeded:{rule.family}:{rule.arguments}" for rule in seeded),
        )
    finally:
        # In a `finally` because the seeds are a real change to a shared host and
        # must come back out whether or not the phase above finished. The verdict
        # is already recorded by the time this runs, and `run_cleanup` never
        # raises: a cleanup failure is a fact about the HOST and it lands in the
        # release's `cleanup` and closure, never in an item's status.
        run_cleanup(ctx, controller, effects, seeded)

    # ── item 9: three terms, enforced by build_receipt ──────────────────────
    execution_report = report.descriptor_digest
    results.record(
        "digest_equality",
        PASSED,
        "descriptor == authorized plan == controller execution report "
        f"({descriptor_digest})",
        "build_receipt(require_same_digest)",
    )

    receipt = build_receipt(
        foundation_revision=args.foundation_revision,
        foundation_artifact_digest=args.foundation_artifact,
        authorization_run_id=args.authorization_run,
        authorization_document_digest=args.authorization_doc_digest,
        descriptor_digest=descriptor_digest,
        execution_report_digest=execution_report,
        fixture_digest=str(Digest.of(fixture_bytes)),
        controller_identity=str(controller_identity),
        target=args.target,
        lease_id=lease.authorization_run_id,
        probe_identity=str(evidence.get("vantage", {}).get("address_v4", "")),
        started_at=started,
        finished_at=datetime.now(UTC).isoformat(),
        results=results.all(),
    )

    pathlib.Path(args.receipt_out).write_text(
        json.dumps(receipt.content, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    if args.status_out:
        pathlib.Path(args.status_out).write_text(
            render_status_document(receipt), encoding="utf-8"
        )
    ctx.receipt_digest = receipt.sha256_digest()
    print(f"receipt_digest={ctx.receipt_digest}")
    failed = [r for r in receipt.results if not r.status.satisfies_publication]
    for row in failed:
        print(f"NOT PASSED: {row.code} = {row.status.value}: {row.detail}")
    # A receipt is a terminal outcome whether or not every item passed, so the
    # lease is released here too. `satisfies_publication` decides what may be
    # PUBLISHED; it does not decide whether the run finished with the host.
    record_terminal(ctx, args, TerminalOutcome(receipt_digest=ctx.receipt_digest))
    return EXIT_REFUSED if failed else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="exposure_rehearsal_runner.py",
        description="Execute Lane 3 through the controller and emit a receipt.",
    )
    for flag, help_text in (
        ("--foundation-revision", "exact protected-main commit under test"),
        (
            "--foundation-artifact",
            "digest read from the verified candidate receipt",
        ),
        ("--authorization-run", "Platform CP authorization run id"),
        ("--authorization-doc-digest", "digest of the signed authorization document"),
        ("--controller-identity", "fingerprint of the dedicated controller key"),
        ("--controller-key", "path to the controller private key (a POINTER)"),
        ("--target", "the leased rehearsal target"),
        ("--vm-slot", "the Proxmox SLOT the target occupies, `node/vmid`"),
        ("--candidate-version", "the Foundation candidate version under test"),
        ("--probe-host", "the external vantage the probe phase runs from"),
        ("--inside-vantage", "the vantage INSIDE the accepted source set"),
        ("--inside-jump-key", "private key for the inside vantage jump"),
        ("--observer-user", "the restricted target-side observation user"),
        ("--observer-key", "private key for that observation identity"),
        ("--probe-evidence", "JSON of the external vantage's measurements"),
        ("--descriptor", "the exact rehearsal fixture"),
        ("--receipt-out", "where to write RehearsalReceipt.v1"),
    ):
        parser.add_argument(flag, required=True, help=help_text)
    parser.add_argument("--status-out", default="", help="generated status document")
    parser.add_argument("--deploy-dir", default="/srv/lane3")
    parser.add_argument("--lease-dir", default=None)
    parser.add_argument("--lock-dir", default="/var/lock/dotmac")
    parser.add_argument("--timeout", type=int, default=120)
    # NOT where the release lives. `lease.release_path` derives that from the
    # lease, and `write_release` takes no override, because the store is the
    # ledger and a second location would be a second ledger. This is where a
    # COPY of the stored record is placed for artifact upload, and it is only
    # ever written after the store write has succeeded.
    parser.add_argument("--release-out", default="")
    parser.add_argument(
        "--terminal-evidence-out", default="lane3-terminal-evidence.json"
    )
    parser.add_argument("--principal-audience", default=DEFAULT_PRINCIPAL_AUDIENCE)
    args = parser.parse_args(argv)
    if args.lease_dir is None:
        from dotmac_deployment_foundation.lease import DEFAULT_LEASE_DIR

        args.lease_dir = DEFAULT_LEASE_DIR

    ctx = TerminalContext()
    try:
        return run(args, ctx)
    except DeploymentFoundationError as exc:
        # A TYPED terminal outcome, and only that. There is deliberately no
        # `except Exception` here: an unexpected exception has no member in the
        # closed vocabulary, so it writes no release and the lease stays HELD —
        # the same as a SIGKILL or the runner dying. Absence means held, and
        # collapsing "we refused for a reason we can name" into "the process
        # stopped" is how a killed run comes to authorise a destroy.
        print(f"REFUSED: {exc}", file=sys.stderr)
        member = classify_refusal(
            exc, lease_in_hand=ctx.lease_in_hand, host_mutated=ctx.host_mutated
        )
        if member is None:
            ctx.notes.append(
                f"NO RELEASE WRITTEN: {type(exc).__name__} was raised with no "
                "exact HostLease.v2 in hand — missing, expired, foreign to this "
                "authorization run, or refused before the lease was reached. "
                "There is nothing to discharge and no lease digest to name a "
                "release by, so none is written and the host keeps the standing "
                "it already had. An expired lease stays EXPIRED_HELD, which is "
                "the answer for a holder nobody can ask anything of"
            )
        record_terminal(
            ctx, args, None if member is None else TerminalOutcome(refusal=member)
        )
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
