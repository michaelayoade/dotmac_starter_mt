"""``DeploymentEvidence.v1`` — the record is closed by STRUCTURE, not by a guard.

## What this file is actually proving

The requirement was that evidence must not contain material, verifier hashes,
DSNs, SQL, stdout, stderr or raw exception text. The obvious implementation is a
filter run before each write.

**A filter over a free-form dict is a convention; a closed type is a
structure.** The property asserted here is not "a guard rejects the forbidden
value" — it is **there is no key the forbidden value could be written under**.
Almost every test below is an assertion about the KEY SET rather than about a
value, because that is where the property lives.

`require_no_secrets` is still applied, and is still worth having, but it could
never have been the whole answer: it is a SHAPE detector, and stderr is not
secret-shaped, a DSN inside an exception message is not secret-shaped, and
neither is a fragment of SQL. A guard placed before every write would have passed
a document containing all three while reading in review exactly like one that
worked.

## The failure paths get their own tests

Raw exception text reached the old document through `failure` (assigned from
`str(exc)`), `steps[].detail` and a note reading
``f"evidence could not be written: {exc}"``. All three are failure-path
channels, and failure paths are the least exercised. A success-path-only proof
would have said nothing about any of them.

## Codes, not prose

Assert `exc.value.code`; read the sentence. A module with several refusals has to
be testable on WHICH one fired, and `match=` on a sentence makes the sentence the
contract.
"""

from __future__ import annotations

import json

import pytest
from dotmac_deployment_foundation.deployment_evidence import (
    DEPLOYMENT_EVIDENCE_SCHEMA,
    EVIDENCE_BAD_KIND,
    EVIDENCE_BAD_STANDING,
    EVIDENCE_NOT_A_STEP,
    DeploymentEvidenceV1,
    RunStanding,
    StepEvidenceV1,
    StepStanding,
)
from dotmac_deployment_foundation.engine.plan import StepKind
from dotmac_deployment_foundation.engine.run import (
    DeploymentOutcome,
    Executor,
    StepRecord,
)
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.telemetry import (
    ANNOTATION_DETAIL_NOT_A_TOKEN,
    Annotation,
)

from tests.unit.test_deployment_foundation_execution_binding import (
    _fixture,
    _grant,
    _plan_and_digest,
    evidence_policy,
)

#: The exact key set of the document. Written out LONGHAND rather than derived
#: from the dataclass, deliberately: a derived expectation agrees with the type
#: for every input, including the input where somebody added a `notes` field
#: back. This is the assertion, so it must be able to be wrong.
DOCUMENT_KEYS = {
    "attempt_no",
    "control_plan_digest",
    "descriptor_digest",
    "execution_plan_digest",
    "execution_sequence",
    "failed_step",
    "image_digest",
    "image_reference",
    "manifest_digest",
    "mutated",
    "operation",
    "product",
    "schema",
    "source_revision",
    "standing",
    "steps",
    "strategy",
    "succeeded",
}

STEP_KEYS = {"duration_seconds", "kind", "standing", "target"}

#: The channels that carried raw exception text, prose and stderr before this
#: type existed. Named individually so a reader knows what was removed.
RETIRED_CHANNELS = ("failure", "detail", "notes", "rollback_reason")

#: What the publication gate reads out of a record
#: (`scripts/release_facility.py::_installed_admit_smoke`). Narrowing the
#: document past these breaks the gate that publishes the facility.
GATE_FIELDS = (
    "succeeded",
    "execution_plan_digest",
    "descriptor_digest",
    "control_plan_digest",
)


def _step(standing: StepStanding = StepStanding.OK) -> StepEvidenceV1:
    return StepEvidenceV1(
        kind="acquire_lock", target="host", standing=standing, duration_seconds=0.5
    )


