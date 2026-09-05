"""Lane 3 may not treat dispatch text as authorization.

`scripts/exposure_rehearsal_runner.py` accepted `--authorization-run` and
`--authorization-doc-digest` as `workflow_dispatch` strings and executed on
them. The only comparison ever made was `lease.covers(authorization_run_id=...)`
— a string equality against a lease record the same operator writes — and the
digest reached `build_receipt`, which asserted it equalled the descriptor
digest, a value any caller computes locally from a file in this repository.
Nothing imported `provenance.py` or `authorization.py`; no `VerifiedAuthorization`
and no `ExecutionGrant` were ever constructed. So a fabricated run id and a
computed digest drove the lane green with no authorization in existence.

Every test here is written the way ADR-0018 requires a detector to be written:
the defect is PLANTED and observed to be refused, and the refusal is then shown
to be capable of admitting — because a check that refuses every input passes its
own negative case for the wrong reason. The admit is reachable precisely because
`discover_bindings` takes injected `entries`, so a stub verifier can stand where
an assembly's would.

The three-status rule is the second thing under test. `Standing.exit_status`
must keep a refusal (1) and an unanswerable question (2) apart, in both
directions: today's environment has no verifier at all and must report 2, while
an environment that CAN answer and answers no must report 1. Collapsing either
into the other is how an indeterminate gate comes to read as a pass.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pathlib
import sys
from datetime import UTC, datetime

import pytest
from dotmac_deployment_foundation.execution_bindings import (
    ENTRY_POINT_GROUP as PACKAGE_ENTRY_POINT_GROUP,
)
from dotmac_deployment_foundation.execution_bindings import ExecutionBindings

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_authorization as authorization  # noqa: E402

RUNNER = ROOT / "scripts" / "exposure_rehearsal_runner.py"
WORKFLOW = ROOT / ".github" / "workflows" / "exposure-rehearsal.yml"
FIXTURE = ROOT / "scripts" / "exposure-rehearsal" / "product.toml"

#: A REAL OpenSSH fingerprint — `SHA256:` and 43 characters of unpadded base64.
FINGERPRINT = "SHA256:T1kdK/6QTzzwU1EienO6nUgk8wu9UpjqB8BatKbndSE"

DESCRIPTOR_DIGEST = "sha256:" + "a" * 64
TARGET = "lane3-rehearsal-target"
#: Inside the receipt's approval window, injected rather than read from a clock.
NOW = datetime(2026, 9, 5, 1, 0, 0, tzinfo=UTC)


def _load_runner():  # type: ignore[no-untyped-def]
    """Import the runner by path — it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location("_lane3_authz_runner", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE execution: the module imports its own siblings, and a
    # half-initialised entry is what a partially executed module needs to find.
    sys.modules["_lane3_authz_runner"] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


# ── the stubs that make the ADMIT reachable ─────────────────────────────────


class _Attesting:
    """A verifier that vouches for whatever it is handed.

    Correct ONLY as a test double, and it is what an assembly's real verifier
    replaces: it decides nothing about signatures, which is exactly why the
    Foundation refuses to ship one. Its job here is to make the admit path
    reachable so the refusals below can be shown to discriminate.
    """

    def attest(self, material):  # type: ignore[no-untyped-def]
        return material


class _Refusing:
    """A verifier that judges the material and says no. An ANSWER, not a gap."""

    def attest(self, material):  # type: ignore[no-untyped-def]
        raise ValueError("the envelope signature does not verify")


class _Entry:
    """The shape `importlib.metadata` hands `discover_one`: a name and a load."""

    def __init__(self, name: str, bindings: ExecutionBindings) -> None:
        self.name = name
        self._bindings = bindings
        self.dist = type("_Dist", (), {"name": "lane3-test-bindings"})()

    def load(self):  # type: ignore[no-untyped-def]
        return lambda: self._bindings


def _entries(verifier: object | None) -> list[_Entry]:
    bindings = ExecutionBindings(
        provider="lane3-test-bindings",
        # Something must be injected or `ExecutionBindings` refuses an empty
        # declaration, so a bindings object WITHOUT a verifier still needs a
        # second field — which is the `bindings but no verifier` case below.
        build_effects=(lambda spec, deploy_dir: None) if verifier is None else None,
        authorization_verifier=verifier,  # type: ignore[arg-type]
    )
    return [_Entry("lane3-test-bindings", bindings)]


def _receipt(
    *,
    descriptor_digest: str = DESCRIPTOR_DIGEST,
    target: str = TARGET,
    operation: str = "deploy",
) -> dict[str, object]:
    """A structurally complete `AuthorizationReceipt` document, all 14 keys."""
    return {
        "plan_id": "plan-lane3-1",
        "target_ref": target,
        "descriptor_digest": descriptor_digest,
        "execution_plan_digest": "sha256:" + "b" * 64,
        "execution_sequence": 1,
        "attempt_no": 1,
        "control_plan_digest": "c" * 64,
        "policy_code": "lane3.rehearsal",
        "policy_version": 1,
        "decision_ref": "decision-lane3-1",
        "approved_at": "2026-09-05T00:00:00+00:00",
        "expires_at": "2026-09-05T12:00:00+00:00",
        "control_version": "0.1.0a11",
        "operation": operation,
    }


def _document(tmp_path: pathlib.Path, content: object) -> str:
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


def _establish(**overrides):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "descriptor_digest": DESCRIPTOR_DIGEST,
        "target": TARGET,
        "authorization_document": None,
        "now": NOW,
    }
    values.update(overrides)
    return authorization.establish_authorization(**values)  # type: ignore[arg-type]


