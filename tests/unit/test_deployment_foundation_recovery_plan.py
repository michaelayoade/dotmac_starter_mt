"""``RecoveryExecutionPlanV1`` — a distinct document for a distinct act.

## What this file is really about

The plan type is the easy half. The half that earns the file is
**non-interchangeability**, and it is a live hazard rather than a theoretical
one, because `RecoveryExecutionPlanV1` deliberately carries no ``operation``
field.

On `FoundationExecutionPlanV1` that field does DOUBLE duty: it constrains the
vocabulary against `authorization.OPERATIONS`, and it makes the document
self-identifying. A recovery plan has no sibling to be told apart from, so the
field would carry no information — and a field carrying no information is one a
later author finds something to put in. Dropping it is right, and it leaves the
second job needing an owner. The owner is the SCHEMA, and an owner nobody drives
a swap through is a claim.

So every acceptance point below is exercised with a REAL document or object of
the wrong kind, in BOTH directions, and each must refuse with its OWN stable
code. A type annotation is not a check. `isinstance` in a docstring is not a
check. The swap is the check.

## Codes, not prose

This module has six refusals. Every assertion below reads `exc.value.code`
against a constant, because `match=` on a sentence makes the sentence the
contract — after which the message cannot be improved without breaking a test,
and a test that only ever saw one wording cannot tell two refusals apart. The
package made that argument for `discover_one` and `recovery_execution`; it holds
here for the same reason.

## Unreachable, and asserted so

Nothing constructs one of these on any path that touches a host. The last test
in this file derives that from the package source rather than stating it, so the
day someone wires it up they are told to come and read the staging argument
first.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from dotmac_deployment_foundation.canonical_plan import (
    EXECUTION_PLAN_WRONG_TYPE,
    PLAN_NOT_THIS_DOCUMENT,
    PLAN_VALUE_REFUSED,
    RECOVERY_PLAN_WRONG_TYPE,
)
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.execution_plan import (
    EXECUTION_PLAN_SCHEMA,
    FoundationExecutionPlanV1,
    HostPrestateV1,
    canonical_execution_plan_bytes,
    require_execution_plan_digest,
)
from dotmac_deployment_foundation.recovery_plan import (
    RECOVERY_PLAN_DIGEST_MISMATCH,
    RECOVERY_PLAN_DIGEST_SCHEMA,
    RECOVERY_PLAN_EMPTY_FIELD,
    RECOVERY_PLAN_NO_VERIFICATION,
    RECOVERY_PLAN_SCHEMA,
    RECOVERY_PLAN_UNKNOWN_VERIFICATION,
    CapturedPrestateV1,
    DesiredPoststateV1,
    FailedSystemObservationV1,
    RecoveryExecutionPlanV1,
    canonical_recovery_plan_bytes,
    declarable_verifications,
    recovery_plan_digest,
    render_recovery_plan,
    require_recovery_plan_digest,
)
from dotmac_deployment_foundation.version import VERSION

PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "dotmac-deployment-foundation"
    / "src"
    / "dotmac_deployment_foundation"
)

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
C = "sha256:" + "c" * 64
D = "sha256:" + "d" * 64


def _prestate() -> CapturedPrestateV1:
    return CapturedPrestateV1(
        source_target="prod-lagos-01",
        descriptor_digest=A,
        bundle_manifest_digest=B,
    )


def _failed() -> FailedSystemObservationV1:
    return FailedSystemObservationV1(
        target="prod-lagos-01",
        roles=HostPrestateV1(roles=(("app", C),)),
        observed_descriptor_digest=A,
    )


def _poststate(
    verifications: tuple[str, ...] = ("schema", "roles"),
) -> DesiredPoststateV1:
    return DesiredPoststateV1(
        descriptor_digest=A,
        bundle_manifest_digest=B,
        verifications=verifications,
    )


def _recovery(**over) -> RecoveryExecutionPlanV1:
    kwargs = {
        "product": "dotmac_starter_mt",
        "target": "prod-lagos-01",
        "image_reference": "ghcr.io/dotmac/starter:1.2.3",
        "image_digest": D,
        "captured_prestate": _prestate(),
        "failed_state": _failed(),
        "desired_poststate": _poststate(),
        "environment_inventory": ("DATABASE_URL", "SECRET_KEY"),
    }
    kwargs.update(over)
    return render_recovery_plan(**kwargs)


def _deploy() -> FoundationExecutionPlanV1:
    """A minimal but REAL deployment plan — the swap subject."""
    return FoundationExecutionPlanV1(
        product="dotmac_starter_mt",
        target="prod-lagos-01",
        operation="deploy",
        foundation_version=VERSION,
        image_reference="ghcr.io/dotmac/starter:1.2.3",
        image_digest=D,
        source_revision="0" * 40,
        manifest_digest=C,
        descriptor_digest=A,
        strategy="warm_candidate",
        environment_inventory=("DATABASE_URL",),
        host_prestate=HostPrestateV1(roles=(("app", C),)),
        application_profile_digest="",
        steps=(("command", "app", ("echo", "hi"), 60, 0),),
    )


# ── the document ────────────────────────────────────────────────────────────


def test_the_plan_renders_and_digests() -> None:
    plan = _recovery()
    assert plan.digest().startswith("sha256:")
    assert plan.as_document()["schema"] == RECOVERY_PLAN_SCHEMA
    assert plan.foundation_version == VERSION


def test_the_digest_schema_name_is_not_the_document_schema_name() -> None:
    """Two names for two things, the same split `ExecutionPlanDigestV1` draws.
    Whoever handles the value needs a word for it that is not the word for a
    document they never parse."""
    assert RECOVERY_PLAN_DIGEST_SCHEMA != RECOVERY_PLAN_SCHEMA
    assert RECOVERY_PLAN_DIGEST_SCHEMA == "RecoveryExecutionPlanDigestV1"


def test_the_plan_carries_no_operation_field() -> None:
    """The design decision, asserted so it cannot be added back quietly.

    A recovery has no sibling document to be distinguished from, so the field
    would carry no information — and a field carrying no information is one a
    later author will find something to put in. If this fails, read
    `recovery_plan`'s module docstring before deleting the assertion: dropping
    the field is what makes the schema guard load-bearing, and adding it back
    without removing the guard would leave two owners for one job.
    """
    assert "operation" not in _recovery().as_document()
    assert "operation" in _deploy().as_document()


def test_the_plan_carries_no_steps_and_the_reason_is_enforced_elsewhere() -> None:
    """`RESTORE_PROCEDURE` is a constant of the facility version, which
    `foundation_version` already binds, and `RecoveryExecutor.run` asserts the
    plan it walks IS that contract rather than a copy. Ten fixed steps in the
    document would carry no information and would invite a later author to make
    them caller-chosen."""
    assert "steps" not in _recovery().as_document()
    source = (PACKAGE / "recovery_execution.py").read_text(encoding="utf-8")
    assert "the restore plan is not the declared procedure" in source


@pytest.mark.parametrize(
    "binding",
    ["captured_prestate", "failed_state", "desired_poststate"],
)
def test_each_of_the_three_bindings_is_part_of_the_digest(binding: str) -> None:
    """A binding outside the digest is a binding an approval does not cover."""
    base = _recovery()
    swapped = {
        "captured_prestate": CapturedPrestateV1(
            source_target="staging-01", descriptor_digest=A, bundle_manifest_digest=B
        ),
        "failed_state": FailedSystemObservationV1(
            target="prod-lagos-01",
            roles=HostPrestateV1(roles=()),
            observed_descriptor_digest=A,
        ),
        "desired_poststate": _poststate(("schema",)),
    }[binding]
    assert base.digest() != _recovery(**{binding: swapped}).digest()


def test_an_empty_failed_state_is_a_CLAIM_not_an_absence() -> None:
    """ "No role containers are running" is an ordinary and highly relevant
    observation about a failed system, and it must be representable and
    digest-bearing — not indistinguishable from "we did not look"."""
    empty = FailedSystemObservationV1(
        target="prod-lagos-01",
        roles=HostPrestateV1.first_deploy(),
        observed_descriptor_digest=A,
    )
    assert empty.as_document()["roles"] == {"roles": []}
    assert _recovery(failed_state=empty).digest() != _recovery().digest()


def test_the_environment_inventory_is_a_sorted_set() -> None:
    assert _recovery(
        environment_inventory=("SECRET_KEY", "DATABASE_URL", "DATABASE_URL")
    ).as_document()["environment_inventory"] == ["DATABASE_URL", "SECRET_KEY"]


# ── the poststate is digests and NAMES, never parsed catalogue facts ────────


def test_the_poststate_verification_vocabulary_is_READ_not_respelled() -> None:
    """A local tuple here would be a second authority over one vocabulary, and
    the two would diverge silently the moment `UNDECLARED_COMPARISONS` retires a
    member into the declarable set — this module would keep refusing a name
    descriptors had started accepting, and the refusal would look correct."""
    from dotmac_deployment_foundation.spec import BackupDataset

    assert declarable_verifications() == tuple(BackupDataset.VERIFICATIONS)
    assert declarable_verifications()  # not vacuous


def test_a_poststate_demanding_nothing_is_refused() -> None:
    with pytest.raises(SpecError) as exc:
        _poststate(())
    assert exc.value.code == RECOVERY_PLAN_NO_VERIFICATION


def test_a_poststate_demanding_an_undeclarable_proof_is_refused() -> None:
    """`row_security` is a real comparison `verify_recovery` performs and no
    descriptor can declare — it is in `recovery.UNDECLARED_COMPARISONS`. A
    poststate demanding it is unfalsifiable today, and an unfalsifiable
    requirement gets removed rather than met."""
    from dotmac_deployment_foundation.recovery import UNDECLARED_COMPARISONS

    assert "row_security" in UNDECLARED_COMPARISONS
    with pytest.raises(SpecError) as exc:
        _poststate(("schema", "row_security"))
    assert exc.value.code == RECOVERY_PLAN_UNKNOWN_VERIFICATION


def test_the_poststate_holds_no_catalogue_facts() -> None:
    """Digests and names, never parsed facts — the same refusal that kept a
    fourteenth `BundleComponent` out. `recovery.py` runs with no database
    because the manifest carries digests and counts rather than facts, and a
    plan on that path must not take the property away."""
    document = _recovery().as_document()["desired_poststate"]
    assert set(document) == {
        "bundle_manifest_digest",
        "descriptor_digest",
        "verifications",
    }
    assert all(isinstance(name, str) for name in document["verifications"])


@pytest.mark.parametrize("field", ["source_target", "target", "product", "plan_target"])
def test_an_unnamed_subject_is_refused(field: str) -> None:
    with pytest.raises(SpecError) as exc:
        if field == "source_target":
            CapturedPrestateV1(
                source_target=" ", descriptor_digest=A, bundle_manifest_digest=B
            )
        elif field == "target":
            FailedSystemObservationV1(
                target="", roles=HostPrestateV1(roles=()), observed_descriptor_digest=A
            )
        elif field == "product":
            _recovery(product="")
        else:
            _recovery(target="  ")
    assert exc.value.code == RECOVERY_PLAN_EMPTY_FIELD


def test_a_lookalike_observation_is_refused() -> None:
    class NotAPrestate:
        def as_document(self):
            return {"roles": []}

    with pytest.raises(SpecError) as exc:
        FailedSystemObservationV1(
            target="prod-lagos-01",
            roles=NotAPrestate(),  # type: ignore[arg-type]
            observed_descriptor_digest=A,
        )
    assert exc.value.code == RECOVERY_PLAN_WRONG_TYPE


# ── NON-INTERCHANGEABILITY: the swap, both directions ───────────────────────
#
# Five acceptance points. Each is driven with a REAL object or document of the
# wrong kind and must produce its OWN code.


def test_the_deploy_canonicalizer_refuses_a_recovery_DOCUMENT() -> None:
    with pytest.raises(SpecError) as exc:
        canonical_execution_plan_bytes(_recovery().as_document())
    assert exc.value.code == PLAN_NOT_THIS_DOCUMENT


def test_the_recovery_canonicalizer_refuses_a_deploy_DOCUMENT() -> None:
    with pytest.raises(SpecError) as exc:
        canonical_recovery_plan_bytes(_deploy().as_document())
    assert exc.value.code == PLAN_NOT_THIS_DOCUMENT


def test_the_deploy_digest_gate_refuses_a_recovery_PLAN_as_a_type_error() -> None:
    """THE hole this slice closes, and the one worth reading twice.

    Every plan kind in this package has a `digest()`. Before the type check,
    handing a recovery plan here computed a perfectly good recovery digest,
    compared it with a deploy authorization, and reported *"something changed
    between authorization and execution"* — which sends an operator to look for
    a changed descriptor when what actually happened is that two different acts
    were confused. A refusal that misdescribes its own cause is worse than none,
    because it is actionable in the wrong direction.

    So this asserts the code is WRONG_TYPE and specifically NOT the digest
    mismatch, which is the failure it used to be.
    """
    plan = _recovery()
    with pytest.raises(PreconditionFailed) as exc:
        require_execution_plan_digest(plan, authorized=plan.digest())  # type: ignore[arg-type]
    assert exc.value.code == EXECUTION_PLAN_WRONG_TYPE
    assert exc.value.code != RECOVERY_PLAN_DIGEST_MISMATCH


def test_the_recovery_digest_gate_refuses_a_deploy_PLAN_as_a_type_error() -> None:
    plan = _deploy()
    with pytest.raises(PreconditionFailed) as exc:
        require_recovery_plan_digest(plan, authorized=plan.digest())  # type: ignore[arg-type]
    assert exc.value.code == RECOVERY_PLAN_WRONG_TYPE


def test_the_executor_refuses_a_recovery_plan_at_CONSTRUCTION() -> None:
    """Not at first attribute access. Before the check, a swapped plan travelled
    into `_require_execution_plan` and died on `AttributeError: ... has no
    attribute 'operation'` — after the executor had been built, and presented to
    an operator as a traceback rather than a refusal."""
    from dotmac_deployment_foundation.engine.run import Executor

    with pytest.raises(PreconditionFailed) as exc:
        Executor(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            execution_plan=_recovery(),  # type: ignore[arg-type]
        )
    assert exc.value.code == EXECUTION_PLAN_WRONG_TYPE


def test_the_two_directions_have_DIFFERENT_codes() -> None:
    """One shared "wrong plan kind" code would let one direction be proven
    twice while the other was never proven at all."""
    assert EXECUTION_PLAN_WRONG_TYPE != RECOVERY_PLAN_WRONG_TYPE


def test_the_two_plan_kinds_never_share_a_digest() -> None:
    """The last line of defence, and the cheapest. Two documents that hash the
    same would make every guard above cosmetic."""
    assert _recovery().digest() != _deploy().digest()
    assert RECOVERY_PLAN_SCHEMA != EXECUTION_PLAN_SCHEMA


# ── positive control: the swaps are refused, the real things are not ────────


def test_each_acceptance_point_ADMITS_its_own_kind() -> None:
    """Without this every refusal above could be a function that refuses
    everything, and a suite of negatives over a broken gate reads identically to
    a suite of negatives over a working one."""
    recovery, deploy = _recovery(), _deploy()
    assert canonical_recovery_plan_bytes(recovery.as_document())
    assert canonical_execution_plan_bytes(deploy.as_document())
    assert require_recovery_plan_digest(recovery, authorized=recovery.digest())
    assert require_execution_plan_digest(deploy, authorized=deploy.digest())


def test_a_genuine_digest_mismatch_still_reports_a_MISMATCH() -> None:
    """The type check must not have swallowed the check it sits in front of."""
    with pytest.raises(PreconditionFailed) as exc:
        require_recovery_plan_digest(_recovery(), authorized="sha256:" + "0" * 64)
    assert exc.value.code == RECOVERY_PLAN_DIGEST_MISMATCH


# ── the shared canonicalization core ────────────────────────────────────────


def test_the_extraction_did_not_move_a_single_byte() -> None:
    """`canonical_execution_plan_bytes` is read across two other repositories,
    so moving its rules into `canonical_plan` had to be byte-neutral.

    Proved against an INDEPENDENTLY written encoder rather than against the
    function itself — comparing the function with itself would pass for any
    implementation, including a broken one.
    """
    document = _deploy().as_document()
    expected = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    assert canonical_execution_plan_bytes(document) == expected
    assert canonical_recovery_plan_bytes(_recovery().as_document()) == json.dumps(
        _recovery().as_document(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda d: d.update({"target": "prod-café"}), "rule 2, non-ASCII"),
        (lambda d: d.update({"image_reference": None}), "rule 5, null"),
        (lambda d: d.update({"image_reference": 1.5}), "rule 6, float"),
    ],
)
def test_the_recovery_document_gets_all_the_value_rules(mutate, why: str) -> None:
    """The whole point of the extraction: a second plan kind inherits every rule
    by construction rather than by its author remembering them."""
    document = _recovery().as_document()
    mutate(document)
    with pytest.raises(SpecError) as exc:
        canonical_recovery_plan_bytes(document)
    assert exc.value.code == PLAN_VALUE_REFUSED, why


def test_key_order_does_not_change_the_recovery_digest() -> None:
    document = _recovery().as_document()
    assert recovery_plan_digest(document) == recovery_plan_digest(
        dict(reversed(list(document.items())))
    )


def test_the_digest_covers_the_recovery_document_alone() -> None:
    """Rule 9, reproduced for the new document: the wrapper that caused the
    original Control divergence must be refused here too."""
    document = _recovery().as_document()
    wrapped = {"schema": "ControlPlanSnapshot.v1", "plan": document}
    with pytest.raises(SpecError) as exc:
        canonical_recovery_plan_bytes(wrapped)
    assert exc.value.code == PLAN_NOT_THIS_DOCUMENT


def test_every_plan_canonicalizer_goes_through_the_one_core() -> None:
    """DERIVED extent, AST rather than grep — a mention in a docstring is not a
    call, and this package has already shipped a detector that matched its own
    prose.

    A third plan document that hand-rolls `json.dumps` fails HERE, which is what
    makes the extraction a ratchet rather than an intention.
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in {"canonical_plan.py", "document.py", "recovery.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name != "dumps":
                continue
            if any(kw.arg == "sort_keys" for kw in node.keywords):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "a canonical-looking json.dumps outside the core: "
        f"{offenders}. Plan documents canonicalize through "
        "canonical_plan.canonical_plan_bytes, or there are two answers to one "
        "question again"
    )


