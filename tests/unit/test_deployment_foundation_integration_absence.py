"""`IntegrationSurfaceAbsenceProofV1` — absence as a positive proven claim.

## Four states, and none is the absence of the others

**bound** (a provider answers) · **not yet implemented** (owed and missing) ·
**inapplicable** (refused by ruling) · **absent-proven** (this type). Collapsing
any pair is the two-values-for-three-cases shape; here it is four.

## It satisfies the concern, and that is forced rather than preferred

13/13 is required before a candidate is built. If a proven absence could not
satisfy a concern, a product with genuinely no integration surface could never
reach 13/13 — and an unmeetable gate gets weakened or waived rather than met.

**But only when ESTABLISHED, never when merely well-formed.** That is what keeps
"the gate is reachable" from becoming "the gate is bypassable", and it is why
`satisfies()` compares against an independently computed inventory digest rather
than trusting what construction accepted.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest
from dotmac_deployment_foundation import application_profile
from dotmac_deployment_foundation.application_profile import (
    ABSENCE_INVENTORY_INCOMPLETE,
    ABSENCE_NOT_ABSENT,
    ABSENCE_UNESTABLISHED,
    ABSENCE_UNREGISTERED_SURFACE,
    ABSENCE_WRONG_CONCERN,
    INTEGRATION_ABSENCE_SCHEMA,
    INTEGRATION_SURFACE_FAMILIES,
    FoundationConcern,
    IntegrationSurfaceAbsenceProofV1,
)
from dotmac_deployment_foundation.errors import SpecError

#: Derived from the IMPORTED module rather than written as a repo-relative
#: path: a check that reads a different copy of the file from the one under
#: test is a defect shape in its own right, and a relative path also depends on
#: pytest's working directory.
_SOURCE = pathlib.Path(inspect.getsourcefile(application_profile) or "")

#: The PLATFORM APPLICATION WHEEL's digest — not an OCI image digest. Named
#: for what it is, because the constant's old name (`IMAGE`) was half of why the
#: field it feeds was called `image_digest`.
ARTIFACT = "sha256:" + "a" * 64
INVENTORY = "sha256:" + "b" * 64


def _proof(**over) -> IntegrationSurfaceAbsenceProofV1:
    kwargs = {
        "concern": FoundationConcern.INTEGRATION,
        "source_revision": "0" * 40,
        "artifact_digest": ARTIFACT,
        "observed_inventory_digest": INVENTORY,
        "families": dict.fromkeys(INTEGRATION_SURFACE_FAMILIES, ()),
        "method": "entry-point metadata + AST walk over the installed wheel",
        "positive_control": ("dotmac_integration.connectors:paystack",),
        "established_at": "2026-09-04T12:00:00Z",
        "established_by": "platform-cp-profile-job",
    }
    kwargs.update(over)
    return IntegrationSurfaceAbsenceProofV1(**kwargs)


# ── established, not merely well-formed ────────────────────────────────────


def test_a_well_formed_proof_satisfies_only_against_the_OBSERVED_inventory() -> None:
    """The half that keeps the gate reachable without making it bypassable.

    A caller can write any string into `observed_inventory_digest`. It cannot
    make that string EQUAL one an independent party derived from the artifact
    without having examined it — so this compares rather than trusts.
    """
    proof = _proof()
    assert proof.satisfies(
        FoundationConcern.INTEGRATION,
        artifact_digest=ARTIFACT,
        inventory_digest=INVENTORY,
    )


def test_a_manufactured_digest_does_not_satisfy() -> None:
    """A placeholder wearing a type: perfectly well-formed, establishes nothing."""
    proof = _proof(observed_inventory_digest="sha256:" + "9" * 64)
    assert not proof.satisfies(
        FoundationConcern.INTEGRATION,
        artifact_digest=ARTIFACT,
        inventory_digest=INVENTORY,
    )


def test_a_proof_for_another_ARTIFACT_does_not_satisfy() -> None:
    """It may be perfectly well-formed and still say nothing about THIS wheel."""
    assert not _proof().satisfies(
        FoundationConcern.INTEGRATION,
        artifact_digest="sha256:" + "c" * 64,
        inventory_digest=INVENTORY,
    )


def test_a_proof_cannot_certify_ANOTHER_concern() -> None:
    """Discriminated. One proof certifying any concern's emptiness is the same
    failure as a single `AbsenceProof` for all thirteen, one level up."""
    assert not _proof().satisfies(
        FoundationConcern.WORKER_EXECUTION,
        artifact_digest=ARTIFACT,
        inventory_digest=INVENTORY,
    )


def test_construction_alone_grants_nothing() -> None:
    """The two questions are separate and must stay separate: `__post_init__`
    answers "is this well-formed", `satisfies` answers "did it establish
    anything". A type that granted at construction would be a placeholder."""
    proof = _proof(observed_inventory_digest="sha256:" + "9" * 64)
    assert proof  # constructed fine
    assert not proof.satisfies(
        FoundationConcern.INTEGRATION,
        artifact_digest=ARTIFACT,
        inventory_digest=INVENTORY,
    )


