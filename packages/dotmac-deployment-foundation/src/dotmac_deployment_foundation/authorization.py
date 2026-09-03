"""``ExecutionGrant.v1`` — a controller may not execute on an argument.

Before this module, `dotmac-deploy deploy --execute` ran a real deployment
against a real host. The entire distance between "print a plan" and "mutate
production" was one boolean flag that the caller supplied to itself.

That is an **advisory** authorization: the tool asks whether you meant it, and
the answer is whatever you typed. It is not a control, because the party being
restrained is the party answering the question.

## What "insufficient by construction" means here

The requirement is not that `--execute` be *accompanied* by a check. A check
placed next to the flag is convention: it holds until someone adds a second
entry point, calls `Executor` directly from a script, or writes a helper that
forgets. All three have happened in this codebase's history.

Instead the seam itself is closed. :class:`~.engine.run.Executor` cannot be
CONSTRUCTED without an :class:`ExecutionGrant`, and an `ExecutionGrant` cannot
be constructed without the module-private witness that only :func:`authorize`
holds. So there is no path from a flag to a mutation that does not pass through
verification — not because every caller remembers to check, but because a
caller who skips it has nothing to pass.

The witness is deliberately crude. It does not stop a determined caller from
importing `_ISSUED`; nothing in Python can. What it does is make the bypass
**one grep and one obviously-wrong import**, rather than an omission that looks
exactly like ordinary code. A guard whose circumvention is invisible in review
is not a guard.

## Deploy and rollback are separately authorized

`operation` is bound into the grant and into the receipt Control issues, and
`run()` refuses a rollback grant while `rollback()` refuses a deploy grant.

This is not symmetry for its own sake. A rollback authorized under the same
grant as its deploy means a compromised deploy authorization can also **erase
its own evidence** — deploy something, then roll back to hide it, on one
approval. Separating them means the second act needs a second decision from
Control, which is the only party that can refuse it.

Note what is NOT split: the automatic recovery inside a failed `run()`. There
isn't one — `Executor.run` does not call `rollback` internally, it returns the
failure on the outcome. So "rollback" here always means a deliberate, separately
requested operation, and there is no in-deploy repair path that a strict reading
would accidentally forbid.

## This module decides nothing about approval

Same line `provenance.py` draws, for the same reason. Control owns whether a
deployment *should* happen. This owns whether the thing about to run is the
thing that was authorized — an equality check over digests, target and
operation. A receipt that approves a different descriptor is not permission; it
is evidence that two things drifted apart.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Final

from .errors import PreconditionFailed, SpecError
from .provenance import AuthorizationReceipt, VerifiedAuthorization, normalize_digest

__all__ = [
    "OPERATIONS",
    "ExecutionGrant",
    "authorize",
]

#: The operations Control can authorize. An open string would let a caller
#: invent an operation nobody wrote a policy for.
#:
#: ``recover`` WAS a member for one commit and is WITHDRAWN. That reversal is
#: the record, so read it before adding it back.
#:
#: The ordering argument was applied correctly and to the wrong half. `recover`
#: was added once `recovery_execution.py` existed — an executor first, then the
#: vocabulary — and the executor is real. What it executes is a restore
#: REHEARSAL: `RESTORE_PROCEDURE` step 1 creates a FRESH, ISOLATED cluster that
#: must not be the product's, and the terminal verdict destroys it. That is not
#: what `recover` means to the party asking for it. An operator naming a
#: `recover` operation is asking to recover a FAILED PRODUCTION SYSTEM, which
#: needs a captured prestate, the failed system's own observed state, and a
#: desired poststate — none of which this executor takes or could take.
#:
#: So the member named an operation this facility could not perform, which is
#: the exact defect the ordering rule exists to prevent, arrived at by
#: satisfying the rule's letter. An executor existing is not the test; an
#: executor for THE NAMED ACT is.
#:
#: Building the authorization chain around it anyway would have been worse than
#: the gap. A grant, a replay coordinate, a Control settlement and a signed
#: result wrapped around an isolated rehearsal is a chain whose every link is
#: correct and whose SUBJECT is the wrong act — and it would read as done.
#:
#: This returns the vocabulary to the state `0.3.0a5`'s candidate receipt
#: already records as deliberate: Control 0.1.0a10 declares a `recover` member
#: this facility does not. That asymmetry is known, written down, and honest.
#: Control can authorize an operation this facility cannot name; it could
#: previously authorize one this facility could name and not perform, which is
#: strictly worse.
#:
#: The successor is a7: authorized failed-production recovery, with its own
#: `RecoveryExecutionPlanV1` (a deployment-shaped plan is not a recovery plan),
#: an `ExecutionGrant`, the replay coordinate, Control settlement, a signed
#: result, and the three bindings above. `recover` is re-added THEN, by that
#: change, against an executor for the act it names.
#:
#: The two are mutually non-authorizing. A deploy approval that also permitted
#: the rollback would let one decision make a change and erase it. Each is its
#: own consent conversation, which is the same reason Control gives for keeping
#: them separate words.
OPERATIONS: Final[tuple[str, ...]] = ("deploy", "rollback")


class _Witness:
    """Proof that :func:`authorize` built this value, not a caller."""

    __slots__ = ()


_ISSUED: Final = _Witness()


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionGrant:
    """Permission for ONE operation on ONE descriptor against ONE target.

    Every field is part of the binding. Dropping any one of them produces a
    grant that is reusable somewhere it was never meant to apply:

    - without `operation`, a deploy approval also authorizes the rollback that
      erases it;
    - without `descriptor_digest`, an approval for a reviewed descriptor
      authorizes an edited one;
    - without `target`, an approval for staging authorizes production;
    - without `execution_plan_digest`, an approval for a descriptor authorizes
      any plan derived from it — and one descriptor yields a different plan per
      target and per operation, so that is not the same permission at all.
    """

    #: Positional and first, with no default, so a hand-built grant cannot be
    #: mistaken for an ordinary constructor call in review.
    witness: _Witness
    operation: str
    descriptor_digest: str
    target: str
    #: `ExecutionPlanDigestV1`, carried FROM the receipt so the executor cannot
    #: be handed an authorized digest that came from anywhere else. This is
    #: what makes an unbound executor unconstructable rather than merely
    #: discouraged: every `Executor` has a grant, and every grant has this.
    execution_plan_digest: str
    #: Control's replay coordinate, carried FROM the receipt so host
    #: consumption and the execution report cannot source it anywhere else.
    execution_sequence: int
    attempt_no: int
    receipt: AuthorizationReceipt

    def __post_init__(self) -> None:
        if self.witness is not _ISSUED:
            raise PreconditionFailed(
                "an ExecutionGrant may only be produced by authorize(). A "
                "hand-built grant is an execution that authorized itself, "
                "which is the exact failure this type exists to make "
                "impossible to write by accident"
            )
        if self.operation not in OPERATIONS:
            raise SpecError(
                f"unknown operation {self.operation!r}; expected one of "
                f"{list(OPERATIONS)}"
            )

    def require(self, *, operation: str, descriptor_digest: str) -> None:
        """Refuse unless this grant covers exactly this work.

        Re-checked at the point of use rather than trusted from construction.
        The grant is built early — while a plan is being assembled — and used
        later, and "the descriptor has not changed since we were authorized"
        is precisely the property a long-running process cannot assume.

        **Target is deliberately not re-checked here.** The executor has no
        independent notion of which host it is pointed at — it mutates through
        an injected `Effects` — so a target comparison at this point could only
        compare the grant against itself and would pass unconditionally. That
        is worse than no check: it reads in a diff exactly like a real one. The
        target binding is made once, in :func:`authorize`, against a target the
        CALLER states independently of the receipt.
        """
        wanted = normalize_digest(
            descriptor_digest, where="ExecutionGrant.require.descriptor_digest"
        )
        if self.operation != operation:
            raise PreconditionFailed(
                f"this grant authorizes {self.operation!r}, not {operation!r}. "
                f"Each of {list(OPERATIONS)} is authorized separately: a deploy "
                "approval that also permitted the rollback would let one "
                "decision both make a change and erase it, and one that also "
                "permitted a recover would reach a path that creates clusters "
                "and destroys targets"
            )
        if self.descriptor_digest != wanted:
            raise PreconditionFailed(
                f"the authorized descriptor ({self.descriptor_digest}) is not "
                f"the descriptor in hand ({wanted}). Something changed between "
                "authorization and execution, and executing would run what was "
                "not reviewed"
            )


def authorize(
    *,
    verified: VerifiedAuthorization,
    operation: str,
    descriptor_digest: str,
    target: str,
    now: datetime,
) -> ExecutionGrant:
    """Turn ATTESTED terms into permission to run, or refuse.

    Takes a :class:`~.provenance.VerifiedAuthorization`, never a bare
    `AuthorizationReceipt`. A receipt is a structurally complete document; it
    becomes verified terms only by passing through an injected
    `AuthorizationVerifier`, and requiring the verified type here is what stops
    a caller parsing a JSON file straight into an execution.

    The ONLY issuer of :class:`ExecutionGrant`. Every refusal below is a
    mismatch between what Control authorized and what the caller is holding —
    never a judgement about whether the approval should have been granted,
    which belongs to Control and is not re-litigated here.

    `target` must be stated by the caller INDEPENDENTLY of the receipt — the
    CLI takes it from `--target`, not from `receipt.target_ref`. Deriving it
    from the receipt would make the comparison below compare the receipt with
    itself and pass for every input, which is the shape of a check that has
    stopped checking.
    """
    if operation not in OPERATIONS:
        raise SpecError(
            f"unknown operation {operation!r}; expected one of {list(OPERATIONS)}"
        )
    wanted = normalize_digest(descriptor_digest, where="authorize.descriptor_digest")
    receipt = verified.receipt
    # Time first, before any equality check. An expired approval is refused for
    # being expired rather than for whichever digest happens to disagree — and
    # if every digest agrees, an expired approval must still refuse. `now` is
    # supplied by the caller because nothing in this facility reads a clock.
    receipt.require_live(now=now)

    if receipt.operation != operation:
        raise PreconditionFailed(
            f"Control authorized {receipt.operation!r} but {operation!r} was "
            "requested. Ask Control for a receipt naming this operation — a "
            "deploy approval is not a rollback approval, and neither is a "
            "recovery approval"
        )
    if receipt.descriptor_digest_normalized != wanted:
        raise PreconditionFailed(
            f"the receipt authorizes descriptor "
            f"{receipt.descriptor_digest_normalized} but the descriptor in "
            f"hand is {wanted}. This is not an approval for this deployment"
        )
    if receipt.target_ref != target:
        raise PreconditionFailed(
            f"the receipt authorizes target {receipt.target_ref!r}, not " f"{target!r}"
        )
    return ExecutionGrant(
        _ISSUED,
        operation=operation,
        descriptor_digest=wanted,
        target=target,
        execution_plan_digest=receipt.execution_plan_digest_normalized,
        execution_sequence=int(receipt.execution_sequence),
        attempt_no=int(receipt.attempt_no),
        receipt=receipt,
    )