def test_that_sweep_would_actually_find_one() -> None:
    """Sensitivity. An AST walk that matched nothing would make the test above
    pass over an empty set — which is how a scanner over a clean tree passes for
    the wrong reason."""
    tree = ast.parse('json.dumps(d, sort_keys=True, separators=(",", ":"))')
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "dumps"
        and any(kw.arg == "sort_keys" for kw in node.keywords)
    ]
    assert len(found) == 1


# ── unreachable, and derived rather than stated ─────────────────────────────


def test_nothing_on_a_host_path_constructs_a_recovery_plan() -> None:
    """`recover` is not in `authorization.OPERATIONS`, there is no `recover`
    subcommand, and no grant covers the act — so this type must not be reachable
    from anything that touches a host.

    The staging is the one `ApplicationFoundationProfile.v1` used: the type
    refuses first, reachability comes later, so a half-built authorization chain
    cannot read as done. A grant, a replay coordinate and a signed result
    wrapped around a plan nobody can execute would be a chain whose every link is
    correct and whose SUBJECT does not exist — and it would review as finished.

    When reachability lands, this test is the thing that says so out loud. Update
    it in that change; do not delete it.
    """
    importers = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in {"recovery_plan.py", "__init__.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "recovery_plan":
                importers.append(path.name)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module.endswith(".recovery_plan"))
            ):
                importers.append(path.name)
    assert importers == [], (
        f"{importers} import recovery_plan. Nothing may reach this type from a "
        "host path while `recover` is out of authorization.OPERATIONS — read "
        "this test's docstring before changing it"
    )


def test_recover_is_still_out_of_the_authorization_vocabulary() -> None:
    """Stated here as well as in `test_deployment_foundation_execution_plan.py`,
    because a reader of THIS file needs to know the plan type is not authorized
    by anything — and because the tempting way to make a recovery reachable is
    to widen that tuple."""
    from dotmac_deployment_foundation.authorization import OPERATIONS

    assert set(OPERATIONS) == {"deploy", "rollback"}
