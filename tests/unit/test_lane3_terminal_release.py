"""Lane 3 must RECORD how it ended, and the record must be one it can defend.

`HostLeaseRelease.v1` shipped in #618 with no writer anywhere. `load_lease`
refused to BEGIN without a lease record while the END of every run was an
inference — a process gone, a timestamp passed — and the decision that reads it
is whether a machine may be wiped. This file exercises the writer that closes
that half.

Every test here fails before the change for the same structural reason: until
this commit `scripts/exposure_rehearsal_runner.py` contained no reference to
`HostLeaseRelease`, `TerminalOutcome`, `CapturingRunner`, `build_release`,
`record_terminal` or `classify_refusal` — a grep for `HostLeaseRelease` across
the tree found the schema, its own test, the changelog and the release-facility
manifest, and nothing else. The names these tests import did not exist, so each
one fails at import or attribute lookup.

Lane 3 cannot be run to establish any of this: the programme is under a NO-GO,
there is no registered control runner, and the target is a leased host. A unit
test over the pure decisions is the only thing that can show these guards bite,
which is why every one of them is written as a plantable defect with its
near-miss beside it.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pathlib
import sys

import pytest
import yaml
from dotmac_deployment_foundation.engine.run import CommandResult
from dotmac_deployment_foundation.errors import (
    DeploymentFoundationError,
    PreconditionFailed,
    SpecError,
    StepFailed,
)
from dotmac_deployment_foundation.lease import HostLease
from dotmac_deployment_foundation.lease_release import (
    CleanupDisposition,
    HostClosure,
    HostLeaseReleaseV1,
    ReleasingPrincipal,
    TerminalOutcome,
    TerminalRefusal,
    lease_digest,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "exposure_rehearsal_runner.py"
WORKFLOW = ROOT / ".github" / "workflows" / "exposure-rehearsal.yml"


def _load():  # type: ignore[no-untyped-def]
    """Import the runner by path — it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location("_lane3_runner", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE execution: the module imports its own siblings, and a
    # half-initialised entry is what a partially executed module needs to find.
    sys.modules["_lane3_runner"] = module
    spec.loader.exec_module(module)
    return module


runner = _load()

#: A sha256 shape, which is what `host_mutation_evidence` is validated against.
FINGERPRINT = "sha256:" + "ab" * 32
SUBJECT = "repo:michaelayoade/dotmac_starter_mt:ref:refs/heads/main"
RUN_BINDING = "github-actions:michaelayoade/dotmac_starter_mt:9001:1"


def _lease() -> HostLease:
    return HostLease(
        target="lane3-rehearsal-target",
        holder="deployment-foundation-rehearsal",
        authorization_run_id="authz-77",
        starts_at="2026-09-04T00:00:00+00:00",
        expires_at="2026-09-04T06:00:00+00:00",
        compose_project_prefix="lane3_",
        controller_identity_fingerprint=FINGERPRINT,
        workload_principal=SUBJECT,
    )


def _args(tmp_path: pathlib.Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "target": "lane3-rehearsal-target",
        "vm_slot": "dotmacproxmox/102",
        "candidate_version": "0.3.0a5",
        "foundation_revision": "2288b4d68f6b93d3e391d0dafa04987fb3f750f7",
        "authorization_run": "authz-77",
        "controller_identity": FINGERPRINT,
        "lease_dir": str(tmp_path / "leases"),
        "release_out": str(tmp_path / "release.json"),
        "terminal_evidence_out": str(tmp_path / "evidence.json"),
        "principal_audience": "dotmac-lane3-release",
        "timeout": 5,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _context(**overrides: object) -> object:
    ctx = runner.TerminalContext()
    ctx.lease = _lease()
    for name, value in overrides.items():
        setattr(ctx, name, value)
    return ctx


@pytest.fixture()
def proven(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proven workload principal, without reaching a token endpoint."""
    monkeypatch.setattr(
        runner,
        "prove_principal",
        lambda **_kwargs: (
            ReleasingPrincipal(
                kind="github_actions_workload",
                subject=SUBJECT,
                run_binding=RUN_BINDING,
            ),
            RUN_BINDING,
        ),
    )


# ── the two constraints, and the intersection that is neither of them ───────


def test_a_receipt_releases_the_host_as_reusable(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """The positive control. Refusing everything would score full marks without it."""
    ctx = _context()
    ctx.record_cleanup(
        runner.CleanupAct("disarm", CleanupDisposition.PURGED, "all successful")
    )
    release = runner.build_release(
        ctx,
        _args(tmp_path),
        TerminalOutcome(receipt_digest="sha256:" + "cd" * 32),
    )
    assert release.closure is HostClosure.REUSABLE
    assert release.cleanup is CleanupDisposition.PURGED
    assert release.lease_digest == lease_digest(_lease())
    assert release.released_by.subject == SUBJECT
    assert release.rehearsal_run_id == RUN_BINDING


def test_precondition_unfit_with_an_uncaptured_cleanup_is_not_reusable(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """THE case the intersection exists for.

    `precondition_unfit` is unrestricted on the refusal axis and describes a
    host nothing touched — the most releasable refusal there is. An uncaptured
    cleanup is restricted on the other axis. Either constraint consulted alone
    lets this through, and a host the next lease inherits as clean is the
    failure both of them are written against.
    """
    ctx = _context(host_mutated=False)
    ctx.record_cleanup(
        runner.CleanupAct(
            "withdraw",
            CleanupDisposition.OUTCOME_UNKNOWN,
            "attempted, nothing captured",
        )
    )
    release = runner.build_release(
        ctx,
        _args(tmp_path),
        TerminalOutcome(refusal=TerminalRefusal.PRECONDITION_UNFIT),
    )
    assert release.closure is not HostClosure.REUSABLE
    assert release.closure is HostClosure.INSPECTION_REQUIRED


def test_the_refusal_axis_alone_would_have_allowed_that(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """The planted near-miss, which is what makes the test above mean anything.

    Same refusal, cleanup captured as purged: `reusable`. So the refusal axis
    genuinely permits `reusable` for `precondition_unfit`, and the answer above
    changed because of the CLEANUP axis rather than because this refusal was
    restricted all along. Without this, a writer that always answered
    `inspection_required` would pass the test above.
    """
    ctx = _context(host_mutated=False)
    ctx.record_cleanup(
        runner.CleanupAct("withdraw", CleanupDisposition.PURGED, "all successful")
    )
    release = runner.build_release(
        ctx,
        _args(tmp_path),
        TerminalOutcome(refusal=TerminalRefusal.PRECONDITION_UNFIT),
    )
    assert release.closure is HostClosure.REUSABLE


def test_the_cleanup_axis_alone_would_have_allowed_the_other_direction(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """The symmetric half: a clean cleanup does not launder a failed provocation.

    `provocation_unestablished` is the one refusal where the host was mutated
    and the mutation failed. A purged cleanup is unrestricted on its own axis,
    so a writer consulting only the cleanup would answer `reusable` here.
    """
    ctx = _context(host_mutated=True)
    ctx.record_cleanup(
        runner.CleanupAct("withdraw", CleanupDisposition.PURGED, "all successful")
    )
    release = runner.build_release(
        ctx,
        _args(tmp_path),
        TerminalOutcome(refusal=TerminalRefusal.PROVOCATION_UNESTABLISHED),
    )
    assert release.closure is HostClosure.INSPECTION_REQUIRED


# ── the four cleanup dispositions, each with the answer it must not borrow ──


class _Inner:
    """A controller runner standing in for SSH, answering a scripted exit code."""

    def __init__(self, *exit_codes: int) -> None:
        self.exit_codes = list(exit_codes)
        self.calls: list[list[str]] = []

    def __call__(
        self,
        argv: list[str],
        *,
        timeout: int = 60,
        env: dict[str, str] | None = None,
        capture: bool = True,
    ) -> CommandResult:
        self.calls.append(list(argv))
        code = self.exit_codes.pop(0) if self.exit_codes else 0
        return CommandResult(exit_code=code, stdout="", stderr="denied")


def test_a_cleanup_that_issued_a_failing_command_is_failed() -> None:
    """The planted defect, and it is the live one.

    `withdraw_foreign_rules` issues its deletes and ignores whether they
    worked, so a foreign rule still sitting in the chain reads today as a clean
    run. Watching the runner both of them call is what turns that discarded exit
    code back into a fact.
    """
    controller = runner.CapturingRunner(_Inner(1))

    def act() -> None:
        controller(["ip6tables", "-D", "X"])

    recorded = runner.attempt_cleanup("withdraw", controller, act)
    assert recorded.disposition is CleanupDisposition.FAILED
    assert "still there" in recorded.detail


def test_a_cleanup_whose_every_command_succeeded_is_purged() -> None:
    """The near-miss. A detector that called everything `failed` would be useless."""
    controller = runner.CapturingRunner(_Inner(0, 0))

    def act() -> None:
        controller(["iptables", "-D", "X"])
        controller(["ip6tables", "-D", "X"])

    assert (
        runner.attempt_cleanup("withdraw", controller, act).disposition
        is CleanupDisposition.PURGED
    )


def test_an_attempted_cleanup_with_nothing_observed_is_unknown_not_purged() -> None:
    """`outcome_unknown` must not borrow `purged`'s answer.

    An act that ran and issued no observable command certifies nothing about
    what it was to remove. Answering `purged` there is a claim; answering
    `not_attempted` is a different claim, and both look safer than the truth.
    """
    controller = runner.CapturingRunner(_Inner())
    act = runner.attempt_cleanup("disarm", controller, lambda: None)
    assert act.disposition is CleanupDisposition.OUTCOME_UNKNOWN
    assert act.disposition is not CleanupDisposition.NOT_ATTEMPTED


def test_a_cleanup_that_raised_after_issuing_commands_is_unknown() -> None:
    """A timeout mid-cleanup: it was attempted, and whether it finished is not known."""
    controller = runner.CapturingRunner(_Inner(0))

    def act() -> None:
        controller(["iptables", "-D", "X"])
        raise TimeoutError("ssh timed out")

    recorded = runner.attempt_cleanup("withdraw", controller, act)
    assert recorded.disposition is CleanupDisposition.OUTCOME_UNKNOWN
    assert "TimeoutError" in recorded.detail


def test_a_cleanup_failure_never_escapes_into_the_verdict() -> None:
    """The receipt is fixed by the time cleanup runs; a failure is about the HOST."""
    controller = runner.CapturingRunner(_Inner())

    def act() -> None:
        raise StepFailed("exposure", "ip6tables refused")

    recorded = runner.attempt_cleanup("disarm", controller, act)
    assert recorded.disposition is CleanupDisposition.FAILED


def test_an_act_that_never_ran_is_not_attempted_and_outranks_purged() -> None:
    """Combining several acts into one field must never round up to `purged`."""
    ctx = _context()
    ctx.record_cleanup(
        runner.CleanupAct("disarm", CleanupDisposition.PURGED, "all successful")
    )
    ctx.record_cleanup(
        runner.CleanupAct(
            "withdraw", CleanupDisposition.NOT_ATTEMPTED, "nothing was seeded"
        )
    )
    assert ctx.cleanup_disposition() is CleanupDisposition.NOT_ATTEMPTED


def test_a_failed_act_outranks_an_unknown_one() -> None:
    ctx = _context()
    ctx.record_cleanup(
        runner.CleanupAct("a", CleanupDisposition.OUTCOME_UNKNOWN, "unknown")
    )
    ctx.record_cleanup(runner.CleanupAct("b", CleanupDisposition.FAILED, "failed"))
    assert ctx.cleanup_disposition() is CleanupDisposition.FAILED


def test_no_act_at_all_is_not_attempted() -> None:
    assert _context().cleanup_disposition() is CleanupDisposition.NOT_ATTEMPTED


# ── the principal, which is the workload and never anything else ────────────


def test_no_release_without_a_provable_principal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lease nobody can close is safer than one closed by an unprovable claim."""
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    args = _args(tmp_path)
    ctx = _context()
    digest = runner.record_terminal(
        ctx, args, TerminalOutcome(refusal=TerminalRefusal.EVIDENCE_UNREADABLE)
    )
    assert digest == ""
    assert not pathlib.Path(args.release_out).exists()
    evidence = json.loads(pathlib.Path(args.terminal_evidence_out).read_text())
    assert evidence["release_digest"] == ""
    assert any("NO RELEASE WRITTEN" in note for note in evidence["notes"])


def test_the_controller_fingerprint_is_never_used_as_the_principal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planted defect: a fallback from the identity token to the CLI.

    `--controller-identity` is present and valid on this run, and it is the
    obvious thing a writer under pressure reaches for. It is the key that
    MUTATED the host, not the party that closed the lease, and it must not
    stand in.
    """
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with pytest.raises(runner.PrincipalUnprovable):
        runner.build_release(
            _context(),
            _args(tmp_path),
            TerminalOutcome(refusal=TerminalRefusal.PRECONDITION_UNFIT),
        )


def test_a_proven_principal_that_does_not_hold_this_lease_writes_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proven is not the same as entitled.

    The destroy gate refuses a release whose principal is not the lease's
    workload. Writing one anyway would flip this host's standing to RELEASED
    while destruction stays refused — the worst of both answers.
    """
    other = "repo:michaelayoade/dotmac_starter_mt:ref:refs/heads/other"
    monkeypatch.setattr(
        runner,
        "prove_principal",
        lambda **_k: (
            ReleasingPrincipal(
                kind="github_actions_workload",
                subject=other,
                run_binding=RUN_BINDING,
            ),
            RUN_BINDING,
        ),
    )
    with pytest.raises(runner.ReleaseNotWritable):
        runner.build_release(
            _context(),
            _args(tmp_path),
            TerminalOutcome(refusal=TerminalRefusal.PRECONDITION_UNFIT),
        )


def test_no_lease_in_hand_means_no_release(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """A refusal before `load_lease` has nothing to discharge and names nothing."""
    ctx = runner.TerminalContext()
    with pytest.raises(runner.ReleaseNotWritable):
        runner.build_release(
            ctx,
            _args(tmp_path),
            TerminalOutcome(refusal=TerminalRefusal.EVIDENCE_UNREADABLE),
        )


# ── one write, once ─────────────────────────────────────────────────────────


def test_a_second_release_refuses_and_leaves_the_first_intact(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "releases" / "target.json"
    runner.write_create_only(path, {"schema": "first"})
    with pytest.raises(runner.ReleaseNotWritable):
        runner.write_create_only(path, {"schema": "second"})
    assert json.loads(path.read_text())["schema"] == "first"
    assert not list(path.parent.glob(".*partial"))


def test_the_second_write_refusal_is_not_vacuous(tmp_path: pathlib.Path) -> None:
    """The near-miss: a DIFFERENT lease's release still writes."""
    runner.write_create_only(tmp_path / "a.json", {"schema": "a"})
    runner.write_create_only(tmp_path / "b.json", {"schema": "b"})
    assert json.loads((tmp_path / "b.json").read_text())["schema"] == "b"


# ── absence means held ──────────────────────────────────────────────────────


def test_a_refusal_raised_below_this_lane_has_no_member() -> None:
    """The gap named honestly rather than filled.

    `StepFailed` from the effects and `PreconditionFailed` from `load_lease`
    end a run and are not in the twelve-site derivation `TerminalRefusal` was
    built from. There is no member for them, so no release is written and the
    host stays HELD — which is the safe half of the gap, not a repair of it.
    """
    assert runner.classify_refusal(StepFailed("exposure", "boom")) is None
    assert runner.classify_refusal(PreconditionFailed("no lease")) is None
    assert runner.classify_refusal(SpecError("bad descriptor")) is None
    assert runner.classify_refusal(DeploymentFoundationError("bare")) is None


@pytest.mark.parametrize(
    ("kind", "member"),
    [
        (runner.ResultRecordedTwice, TerminalRefusal.RECEIPT_INCONSISTENT),
        (runner.EvidenceUnreadable, TerminalRefusal.EVIDENCE_UNREADABLE),
        (runner.EvidenceIncomplete, TerminalRefusal.EVIDENCE_INCOMPLETE),
        (runner.ProbeRefused, TerminalRefusal.PROBE_REFUSED),
        (runner.PreconditionUnfit, TerminalRefusal.PRECONDITION_UNFIT),
        (
            runner.ProvocationUnestablished,
            TerminalRefusal.PROVOCATION_UNESTABLISHED,
        ),
    ],
)
def test_every_refusal_this_lane_raises_is_mapped(
    kind: type[DeploymentFoundationError], member: TerminalRefusal
) -> None:
    """The near-miss for the test above: these are named, and none of them is None."""
    assert runner.classify_refusal(kind("x")) is member


def test_the_mapping_is_derived_from_the_class_hierarchy_not_a_line_list() -> None:
    """Every refusal class this module defines carries a member, by construction.

    Three people derived the site list by grepping and got three different wrong
    answers, so the obligation is checked against the CLASSES rather than
    against positions in a file that moves under them.
    """
    mapped = {kind for kind, _ in runner._REFUSAL_BY_TYPE}
    defined = {
        value
        for value in vars(runner).values()
        if isinstance(value, type)
        and issubclass(value, DeploymentFoundationError)
        and value is not DeploymentFoundationError
        and value.__module__ == runner.__name__
    }
    assert (
        defined and defined == mapped
    ), f"unmapped refusal class(es): {sorted(c.__name__ for c in defined - mapped)}"


def test_that_obligation_would_notice_a_new_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sensitivity proof: plant an unmapped refusal and show it is named."""

    class Unmapped(DeploymentFoundationError):
        pass

    Unmapped.__module__ = runner.__name__
    monkeypatch.setattr(runner, "Unmapped", Unmapped, raising=False)
    defined = {
        value
        for value in vars(runner).values()
        if isinstance(value, type)
        and issubclass(value, DeploymentFoundationError)
        and value is not DeploymentFoundationError
        and value.__module__ == runner.__name__
    }
    assert Unmapped in defined - {kind for kind, _ in runner._REFUSAL_BY_TYPE}
    assert runner.classify_refusal(Unmapped("x")) is None


# ── the crash boundary, read off the source ─────────────────────────────────


def _broad_handlers(source: str, function: str) -> list[str]:
    """Handlers in `function` that catch more than a named refusal."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function:
            continue
        tries = [n for n in ast.walk(node) if isinstance(n, ast.Try)]
        for handler in [h for attempt in tries for h in attempt.handlers]:
            if handler.type is None:
                found.append("bare except")
                continue
            names = (
                [handler.type]
                if not isinstance(handler.type, ast.Tuple)
                else list(handler.type.elts)
            )
            for name in names:
                if ast.unparse(name) in {"Exception", "BaseException"}:
                    found.append(ast.unparse(name))
    return found


def test_the_release_write_is_never_reached_from_a_broad_handler() -> None:
    """A crash must leave NO record, so `main` catches only what it can name.

    Collapsing "we refused for a reason with a member" into "the process
    stopped" is how a killed run comes to authorise a destroy, and an
    `except Exception` around the write is exactly that collapse.
    """
    assert _broad_handlers(RUNNER.read_text(encoding="utf-8"), "main") == []


def test_that_detector_bites() -> None:
    """A check over a clean tree proves nothing about itself."""
    planted = (
        "def main():\n"
        "    try:\n"
        "        return run()\n"
        "    except Exception:\n"
        "        record_terminal()\n"
    )
    assert _broad_handlers(planted, "main") == ["Exception"]
    near_miss = (
        "def main():\n"
        "    try:\n"
        "        return run()\n"
        "    except DeploymentFoundationError:\n"
        "        record_terminal()\n"
    )
    assert _broad_handlers(near_miss, "main") == []


def test_the_preconditions_are_asked_before_the_first_mutation() -> None:
    """`precondition_unfit` asserts the host was never touched, so it must be true.

    All three sites that raise it used to be reached only after
    `transaction.run()` had applied the stack and rewritten both filter chains.
    """
    source = RUNNER.read_text(encoding="utf-8")
    guard = source.index("with refusal_of(PreconditionUnfit):")
    mutation = source.index("ctx.host_mutated = True")
    assert guard < mutation
    assert "port=private_port(spec)" not in source
    assert "accepted_source_sets=(inside_source_set(spec),)" not in source


# ── the terminal record reaches the artifact store without a receipt ────────


def test_the_terminal_record_is_uploaded_even_when_no_receipt_exists() -> None:
    """A run that refused still produced a terminal record.

    The receipt step lists `receipt.json` under `if-no-files-found: error`;
    folding these paths into it would tie the record of a refusal to the
    presence of a receipt, and a refused run is the case where there is none.
    """
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["rehearse"]["steps"]
    wanted = "Publish the terminal lease record"
    terminal = [step for step in steps if step.get("name") == wanted]
    assert terminal, "the terminal record has no upload step"
    step = terminal[0]
    assert step["if"] == "always()"
    paths = str(step["with"]["path"]).split()
    assert "lane3-lease-release.json" in paths
    assert "lane3-terminal-evidence.json" in paths
    assert "receipt.json" not in paths
    # `warn`, not `error`: absence means the process died before it could say
    # anything and the lease is still HELD. Turning that into a second red step
    # would replace the real signal with a different one.
    assert step["with"]["if-no-files-found"] == "warn"


def test_the_job_can_prove_its_workload_identity() -> None:
    """Without `id-token: write` the principal is unprovable and nothing releases."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert document["permissions"]["id-token"] == "write"


def test_the_run_is_bound_to_a_slot_and_a_candidate() -> None:
    """A record naming an address would bind a value the restoration can change."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    inputs = document[True]["workflow_dispatch"]["inputs"]
    assert inputs["vm_slot"]["required"] is True
    assert "default" not in inputs["vm_slot"]
    assert inputs["candidate_version"]["required"] is True
    body = WORKFLOW.read_text(encoding="utf-8")
    assert "--vm-slot" in body
    assert "--candidate-version" in body


def test_the_release_is_not_written_into_the_workspace_by_accident(
    tmp_path: pathlib.Path,
) -> None:
    """The documented default is beside the lease it discharges."""
    args = _args(tmp_path, release_out="")
    assert runner.release_path(args) == (
        pathlib.Path(args.lease_dir) / "releases" / "lane3-rehearsal-target.json"
    )


def test_the_document_the_writer_emits_is_the_schema_document(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """A written release must be one `HostLeaseReleaseV1` produced, not a dict."""
    ctx = _context()
    release = runner.build_release(
        ctx, _args(tmp_path), TerminalOutcome(receipt_digest="sha256:" + "ef" * 32)
    )
    assert isinstance(release, HostLeaseReleaseV1)
    assert release.as_document()["schema"] == "HostLeaseRelease.v1"
    assert release.as_document()["vm_slot"] == "dotmacproxmox/102"
