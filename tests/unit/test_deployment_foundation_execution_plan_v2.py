"""``FoundationExecutionPlanV2`` — the plan that can express a principal bootstrap.

## Three plan kinds now, so the substitution matrix is 3x3

`FoundationExecutionPlanV1`, `FoundationExecutionPlanV2` and
`RecoveryExecutionPlanV1` all canonicalize, all produce a digest, and all reach
acceptance points that judge one of them. Nine ordered pairs; three admit and six
must refuse, each with the refusing side's OWN code.

A shared "wrong plan kind" code would let one direction be proven twice while
another was never proven at all — the failure that gets likelier as the matrix
grows, which is why it is worth stating now rather than when there are four.

## What the type refuses that a guard could not

`PostgresPrincipalCredentialBootstrapV1` has no field for a password, a DSN, SQL
or an executable command. That is a structure, not a rejection: there is nowhere
to put one. The tests below prove it in both directions — every field refuses
material at construction, AND `require_no_secrets` genuinely traverses into the
new member, so the belt covers it if the structure were ever relaxed.

The second half matters because the first makes the belt unreachable in practice.
A guard nobody can trigger is a guard nobody has proved.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotmac_deployment_foundation.canonical_plan import (
    EXECUTION_PLAN_WRONG_TYPE,
    PLAN_NOT_THIS_DOCUMENT,
    RECOVERY_PLAN_WRONG_TYPE,
)
from dotmac_deployment_foundation.errors import (
    PreconditionFailed,
    SecretValueError,
    SpecError,
)
from dotmac_deployment_foundation.execution_plan import (
    EXECUTION_PLAN_DIGEST_SCHEMA,
    FoundationExecutionPlanV1,
    HostPrestateV1,
    canonical_execution_plan_bytes,
    require_execution_plan_digest,
)
from dotmac_deployment_foundation.execution_plan_v2 import (
    BOOTSTRAP_BAD_PRINCIPAL,
    BOOTSTRAP_BAD_REFERENCE,
    BOOTSTRAP_BAD_VERSION,
    BOOTSTRAP_TRANSITIONS,
    EXECUTION_PLAN_V2_SCHEMA,
    EXECUTION_PLAN_V2_WRONG_TYPE,
    FoundationExecutionPlanV2,
    PostgresPrincipalCredentialBootstrapV1,
    canonical_execution_plan_v2_bytes,
    render_execution_plan_v2,
    require_execution_plan_v2_digest,
)
from dotmac_deployment_foundation.recovery_plan import (
    CapturedPrestateV1,
    DesiredPoststateV1,
    FailedSystemObservationV1,
    RecoveryExecutionPlanV1,
    canonical_recovery_plan_bytes,
    require_recovery_plan_digest,
)
from dotmac_deployment_foundation.secrets_guard import require_no_secrets
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


def _bootstrap(**over) -> PostgresPrincipalCredentialBootstrapV1:
    kwargs = {
        "service": "platform_cp",
        "principal": "platform_outbox_dispatcher",
        "secret_path": "bao://secret/dotmac/platform/outbox",
        "secret_field": "password",
        "expected_version": 1,
    }
    kwargs.update(over)
    return PostgresPrincipalCredentialBootstrapV1(**kwargs)


def _v1() -> FoundationExecutionPlanV1:
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


def _v2(**over) -> FoundationExecutionPlanV2:
    return render_execution_plan_v2(
        _v1(), principal_bootstraps=over.pop("principal_bootstraps", (_bootstrap(),))
    )


def _recovery() -> RecoveryExecutionPlanV1:
    return RecoveryExecutionPlanV1(
        product="dotmac_starter_mt",
        target="prod-lagos-01",
        foundation_version=VERSION,
        image_reference="ghcr.io/dotmac/starter:1.2.3",
        image_digest=D,
        captured_prestate=CapturedPrestateV1(
            source_target="prod-lagos-01", descriptor_digest=A, bundle_manifest_digest=B
        ),
        failed_state=FailedSystemObservationV1(
            target="prod-lagos-01",
            roles=HostPrestateV1(roles=(("app", C),)),
            observed_descriptor_digest=A,
        ),
        desired_poststate=DesiredPoststateV1(
            descriptor_digest=A, bundle_manifest_digest=B, verifications=("schema",)
        ),
        environment_inventory=("DATABASE_URL",),
    )


# ── the 3x3 substitution matrix ─────────────────────────────────────────────

KINDS = {
    "v1": (_v1, canonical_execution_plan_bytes),
    "v2": (_v2, canonical_execution_plan_v2_bytes),
    "recovery": (_recovery, canonical_recovery_plan_bytes),
}


@pytest.mark.parametrize("name", sorted(KINDS))
def test_each_canonicalizer_admits_its_OWN_document(name: str) -> None:
    """The diagonal. Without it the six refusals below could all be produced by
    a function that refuses everything."""
    make, canonicalize = KINDS[name]
    assert canonicalize(make().as_document())


@pytest.mark.parametrize("document_kind", sorted(KINDS))
@pytest.mark.parametrize("canonicalizer_kind", sorted(KINDS))
def test_no_canonicalizer_accepts_ANOTHER_kinds_document(
    document_kind: str, canonicalizer_kind: str
) -> None:
    """The six off-diagonal pairs, driven with a REAL document of each kind.

    A type annotation is not a check and an `isinstance` in a docstring is not a
    check. The swap is the check.
    """
    if document_kind == canonicalizer_kind:
        pytest.skip("the diagonal has its own test")
    document = KINDS[document_kind][0]().as_document()
    canonicalize = KINDS[canonicalizer_kind][1]
    with pytest.raises(SpecError) as exc:
        canonicalize(document)
    assert exc.value.code == PLAN_NOT_THIS_DOCUMENT


GATES = {
    "v1": (_v1, require_execution_plan_digest, EXECUTION_PLAN_WRONG_TYPE),
    "v2": (_v2, require_execution_plan_v2_digest, EXECUTION_PLAN_V2_WRONG_TYPE),
    "recovery": (_recovery, require_recovery_plan_digest, RECOVERY_PLAN_WRONG_TYPE),
}


@pytest.mark.parametrize("name", sorted(GATES))
def test_each_digest_gate_admits_its_OWN_plan(name: str) -> None:
    make, gate, _ = GATES[name]
    plan = make()
    assert gate(plan, authorized=plan.digest()) == plan.digest()


@pytest.mark.parametrize("plan_kind", sorted(GATES))
@pytest.mark.parametrize("gate_kind", sorted(GATES))
def test_no_digest_gate_accepts_ANOTHER_kinds_plan(
    plan_kind: str, gate_kind: str
) -> None:
    """Every plan kind here can produce a digest, which is exactly why the type
    must be checked BEFORE the digest: otherwise a swap reports a digest
    mismatch, which reads as a changed plan and sends the reader the wrong way."""
    if plan_kind == gate_kind:
        pytest.skip("the diagonal has its own test")
    plan = GATES[plan_kind][0]()
    _, gate, code = GATES[gate_kind]
    with pytest.raises(PreconditionFailed) as exc:
        gate(plan, authorized=plan.digest())
    assert exc.value.code == code


def test_the_three_wrong_type_codes_are_all_different() -> None:
    """One shared code would let a direction be proven twice and another not at
    all — likelier the bigger the matrix gets."""
    codes = {
        EXECUTION_PLAN_WRONG_TYPE,
        EXECUTION_PLAN_V2_WRONG_TYPE,
        RECOVERY_PLAN_WRONG_TYPE,
    }
    assert len(codes) == 3


def test_the_three_kinds_never_share_a_digest() -> None:
    assert len({_v1().digest(), _v2().digest(), _recovery().digest()}) == 3


# ── the digest NAME is deliberate ──────────────────────────────────────────


def test_a_v2_document_still_produces_an_ExecutionPlanDigestV1() -> None:
    """Ruled by Michael and recorded here so it is not "fixed".

    The VALUE schema and the DOCUMENT schema are separate names on purpose.
    Control freezes the value and never parses the document — it has no
    canonicalizer for it, by design — so a second document version does not make
    a second value type, and the two repositories binding to the value name keep
    binding to it. A reader who "corrects" this breaks both at once.
    """
    assert EXECUTION_PLAN_DIGEST_SCHEMA == "ExecutionPlanDigestV1"
    assert _v2().as_document()["schema"] == EXECUTION_PLAN_V2_SCHEMA
    assert _v2().digest().startswith("sha256:")


def test_the_reason_is_recorded_where_the_next_reader_meets_it() -> None:
    """Not a style check. The edit this guards against looks like a tidy-up, so
    the argument has to be in the module a person opens, not in a changelog."""
    source = (PACKAGE / "execution_plan_v2.py").read_text(encoding="utf-8")
    assert "value schema and the document schema" in source
    assert "never parses the document" in source


# ── the bootstrap member carries a REFERENCE, never material ───────────────


def test_the_member_has_no_field_material_could_go_in() -> None:
    """The structure, asserted as a key set. Not "a guard rejects a password" —
    there is nowhere to put one."""
    document = _bootstrap().as_document()
    assert set(document) == {
        "expected_version",
        "principal",
        "secret_field",
        "secret_path",
        "service",
        "transition",
    }
    for forbidden in ("password", "dsn", "sql", "command", "value", "credential"):
        assert forbidden not in document


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("secret_path", "hunter2hunter2hunter2", BOOTSTRAP_BAD_REFERENCE),
        ("secret_path", "postgres://u:p@h/db", BOOTSTRAP_BAD_REFERENCE),
        ("secret_field", "ALTER ROLE x PASSWORD 'y'", BOOTSTRAP_BAD_REFERENCE),
        ("principal", 'x"; DROP ROLE y; --', BOOTSTRAP_BAD_PRINCIPAL),
        ("principal", "Platform_Outbox", BOOTSTRAP_BAD_PRINCIPAL),
        ("service", "platform cp", BOOTSTRAP_BAD_PRINCIPAL),
    ],
)
def test_every_field_refuses_material_or_a_statement(
    field: str, value: str, code: str
) -> None:
    """PostgreSQL permits a quoted identifier containing almost anything —
    including a quote, which is how an identifier becomes a statement. This
    facility is deliberately narrower."""
    with pytest.raises(SpecError) as exc:
        _bootstrap(**{field: value})
    assert exc.value.code == code


def test_the_secrets_belt_DOES_reach_the_new_member() -> None:
    """The half the structure makes unreachable, proved anyway.

    Every field above refuses material at construction, so `require_no_secrets`
    can never fire on a real bootstrap — which would leave the belt covering the
    new member only by assumption. Planted on a hand-built document instead: if
    the type were ever relaxed, the guard is already there.
    """
    document = _v2().as_document()
    document["principal_bootstraps"][0]["secret_path"] = (
        "postgres://app:hunter2hunter2@db.internal:5432/platform"
    )
    with pytest.raises(SecretValueError):
        require_no_secrets(document, source="planted")


def test_a_real_v2_plan_passes_the_belt() -> None:
    """Positive control for the test above: the guard must not be one that
    refuses every document it is shown."""
    require_no_secrets(_v2().as_document(), source="real")


# ── expected_version 1 IS the compare-and-set ──────────────────────────────


@pytest.mark.parametrize("bad", [0, -1, 2, True, 1.0, "1", None])
def test_an_absent_to_present_bootstrap_must_expect_version_1(bad: object) -> None:
    """Version 1 is a compare-and-set against "no record exists". Any other
    expectation lets a second run overwrite a credential other systems already
    hold — a rotation nobody asked for, wearing a bootstrap's name.

    `True` is in the list because `bool` is an `int` in Python and would sail
    through a bare `isinstance(value, int)` as the version 1.
    """
    with pytest.raises(SpecError) as exc:
        _bootstrap(expected_version=bad)
    assert exc.value.code == BOOTSTRAP_BAD_VERSION


def test_the_transition_vocabulary_is_closed() -> None:
    assert BOOTSTRAP_TRANSITIONS == ("absent_to_present",)
    with pytest.raises(SpecError) as exc:
        _bootstrap(transition="rotate")
    assert exc.value.code == BOOTSTRAP_BAD_VERSION


# ── the member is part of the binding ──────────────────────────────────────


def test_the_bootstraps_are_inside_the_digest() -> None:
    """An approval for a deployment that installs no credential must not
    authorize one that does."""
    assert _v2(principal_bootstraps=()).digest() != _v2().digest()


def test_a_different_principal_is_a_different_plan() -> None:
    other = _bootstrap(principal="platform_reader")
    assert _v2(principal_bootstraps=(other,)).digest() != _v2().digest()


def test_bootstraps_are_a_SET_so_listing_order_cannot_change_the_digest() -> None:
    one, two = _bootstrap(), _bootstrap(principal="platform_reader")
    assert (
        _v2(principal_bootstraps=(one, two)).digest()
        == _v2(principal_bootstraps=(two, one)).digest()
    )


def test_one_principal_may_not_appear_twice() -> None:
    with pytest.raises(SpecError) as exc:
        _v2(principal_bootstraps=(_bootstrap(), _bootstrap()))
    assert exc.value.code == EXECUTION_PLAN_V2_WRONG_TYPE


def test_a_raw_mapping_is_not_a_bootstrap() -> None:
    """A mapping would let any key through, including one holding a password."""
    with pytest.raises(SpecError) as exc:
        _v2(principal_bootstraps=({"principal": "x", "password": "hunter2"},))
    assert exc.value.code == EXECUTION_PLAN_V2_WRONG_TYPE


def test_v1_is_unchanged_and_carries_no_bootstraps() -> None:
    """V2 exists so V1 does not have to move. Two other repositories compute or
    compare V1's digest."""
    assert "principal_bootstraps" not in _v1().as_document()
    assert not hasattr(_v1(), "principal_bootstraps")


