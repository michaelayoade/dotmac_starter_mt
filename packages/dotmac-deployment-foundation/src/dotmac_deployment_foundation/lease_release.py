"""``HostLeaseRelease.v1`` — the other end of the lease contract, never built.

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
* ``candidate_version`` / ``source_revision`` — WHICH artifact ran. A release
  from another candidate's run is evidence about another run.
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
import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final

from .canonical_plan import canonical_plan_bytes
from .digest import Digest
from .errors import PreconditionFailed, SpecError
from .lease import HOST_LEASE_SCHEMA, HostLease

__all__ = [
    "CLEANUP_DISPOSITIONS",
    "LEASE_RELEASE_SCHEMA",
    "RELEASE_DUPLICATE",
    "RELEASE_FOREIGN",
    "RELEASE_MALFORMED",
    "RELEASE_MISSING",
    "RELEASE_NOT_TERMINAL",
    "RELEASE_PREMATURE",
    "RELEASE_STALE",
    "TERMINAL_REFUSALS",
    "CleanupDisposition",
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

    **Derive the mapping from the twelve SEMANTIC refusal paths — every raise of
    a `DeploymentFoundationError` subclass, `ProvocationError` included — rather
    than by counting textual `raise DeploymentFoundationError` statements.**

    That is the rule rather than the result, and it is here because three
    independent attempts got three different wrong answers, each from the shape
    of the tool rather than the shape of the question:

    * a text search for `raise DeploymentFoundationError` finds the base class
      name and misses every subclass — that is how eight was reached;
    * a scan of one file misses the other two — that is how ten was reached;
    * a summary relayed without its mapping loses which member a site belongs to
      — that is how a member came to look inert when it was not, and then
      genuinely was for a different reason.

    Counting `raise` statements gives thirteen. One of them is
    ``raise SystemExit(main())``, which is process propagation and not a terminal
    disposition. **Twelve is the mappable set.**

    ## The twelve, mapped


    The first derivation found eight, by scanning `exposure_rehearsal_runner.py`
    for `raise DeploymentFoundationError`. That was a grep's answer. Three more
    raise `ProvocationError` in `lane3_provocation.py` — which SUBCLASSES
    `DeploymentFoundationError`, so they are terminal refusals like any other —
    and two more live in `lane3_inside_vantage.py`, a file neither side had
    scanned. The writing lane's own independent count was ten and was wrong for
    the same reason: *"my own count was made by the shape of a grep rather than
    by the shape of the class hierarchy."*

    Thirteen, by AST over every `raise` of a `DeploymentFoundationError`
    subclass across the three files. The mapping is recorded here because a
    vocabulary whose derivation is not written down is a vocabulary the next
    person re-derives differently:

    ==========================================  ============================
    site                                        member
    ==========================================  ============================
    runner 154   an item recorded twice         `receipt_inconsistent`
    runner 204   probe evidence unreadable      `evidence_unreadable`
    runner 242   a required result absent       `evidence_incomplete`
    runner 274   far-end observation unreadable `evidence_unreadable`
    runner 281   far-end observation not JSON   `evidence_unreadable`
    runner 304   the probe phase refused        `probe_refused`
    runner 311   probe phase emitted no JSON    `evidence_unreadable`
    runner 543   no `service_running` recorded  `evidence_incomplete`
    vantage 134  the probe HARNESS could not run `precondition_unfit`
    vantage 141  inside probe emitted no JSON   `evidence_unreadable`
    provoke 150  foreign rule could not be set  `provocation_unestablished`
    provoke 216  descriptor declares no private `precondition_unfit`
    provoke 236  private port names no sources  `precondition_unfit`
    ==========================================  ============================

    **TWELVE terminal refusals, not thirteen.** There are thirteen `raise`
    statements across the three files and the thirteenth is
    ``raise SystemExit(main())`` — an exit, not a refusal. Stated here so the
    next reader does not go hunting for a mapping that should not exist.

    **Site 134 is not a probe refusal, and its name misled everyone including
    the lane that wrote it.** Reading the SCRIPT rather than the raise settles
    it: `classify()` wraps its `ssh` in ``|| true`` and its `case` falls through
    totally to ``unknown``, so EVERY way the vantage can be unavailable is
    reported as DATA — ``prohibited``, ``silent``, ``unknown`` — and never as a
    failure. That was the point of the three-outcome split: a jump that refuses
    must not be indistinguishable from a closed port. The script therefore exits
    non-zero only on its ``${…:?}`` guards — a missing positional argument, or
    ``LANE3_JUMP_KEY`` unset. So 134 means the probe HARNESS could not be
    invoked: nothing measured, and in the unset-key case nothing attempted.
    That is the precondition pole, not the probe pole.

    ## The granularity is the DESTROY DECISION's, not the runner's

    Three different parse failures are one fact to somebody deciding whether to
    wipe a machine: the refusal was about EVIDENCE rather than about the host.
    Site 274 exits non-zero where 281 emits bad JSON, and that difference is not
    one anyone deciding about a machine can act on. Per-site detail belongs in
    Lane 3's own receipt, which is where a reader diagnosing the RUN will look.

    ## Site 134 and the `vantage_unavailable` question, settled from the code

    The writing lane filed inside-vantage 134 as `vantage_unavailable` — *"the
    jump could not be used at all"* — a genuinely different fact from 304, where
    a probe ran and the target refused it. Those point at opposite
    investigations: one says the target refused something, the other says access
    to the vantage is broken and the target was never asked.

    **The distinction is real and the code cannot currently produce it.** Read
    rather than taken on the label: 134 and 304 are structurally identical.
    Both run an external shell script through `subprocess.run(..., check=False)`
    and both raise on `returncode != 0`, carrying stderr into the message. An
    AST sweep finds three `returncode` comparisons across the two files and every
    one of them is `!= 0`. **Nothing anywhere interprets these scripts' exit
    codes.**

    So 134 collapses "the jump was unreachable", "the probe ran and refused" and
    "the script had a bad argument" into one raise, and so does 304 — the
    conflation is symmetric and belongs to the scripts, not to this vocabulary.

    Adding `vantage_unavailable` on that basis would be worse than leaving it
    out. It would be a member the writer **cannot correctly populate**: it would
    have to guess which kind of failure occurred by matching stderr prose, which
    is the thing every `code` in this package exists to avoid. An inert member is
    a code for something that cannot happen; a member populated by guesswork is a
    code that says something false.

    **What would make seven derivable rather than guessed:** distinct exit codes
    from the probe scripts — a jump-unreachable status separate from a
    probe-refused one — after which both raise sites can discriminate and
    `vantage_unavailable` arrives WITH ITS SITE, which is the amendment path this
    enum's last paragraph describes.
    `test_the_two_subprocess_sites_still_cannot_discriminate` fails when that
    lands, so the vocabulary is revisited by a red build rather than by somebody
    remembering.

    ## SIX members, and a seventh would have been inert or wrong

    An earlier draft carried `vantage_unavailable`, derived from site 274 alone.
    Remapping 274 to `evidence_unreadable` leaves that member with no site that
    raises it, and site 134 cannot supply one for the reason above. **A member
    nobody raises is a code for something that cannot happen and a test that can
    never fail**, which is the defect the release-evidence lane caught in itself
    one level up and asked this lane to check its own half against. It is not
    retained against a refusal that does not exist; when one does, it arrives
    with its site.

    ## The mapping is an obligation

    Every terminal refusal in the writing lane maps to exactly one member. An
    unmapped refusal is an amendment to this enum — a reviewed diff — and never
    a free-text escape.
    """

    #: Evidence existed and could not be read. Runner 204, 274, 281, 311;
    #: inside-vantage 141. Includes a reader subprocess exiting non-zero: to a
    #: destroy decision that is the same fact as malformed output.
    EVIDENCE_UNREADABLE = "evidence_unreadable"
    #: Evidence was read and a required fact was absent. Runner 242, 543 — "an
    #: unmeasured probe is not a passing one".
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    #: A probe refused to run. Runner 304, inside-vantage 134.
    PROBE_REFUSED = "probe_refused"
    #: The runner's own record contradicted itself. Runner 154 — "overwriting an
    #: outcome is how a failure becomes a pass without anyone deciding to".
    RECEIPT_INCONSISTENT = "receipt_inconsistent"
    #: A PRECONDITION of the rehearsal is unfit, so it could not begin.
    #: Provocation 216 and 236 (no `private` port; a private port naming no
    #: source set) and inside-vantage 134 (the probe harness could not be
    #: invoked — a missing argument or an unset jump key).
    #:
    #: Named for the invariant rather than the instance. The ruling called this
    #: `descriptor_unfit`, which fits 216 and 236 and not 134 — and the
    #: difference between "the descriptor names no private port" and "no jump key
    #: was configured" is not one anyone deciding whether to wipe a machine can
    #: act on. Same granularity rule the rest of this vocabulary was derived at.
    #:
    #: The pole is UNTOUCHED AND SAFELY RELEASABLE: nothing attempted, nothing
    #: mutated, and re-running against the same fixture fails identically. In
    #: 134's unset-key case not a single TCP connection is opened. The repair is
    #: fixing an input, never inspecting a machine.
    PRECONDITION_UNFIT = "precondition_unfit"
    #: The provocation could not be established — the seeder failed to place the
    #: foreign rule. Provocation 150.
    #:
    #: THE ONLY REFUSAL WHERE THE HOST WAS MUTATED AND THE MUTATION FAILED.
    #: Partial rollback was attempted, so the machine is in a state nobody has
    #: certified. `PRECONDITION_UNFIT` means *do not touch the machine, fix the
    #: input*; this means *inspect the machine before re-running*. Opposite
    #: operator actions, and one member cannot carry both — which is why this is
    #: not `PROBE_REFUSED` by elimination: no probe ran.
    PROVOCATION_UNESTABLISHED = "provocation_unestablished"


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
#: * `PROVOCATION_UNESTABLISHED` may NEVER advertise the host as generally
#:   reusable. It is the one refusal where the host was mutated and the mutation
#:   failed, with a partial unwind, so the machine is in a state nobody has
#:   certified.
#: * `PRECONDITION_UNFIT` means untouched and safely releasable, and must be ABLE
#:   to take `REUSABLE` — a constraint that can only ever say no cannot be shown
#:   to permit anything, so the permissive pole is asserted as well.
#:
#: The others are unconstrained BY RULING rather than by this module's judgement.
#: Narrowing one is a reviewed diff here, with the reason a destroy decision
#: would turn on.
_PERMITTED_CLOSURES: Final[dict[TerminalRefusal, frozenset[HostClosure]]] = {
    TerminalRefusal.PROVOCATION_UNESTABLISHED: frozenset(
        {HostClosure.INSPECTION_REQUIRED, HostClosure.DESTROY_ONLY}
    ),
}


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