# ── the closed inventory ───────────────────────────────────────────────────


def test_the_inventory_is_closed_and_written_longhand() -> None:
    """Enumerated BEFORE any proof runs and never from a proof's own results.
    An open inventory makes absence unfalsifiable."""
    assert set(INTEGRATION_SURFACE_FAMILIES) == {
        "outbound_connector",
        "inbound_webhook",
        "scheduled_sync",
        "message_consumer",
        "external_api_client",
    }


def test_an_incomplete_enumeration_REFUSES_rather_than_reporting_a_subset() -> None:
    """A family never looked at is not a family found empty."""
    with pytest.raises(SpecError) as exc:
        _proof(families=dict.fromkeys(INTEGRATION_SURFACE_FAMILIES[:3], ()))
    assert exc.value.code == ABSENCE_INVENTORY_INCOMPLETE


def test_an_unregistered_surface_REFUSES_rather_than_disappearing() -> None:
    """The failure mode absence proofs actually have: a surface nobody
    enumerated silently satisfies "none present"."""
    families = dict.fromkeys(INTEGRATION_SURFACE_FAMILIES, ())
    families["grpc_stream"] = ()
    with pytest.raises(SpecError) as exc:
        _proof(families=families)
    assert exc.value.code == ABSENCE_UNREGISTERED_SURFACE


# ── removing the surface changes the answer ────────────────────────────────


def test_ONE_installed_surface_makes_the_concern_unbound_not_absent() -> None:
    """The non-vacuity control: removing the surface must change the answer.

    With a surface present the proof cannot even be constructed — the concern is
    UNBOUND, which needs a provider rather than a proof. That is a different
    state, and conflating them would let a product with a live connector claim
    it has none.
    """
    families = dict.fromkeys(INTEGRATION_SURFACE_FAMILIES, ())
    families["outbound_connector"] = ("dotmac-connector-paystack",)
    with pytest.raises(SpecError) as exc:
        _proof(families=families)
    assert exc.value.code == ABSENCE_NOT_ABSENT
    # And with it removed, the same inputs construct and satisfy.
    assert _proof().satisfies(
        FoundationConcern.INTEGRATION,
        artifact_digest=ARTIFACT,
        inventory_digest=INVENTORY,
    )


# ── provenance is required, not decorative ────────────────────────────────


@pytest.mark.parametrize(
    "field", ["source_revision", "method", "established_at", "established_by"]
)
def test_a_proof_without_provenance_is_refused(field: str) -> None:
    """Who established it, against what, and when — or it is a null with a type
    around it."""
    with pytest.raises(SpecError) as exc:
        _proof(**{field: "  "})
    assert exc.value.code == ABSENCE_UNESTABLISHED


def test_a_proof_without_a_POSITIVE_CONTROL_is_refused() -> None:
    """ADR 0033 § 3. Without it, a prover that never finds anything and an
    artifact that has nothing are the same colour."""
    with pytest.raises(SpecError) as exc:
        _proof(positive_control=())
    assert exc.value.code == ABSENCE_UNESTABLISHED


def test_a_non_concern_is_refused() -> None:
    with pytest.raises(SpecError) as exc:
        _proof(concern="integration")  # type: ignore[arg-type]
    assert exc.value.code == ABSENCE_WRONG_CONCERN


# ── the document ───────────────────────────────────────────────────────────


def test_the_document_names_its_state_and_schema() -> None:
    """`absent_proven` is one of four states and is named, so a reader never has
    to infer it from a missing binding."""
    document = _proof().as_document()
    assert document["schema"] == INTEGRATION_ABSENCE_SCHEMA
    assert document["state"] == "absent_proven"
    assert document["concern"] == "integration"
    assert set(document["families"]) == set(INTEGRATION_SURFACE_FAMILIES)