def test_the_shared_terms_have_ONE_renderer() -> None:
    """`render_execution_plan_v2` takes the rendered V1 rather than re-deriving
    twelve fields, so the two cannot come to disagree about what a deployment IS
    while differing only in what it additionally does."""
    v1, v2 = _v1(), _v2()
    for field in (
        "product",
        "target",
        "operation",
        "image_digest",
        "descriptor_digest",
        "manifest_digest",
        "strategy",
    ):
        assert v2.as_document()[field] == v1.as_document()[field]


# ── unreachable, and derived rather than stated ────────────────────────────


def test_nothing_on_a_host_path_constructs_a_v2_plan() -> None:
    """No `StepKind` for the act, no `Effects` method to invoke it, no CLI
    subcommand — because adding an `Effects` method widens a protocol whose
    implementers include the probe wheel the PUBLICATION GATE installs, which
    would make the gate's own fixture non-conforming until updated. That is held
    pending a ruling; this module is the half that does not depend on it.

    When the invocation half lands, this test is what says so out loud. Update it
    in that change; do not delete it.
    """
    importers = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in {"execution_plan_v2.py", "__init__.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                "execution_plan_v2"
            ):
                importers.append(path.name)
    assert importers == [], (
        f"{importers} import execution_plan_v2 — read this test's docstring "
        "before changing it"
    )


def test_the_operation_vocabulary_did_not_widen() -> None:
    """V2 adds a capability, not an operation. `recover` stays out."""
    from dotmac_deployment_foundation.authorization import OPERATIONS

    assert set(OPERATIONS) == {"deploy", "rollback"}