def _evidence(**over) -> DeploymentEvidenceV1:
    kwargs = {
        "product": "dotmac_starter_mt",
        "image_reference": "ghcr.io/dotmac/starter:1.2.3",
        "image_digest": "sha256:" + "a" * 64,
        "source_revision": "0" * 40,
        "manifest_digest": "sha256:" + "b" * 64,
        "execution_plan_digest": "sha256:" + "c" * 64,
        "descriptor_digest": "sha256:" + "d" * 64,
        "control_plan_digest": "f" * 64,
        "execution_sequence": 7,
        "attempt_no": 2,
        "operation": "deploy",
        "strategy": "warm_candidate",
        "standing": RunStanding.SUCCEEDED,
        "succeeded": True,
        "mutated": False,
        "failed_step": "",
        "steps": (_step(),),
    }
    kwargs.update(over)
    return DeploymentEvidenceV1(**kwargs)


# ── the closed key set IS the property ──────────────────────────────────────


def test_the_document_key_set_is_exactly_this_and_nothing_else() -> None:
    assert set(_evidence().as_document()) == DOCUMENT_KEYS


def test_the_step_key_set_is_exactly_this_and_nothing_else() -> None:
    assert set(_evidence().as_document()["steps"][0]) == STEP_KEYS


@pytest.mark.parametrize("channel", RETIRED_CHANNELS)
def test_a_retired_text_channel_has_no_key_at_either_level(channel: str) -> None:
    """The whole point, one channel at a time. There is nowhere to put it."""
    document = _evidence().as_document()
    assert channel not in document
    assert channel not in document["steps"][0]


def test_the_key_set_assertion_would_notice_a_new_key() -> None:
    """Sensitivity. `DOCUMENT_KEYS` is written longhand so it CAN be wrong; this
    proves the comparison is load-bearing rather than tautological."""
    document = _evidence().as_document()
    document["notes"] = ["something a later author added"]
    assert set(document) != DOCUMENT_KEYS


@pytest.mark.parametrize("field", GATE_FIELDS)
def test_the_publication_gate_can_still_read_what_it_reads(field: str) -> None:
    """Closing the document must not close it past the gate that publishes this
    facility. `_installed_admit_smoke` reads these four out of a record; a
    document narrowed to one step's four fields would break it, and would break
    Control's settlement through `execution_sequence`/`attempt_no` as well."""
    document = _evidence().as_document()
    assert field in document
    assert document["execution_sequence"] == 7
    assert document["attempt_no"] == 2


# ── the forbidden values are not representable ─────────────────────────────


def test_an_exception_cannot_be_placed_in_a_step() -> None:
    """The shape that actually happened: `str(exc)` reaching a persisted record.
    There is no field on a step that takes a sentence."""
    with pytest.raises(SpecError) as exc:
        StepEvidenceV1(
            kind="connection to db failed: FATAL password authentication failed",
            target="host",
            standing=StepStanding.FAILED,
            duration_seconds=0.1,
        )
    assert exc.value.code == EVIDENCE_BAD_KIND


def test_a_standing_may_not_be_an_open_string() -> None:
    """An open string here would be the free-text `detail` field under a new
    name, which is how a closed document reopens."""
    with pytest.raises(SpecError) as exc:
        StepEvidenceV1(
            kind="migrate",
            target="host",
            standing="psql: FATAL: role does not exist",  # type: ignore[arg-type]
            duration_seconds=0.1,
        )
    assert exc.value.code == EVIDENCE_BAD_STANDING


def test_the_run_standing_may_not_be_an_open_string() -> None:
    with pytest.raises(SpecError) as exc:
        _evidence(standing="failed because postgres://u:p@h/db refused")
    assert exc.value.code == EVIDENCE_BAD_STANDING


def test_a_step_may_not_be_a_raw_mapping() -> None:
    """A mapping would let any key through, which is the whole thing the
    document closes — so `steps` refuses one even though it would serialise."""
    with pytest.raises(SpecError) as exc:
        _evidence(steps=({"kind": "migrate", "stderr": "psql: FATAL ..."},))
    assert exc.value.code == EVIDENCE_NOT_A_STEP


def test_failed_step_names_a_step_not_a_reason() -> None:
    with pytest.raises(SpecError) as exc:
        _evidence(failed_step="migrate failed: relation already exists")
    assert exc.value.code == EVIDENCE_BAD_KIND