CLEANUP_DISPOSITIONS: Final[tuple[str, ...]] = tuple(
    d.value for d in CleanupDisposition
)


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
    source_revision: str
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
    host_mutation_evidence: str
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
            "source_revision",
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
        if not _DIGEST.match(str(self.host_mutation_evidence)):
            raise SpecError(
                f"host_mutation_evidence {self.host_mutation_evidence!r} is not "
                "a sha256 fingerprint. The controller key that mutated the host "
                "is retained as evidence in its own right — it is simply not the "
                "principal that closed the lease",
                code=RELEASE_MALFORMED,
            )
        if not isinstance(self.closure, HostClosure):
            raise SpecError(
                f"closure must be a HostClosure, got {type(self.closure).__name__}",
                code=RELEASE_MALFORMED,
            )
        refusal = self.outcome.refusal
        permitted = _PERMITTED_CLOSURES.get(refusal) if refusal else None
        if permitted is not None and self.closure not in permitted:
            raise SpecError(
                f"a {refusal.value!r} release may close only into "
                f"{sorted(c.value for c in permitted)}, not "
                f"{self.closure.value!r}. That refusal is the one where the host "
                "was MUTATED and the mutation FAILED, with a partial unwind, so "
                "the machine is in a state nobody has certified — advertising it "
                "as generally reusable is how the next lease inherits it as clean",
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
            "host_mutation_evidence": str(self.host_mutation_evidence),
            "released_by": self.released_by.as_document(),
            "schema": LEASE_RELEASE_SCHEMA,
            "source_revision": self.source_revision,
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
    if str(release.host_mutation_evidence) != str(
        lease.controller_identity_fingerprint
    ):
        raise PreconditionFailed(
            f"the release records host mutation by "
            f"{release.host_mutation_evidence} and the lease was taken by "
            f"{lease.controller_identity_fingerprint}. A release whose "
            "host-mutation evidence is not this lease's controller is bound to "
            "the wrong work",
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
    if release.digest() in seen_release_digests:
        raise PreconditionFailed(
            f"this release ({release.digest()}) has already been recorded. A "
            "second release of one lease is either a replay or two runs each "
            "believing they finished the same work",
            code=RELEASE_DUPLICATE,
        )
    return release
