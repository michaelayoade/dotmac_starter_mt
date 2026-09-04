"""``DeploymentEvidence.v1`` — a CLOSED record of what a run did.

## Why this is a type and not a guard

The instruction that produced this module says evidence must not contain
material, verifier hashes, DSNs, SQL, stdout, stderr or raw exception text. The
obvious implementation is a filter that inspects the document before each write.

**A filter over a free-form dict is a convention; a closed type is a
structure.** The property worth having is not *"a guard rejects the forbidden
value after the fact"* — it is *"there is no key the forbidden value could be
written under"*. `ConcernBinding` in `application_profile.py` makes the same
argument for the same reason, and `execution_bindings.ExecutionBindings` makes
it for injected callables.

The distinction is not academic here. `secrets_guard.require_no_secrets` is a
SHAPE detector: it finds secret-*shaped* values. Stderr is not secret-shaped. A
DSN inside an exception message is not secret-shaped. Neither is a fragment of
SQL. A guard placed before every write would have passed a document containing
all three, while reading in review exactly like a guard that worked.

`require_no_secrets` is still applied, as the belt: a closed field set stops a
forbidden KIND of value, and the shape detector stops a permitted field carrying
an impermissible one. Neither substitutes for the other.

## What was in the document before, and where it went

The previous free-form `as_evidence()` mapping carried three open text channels,
and raw exception text reached all three:

* ``failure`` — assigned from ``str(exc)``;
* ``steps[].detail`` — free prose, twelve of the seventeen step handlers built
  it with an f-string interpolating live values;
* ``notes`` — including, literally,
  ``f"evidence could not be written: {exc}"``.

None survives. `failure` and `detail` are replaced by a CLOSED standing
vocabulary, and `notes` is gone from the document entirely.

**Diagnostics did not disappear; they stopped being persisted.** An operator
still needs the sentence, and the CLI still prints it. The separation is the
point: evidence is a durable record that travels, is read back, and is compared;
a diagnostic is ephemeral operator output. Putting the second inside the first is
how stderr comes to live in a signed record, and it is why the failure paths
matter more than the success paths — a failing run is where raw text appears and
is the path least exercised.

## What CANNOT be removed, and why the closed set is not smaller

Four fields are read by the publication gate
(`scripts/release_facility.py::_installed_admit_smoke`): ``succeeded``,
``execution_plan_digest``, ``descriptor_digest`` and ``control_plan_digest``.
Two more are Control's replay coordinate — ``execution_sequence`` and
``attempt_no`` — echoed verbatim and never derived, which is what lets Control
place a re-executed run against its target's high-water mark. Narrowing the
document to the four fields a single STEP reports would break settlement and the
gate at once.

So the closed set is the union of: the binding terms a reader must be able to
check, the replay coordinate, and per-step standings. Nothing free-text.

## ``installed`` and ``reconciled_after_commit`` are DIFFERENT standings

Same end state, different histories, and the document says which. A run that
installed a one-time effect and a run that found it already present because a
previous attempt died after the commit point are not the same event, and the
crash path is exactly when somebody needs to tell them apart. A reader who has to
reconstruct the difference from surrounding context is reading a document that
did not record it.

## A LEAF module, deliberately

It imports `errors` and `secrets_guard` and nothing else — in particular not
`engine.plan`, which would make it part of the `execution_plan` ↔ `engine` cycle
that `RecoveryExecutionPlanV1` uncovered. So ``kind`` is validated as a
machine-shaped token rather than against `StepKind`, and the binding to that enum
is proved in the test file, which may import both.
"""

from __future__ import annotations

import dataclasses
import re
from enum import Enum
from typing import Any, Final

from .errors import SpecError
from .secrets_guard import require_no_secrets

__all__ = [
    "DEPLOYMENT_EVIDENCE_SCHEMA",
    "EVIDENCE_BAD_KIND",
    "EVIDENCE_BAD_STANDING",
    "EVIDENCE_NOT_A_STEP",
    "DeploymentEvidenceV1",
    "RunStanding",
    "StepEvidenceV1",
    "StepStanding",
]

DEPLOYMENT_EVIDENCE_SCHEMA: Final = "DeploymentEvidence.v1"

#: Stable identifiers for this module's refusals. Assert these; read the prose.
EVIDENCE_BAD_KIND: Final = "deployment_evidence.bad_kind"
EVIDENCE_BAD_STANDING: Final = "deployment_evidence.bad_standing"
EVIDENCE_NOT_A_STEP: Final = "deployment_evidence.not_a_step"