def test_the_document_serialises_with_no_stringify_escape_hatch() -> None:
    """`default=str` is gone from the read-back comparison. This is the property
    that makes its removal safe: everything in the document is already a JSON
    primitive, so a strict `json.dumps` succeeds — and an unexpected object
    would now RAISE rather than be silently turned into its repr."""
    assert json.dumps(_evidence().as_document(), sort_keys=True)


# ── installed vs reconciled_after_commit ───────────────────────────────────


def test_installed_and_reconciled_are_distinguishable_IN_the_document() -> None:
    """Same end state, different histories, and the crash path is exactly when
    someone needs to tell them apart. A reader must not have to reconstruct the
    difference from surrounding context."""
    installed = _evidence(steps=(_step(StepStanding.INSTALLED),)).as_document()
    reconciled = _evidence(
        steps=(_step(StepStanding.RECONCILED_AFTER_COMMIT),)
    ).as_document()
    assert installed["steps"][0]["standing"] == "installed"
    assert reconciled["steps"][0]["standing"] == "reconciled_after_commit"
    assert installed != reconciled


def test_the_standing_vocabulary_is_closed_and_small() -> None:
    """Written longhand: a member appearing or disappearing must be a diff
    somebody reviews, never a constant that silently followed the code."""
    assert {s.value for s in StepStanding} == {
        "ok",
        "installed",
        "reconciled_after_commit",
        "refused",
        "failed",
        "non_fatal",
    }
    assert {s.value for s in RunStanding} == {"succeeded", "refused", "failed"}


# ── the leaf binding to StepKind, proved without importing it ──────────────


@pytest.mark.parametrize("kind", [k.value for k in StepKind])
def test_every_real_step_kind_is_an_acceptable_kind(kind: str) -> None:
    """`deployment_evidence` is a LEAF and deliberately does not import
    `engine.plan` — that import is what put `execution_plan` in a cycle with the
    engine. So the binding to the real vocabulary is proved HERE, in a module
    that may import both, rather than asserted in a docstring."""
    assert (
        StepEvidenceV1(
            kind=kind, target="host", standing=StepStanding.OK, duration_seconds=0.0
        ).kind
        == kind
    )


def test_that_parametrisation_is_not_empty() -> None:
    assert len(list(StepKind)) >= 20


# ── the annotation channel, which left the host ────────────────────────────


def test_an_annotation_refuses_free_text() -> None:
    """`Executor` filled this with `outcome.failure` — `str(exc)` — on the
    failure path, so raw exception text was leaving for the observability
    platform on the path least exercised."""
    with pytest.raises(SpecError) as exc:
        Annotation(
            event="deployment.failure",
            product="p",
            environment="prod",
            deployment_id="d",
            image_digest="sha256:" + "a" * 64,
            git_sha="0" * 40,
            strategy="warm_candidate",
            detail="migrate failed: FATAL: password authentication failed for 'app'",
        ).as_mapping()
    assert exc.value.code == ANNOTATION_DETAIL_NOT_A_TOKEN


def test_an_annotation_admits_a_standing() -> None:
    """The positive control: the refusal above must not be a function that
    refuses everything."""
    sent = Annotation(
        event="deployment.failure",
        product="p",
        environment="prod",
        deployment_id="d",
        image_digest="sha256:" + "a" * 64,
        git_sha="0" * 40,
        strategy="warm_candidate",
        detail=RunStanding.FAILED.value,
    ).as_mapping()
    assert sent["detail"] == "failed"


def test_both_annotation_serialisers_are_guarded() -> None:
    """`as_json` is a second door to the same seam. A guard on one of two doors
    is not a guard."""
    annotation = Annotation(
        event="deployment.failure",
        product="p",
        environment="prod",
        deployment_id="d",
        image_digest="sha256:" + "a" * 64,
        git_sha="0" * 40,
        strategy="warm_candidate",
        detail="psql: FATAL: database does not exist",
    )
    for door in (annotation.as_mapping, annotation.as_json):
        with pytest.raises(SpecError) as exc:
            door()
        assert exc.value.code == ANNOTATION_DETAIL_NOT_A_TOKEN


# ── the FAILURE PATH, end to end on a real executor ────────────────────────


