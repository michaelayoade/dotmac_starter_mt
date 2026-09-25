"""ExecutionGrant — a controller may not execute on an argument.

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

The executor requires an :class:`ExecutionGrant` issued by the V3 authority
path. The former V1 ``authorize`` function is historical and always refuses.
The module-private witness is a review convention, not an unforgeable Python
capability; the real authority boundary is the installed assembly's fixed V2
pair provider, Control consumption, and Foundation's independent checks.

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
from typing import TYPE_CHECKING, Final

from .errors import PreconditionFailed, SpecError
from .provenance import (
    AuthorizationReceiptV2,
    VerifiedAuthorization,
    normalize_digest,
)

if TYPE_CHECKING:
    from .authorization_v3 import ExecutionAuthorityV3Provider

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
#: already records as deliberate: Control declares a `recover` member this
#: facility does not. That asymmetry is known, written down, and honest.
#: Control can authorize an operation this facility cannot name; it could
#: previously authorize one this facility could name and not perform, which is
#: strictly worse.
#:
#: THE ASYMMETRY WAS MEASURED ON 2026-09-04 AND IS SHARPER THAN THE PARAGRAPH
#: ABOVE ADMITS. Read against `dotmac_deployment_control` at the peeled ``a11``
#: tag ``98b2a257f4185ee134b54a0349ad09d76f05286b``:
#:
#:   * Control's vocabulary is ``{deploy, rollback, recover}``. Its own module
#:     docstring says the set is closed so that it cannot *"freeze, sign and
#:     dispatch an authorization the executor is structurally unable to
#:     honour"* — and it now can. ``recover`` went in at ``a10`` on the stated
#:     premise that this facility's ``a5`` was being built against the same
#:     three members; the withdrawal recorded above falsified that premise, and
#:     nothing on Control's side refuses it: `require_operation` accepts
#:     ``recover`` and the dispatch envelope carries it through.
#:   * There is NO recover-specific settlement contract on that side.
#:     ``settle_attempt`` is operation-agnostic — it settles on OUTCOME and
#:     never reads ``operation`` — so there is no recover receipt shape and no
#:     recover verification to build against.
#:
#: What saves this side from a silent admit is one line, and it is worth naming
#: because it was a deliberate repair rather than luck:
#: `provenance.AuthorizationReceipt.__post_init__` READS this constant instead
#: of respelling the pair, so a ``recover`` receipt raises `SpecError` at
#: CONSTRUCTION — before a grant, before a plan, before any effect. The
#: divergence yields an unusable authorization, not an unauthorized execution.
#:
#: **The successor does NOT close this by re-adding the member here.** That
#: repair is the one this annotation exists to prevent: adding ``recover`` back
#: to make a counterparty's vocabulary line up is the same move as adding it to
#: satisfy the ordering rule's letter, and it would again name an act on the
#: strength of something other than an executor for it. The vocabulary
#: divergence is Control's to repair. What the successor owns is the CAPABILITY
#: — authorized failed-production recovery, with its own
#: `RecoveryExecutionPlanV1` (a deployment-shaped plan is not a recovery plan),
#: an authorization binding, the replay coordinate, a signed result Control's
#: existing operation-agnostic settlement can consume, and the three bindings
#: above. WHICH vocabulary then carries the act is an open decision, and it is
#: made by whoever holds that decision rather than by this comment.
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
    receipt: AuthorizationReceiptV2
    v3_provider: ExecutionAuthorityV3Provider
    #: Canonical immutable snapshots of the exact Control pair presented at
    #: issue time. The fixed provider receives these same bytes at consumption,
    #: not caller-supplied replacement documents.
    authorization_material_json: bytes
    dispatch_material_json: bytes

    def __post_init__(self) -> None:
        if self.witness is not _ISSUED:
            raise PreconditionFailed(
                "an ExecutionGrant may only be produced by authorize_v3(). A "
                "hand-built grant is an execution that authorized itself, "
                "which is the exact failure this type exists to make "
                "impossible to write by accident"
            )
        if self.operation not in OPERATIONS:
            raise SpecError(
                f"unknown operation {self.operation!r}; expected one of "
                f"{list(OPERATIONS)}"
            )
        if not isinstance(self.receipt, AuthorizationReceiptV2):
            raise PreconditionFailed(
                "ExecutionGrant requires an attested Control V2 pair; "
                "a V1 receipt cannot authorize execution"
            )
        if self.v3_provider is None:
            raise PreconditionFailed("ExecutionGrant requires its fixed V3 provider")
        if (
            not isinstance(self.authorization_material_json, bytes)
            or not self.authorization_material_json
            or not isinstance(self.dispatch_material_json, bytes)
            or not self.dispatch_material_json
        ):
            raise PreconditionFailed(
                "ExecutionGrant requires the original Control pair"
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
        target binding is made in :func:`authorize_v3`, against a target the
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
    """Historical V1 seam; deliberately cannot issue execution authority.

    V1 parsing remains available for evidence inspection.  Only the V3 path,
    with a Control V2 signed pair and fixed trusted provider, issues grants.
    """
    raise PreconditionFailed(
        "V1 authorization is historical and non-authorizing; provide a "
        "FoundationExecutionPlanV3 and Control AuthorizationReceiptV2 pair"
    )
