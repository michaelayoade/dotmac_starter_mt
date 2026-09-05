"""``HostLeaseRelease.v1`` — the other end of the lease contract, not published.

The type reached the one unrecorded, drifted `0.4.0a1` candidate, but no
admissible or published artifact carries it and no Lane 3 run has exercised it.

`load_lease` already refuses to BEGIN without a lease record. Nothing recorded
the END, so the only evidence a shared host was finished with was the absence of
a running process and a timestamp going by. Both are inferences, and neither is
a record.

## The design: EXPIRY AND RELEASE ARE DIFFERENT FACTS

They are separate members of :class:`HostStanding`, not two readings of one
field, and this is the whole point of the module:

* an **expired** lease means *this run may not continue*;
* a **released** lease means *the host may be destroyed*.

Collapsing them is what would let a crashed run's timeout authorise a wipe. A
run that dies at 03:00 leaves no release; its lease expires at 04:00; and a
destroyer reading only "is the lease live?" would find "no" and take that as
permission. It is not permission — it is the absence of a holder who can say
anything at all, which is precisely when destroying a host is least safe.

So :attr:`HostStanding.EXPIRED_HELD` exists as its own answer, and
:func:`require_release_before_destruction` refuses on it by name. **A crash
before release leaves the VM held.** That is not a bug to route around; it is
the outcome the record exists to produce, and the repair is a human releasing
deliberately, which leaves a record, rather than a timeout doing it silently,
which leaves none.

## A REFUSED run is terminal too

The outcome is discriminated: a receipt digest, or typed terminal-refusal
evidence. Both are terminal and both may release.

A schema that accepted only a receipt would leave the host held forever after
any legitimate refusal — and somebody would then release it by hand, which is
exactly the mechanism this record exists to remove. A record that cannot express
a real outcome does not prevent that outcome; it prevents the outcome being
recorded.

The refusal evidence is a machine TOKEN and never prose, for the reason
`deployment_evidence` gives about standings: a sentence in a durable record is a
channel, and the vocabulary belongs to the lane that raises it. This facility
validates the shape and refuses an empty one; Lane 3 owns which codes exist.

## What binds, and why each

Seven, and dropping any one produces a release that is evidence about something
else:

* ``lease_digest`` — the EXACT lease being released, by content. A release
  naming a target rather than a lease would discharge whichever lease happened
  to be on that target next.
* ``vm_identity`` — the machine. A target name can be re-pointed; the host that
  gets destroyed is a machine.
* ``candidate_version`` / ``candidate_source_revision`` — WHICH artifact ran,
  and the commit it was BUILT FROM. A release from another candidate's run is
  evidence about another run.
* ``runner_revision`` — the commit whose Lane 3 RUNNER executed. A DIFFERENT
  question from the one above, and it used to be the answer to both: the field
  named ``source_revision`` was populated with the runner's SHA, which made a
  record that claims to say which artifact ran actually say which runner ran.
  Two questions, two fields.

  **The publication revision is deliberately absent.** A release is written by
  the Lane 3 runner, and the runner cannot observe which commit will later
  publish — that decision has not been taken when this record is sealed. A field
  its only producer cannot fill truthfully must not exist: it would be populated
  by a guess, a default, or a copy of one of the two above, and each of those
  reads to a destroyer as an established fact.
* ``authorization_run_id`` / ``rehearsal_run_id`` — the decision and the
  execution. `HostLease` already refuses to be self-granted; a release that
  could be self-granted would reopen that at the other end.
* ``outcome`` — receipt or typed refusal, above.
* ``released_at`` / ``released_by`` — when, and by whom. A record with no
  principal cannot be asked anything afterwards.
* ``cleanup`` — what happened to what the lease created. Closed, because
  "cleaned up" is the claim most worth being unable to make vaguely.

## Foundation defines it; Lane 3 writes it

This module emits no file and takes no lock. It is the schema and the refusals,
and the runner is the writer — the same split `HostLease` already has, where
this facility owns the record and Platform CP owns the authorization it
references. A schema the runner cannot emit from what it knows would be two
halves that do not meet, so every field above is something a rehearsal run holds
by the time it is terminal.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Final

from .canonical_plan import canonical_plan_bytes
from .controller_identity import ControllerSshFingerprintV1
from .digest import Digest
from .errors import PreconditionFailed, SpecError
from .lease import (
    DEFAULT_LEASE_DIR,
    HOST_LEASE_SCHEMA,
    HostLease,
    load_lease,
    release_path,
    write_store_record_once,
)

__all__ = [
    "require_release_for_reuse",
    "require_release_for_destruction",
    "load_release",
    "write_release",
    "CLEANUP_DISPOSITIONS",
    "LEASE_RELEASE_SCHEMA",
    "RELEASE_DUPLICATE",
    "RELEASE_FOREIGN",
    "RELEASE_MALFORMED",
    "RELEASE_MISSING",
    "RELEASE_NOT_DESTROYABLE",
    "RELEASE_NOT_TERMINAL",
    "RELEASE_PREMATURE",
    "RELEASE_STALE",
    "TERMINAL_REFUSALS",
    "CleanupDisposition",
    "ControllerSshFingerprintV1",
    "HostClosure",
    "HostLeaseReleaseV1",
    "HostStanding",
    "TerminalOutcome",
    "ReleasingPrincipal",
    "RELEASING_PRINCIPAL_KINDS",
    "TerminalRefusal",
    "host_standing",
    "lease_digest",
    "require_release_before_destruction",
]

LEASE_RELEASE_SCHEMA: Final = "HostLeaseRelease.v1"

#: Stable identifiers. Assert these; read the prose.
RELEASE_MISSING: Final = "lease_release.missing"
RELEASE_STALE: Final = "lease_release.stale"
RELEASE_FOREIGN: Final = "lease_release.foreign"
RELEASE_MALFORMED: Final = "lease_release.malformed"
RELEASE_PREMATURE: Final = "lease_release.premature"
RELEASE_DUPLICATE: Final = "lease_release.duplicate"
RELEASE_NOT_TERMINAL: Final = "lease_release.not_terminal"

#: Refused: a release exists and does not permit destroying this host.
RELEASE_NOT_DESTROYABLE: Final = "lease_release.not_destroyable"

_TOKEN: Final = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")

#: ``node/vmid`` — the Proxmox SLOT. See `HostLeaseReleaseV1.vm_slot`.
_VM_SLOT: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}/[1-9][0-9]{0,8}$")

#: A machine-id: 32 lower-case hex, as systemd writes it.
_MACHINE_ID: Final = re.compile(r"^[0-9a-f]{32}$")


class TerminalRefusal(str, Enum):
    """Why a rehearsal ended without a receipt. CLOSED, and this facility owns it.

    ## Why the set is here rather than in the writing lane

    A schema that validates a code against a set the writer invents is not a
    validation — it is a shape check wearing one. The reader of this record is
    whoever decides whether a host may be destroyed, so the vocabulary it
    branches on belongs with the record, not with the runner's internals.

    ## THE RULE, so the next person derives it the same way

    **Derive the mapping from the SEMANTIC refusal paths — every raise of a
    `DeploymentFoundationError` subclass, `ProvocationError` included — and
    discriminate each one by WHERE IT SITS RELATIVE TO THE FIRST MUTATION,
    never by which file or line it is in.**

    That is the rule rather than the result, and it is here because four
    independent attempts got four different wrong answers, each from the shape
    of the tool rather than the shape of the question:

    * a text search for `raise DeploymentFoundationError` finds the base class
      name and misses every subclass — that is how eight was reached;
    * a scan of one file misses the other two — that is how ten was reached;
    * a summary relayed without its mapping loses which member a site belongs to
      — that is how a member came to look inert when it was not, and then
      genuinely was for a different reason;
    * **an earlier revision of this table filed sites by LINE NUMBER**, and the
      numbers went stale the moment the raises moved. Three of them were
      recorded as "host untouched, safely releasable" while sitting AFTER the
      compose stack had been applied and both filter chains rewritten. A release
      carrying `precondition_unfit` from there would have told a destroyer the
      host was untouched when it was not.

    So the table below is keyed by the QUESTION each refusal answers and by its
    position relative to first mutation. Positions are checked against the code,
    never against a comment.

    ## The mapping

    ==============================================  ========================
    refusal                                         member
    ==============================================  ========================
    an item recorded twice                          `receipt_inconsistent`
    probe evidence unreadable                       `evidence_unreadable`
    a required result absent                        `evidence_incomplete`
    far-end observation unreadable / not JSON       `evidence_unreadable`
    the probe phase RAN and refused                 `probe_refused`
    probe phase emitted no JSON                     `evidence_unreadable`
    no `service_running` recorded                   `evidence_incomplete`
    inside-probe harness/argument/key, PRE-contact  `precondition_unfit`
    inside probe exited non-zero, POST-mutation     `evidence_unreadable`
    inside probe emitted no JSON                    `evidence_unreadable`
    descriptor declares no private port             `precondition_unfit`
    private port names no sources                   `precondition_unfit`
    a controller identity that is not a fingerprint `precondition_unfit`
    the foreign rule could not be seeded            `host_state_uncertified`
    ANY below-lane refusal, LEASE IN HAND           `host_state_uncertified`
    ==============================================  ========================

    ## A member is only ever RECORDED when an exact lease was in hand

    The table above maps a refusal to a member. It does NOT say a release
    carrying that member gets written, and the difference became load-bearing on
    2026-09-04.

    A release discharges a lease. With no exact ``HostLease.v2`` in hand — a
    descriptor that would not parse, a record that is missing, expired, or
    issued for another authorization run — there is nothing to discharge and no
    lease digest to name a release by, so the writing lane writes NOTHING and the
    host keeps the standing it had. An expired lease stays
    :attr:`HostStanding.EXPIRED_HELD`, which is this module's whole reason for
    having that member: a holder nobody can ask anything of is the case where
    destroying a host is least safe, and a terminal record must not quietly
    convert it into something else.

    Two consequences worth stating rather than leaving to be rediscovered:

    * ``ANY below-lane refusal`` above is discriminated by the LEASE, not by
      position past first mutation. A generic failure under the lease is
      ``host_state_uncertified`` even when nothing was mutated: the run owned the
      host and could not establish its state, and *"nothing was attempted"* is a
      different claim from *"nobody can say"*. Only the second is defensible from
      a refusal the lane cannot name, and ``_PERMITTED_CLOSURES`` already keeps
      that member away from ``REUSABLE``.
    * ``a controller identity that is not a fingerprint`` is mapped to
      ``precondition_unfit`` and today the writing lane parses that argument
      BEFORE it loads the lease, so no release can carry it from that site. The
      mapping is still correct about the refusal. Whether the parse should move
      behind ``load_lease`` so the case becomes recordable is OPEN and is not
      decided here: it changes what the writing lane establishes and in which
      order, and that is the lane's ruling to make, not this schema's.

    ## Site 134, settled semantically rather than by line number

    The inside-vantage probe used to be filed under two members at once:
    `PRECONDITION_UNFIT`'s docstring claimed it, and `EVIDENCE_UNREADABLE`'s said
    a reader subprocess exiting non-zero is *"the same fact as malformed output"*
    to a destroy decision. Both could not stand. The ruling splits it by
    position, and the code now matches:

    * **Missing harness, argument, or jump key, detected BEFORE host contact**
      → `PRECONDITION_UNFIT`. `probe_inside_vantage.sh` wraps its `ssh` in
      `|| true` and its `case` falls through totally to `unknown`, so every way
      the vantage can be unavailable is reported as DATA. It exits non-zero only
      on its `${...:?}` guards — a missing positional argument or an unset
      `LANE3_JUMP_KEY` — and those are facts about the INVOCATION, decidable
      from the arguments alone. The runner asks them before it opens a single
      connection, which is where the pole's sentence is true.
    * **The probe subprocess exiting non-zero, AFTER mutation** →
      `EVIDENCE_UNREADABLE`. To a destroy decision that is the same fact as
      malformed output, and it is reached long after the apply, where
      "the host was never touched" is false.
    * **`PROBE_REFUSED` applies ONLY when a probe actually ran and refused.**
      The inside-vantage site is no longer filed under it.

    ## The two subprocess sites still cannot discriminate

    Both the inside-vantage call and the probe phase run an external shell script
    through `subprocess.run(..., check=False)` and raise on `returncode != 0`.
    Nothing anywhere interprets these scripts' exit codes, so neither can tell
    "the jump was unreachable" from "the script had a bad argument". A
    `vantage_unavailable` member would therefore be one the writer **cannot
    correctly populate** — it would have to guess by matching stderr prose, which
    is what every `code` here exists to avoid. An inert member is a code for
    something that cannot happen; a member populated by guesswork is a code that
    says something false.

    What would make it derivable: distinct exit codes from the probe scripts.
    `test_the_two_subprocess_sites_still_cannot_discriminate` fails when that
    lands, so the vocabulary is revisited by a red build rather than by somebody
    remembering.

    ## The granularity is the DESTROY DECISION's, not the runner's

    Three different parse failures are one fact to somebody deciding whether to
    wipe a machine: the refusal was about EVIDENCE rather than about the host.
    A site that exits non-zero and a site that emits bad JSON differ in a way
    nobody deciding about a machine can act on. Per-site detail belongs in Lane
    3's own receipt, which is where a reader diagnosing the RUN will look.

    ## SIX members, and a seventh would have been inert or wrong

    An earlier draft carried `vantage_unavailable`, derived from a site that was
    then correctly remapped — leaving a member with no site that raises it. **A
    member nobody raises is a code for something that cannot happen and a test
    that can never fail**, which is the defect the release-evidence lane caught
    in itself one level up. It is not retained against a refusal that does not
    exist; when one does, it arrives with its site.

    ## The mapping is an obligation

    Every terminal refusal in the writing lane maps to exactly one member. An
    unmapped refusal is an amendment to this enum — a reviewed diff — and never
    a free-text escape.
    """

    #: Evidence existed and could not be read. Probe evidence that will not
    #: parse, a far-end observation that exits non-zero or emits no JSON, a probe
    #: phase with no JSON, and the inside-vantage probe's own two failures.
    #: Includes a reader subprocess exiting non-zero: to a destroy decision that
    #: is the same fact as malformed output.
    EVIDENCE_UNREADABLE = "evidence_unreadable"
    #: Evidence was read and a required fact was absent — "an unmeasured probe is
    #: not a passing one".
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    #: A probe RAN AND REFUSED. Only that: a harness that could not be invoked
    #: measured nothing and is `PRECONDITION_UNFIT`, and a probe whose output
    #: cannot be read is `EVIDENCE_UNREADABLE`.
    PROBE_REFUSED = "probe_refused"
    #: The runner's own record contradicted itself — "overwriting an outcome is
    #: how a failure becomes a pass without anyone deciding to".
    RECEIPT_INCONSISTENT = "receipt_inconsistent"
    #: A PRECONDITION of the rehearsal is unfit, so it could not begin: a
    #: descriptor naming no `private` port, a private port naming no source set,
    #: a controller identity that is not a key fingerprint, or an inside-probe
    #: harness that could not be invoked (a missing argument or an unset jump
    #: key).
    #:
    #: Named for the invariant rather than the instance. The ruling called this
    #: `descriptor_unfit`, which fits the descriptor cases and not the
    #: invocation ones — and the difference between "the descriptor names no
    #: private port" and "no jump key was configured" is not one anyone deciding
    #: whether to wipe a machine can act on. Same granularity rule the rest of
    #: this vocabulary was derived at.
    #:
    #: The pole is UNTOUCHED AND SAFELY RELEASABLE: nothing attempted, nothing
    #: mutated, and re-running against the same fixture fails identically. The
    #: writing lane must therefore ask every one of these BEFORE first mutation;
    #: asked afterwards the sentence is false about the machine, and that is the
    #: defect that put three of them behind an applied compose stack once.
    #:
    #: Because that is an arrangement of lines and lines move, the pole is also
    #: CHECKED rather than trusted: the writing lane degrades a refusal of this
    #: kind to `HOST_STATE_UNCERTIFIED` when a mutation had already been
    #: attempted, so a check that drifts behind the apply produces an inspection
    #: nobody needed instead of a release advertising an unexamined host as
    #: clean. This member is the only one in the vocabulary whose meaning is a
    #: claim about the machine, which is why it is the only one a mutation can
    #: falsify.
    PRECONDITION_UNFIT = "precondition_unfit"
    #: **NOBODY HAS CERTIFIED WHAT STATE THE HOST IS IN.** The generalization of
    #: what was `provocation_unestablished`, which named one instance of the
    #: condition and therefore left the others with no member at all.
    #:
    #: Two cases, and they need the same operator action:
    #:
    #: * a foreign-rule provocation that could not be established — the seeder
    #:   failed part way and a partial unwind was attempted;
    #: * **any refusal the writing lane cannot name, raised while it held an
    #:   exact lease on the host** — a `StepFailed` from a compose apply being
    #:   the case that forced the generalization. That refusal previously had no
    #:   member anywhere in this vocabulary, so a failed apply left the host
    #:   mutated with no record and no closure, on any run where the apply
    #:   failed.
    #:
    #: **The second case does NOT require a mutation to have been attempted**,
    #: and that is the half that reads wrong and is right. Holding the lease
    #: means the run owned the host; a refusal it cannot name means it could not
    #: establish what state that host is in. "Nothing was attempted" and "nobody
    #: can say" are different claims, and a run that can only defend the second
    #: must not report the first.
    #:
    #: **Note the modality: MAY have begun, not DID begin.** The member covers
    #: the case where nobody can say, which is the whole reason it exists. A
    #: writer that PROVED the host untouched raises `PRECONDITION_UNFIT`; one
    #: that merely did not touch it, and cannot account for the failure, must not
    #: borrow that pole, because it asserts a fact about a machine rather than
    #: about this process.
    #:
    #: `PRECONDITION_UNFIT` means *do not touch the machine, fix the input*;
    #: this means *inspect the machine before re-running*. Opposite operator
    #: actions, and one member cannot carry both — which is also why this is not
    #: `PROBE_REFUSED` by elimination: no probe ran.
    HOST_STATE_UNCERTIFIED = "host_state_uncertified"


TERMINAL_REFUSALS: Final[tuple[str, ...]] = tuple(r.value for r in TerminalRefusal)
_DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")


class HostStanding(str, Enum):
    """What a host IS, as three answers rather than one boolean.

    ``EXPIRED_HELD`` is the member that matters. A boolean "is the lease live?"
    has two values and three cases, so one case has to borrow another's answer —
    and the case that gets borrowed is always the crash, because a crashed run
    and a finished one both stop being live.
    """

    #: The lease is live and unreleased. The holder is working.
    HELD = "held"
    #: The lease is past its window and NOBODY RELEASED IT. The run may not
    #: continue and the host may NOT be destroyed: this is the shape a crash
    #: leaves, and it is the one case where nobody can be asked what happened.
    EXPIRED_HELD = "expired_held"
    #: Explicitly released by a terminal run. The host may be destroyed.
    RELEASED = "released"


class HostClosure(str, Enum):
    """What the host may be used FOR after this release. A SECOND AXIS.

    `TerminalRefusal` says why the run ended. This says what the machine may now
    be used for, and the two must not collapse into one: a refusal code that only
    describes is a refusal code an operator can act against.

    The failure this exists to prevent is concrete — a run that seeded a foreign
    rule, failed, and partially unwound, releasing a host the NEXT lease treats
    as clean.
    """

    #: A next lease may take this host as it stands.
    REUSABLE = "reusable"
    #: Something was mutated and the unwind is uncertified. A human looks before
    #: anything else uses it.
    INSPECTION_REQUIRED = "inspection_required"
    #: Fit only for destruction — the state is not one anybody should build on.
    DESTROY_ONLY = "destroy_only"


#: Which closures each terminal refusal may resolve to. ENFORCED BY THE TYPE,
#: not a convention the writer is trusted to honour.
#:
#: Two entries are ruled and the rest are deliberately unconstrained:
#:
#: * `HOST_STATE_UNCERTIFIED` may NEVER advertise the host as generally
#:   reusable. It is the refusal that says nobody has certified what state the
#:   machine is in — a provocation that failed part way with a partial unwind, or
#:   anything raised after mutation may have begun. `INSPECTION_REQUIRED` or
#:   `DESTROY_ONLY`, and never `REUSABLE`.
#: * `PRECONDITION_UNFIT` means untouched and safely releasable, and must be ABLE
#:   to take `REUSABLE` — a constraint that can only ever say no cannot be shown
#:   to permit anything, so the permissive pole is asserted as well.
#:
#: **Constraining the closure does NOT excuse the cleanup axis from answering.**
#: `cleanup` is required on every release whatever the refusal, and reusability
#: stays the INTERSECTION of the two constraints below. A refusal that bounds the
#: closure to the restricted set still has to say what became of what the lease
#: created, because "the state is uncertified" and "something the lease made is
#: still there" are different facts a destroyer reads differently.
#:
#: The others are unconstrained BY RULING rather than by this module's judgement.
#: Narrowing one is a reviewed diff here, with the reason a destroy decision
#: would turn on.
_PERMITTED_CLOSURES: Final[dict[TerminalRefusal, frozenset[HostClosure]]] = {
    TerminalRefusal.HOST_STATE_UNCERTIFIED: frozenset(
        {HostClosure.INSPECTION_REQUIRED, HostClosure.DESTROY_ONLY}
    ),
}

#: The RESTRICTED set, reached by two independent routes.
_RESTRICTED: Final[frozenset[HostClosure]] = frozenset(
    {HostClosure.INSPECTION_REQUIRED, HostClosure.DESTROY_ONLY}
)


class CleanupDisposition(str, Enum):
    """What became of what the lease created. Closed.

    "Cleaned up" is the claim most worth being unable to make vaguely, so there
    is no free-text option and no default.
    """

    PURGED = "purged"
    RETAINED_FOR_INSPECTION = "retained_for_inspection"
    #: Cleanup was not attempted — an honest answer for a refusal that stopped
    #: before creating anything, and a loud one for a run that should have.
    NOT_ATTEMPTED = "not_attempted"
    #: Cleanup was attempted and FAILED. Something the lease created is still
    #: there.
    FAILED = "failed"
    #: Cleanup was attempted and NOBODY CAPTURED THE OUTCOME.
    #:
    #: Its own member rather than borrowing `NOT_ATTEMPTED`'s, which is the
    #: fourth time this session that two values were asked to cover three cases —
    #: after `HostStanding`, the tri-state renderings, and expiry-versus-release.
    #: "We did not try" and "we tried and do not know" are different facts, and
    #: the borrowed answer is always the one that looks safer.
    #:
    #: Live case: `withdraw_foreign_rules` currently ignores its failures
    #: deliberately, so the outcomes exist and are discarded. An uncaptured
    #: cleanup is not a successful one.
    OUTCOME_UNKNOWN = "outcome_unknown"


CLEANUP_DISPOSITIONS: Final[tuple[str, ...]] = tuple(
    d.value for d in CleanupDisposition
)


#: Which closures each CLEANUP outcome may resolve to — a SECOND constraint,
#: composed with the refusal's by intersection rather than replacing it.
#:
#: Failed AND unknown both reach the restricted set, and neither may reach
#: general availability. They are separate members for the reason above, and
#: they land in the same place for a different one: something the lease created
#: is either still there, or nobody can say it is not. A next lease inheriting
#: that host as clean is the same failure in both cases.
_PERMITTED_BY_CLEANUP: Final[dict[CleanupDisposition, frozenset[HostClosure]]] = {
    CleanupDisposition.FAILED: _RESTRICTED,
    CleanupDisposition.OUTCOME_UNKNOWN: _RESTRICTED,
}


#: How a releasing principal proves itself. Closed, and deliberately not "a
#: name": an operator's free text is exactly what this field may not be.
RELEASING_PRINCIPAL_KINDS: Final[tuple[str, ...]] = ("github_actions_workload",)

#: A workload subject, e.g. ``repo:michaelayoade/dotmac_starter_mt:ref:...``.
_SUBJECT: Final = re.compile(r"^[a-z][a-z0-9_.:/-]{7,254}$")


@dataclasses.dataclass(frozen=True, slots=True)
class ReleasingPrincipal:
    """WHO closed the lease, derived from the run rather than typed by a human.

    ``released_by`` was a non-empty string, which anything satisfies — including
    an operator's name, which is precisely what it may not be. The ruling: the
    authenticated Lane 3 workload principal that writes the terminal transition,
    derived from trusted runner/lease identity, never operator free text, and
    never the SSH/controller key fingerprint.

    ``run_binding`` is what makes it DERIVED rather than declared: it must equal
    the release's own ``rehearsal_run_id``, so a principal is tied to the run
    that produced it and cannot be carried over from another.

    **If the writer cannot prove its principal there is no release, and the host
    stays held.** That is the designed outcome rather than a validation error to
    work around — a lease nobody can close is safer than one closed by an
    unprovable claim.
    """

    kind: str
    subject: str
    run_binding: str

    def __post_init__(self) -> None:
        if self.kind not in RELEASING_PRINCIPAL_KINDS:
            raise SpecError(
                f"releasing principal kind {self.kind!r} is not one of "
                f"{list(RELEASING_PRINCIPAL_KINDS)}. An open kind is operator "
                "free text with a schema around it",
                code=RELEASE_MALFORMED,
            )
        if not _SUBJECT.match(str(self.subject)):
            raise SpecError(
                f"releasing principal subject {self.subject!r} is not a workload "
                "subject. A human name is what this field exists to refuse: the "
                "principal that CLOSED the lease is the authenticated workload, "
                "not whoever was watching",
                code=RELEASE_MALFORMED,
            )
        if not str(self.run_binding).strip():
            raise SpecError(
                "a releasing principal with no run binding is a claim rather "
                "than a derivation",
                code=RELEASE_MALFORMED,
            )

    def as_document(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "run_binding": str(self.run_binding),
            "subject": self.subject,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class TerminalOutcome:
    """How the run ended: a receipt, or a typed refusal. Both are terminal.

    Exactly one of the two, checked. A record carrying both would be a run that
    ended twice; one carrying neither is not terminal, and
    :data:`RELEASE_NOT_TERMINAL` says so rather than letting an unfinished run
    release a host.
    """

    #: ``sha256:…`` of the rehearsal receipt, or ``""`` for a refusal.
    receipt_digest: str = ""
    #: A :class:`TerminalRefusal`, or ``None``. CLOSED and owned here — see
    #: that enum for why the vocabulary cannot belong to the writer.
    refusal: TerminalRefusal | None = None

    def __post_init__(self) -> None:
        receipt = str(self.receipt_digest).strip()
        refusal = self.refusal
        if refusal is not None and not isinstance(refusal, TerminalRefusal):
            raise SpecError(
                f"refusal must be a TerminalRefusal, got {type(refusal).__name__}. "
                "An open string is the prose channel under a new name, and this "
                "record is read by something deciding whether to wipe a machine",
                code=RELEASE_MALFORMED,
            )
        if receipt and refusal:
            raise SpecError(
                "a terminal outcome carries a receipt digest AND a refusal "
                f"({receipt}, {refusal.value}). A run ends once",
                code=RELEASE_MALFORMED,
            )
        if not receipt and not refusal:
            raise SpecError(
                "a terminal outcome carries neither a receipt digest nor a "
                "refusal code, so the run is not terminal and must not release "
                "the host. A refused run IS terminal and may release — a schema "
                "that accepted only receipts would hold the host forever after "
                "any legitimate refusal, and somebody would release it by hand",
                code=RELEASE_NOT_TERMINAL,
            )
        if receipt and not _DIGEST.match(receipt):
            raise SpecError(
                f"receipt_digest {receipt!r} is not sha256: + 64 lower-case hex",
                code=RELEASE_MALFORMED,
            )

    @property
    def refused(self) -> bool:
        return self.refusal is not None

    def as_document(self) -> dict[str, Any]:
        return {
            "receipt_digest": str(self.receipt_digest),
            "refusal": self.refusal.value if self.refusal else "",
        }


def lease_digest(lease: HostLease) -> str:
    """The exact lease, by content.

    Through the shared ten-rule core rather than a fifth hand-rolled
    canonicalizer — `canonical_plan_bytes` is parameterised by schema and is
    document-kind-neutral despite the "plan" in its module name, which is
    historical. A local `json.dumps` here would be the copy this package has
    already paid for three times.
    """
    return str(
        Digest.of(
            canonical_plan_bytes(
                lease.as_document(), schema=HOST_LEASE_SCHEMA, path="host_lease"
            )
        )
    )


@dataclasses.dataclass(frozen=True, slots=True)
class HostLeaseReleaseV1:
    """One terminal release of one lease. Written by the runner, defined here."""

    lease_digest: str
    #: ``node/vmid`` — the Proxmox SLOT, e.g. ``dotmacproxmox/102``. REQUIRED.
    #:
    #: An address is the wrong identity here and the sequence is why: the
    #: addresses are exactly what a destroy-and-restore can change, so a record
    #: binding `10.120.120.54` would bind a value that survives the wipe by
    #: coincidence rather than by identity, and could name a different machine
    #: afterwards.
    #:
    #: The SLOT rather than the installation, because a destroyer acts on a
    #: slot: that is the thing that gets wiped. A machine-id names the
    #: installation and is regenerated by a clean reimage, so it identifies what
    #: was there rather than what is about to be destroyed.
    vm_slot: str
    #: The guest's ``/etc/machine-id``, or ``""``.
    #:
    #: A STATED VALUE and never an omission — the same rule
    #: `FoundationExecutionPlanV1.application_profile_digest` follows, where
    #: ``""`` means "this candidate declares no profile" and a default would let
    #: a caller carry the answer without deciding it.
    #:
    #: It is not required because the runner holds an address and would have to
    #: read it off the guest, and a field only some paths can produce pushes a
    #: writer toward the broad handler that produces it. When present it is
    #: bound and checked, which closes the one case the slot alone cannot: a
    #: slot re-provisioned between release and destroy.
    vm_installation_id: str
    candidate_version: str
    #: The commit the CANDIDATE ARTIFACT was built from — what this record has
    #: always claimed to name and did not. Read from the committed
    #: ``CandidateArtifact.v1``, never from the run's own head SHA.
    candidate_source_revision: str
    #: The commit whose Lane 3 RUNNER executed. Its own field because it answers
    #: its own question; folding it into the one above is the conflation this
    #: rename repairs.
    runner_revision: str
    authorization_run_id: str
    rehearsal_run_id: str
    outcome: TerminalOutcome
    released_at: str
    #: The authenticated workload that CLOSED the lease. See `ReleasingPrincipal`.
    released_by: ReleasingPrincipal
    #: The SSH/controller key that MUTATED the host — a different fact from who
    #: closed the lease, kept in its own field rather than folded into the one
    #: above. Two facts, two fields: who touched the host, and who closed it.
    #: Must equal the lease's own controller identity.
    #:
    #: **Named `controller_identity_fingerprint` on BOTH planes.** It was
    #: `host_mutation_evidence` here while `HostLease` already carried a field of
    #: the other name for the same fact — one thing with two names across a
    #: boundary where the two are COMPARED, which is exactly the shape that
    #: invites somebody to conclude they are different facts. The rename is in
    #: the one `0.4.0a1` candidate, but that candidate is unrecorded, drifted and
    #: inadmissible; no published artifact has carried `HostLeaseRelease.v1`.
    #: A published schema would instead require a new version.
    controller_identity_fingerprint: ControllerSshFingerprintV1
    #: What the host may be used for now. See `HostClosure` — a second axis from
    #: why the run ended, and constrained by it.
    closure: HostClosure
    cleanup: CleanupDisposition

    def __post_init__(self) -> None:
        if not _DIGEST.match(str(self.lease_digest)):
            raise SpecError(
                f"lease_digest {self.lease_digest!r} is not sha256: + 64 "
                "lower-case hex. A release names the exact lease BY CONTENT; "
                "one naming a target would discharge whichever lease happened "
                "to be on that target next",
                code=RELEASE_MALFORMED,
            )
        if not _VM_SLOT.match(str(self.vm_slot)):
            raise SpecError(
                f"vm_slot {self.vm_slot!r} is not `node/vmid`. An ADDRESS is "
                "the wrong identity for a record authorising a destroy: the "
                "addresses are exactly what a destroy-and-restore can change, "
                "so an address would bind by coincidence and could name a "
                "different machine after restoration",
                code=RELEASE_MALFORMED,
            )
        installation = str(self.vm_installation_id).strip()
        if installation and not _MACHINE_ID.match(installation):
            raise SpecError(
                f"vm_installation_id {installation!r} is not a machine-id (32 "
                'lower-case hex). Empty is a STATED "not recorded" and is '
                "accepted; a malformed one is not",
                code=RELEASE_MALFORMED,
            )
        for field in (
            "candidate_version",
            "candidate_source_revision",
            "runner_revision",
            "authorization_run_id",
            "rehearsal_run_id",
        ):
            if not str(getattr(self, field)).strip():
                raise SpecError(
                    f"HostLeaseReleaseV1.{field} is empty. Every field here is "
                    "something a destroyer checks before wiping a machine",
                    code=RELEASE_MALFORMED,
                )
        if not isinstance(self.outcome, TerminalOutcome):
            raise SpecError(
                f"outcome must be a TerminalOutcome, got "
                f"{type(self.outcome).__name__}",
                code=RELEASE_MALFORMED,
            )
        if not isinstance(self.released_by, ReleasingPrincipal):
            raise SpecError(
                "released_by must be a ReleasingPrincipal, got "
                f"{type(self.released_by).__name__}. A non-empty string is "
                "satisfied by an operator's name, which is what the ruling "
                "excludes; no provable principal means no release, and the host "
                "stays held",
                code=RELEASE_MALFORMED,
            )
        if str(self.released_by.run_binding) != str(self.rehearsal_run_id):
            raise SpecError(
                f"the releasing principal is bound to run "
                f"{self.released_by.run_binding!r} and this release is for "
                f"{self.rehearsal_run_id!r}. A principal carried over from "
                "another run is a claim rather than a derivation",
                code=RELEASE_FOREIGN,
            )
        if not isinstance(
            self.controller_identity_fingerprint, ControllerSshFingerprintV1
        ):
            raise SpecError(
                "controller_identity_fingerprint must be a "
                "ControllerSshFingerprintV1, got "
                f"{type(self.controller_identity_fingerprint).__name__}. The "
                "controller key that mutated the host is retained as evidence in "
                "its own right — it is simply not the principal that closed the "
                "lease — and it is ESTABLISHED by decoding rather than accepted "
                "by shape",
                code=RELEASE_MALFORMED,
            )
        if not isinstance(self.closure, HostClosure):
            raise SpecError(
                f"closure must be a HostClosure, got {type(self.closure).__name__}",
                code=RELEASE_MALFORMED,
            )
        # TWO independent constraints, composed by intersection. A release must
        # satisfy what its refusal permits AND what its cleanup outcome permits;
        # either alone would let the other's restricted case through.
        refusal = self.outcome.refusal
        constraints: list[tuple[str, frozenset[HostClosure]]] = []
        if refusal is not None and refusal in _PERMITTED_CLOSURES:
            constraints.append((refusal.value, _PERMITTED_CLOSURES[refusal]))
        if self.cleanup in _PERMITTED_BY_CLEANUP:
            constraints.append(
                (f"cleanup {self.cleanup.value}", _PERMITTED_BY_CLEANUP[self.cleanup])
            )
        for why, permitted in constraints:
            if self.closure not in permitted:
                raise SpecError(
                    f"a release with {why!r} may close only into "
                    f"{sorted(c.value for c in permitted)}, not "
                    f"{self.closure.value!r}. Something the lease created is "
                    "either still there or nobody can say it is not, and "
                    "advertising the host as generally reusable is how the next "
                    "lease inherits it as clean",
                    code=RELEASE_MALFORMED,
                )
        if not isinstance(self.cleanup, CleanupDisposition):
            raise SpecError(
                f"cleanup must be a CleanupDisposition, got "
                f"{type(self.cleanup).__name__}. An open string would make "
                "'cleaned up' a claim anyone could phrase any way",
                code=RELEASE_MALFORMED,
            )
        _instant(self.released_at, field="released_at")

    def as_document(self) -> dict[str, Any]:
        return {
            "authorization_run_id": self.authorization_run_id,
            "candidate_version": self.candidate_version,
            "cleanup": self.cleanup.value,
            "lease_digest": self.lease_digest,
            "outcome": self.outcome.as_document(),
            "rehearsal_run_id": self.rehearsal_run_id,
            "released_at": self.released_at,
            "closure": self.closure.value,
            "controller_identity_fingerprint": str(
                self.controller_identity_fingerprint
            ),
            "released_by": self.released_by.as_document(),
            "schema": LEASE_RELEASE_SCHEMA,
            "candidate_source_revision": self.candidate_source_revision,
            "runner_revision": self.runner_revision,
            "vm_installation_id": str(self.vm_installation_id),
            "vm_slot": self.vm_slot,
        }

    def digest(self) -> str:
        return str(
            Digest.of(
                canonical_plan_bytes(
                    self.as_document(),
                    schema=LEASE_RELEASE_SCHEMA,
                    path="lease_release",
                )
            )
        )


def _instant(value: str, *, field: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise SpecError(
            f"HostLeaseReleaseV1.{field} is required", code=RELEASE_MALFORMED
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpecError(
            f"HostLeaseReleaseV1.{field} {value!r} is not an ISO-8601 instant",
            code=RELEASE_MALFORMED,
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def host_standing(
    lease: HostLease,
    release: HostLeaseReleaseV1 | None,
    *,
    now: datetime,
) -> HostStanding:
    """What this host IS. Three answers, and the third is the one that matters.

    A boolean "is the lease live?" has two values for three cases, so one case
    borrows another's answer — and the borrowed one is always the crash.
    """
    if release is not None:
        return HostStanding.RELEASED
    end = datetime.fromisoformat(str(lease.expires_at).replace("Z", "+00:00"))
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return HostStanding.HELD if now < end else HostStanding.EXPIRED_HELD


def require_release_before_destruction(
    lease: HostLease,
    release: HostLeaseReleaseV1 | None,
    *,
    now: datetime,
    vm_slot: str,
    candidate_version: str,
    vm_installation_id: str = "",
    seen_release_digests: frozenset[str] = frozenset(),
) -> HostLeaseReleaseV1:
    """Refuse to destroy a host that was not deliberately released.

    THE function this module exists for. Every refusal below is a way a host
    could otherwise be wiped on an inference.
    """
    if release is None:
        standing = host_standing(lease, None, now=now)
        if standing is HostStanding.EXPIRED_HELD:
            raise PreconditionFailed(
                f"the lease on {lease.target} expired at {lease.expires_at} and "
                "was never released, so this host is EXPIRED_HELD. Expiry means "
                "the run may not continue; it does NOT authorize destroying the "
                "machine. This is the shape a crash leaves, and it is the one "
                "case where nobody can be asked what happened — release it "
                "deliberately, which leaves a record, rather than letting a "
                "timeout do it silently, which leaves none",
                code=RELEASE_MISSING,
            )
        raise PreconditionFailed(
            f"the lease on {lease.target} is live until {lease.expires_at} and "
            "has not been released. Destroying a host out from under a working "
            "holder is the failure the lease exists to prevent",
            code=RELEASE_PREMATURE,
        )

    expected = lease_digest(lease)
    if release.lease_digest != expected:
        raise PreconditionFailed(
            f"the release names lease {release.lease_digest} and the lease in "
            f"hand digests to {expected}. A release for another lease is "
            "evidence about another piece of work",
            code=RELEASE_FOREIGN,
        )
    if str(release.vm_slot) != str(vm_slot):
        raise PreconditionFailed(
            f"the release names slot {release.vm_slot!r} and the slot about to "
            f"be destroyed is {vm_slot!r}. An address can be re-pointed by the "
            "very restoration this authorises; the thing that gets wiped is a "
            "slot",
            code=RELEASE_FOREIGN,
        )
    recorded = str(release.vm_installation_id).strip()
    observed = str(vm_installation_id).strip()
    if recorded and observed and recorded != observed:
        raise PreconditionFailed(
            f"the release was written for installation {recorded!r} and the "
            f"slot now holds {observed!r}. The slot is right and the machine in "
            "it is not the one that was released — a slot re-provisioned "
            "between release and destroy is the one case the slot alone cannot "
            "catch",
            code=RELEASE_FOREIGN,
        )
    if str(release.candidate_version) != str(candidate_version):
        raise PreconditionFailed(
            f"the release is for candidate {release.candidate_version!r} and "
            f"this is {candidate_version!r}. A release from another candidate's "
            "run says nothing about this one",
            code=RELEASE_FOREIGN,
        )
    if str(release.authorization_run_id) != str(lease.authorization_run_id):
        raise PreconditionFailed(
            f"the release references authorization run "
            f"{release.authorization_run_id!r} and the lease was taken under "
            f"{lease.authorization_run_id!r}. `HostLease` already refuses to be "
            "self-granted; a release that could be self-granted would reopen "
            "that at the other end",
            code=RELEASE_FOREIGN,
        )
    # Compared as DECODED DIGESTS, not as text. `ControllerSshFingerprintV1`
    # equality is over the 32 bytes, so this asks whether the release names the
    # same KEY — which is the question — rather than whether two records spell
    # one key the same way. Parsing proves shape; this is where identity is
    # proven, and a well-formed fingerprint of the WRONG key fails HERE.
    if release.controller_identity_fingerprint != lease.controller_identity_fingerprint:
        raise PreconditionFailed(
            f"the release records host mutation by "
            f"{release.controller_identity_fingerprint} and the lease was taken "
            f"by {lease.controller_identity_fingerprint}. A release whose "
            "controller identity is not this lease's controller is bound to "
            "the wrong work",
            code=RELEASE_FOREIGN,
        )
    if str(release.released_by.subject) != str(lease.workload_principal):
        raise PreconditionFailed(
            f"the release was written by principal "
            f"{release.released_by.subject!r} and this lease is held by "
            f"{lease.workload_principal!r}. `released_by` must equal the "
            "workload principal bound into THIS lease — a principal that merely "
            "authenticated is not the party that took the host, and a changed "
            "workload principal requires a newly issued lease rather than a "
            "quietly re-used one",
            code=RELEASE_FOREIGN,
        )
    released = _instant(release.released_at, field="released_at")
    starts = datetime.fromisoformat(str(lease.starts_at).replace("Z", "+00:00"))
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=UTC)
    if released < starts:
        raise PreconditionFailed(
            f"the release is dated {release.released_at}, before the lease "
            f"began at {lease.starts_at}",
            code=RELEASE_STALE,
        )
    # THE CLOSURE MUST PERMIT DESTRUCTION, and an explicit release is not by
    # itself permission. `inspection_required` says a human looks BEFORE anything
    # else uses this host — and destroying it is the one act that makes the
    # inspection impossible. Reading "a release exists" as "the host may be
    # wiped" would collapse the second axis back into the first, which is the
    # whole reason `HostClosure` is separate from the refusal.
    if release.closure is HostClosure.INSPECTION_REQUIRED:
        raise PreconditionFailed(
            f"the release closes this host as {release.closure.value!r}, which "
            "requires a human to look at it. Destroying it is the one act that "
            "makes that inspection impossible, so an explicit release is not by "
            "itself permission to wipe — re-close it as "
            f"{HostClosure.DESTROY_ONLY.value!r} once the inspection is done",
            code=RELEASE_NOT_DESTROYABLE,
        )
    if release.digest() in seen_release_digests:
        raise PreconditionFailed(
            f"this release ({release.digest()}) has already been recorded. A "
            "second release of one lease is either a replay or two runs each "
            "believing they finished the same work",
            code=RELEASE_DUPLICATE,
        )
    return release


# ── persistence: the SAME store `load_lease` reads ─────────────────────────


def write_release(
    release: HostLeaseReleaseV1,
    *,
    target: str,
    directory: str | Path = DEFAULT_LEASE_DIR,
) -> Path:
    """Persist the terminal release BESIDE its lease.

    The same authoritative store, through `lease.release_path`, so the destroy
    gate reads one place. A second ledger would let the gate consult one record
    while the lease lived in another, which is how a swapped lease goes
    unnoticed.

    Refuses to overwrite: a second release of one lease is either a replay or two
    runs each believing they finished the same work, and both are things a reader
    must see rather than have resolved for them.

    **The refusal is ATOMIC, not a check followed by a write.** The store is a
    shared host whose whole premise is that agents contend for the target, and
    the workflow that drives it does not cancel a run in progress — so two
    dispatches against one target overlap by design. A `path.exists()` guard
    leaves a window in which both runs see no file and both write; the second
    then silently overwrites the record of how the first ended, and a destroyer
    acts on the wrong terminal outcome. `os.link` makes creating the name and
    failing on a taken name one syscall, so there is no window to lose.

    **Raises `PreconditionFailed`, and that is a contract rather than an
    accident.** Not `OSError`: the `FileExistsError` from the publish is a store
    primitive's signal, and the meaning of a duplicate RELEASE belongs to this
    module. A caller that catches only `OSError` will not catch this — the
    runner's `record_terminal` promises never to raise, so it must catch this
    type by name.

    **There is deliberately no path override.** A workspace copy for artifact
    upload is a COPY taken after a successful store write, never a second write
    path: the store is the ledger, and a parallel write would be the
    second-ledger defect wearing an evidence costume.
    """
    path = release_path(target, directory=directory)
    try:
        return write_store_record_once(path, release.as_document())
    except FileExistsError as exc:
        # The refusal is raised from the ATOMIC publish, not from a preceding
        # `path.exists()`. Check-then-write leaves a window in which two runs
        # both see no file and both write — the very case this refuses.
        raise PreconditionFailed(
            f"a terminal release already exists at {path}. A second release of "
            "one lease is either a replay or two runs each believing they "
            "finished the same work — neither is resolved by overwriting",
            code=RELEASE_DUPLICATE,
        ) from exc


def load_release(
    target: str, *, directory: str | Path = DEFAULT_LEASE_DIR
) -> HostLeaseReleaseV1 | None:
    """The terminal release for this target, or None.

    **None means HELD, and is not an error.** A host with no release is the
    ordinary state of a host being worked on, and the state a crash leaves — the
    caller's gate decides what that means, because "no release exists" and "this
    release does not authorize" are different facts that must not share an
    answer.
    """
    path = release_path(target, directory=directory)
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SpecError(
            f"the release at {path} is not valid JSON: {exc}", code=RELEASE_MALFORMED
        ) from exc
    if document.get("schema") != LEASE_RELEASE_SCHEMA:
        raise SpecError(
            f"expected {LEASE_RELEASE_SCHEMA} at {path}, got "
            f"{document.get('schema')!r}",
            code=RELEASE_MALFORMED,
        )
    outcome = document.get("outcome") or {}
    refusal = str(outcome.get("refusal") or "")
    return HostLeaseReleaseV1(
        lease_digest=str(document["lease_digest"]),
        vm_slot=str(document["vm_slot"]),
        vm_installation_id=str(document.get("vm_installation_id", "")),
        candidate_version=str(document["candidate_version"]),
        candidate_source_revision=str(document["candidate_source_revision"]),
        runner_revision=str(document["runner_revision"]),
        authorization_run_id=str(document["authorization_run_id"]),
        rehearsal_run_id=str(document["rehearsal_run_id"]),
        outcome=TerminalOutcome(
            receipt_digest=str(outcome.get("receipt_digest", "")),
            refusal=TerminalRefusal(refusal) if refusal else None,
        ),
        released_at=str(document["released_at"]),
        released_by=ReleasingPrincipal(
            kind=str(document["released_by"]["kind"]),
            subject=str(document["released_by"]["subject"]),
            run_binding=str(document["released_by"]["run_binding"]),
        ),
        controller_identity_fingerprint=ControllerSshFingerprintV1.parse(
            document["controller_identity_fingerprint"],
            field=f"the release at {path}: controller_identity_fingerprint",
            code=RELEASE_MALFORMED,
        ),
        closure=HostClosure(str(document["closure"])),
        cleanup=CleanupDisposition(str(document["cleanup"])),
    )


def require_release_for_destruction(
    target: str,
    *,
    directory: str | Path = DEFAULT_LEASE_DIR,
    now: datetime,
    vm_slot: str,
    candidate_version: str,
    vm_installation_id: str = "",
) -> HostLeaseReleaseV1:
    """THE call a destroyer or reimager makes. Reads one store, gates on it.

    Loads the lease and the release from the same directory and applies every
    refusal. A caller that assembled these itself would be free to load the lease
    from one place and the release from another, which is the shape this function
    exists to make unavailable.

    A V1 lease refuses here by refusing to LOAD: it names no workload principal,
    so nothing it says can be bound to a releasing party.
    """
    lease = load_lease(target, directory=directory)
    return require_release_before_destruction(
        lease,
        load_release(target, directory=directory),
        now=now,
        vm_slot=vm_slot,
        candidate_version=candidate_version,
        vm_installation_id=vm_installation_id,
    )


def require_release_for_reuse(
    target: str, *, directory: str | Path = DEFAULT_LEASE_DIR, now: datetime
) -> HostLeaseReleaseV1:
    """The OTHER question, and it has a different answer.

    "May this host be destroyed?" and "may a next lease take it as it stands?"
    are not the same, and a host may legitimately answer yes to the first and no
    to the second — `destroy_only` is exactly that.

    So reuse has its own gate, and it is where `failed` and `outcome_unknown`
    cleanup refuse: what the lease created is either still there or nobody can
    say it is not, and a next lease inheriting that host as clean is the same
    failure either way.
    """
    lease = load_lease(target, directory=directory)
    release = load_release(target, directory=directory)
    if release is None:
        standing = host_standing(lease, None, now=now)
        raise PreconditionFailed(
            f"the host at {target} is {standing.value!r} and has no terminal "
            "release, so nothing has said it may be taken by another lease",
            code=RELEASE_MISSING,
        )
    if release.closure is not HostClosure.REUSABLE:
        raise PreconditionFailed(
            f"the release closes this host as {release.closure.value!r}, not "
            f"{HostClosure.REUSABLE.value!r}. A next lease taking it as it "
            "stands is exactly what that closure withholds — and with cleanup "
            f"{release.cleanup.value!r}, what the lease created is either still "
            "there or nobody can say it is not",
            code=RELEASE_NOT_DESTROYABLE,
        )
    return release