def _refused_run():  # type: ignore[no-untyped-def]
    """A real Executor driven to a real refusal INSIDE the step loop.

    The refusal has to happen in `_run_steps`, not before it. An unauthorized
    plan digest refuses in `run()` ahead of the try block — the outcome does not
    exist yet, so there is nothing to record and the exception propagates. A
    GATE step refusing is the case that produces evidence, and it is also the
    case an operator actually meets: the image named by the descriptor is not on
    the host, `_do_verify_image` raises `PreconditionFailed`, `_run_steps`
    records the step and re-raises, and `run()` returns the outcome.

    This distinction is worth keeping in the fixture rather than the prose: the
    first version of this helper used a digest mismatch and every test built on
    it errored instead of asserting, which reads in CI like the evidence type
    being broken rather than the fixture.
    """
    spec, plan, effects = _fixture()
    effects.present = False  # the image is not on the host
    execution_plan, digest = _plan_and_digest(spec, plan, effects=effects)
    grant = _grant(spec, execution_plan_digest=digest)
    executor = Executor(
        spec,
        effects,
        grant,
        execution_plan=execution_plan,
        sleep=lambda _: None,
        evidence_policy=evidence_policy(),
    )
    outcome = executor.run(plan)
    assert not outcome.succeeded, "the fixture stopped refusing; it proves nothing"
    return outcome


def test_a_refused_run_records_a_standing_and_no_exception_text() -> None:
    """The end-to-end version of everything above. The run really refuses, the
    outcome really holds `str(exc)`, and the DOCUMENT holds neither it nor any
    field it could go in."""
    outcome = _refused_run()
    assert outcome.succeeded is False
    assert outcome.failure, "the in-process diagnostic must still exist"
    document = outcome.as_evidence()
    assert set(document) == DOCUMENT_KEYS
    assert document["standing"] in {"refused", "failed"}
    assert document["succeeded"] is False
    flat = json.dumps(document, sort_keys=True)
    assert outcome.failure not in flat, (
        "the failure sentence reached the persisted document — the closed type "
        "is not closed"
    )


def test_the_diagnostic_survives_in_process_even_though_it_does_not_travel() -> None:
    """ "Diagnostics did not disappear; they stopped being persisted" is a claim,
    so it is asserted. `StepRecord.detail` and `outcome.failure` are what the
    CLI prints; neither has a key in the document."""
    outcome = _refused_run()
    assert isinstance(outcome.failure, str) and outcome.failure
    assert any(isinstance(record.detail, str) for record in outcome.records)
    assert "detail" not in outcome.as_evidence()["steps"][0]


def test_a_refusal_and_a_failure_are_DIFFERENT_standings() -> None:
    """`PreconditionFailed` means nothing was mutated and the caller may re-run
    the identical command; `StepFailed` means state may have changed. Collapsing
    them into "not ok" throws away the one distinction that decides what an
    operator does next."""
    outcome = DeploymentOutcome(plan=_refused_run().plan)
    outcome.records.append(
        StepRecord(StepKind.MIGRATE, "host", False, "boom", 0.1, StepStanding.FAILED)
    )
    outcome.standing = RunStanding.FAILED
    assert outcome.as_evidence()["steps"][0]["standing"] == "failed"
    assert StepStanding.REFUSED.value != StepStanding.FAILED.value


def test_the_schema_is_named_in_the_document() -> None:
    assert _evidence().as_document()["schema"] == DEPLOYMENT_EVIDENCE_SCHEMA


def test_a_refused_run_mutated_nothing() -> None:
    """Keeps the refusal honest: a document is only interesting if the refusal
    it describes really happened before any effect."""
    assert _refused_run().mutated is False


def test_the_belt_is_still_fastened() -> None:
    """`require_no_secrets` runs over the finished document. It is not the
    braces — it cannot see stderr or SQL — but it catches a PERMITTED field
    carrying an impermissible value, which the closed type cannot."""
    with pytest.raises(Exception) as exc:
        _evidence(product="AKIAIOSFODNN7EXAMPLE_secret=hunter2hunter2").as_document()
    assert exc.type.__name__ in {"SecretValueError", "SpecError"}