# ── the planted defect: fabricated authorization is refused ─────────────────


def test_a_fabricated_authorization_is_refused_and_the_run_never_begins() -> None:
    """THE defect, planted exactly as the workflow used to supply it.

    A run id nobody issued and a document digest the caller computed from the
    descriptor sitting in this repository. Both used to be accepted; neither can
    reach `establish_authorization` now, because neither is a parameter of it —
    and with nothing installed to attest anything, the answer is that the
    question has no answer here.
    """
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        _establish(entries=[])
    assert raised.value.standing is authorization.Standing.UNANSWERABLE
    assert raised.value.exit_status == 2


def test_the_refusal_names_what_is_missing_rather_than_only_that_it_refused() -> None:
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        _establish(entries=[])
    rendered = raised.value.render()
    assert authorization.ENTRY_POINT_GROUP in rendered
    assert "verifier_implementation" in rendered
    assert "bindings_entry_point" in rendered
    # It must also say what it will never accept, or the next reader supplies
    # the digest again and wonders why it did not count.
    assert "never believed" in rendered


def test_a_bindings_distribution_with_no_verifier_is_still_unanswerable() -> None:
    """Injecting effects is not injecting trust roots.

    The near miss: a distribution declares the entry point, so `discover_one`
    returns bindings and a check that stopped at "were bindings found" would
    admit. The question is still unanswerable — nothing can attest.
    """
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        _establish(entries=_entries(None))
    assert raised.value.standing is authorization.Standing.UNANSWERABLE
    assert raised.value.unmet == ("verifier_implementation",)


# ── the same question, ANSWERABLE: refusals become violations ───────────────


def test_a_verifier_with_no_document_is_a_violation_not_indeterminate() -> None:
    """The direction the docstring rule exists for.

    With a verifier installed the environment CAN answer, so a run that cites an
    authorization it cannot show is refused (1) rather than reported as an open
    question (2). A run id plus a digest is a claim about a document, never the
    document.
    """
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        _establish(entries=_entries(_Attesting()))
    assert raised.value.standing is authorization.Standing.UNATTESTABLE
    assert raised.value.exit_status == 1
    assert raised.value.unmet == ("signed_document_reaches_the_runner",)