#: A step kind is a machine token, never a sentence. This is what stops the
#: old free-text ``detail`` re-entering through the one remaining string field
#: on a step: prose does not match, and an exception message never matches.
_TOKEN: Final = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class StepStanding(str, Enum):
    """What a step's outcome WAS. Closed, and small on purpose.

    An open string here would be the free-text ``detail`` field wearing a new
    name — which is exactly how a closed document reopens.

    ``INSTALLED`` and ``RECONCILED_AFTER_COMMIT`` are the two that must never be
    collapsed: a one-time effect this run performed, versus one a previous
    attempt performed before dying after its commit point. Same end state,
    different history, and the crash path is when the difference is needed.
    """

    OK = "ok"
    INSTALLED = "installed"
    RECONCILED_AFTER_COMMIT = "reconciled_after_commit"
    REFUSED = "refused"
    FAILED = "failed"
    #: A deliberate, documented fail-open — image retention is the live case.
    #: Named rather than reported as OK, because "it worked" and "it did not
    #: work and we decided that was acceptable" are different facts.
    NON_FATAL = "non_fatal"


class RunStanding(str, Enum):
    """What the RUN was, as one closed word.

    Replaces the free-text ``failure`` field. A reader who needs the sentence
    reads the CLI output; a reader who needs to decide something reads this.
    """

    SUCCEEDED = "succeeded"
    #: A gate refused before anything was mutated.
    REFUSED = "refused"
    #: A step ran and failed. State may have changed.
    FAILED = "failed"


@dataclasses.dataclass(frozen=True, slots=True)
class StepEvidenceV1:
    """One step, as four facts and no prose."""

    kind: str
    target: str
    standing: StepStanding
    duration_seconds: float

    def __post_init__(self) -> None:
        if not _TOKEN.match(str(self.kind)):
            raise SpecError(
                f"step kind {self.kind!r} is not a machine token. A step kind "
                "names an act from a closed vocabulary; a sentence here would "
                "be the free-text `detail` field this type exists to remove",
                code=EVIDENCE_BAD_KIND,
            )
        if not isinstance(self.standing, StepStanding):
            raise SpecError(
                f"step standing must be a StepStanding, got "
                f"{type(self.standing).__name__}. An open string is the old "
                "free-text channel under a new name",
                code=EVIDENCE_BAD_STANDING,
            )

    def as_document(self) -> dict[str, Any]:
        return {
            "duration_seconds": round(float(self.duration_seconds), 3),
            "kind": self.kind,
            "standing": self.standing.value,
            "target": str(self.target),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class DeploymentEvidenceV1:
    """The whole record. Every field is here because a reader must check it.

    There is no ``failure``, no ``detail`` and no ``notes``. That is the
    structure, not an omission — see the module docstring.
    """

    product: str
    image_reference: str
    image_digest: str
    source_revision: str
    manifest_digest: str
    execution_plan_digest: str
    descriptor_digest: str
    control_plan_digest: str
    execution_sequence: int
    attempt_no: int
    operation: str
    strategy: str
    standing: RunStanding
    succeeded: bool
    mutated: bool
    #: The step that ended the run, or ``""``. A token, never a sentence.
    failed_step: str
    steps: tuple[StepEvidenceV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.standing, RunStanding):
            raise SpecError(
                f"run standing must be a RunStanding, got "
                f"{type(self.standing).__name__}",
                code=EVIDENCE_BAD_STANDING,
            )
        if self.failed_step and not _TOKEN.match(str(self.failed_step)):
            raise SpecError(
                f"failed_step {self.failed_step!r} is not a machine token. This "
                "field names WHICH step ended the run; the reason it ended is a "
                "standing, and the sentence is operator output",
                code=EVIDENCE_BAD_KIND,
            )
        for step in self.steps:
            if not isinstance(step, StepEvidenceV1):
                raise SpecError(
                    f"steps carries a {type(step).__name__}, not a "
                    "StepEvidenceV1. The typed record is the contract; a "
                    "mapping would let any key through, which is the whole "
                    "thing this document closes",
                    code=EVIDENCE_NOT_A_STEP,
                )

    def as_document(self) -> dict[str, Any]:
        """The evidence document. A closed key set, by construction."""
        document = {
            "attempt_no": int(self.attempt_no),
            "control_plan_digest": str(self.control_plan_digest),
            "descriptor_digest": str(self.descriptor_digest),
            "execution_plan_digest": str(self.execution_plan_digest),
            "execution_sequence": int(self.execution_sequence),
            "failed_step": str(self.failed_step),
            "image_digest": str(self.image_digest),
            "image_reference": str(self.image_reference),
            "manifest_digest": str(self.manifest_digest),
            "mutated": bool(self.mutated),
            "operation": str(self.operation),
            "product": str(self.product),
            "schema": DEPLOYMENT_EVIDENCE_SCHEMA,
            "source_revision": str(self.source_revision),
            "standing": self.standing.value,
            "steps": [step.as_document() for step in self.steps],
            "strategy": str(self.strategy),
            "succeeded": bool(self.succeeded),
        }
        # THE BELT, behind the structure. A closed field set stops a forbidden
        # KIND of value; this stops a permitted field carrying an impermissible
        # one — a material name that turned out to be a value, say. Neither
        # substitutes for the other, and running it here rather than at each
        # write site means no write path can be the one that forgot.
        require_no_secrets(document, source="deployment evidence")
        return document
