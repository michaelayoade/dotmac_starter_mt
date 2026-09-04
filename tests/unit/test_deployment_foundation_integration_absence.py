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

IMAGE = "sha256:" + "a" * 64
INVENTORY = "sha256:" + "b" * 64


def _proof(**over) -> IntegrationSurfaceAbsenceProofV1:
    kwargs = {
        "concern": FoundationConcern.INTEGRATION,
        "source_revision": "0" * 40,
        "image_digest": IMAGE,
        "observed_inventory_digest": INVENTORY,
        "families": dict.fromkeys(INTEGRATION_SURFACE_FAMILIES, ()),
        "method": "entry-point metadata + AST walk over the installed image",
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
    make that string EQUAL one an independent party derived from the image
    without having examined that image — so this compares rather than trusts.
    """
    proof = _proof()
    assert proof.satisfies(
        FoundationConcern.INTEGRATION, image_digest=IMAGE, inventory_digest=INVENTORY
    )


def test_a_manufactured_digest_does_not_satisfy() -> None:
    """A placeholder wearing a type: perfectly well-formed, establishes nothing."""
    proof = _proof(observed_inventory_digest="sha256:" + "9" * 64)
    assert not proof.satisfies(
        FoundationConcern.INTEGRATION, image_digest=IMAGE, inventory_digest=INVENTORY
    )


def test_a_proof_for_another_IMAGE_does_not_satisfy() -> None:
    """It may be perfectly well-formed and still say nothing about THIS image."""
    assert not _proof().satisfies(
        FoundationConcern.INTEGRATION,
        image_digest="sha256:" + "c" * 64,
        inventory_digest=INVENTORY,
    )


def test_a_proof_cannot_certify_ANOTHER_concern() -> None:
    """Discriminated. One proof certifying any concern's emptiness is the same
    failure as a single `AbsenceProof` for all thirteen, one level up."""
    assert not _proof().satisfies(
        FoundationConcern.WORKER_EXECUTION,
        image_digest=IMAGE,
        inventory_digest=INVENTORY,
    )


def test_construction_alone_grants_nothing() -> None:
    """The two questions are separate and must stay separate: `__post_init__`
    answers "is this well-formed", `satisfies` answers "did it establish
    anything". A type that granted at construction would be a placeholder."""
    proof = _proof(observed_inventory_digest="sha256:" + "9" * 64)
    assert proof  # constructed fine
    assert not proof.satisfies(
        FoundationConcern.INTEGRATION, image_digest=IMAGE, inventory_digest=INVENTORY
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
        FoundationConcern.INTEGRATION, image_digest=IMAGE, inventory_digest=INVENTORY
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
    assert document["image_digest"] == IMAGE


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
