"""``FoundationExecutionPlanV1`` — the middle term three parties must agree on.

The bug being dissolved is not a wrong digest; it is two digests that could
never be equal while both looked correct. Control's `plan_digest` hashes the
spec **wrapped in six sibling keys**; the Foundation hashes the **descriptor
alone**. Same serialization rules, different payload — so for every input the
comparison fails, and nothing anywhere says so.

Patching either end leaves the shape: whoever normalizes decides what was
authorized, and the other party trusts a reconstruction. So the middle term is a
document the Foundation renders and Control merely freezes, and Control has no
canonicalizer for it by design.

Two repositories bind to the byte-level rules, so the canonicalization tests
below are not stylistic. `test_the_digest_covers_the_document_alone` is the one
that reproduces the original defect: it plants the six-sibling-keys wrapper and
requires the digest to differ and the canonicalizer to refuse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dotmac_deployment_foundation.authorization import OPERATIONS
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.execution_plan import (
    EXECUTION_PLAN_SCHEMA,
    FoundationExecutionPlanV1,
    canonical_execution_plan_bytes,
    execution_plan_digest,
    render_execution_plan,
    require_execution_plan_digest,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from dotmac_deployment_foundation.version import VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = REPO_ROOT / "deploy" / "product.toml"

TARGET = "prod-lagos-01"


def _spec() -> ProductDeploymentSpec:
    """The REAL reference descriptor, with the in-container bind removed.

    `deploy/product.toml`'s app role runs `--host 0.0.0.0` and
    `build_canonical_document` refuses an address literal anywhere in the
    descriptor — the refusal's own docstring calls this exact case out as an
    in-container bind rather than topology in Git, and the compose renderer
    already opts out of it. Anything sending a document to deployment control
    takes the default and raises, so `to_canonical_document()` raises on this
    repository's own reference descriptor today.

    That is pre-existing and is worked around here rather than hidden: building
    on a hand-written fixture instead would let this contract drift from the
    descriptor it has to render.
    """
    text = DESCRIPTOR.read_text(encoding="utf-8").replace('"--host", "0.0.0.0", ', "")
    return ProductDeploymentSpec.loads(text, source="test")


def _rendered(**overrides: Any) -> FoundationExecutionPlanV1:
    spec = _spec()
    kwargs: dict[str, Any] = {
        "target": TARGET,
        "operation": "deploy",
        "descriptor_digest": str(spec.to_canonical_document().sha256_digest()),
    }
    kwargs.update(overrides)
    return render_execution_plan(spec, build_plan(spec), **kwargs)


# ── the value is its own thing ──────────────────────────────────────────────


def test_the_plan_digest_is_none_of_the_three_digests_it_resembles() -> None:
    """Each of the three is a real value a reader could reach for, and every one
    of them is wrong — reaching for Control's internal one is how the
    six-sibling-keys divergence happened."""
    plan = _rendered()
    assert plan.digest() != plan.descriptor_digest
    assert plan.digest() != plan.manifest_digest
    assert plan.digest() != plan.image_digest
    assert plan.digest().startswith("sha256:")


def test_the_digest_is_bound_to_the_target() -> None:
    """Without it, an authorization for staging authorizes production."""
    assert _rendered().digest() != _rendered(target="staging-abuja-01").digest()


def test_the_digest_is_bound_to_the_operation() -> None:
    """Without it, a deploy approval also authorizes the rollback that erases
    it — the line `authorization.py` already draws, applied to the plan."""
    assert _rendered().digest() != _rendered(operation="rollback").digest()


def test_the_digest_is_bound_to_the_descriptor() -> None:
    assert (
        _rendered().digest()
        != _rendered(descriptor_digest="sha256:" + "0" * 64).digest()
    )


def test_the_operation_vocabulary_is_closed_and_shared_with_control() -> None:
    """An open operation is one nobody wrote a policy for. Read from
    `authorization.OPERATIONS` rather than restated, so the plan and the grant
    cannot come to disagree about what a deployment can be."""
    assert set(OPERATIONS) == {"deploy", "rollback"}
    with pytest.raises(SpecError, match="unknown operation"):
        _rendered(operation="reconcile")


def test_a_plan_with_no_target_is_refused() -> None:
    with pytest.raises(SpecError, match="authorizes every host"):
        _rendered(target="   ")


# ── canonicalization, byte level ────────────────────────────────────────────


def test_the_digest_covers_the_document_alone() -> None:
    """THE regression. Control hashed the spec inside six sibling keys and the
    Foundation hashed it alone; both were internally consistent, the values
    could never be equal, and the comparison read as correct.

    Two assertions, and the second matters more: the wrapper produces a
    different digest (so a caller cannot be lucky), and the canonicalizer
    REFUSES the wrapper outright (so a caller cannot even ask for it).
    """
    document = _rendered().as_document()
    wrapped = {
        "created_at": "2026-09-01T00:00:00Z",
        "environment": "production",
        "plan": document,
        "requested_by": "platform-cp",
        "schema": "ControlPlanSnapshot.v1",
        "target": TARGET,
    }
    assert len(wrapped) == 6
    naive = json.dumps(wrapped, sort_keys=True, separators=(",", ":")).encode()
    assert naive != canonical_execution_plan_bytes(document)
    with pytest.raises(SpecError, match="covers THIS document"):
        canonical_execution_plan_bytes(wrapped)


def test_key_order_does_not_change_the_digest() -> None:
    """Rule 3. Two byte strings with identical meaning must not have different
    standings — the rule `evidence.py` and `document.py` already apply."""
    document = _rendered().as_document()
    shuffled = dict(reversed(list(document.items())))
    assert execution_plan_digest(document) == execution_plan_digest(shuffled)


def test_the_canonical_bytes_are_ascii_and_have_no_whitespace() -> None:
    """Rules 1, 2 and 4 together. ASCII by construction AND by check, so the
    two can never disagree and the NFC/NFD question never arises."""
    raw = _rendered().canonical_bytes()
    assert raw.decode("ascii")
    assert b", " not in raw
    assert b": " not in raw
    assert b"\n" not in raw


def test_a_non_ascii_string_is_refused_not_normalized() -> None:
    """A normalizer is a second opinion about what the bytes are, and two
    defensible opinions is the state being replaced."""
    document = _rendered().as_document()
    document["target"] = "prod-lagos-01-café"
    with pytest.raises(SpecError, match="non-ASCII"):
        canonical_execution_plan_bytes(document)


def test_a_null_is_refused() -> None:
    """Rule 5. A missing key and an explicit null are two encodings of one fact,
    and would produce two digests for it."""
    document = _rendered().as_document()
    document["manifest_digest"] = None
    with pytest.raises(SpecError, match="null"):
        canonical_execution_plan_bytes(document)


def test_a_float_is_refused() -> None:
    """Rule 6. A duration serializing as 1.1 on one runtime and
    1.1000000000000001 on another is a digest that disagrees with itself."""
    document = _rendered().as_document()
    document["steps"][0]["timeout_seconds"] = 60.5
    with pytest.raises(SpecError, match="float"):
        canonical_execution_plan_bytes(document)


def test_step_order_is_part_of_the_digest() -> None:
    """Rule 7. A reordered procedure is a different procedure, and a digest that
    could not tell them apart would authorize migrating after the switch."""
    document = _rendered().as_document()
    reordered = dict(document)
    reordered["steps"] = list(reversed(document["steps"]))
    assert execution_plan_digest(document) != execution_plan_digest(reordered)


def test_prose_is_not_part_of_the_digest() -> None:
    """Rule 8. An edit to a step's description must not change a digest Control
    has already signed — prose is not what was authorized."""
    document = _rendered().as_document()
    assert all("description" not in step for step in document["steps"])
    assert all(
        set(step) == {"command", "kind", "retries", "target", "timeout_seconds"}
        for step in document["steps"]
    )


def test_the_foundation_version_is_inside_the_document() -> None:
    """Rule 10. The plan's meaning is the steps THIS version emits, so a
    facility upgrade must not leave an unchanged digest describing a changed
    procedure — the same reason `IngressPolicy.v1` carries it."""
    document = _rendered().as_document()
    assert document["foundation_version"] == VERSION
    assert document["schema"] == EXECUTION_PLAN_SCHEMA


def test_the_digest_is_stable_across_renders() -> None:
    """Nothing here reads a clock, an environment variable or a filesystem."""
    assert _rendered().digest() == _rendered().digest()


# ── no values leave the facility ────────────────────────────────────────────


def test_the_environment_inventory_carries_names_never_values() -> None:
    """ADR-0009. The inventory is what a host must RESOLVE; a resolved value in
    a document three systems store is a secret in three systems."""
    plan = _rendered()
    assert "DATABASE_URL" in plan.environment_inventory
    assert list(plan.environment_inventory) == sorted(set(plan.environment_inventory))
    raw = plan.canonical_bytes().decode("ascii")
    assert "postgresql://" not in raw
    assert "password" not in raw.lower()


# ── the recompute before execution ──────────────────────────────────────────


def test_the_digest_is_recomputed_and_a_changed_plan_is_refused() -> None:
    """Step 4. The plan is rendered while Platform CP asks Control and executed
    later; "nothing changed in between" is exactly what a long-running process
    cannot assume."""
    plan = _rendered()
    assert (
        require_execution_plan_digest(plan, authorized=plan.digest()) == plan.digest()
    )
    with pytest.raises(PreconditionFailed, match="not frozen"):
        require_execution_plan_digest(plan, authorized="sha256:" + "0" * 64)


def test_a_mismatch_is_named_a_changed_plan_not_a_disagreement() -> None:
    """The wording is load-bearing. If a mismatch could mean "two canonicalizers
    disagree", the fix a reader reaches for is a normalizer — which is how the
    original divergence became permanent. Control never reconstructs this
    document, so a mismatch can only mean the plan changed."""
    plan = _rendered()
    with pytest.raises(PreconditionFailed) as caught:
        require_execution_plan_digest(plan, authorized="sha256:" + "1" * 64)
    message = str(caught.value)
    assert "never reconstructs or normalizes" in message
    assert "changed plan" in message