def test_material_the_verifier_refuses_is_an_answer_not_an_absence(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        _establish(
            entries=_entries(_Refusing()),
            authorization_document=_document(tmp_path, _receipt()),
        )
    assert raised.value.standing is authorization.Standing.UNATTESTABLE


def test_an_attested_receipt_for_another_descriptor_does_not_cover_this_run(
    tmp_path: pathlib.Path,
) -> None:
    """Authentic terms, wrong subject. The binding has to bind."""
    other = _receipt(descriptor_digest="sha256:" + "d" * 64)
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        _establish(
            entries=_entries(_Attesting()),
            authorization_document=_document(tmp_path, other),
        )
    assert raised.value.standing is authorization.Standing.UNATTESTABLE


def test_an_attested_receipt_for_another_target_does_not_cover_this_run(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        _establish(
            entries=_entries(_Attesting()),
            authorization_document=_document(
                tmp_path, _receipt(target="some-other-host")
            ),
        )
    assert raised.value.standing is authorization.Standing.UNATTESTABLE


def test_a_rollback_approval_does_not_authorize_the_deploy_this_lane_rehearses(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(authorization.AuthorizationUnverifiable) as raised:
        _establish(
            entries=_entries(_Attesting()),
            authorization_document=_document(tmp_path, _receipt(operation="rollback")),
        )
    assert raised.value.standing is authorization.Standing.UNATTESTABLE


# ── the negative control: the check is not refusing everything ──────────────


def test_an_attested_receipt_for_this_run_ADMITS(tmp_path: pathlib.Path) -> None:
    """The sensitivity proof ADR-0018 demands.

    Without this every assertion above is satisfied by a function that raises
    unconditionally, and a gate observed only at the value it was written to
    return has never been observed at all. A verifier vouches for a receipt
    naming THIS descriptor, THIS target and the deploy operation, and a real
    `ExecutionGrant` comes back — one that `authorize()` alone can mint.
    """
    grant = _establish(
        entries=_entries(_Attesting()),
        authorization_document=_document(tmp_path, _receipt()),
    )
    assert grant.operation == "deploy"
    assert grant.target == TARGET
    assert grant.descriptor_digest == DESCRIPTOR_DIGEST
    # Carried FROM the receipt, which is the only place it may come from.
    assert grant.execution_plan_digest == "sha256:" + "b" * 64
    grant.require(operation="deploy", descriptor_digest=DESCRIPTOR_DIGEST)


def test_standing_of_reports_attested_with_exit_zero(tmp_path: pathlib.Path) -> None:
    standing, report = authorization.standing_of(
        descriptor_digest=DESCRIPTOR_DIGEST,
        target=TARGET,
        authorization_document=_document(tmp_path, _receipt()),
        now=NOW,
        entries=_entries(_Attesting()),
    )
    assert standing is authorization.Standing.ATTESTED
    assert standing.exit_status == 0
    assert "attested" in report


def test_the_three_statuses_are_distinct_and_never_collapse() -> None:
    statuses = {member: member.exit_status for member in authorization.Standing}
    assert sorted(statuses.values()) == [0, 1, 2]
    assert authorization.Standing.UNATTESTABLE.exit_status == 1
    assert authorization.Standing.UNANSWERABLE.exit_status == 2


# ── the preconditions are the deliverable, so they are checked ──────────────


def test_every_precondition_is_uniquely_coded_and_fully_stated() -> None:
    codes = [p.code for p in authorization.PRECONDITIONS]
    assert len(codes) == len(set(codes)), "two preconditions share a code"
    assert codes, "the list may not be empty; an empty list refuses nothing"
    for precondition in authorization.PRECONDITIONS:
        for field in ("statement", "owner", "evidence"):
            value = getattr(precondition, field)
            assert value.strip(), f"{precondition.code} has an empty {field}"


def test_at_least_one_precondition_is_stated_rather_than_enforced() -> None:
    """The honesty check.

    A list where every entry claimed to be observable would be claiming this
    repository can decide questions it cannot — the issuer/verifier schema
    translation and gate item 9's middle term are both stated, and a list that
    hid that would be the checklist-as-coverage failure ADR-0018 names.
    """
    assert any(not p.observable for p in authorization.PRECONDITIONS)
    assert any(p.observable for p in authorization.PRECONDITIONS)


def test_every_cited_precondition_code_exists() -> None:
    """A refusal that cited a code nobody declared would render nothing."""
    declared = {p.code for p in authorization.PRECONDITIONS}
    cited: set[str] = set()
    tree = ast.parse(pathlib.Path(authorization.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "unmet":
                continue
            if isinstance(keyword.value, ast.Tuple):
                cited |= {
                    element.value
                    for element in keyword.value.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                }
    assert cited, "no refusal cites a precondition; the list would be decoration"
    assert cited <= declared, f"refusals cite undeclared codes: {cited - declared}"


def test_the_entry_point_group_matches_the_package_it_names() -> None:
    """The literal is restated here on purpose, so it is compared here too."""
    assert authorization.ENTRY_POINT_GROUP == PACKAGE_ENTRY_POINT_GROUP


# ── the runner: the refusal reaches it, before the lease and before the host ─


def _runner_argv(tmp_path: pathlib.Path) -> list[str]:
    """The exact shape the workflow dispatches, with the authorization FAKED."""
    probe = tmp_path / "probe-evidence.json"
    probe.write_text("{}", encoding="utf-8")
    return [
        "--foundation-revision",
        "a" * 40,
        "--candidate-source-revision",
        "b" * 40,
        "--foundation-artifact",
        "sha256:" + "e" * 64,
        # A run id nobody issued. It used to be enough.
        "--authorization-run",
        "authz-fabricated-0000",
        "--controller-identity",
        FINGERPRINT,
        "--controller-key",
        str(tmp_path / "controller-key"),
        "--target",
        TARGET,
        "--vm-slot",
        "dotmacproxmox/102",
        "--candidate-version",
        "0.4.0a1",
        "--probe-host",
        "probe.example",
        "--inside-vantage",
        "inside.example",
        "--inside-jump-key",
        str(tmp_path / "jump-key"),
        "--observer-user",
        "lane3obs",
        "--observer-key",
        str(tmp_path / "observer-key"),
        "--probe-evidence",
        str(probe),
        "--descriptor",
        str(FIXTURE),
        "--receipt-out",
        str(tmp_path / "receipt.json"),
        "--lease-dir",
        str(tmp_path / "leases"),
        "--terminal-evidence-out",
        str(tmp_path / "terminal.json"),
    ]


def test_the_runner_refuses_a_fabricated_authorization_with_status_two(
    tmp_path: pathlib.Path,
) -> None:
    """End to end through the runner's own entry point.

    Nothing is stubbed here: no verifier is installed in this environment, which
    is the measured state of the fleet, so the run stops at the authorization
    with exit 2. Before this change the same argv reached the lease.
    """
    assert runner.main(_runner_argv(tmp_path)) == 2


def test_the_runner_writes_no_receipt_and_takes_no_lease_when_it_refuses(
    tmp_path: pathlib.Path,
) -> None:
    """Where the refusal SITS is the property, not just that it refuses.

    A refusal after `load_lease` would have taken a shared host; a refusal after
    the apply would have mutated one. The evidence sidecar records
    `lease_in_hand: false` and `host_mutation_attempted: false`, and no receipt
    exists — so the host keeps exactly the standing it already had.
    """
    assert runner.main(_runner_argv(tmp_path)) == 2
    assert not (tmp_path / "receipt.json").exists()
    evidence = json.loads((tmp_path / "terminal.json").read_text(encoding="utf-8"))
    assert evidence["lease_in_hand"] is False
    assert evidence["host_mutation_attempted"] is False
    assert evidence["outcome"] == {"receipt_digest": "", "refusal": ""}


def test_the_runner_declares_no_way_to_supply_an_authorization_digest() -> None:
    """The removed flag may not come back under any spelling.

    `--authorization-doc-digest` was proof by assertion: a value the caller
    computed, compared against a value the caller could also compute. A parser
    that offered it again would make the defect representable again, whatever
    the code behind it did.
    """
    parser_flags = _declared_flags()
    assert "--authorization-document" in parser_flags
    assert not [
        flag for flag in parser_flags if "digest" in flag and "authorization" in flag
    ]


def _declared_flags() -> set[str]:
    """Every `--flag` the runner's parser declares, read statically."""
    source = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    flags: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("--"):
                flags.add(node.value)
    return flags


def test_the_receipts_middle_term_is_not_read_off_the_command_line() -> None:
    """`build_receipt`'s authorized-plan term must come from the GRANT.

    This is the assertion that would have caught the original defect on its own:
    the term flowed straight from `args.authorization_doc_digest` into the
    receipt, so item 9 compared the descriptor against a number the caller typed.
    It now comes off an attested receipt. It is still not
    `ExecutionPlanDigestV1` — see the `middle_term_is_the_execution_plan_digest`
    precondition, which says so rather than letting the substitution pass as a
    repair.
    """
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_receipt"
    ]
    assert len(calls) == 1, "one runner, one receipt construction"
    term = next(
        keyword.value
        for keyword in calls[0].keywords
        if keyword.arg == "authorization_document_digest"
    )
    sources = {node.id for node in ast.walk(term) if isinstance(node, ast.Name)}
    assert "args" not in sources, "the authorized-plan term came off the argv"
    assert "grant" in sources, "the authorized-plan term is not from the grant"


def test_the_runner_establishes_authorization_before_it_loads_the_lease() -> None:
    """Order is the guarantee, and order is what a later commit moves.

    `precondition_unfit` asserts the host was never touched. That claim is only
    true while this call sits ahead of `load_lease` and every mutation, so the
    arrangement is asserted rather than left to a reader to notice.
    """
    source = RUNNER.read_text(encoding="utf-8")
    establish = source.index("establish_authorization(\n")
    lease = source.index("load_lease(args.target")
    assert establish < lease


def test_the_runner_never_calls_the_foundation_verifier_itself() -> None:
    """One owner for the question.

    A second call to `verify_authorization` or `authorize` inside the runner
    would be a second answer, free to drift from the one the gate reports — and
    the drift would only show up on the day an authorization finally existed.
    """
    source = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "verify_authorization" not in called
    assert "authorize" not in called


def test_the_gate_reports_indeterminate_from_its_own_command_line(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The preflight the workflow runs, exercised as the workflow runs it."""
    status = authorization.main(["--descriptor", str(FIXTURE), "--target", TARGET])
    assert status == 2
    captured = capsys.readouterr()
    assert "unanswerable" in captured.err
    assert captured.out == "", "an unanswerable gate must not report on stdout"


def test_a_namespace_carrying_the_old_flag_is_not_silently_honoured() -> None:
    """A stale caller does not get its digest believed.

    An `argparse.Namespace` built by hand — a shim, an old workflow, a test —
    could still carry `authorization_doc_digest`. It reaches nothing: the value
    is not a parameter of `establish_authorization`, so there is no argument for
    it to be passed as.
    """
    stale = argparse.Namespace(
        authorization_doc_digest=DESCRIPTOR_DIGEST, target=TARGET
    )
    with pytest.raises(authorization.AuthorizationUnverifiable):
        _establish(entries=[], target=stale.target)


# ── the workflow: the gate runs, and it runs before anything is touched ─────


def _workflow() -> dict:
    import yaml

    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _rehearse_bodies() -> list[str]:
    return [
        str(step.get("run", "")) for step in _workflow()["jobs"]["rehearse"]["steps"]
    ]


def test_the_dispatch_cannot_supply_an_authorization_digest_any_more() -> None:
    """The fabrication surface is removed at the dispatch, not just guarded.

    `authorization_doc_digest` was a REQUIRED workflow input, so every dispatch
    typed one and the lane consumed it as the authorized-plan term. An input a
    dispatcher fills and nothing can check is proof by assertion.
    """
    document = _workflow()
    # PyYAML 1.1 parses the unquoted YAML key `on` as True.
    trigger = document.get("on", document.get(True, {}))
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert "authorization_doc_digest" not in inputs
    assert not [name for name in inputs if "digest" in name]


def test_the_workflow_gates_on_the_authorization_before_it_touches_anything() -> None:
    """Order again, this time in the lane rather than in the runner.

    The gate must sit after the candidate is installed — it asks about THAT
    interpreter — and before the probe collection, which SSHes to the vantage
    and to the target. A gate placed after them would refuse a run that had
    already reached two hosts.
    """
    bodies = _rehearse_bodies()
    gate = next(
        index
        for index, body in enumerate(bodies)
        if "scripts/lane3_authorization.py" in body
    )
    install = next(
        index for index, body in enumerate(bodies) if "pip install --no-deps" in body
    )
    probe = next(
        index
        for index, body in enumerate(bodies)
        if "collect_probe_evidence.sh" in body
    )
    execute = next(
        index
        for index, body in enumerate(bodies)
        if "scripts/exposure_rehearsal_runner.py" in body
    )
    assert install < gate < probe < execute


def test_the_workflow_asks_the_gate_in_the_candidate_interpreter() -> None:
    """Whether a verifier is installed is a fact about ONE environment.

    Asking with the checkout's interpreter would answer about the source tree
    beside the wheel, which is the substitution the whole lane is built to
    prevent — and it would answer `yes` for a Foundation that was never
    published.
    """
    gate = next(
        body for body in _rehearse_bodies() if "scripts/lane3_authorization.py" in body
    )
    assert ".lane3-foundation/bin/python -E -P scripts/lane3_authorization.py" in gate