def test_the_document_carries_the_artifact_binding() -> None:
    """Platform's readback already refuses a proof produced for a different
    artifact by name, and it reads these two fields to do it."""
    document = _proof().as_document()
    assert document["source_revision"] == "0" * 40
    assert document["artifact_digest"] == ARTIFACT


def test_the_document_no_longer_calls_the_binding_an_IMAGE_digest() -> None:
    """The rename follows a ruling about WHAT the proof binds: the Platform
    application wheel, not the OCI image the proof travels inside.

    A proof embedded in an image cannot carry that image's own digest — the
    digest is over the finished image and the image is not finished until the
    proof is in it. So `image_digest` asked a producer for a value that does not
    exist yet, and the ways out were all worse than the rename.

    Asserted over the emitted DOCUMENT, because that is what a reader binds to.
    Fails before the change: the key was `image_digest`.
    """
    document = _proof().as_document()
    assert "artifact_digest" in document
    assert "image_digest" not in document, (
        "the proof still emits `image_digest`, which invites the circular "
        "binding a proof inside an image cannot satisfy"
    )


def test_the_keyword_is_renamed_at_the_CALL_too() -> None:
    """A record renamed and a parameter left alone would let a caller keep
    passing an OCI image digest into a comparison that now means something else.

    `satisfies` is keyword-only, so the old spelling is a `TypeError` rather
    than a silent mismatch — loud at the call site instead of a proof that
    quietly never satisfies.
    """
    with pytest.raises(TypeError):
        _proof().satisfies(
            FoundationConcern.INTEGRATION,
            image_digest=ARTIFACT,  # type: ignore[call-arg]
            inventory_digest=INVENTORY,
        )


def test_no_image_digest_survives_anywhere_in_this_TYPE() -> None:
    """The prose too, not only the field. A type whose docstring still explains
    itself in terms of an image teaches the next reader the name that was wrong,
    and this repository has shipped a detector that matched its own stale prose.

    Scoped to the class, because `AbsenceProof` and the candidate-image helper in
    the same module carry REAL OCI image digests and keep their names.
    """
    source = _SOURCE.read_text(encoding="utf-8")
    start = source.index("class IntegrationSurfaceAbsenceProofV1")
    end = source.index("class ConcernBinding")
    body = source[start:end]
    offenders = [
        line.strip()
        for line in body.splitlines()
        if "image_digest" in line
        and "NOT ``image_digest``" not in line
        and "was named ``image_digest``" not in line
        and "other ``image_digest``" not in line
    ]
    assert offenders == [], offenders


# ── no inert refusal codes ─────────────────────────────────────────────────


def test_every_declared_absence_code_is_actually_raised() -> None:
    """An inert member is false coverage.

    A declared code with no raiser reads, in a review and in a grep, exactly
    like a refusal that exists. `ABSENCE_FOREIGN_ARTIFACT` was one for the
    length of a draft: declared, exported, and unreachable, because a foreign
    artifact is answered by `satisfies` returning False rather than by a
    refusal. It was removed rather than given a raiser, and this fails if the
    next one appears.

    Parsed, not grepped — a detector that greps its own docstring is a defect
    this lane has already committed once.
    """
    tree = ast.parse(_SOURCE.read_text())
    declared = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        for target in [node.target]
        if isinstance(target, ast.Name) and target.id.startswith("ABSENCE_")
    }
    raised = {
        keyword.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
        for keyword in node.exc.keywords
        if keyword.arg == "code" and isinstance(keyword.value, ast.Name)
    }
    assert declared, "the parse found no declared codes — it would pass vacuously"
    assert declared <= raised, f"declared but never raised: {sorted(declared - raised)}"


def test_that_guard_can_see_an_inert_code() -> None:
    """Sensitivity. The check above passes over a file with no inert code, so
    prove it is capable of failing rather than merely quiet."""
    tree = ast.parse(
        "from typing import Final\n"
        'ABSENCE_LIVE: Final = "a"\n'
        'ABSENCE_INERT: Final = "b"\n'
        "def f():\n"
        "    raise SpecError('x', code=ABSENCE_LIVE)\n"
    )
    declared = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        for target in [node.target]
        if isinstance(target, ast.Name) and target.id.startswith("ABSENCE_")
    }
    raised = {
        keyword.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
        for keyword in node.exc.keywords
        if keyword.arg == "code" and isinstance(keyword.value, ast.Name)
    }
    assert sorted(declared - raised) == ["ABSENCE_INERT"]
