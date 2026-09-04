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
import textwrap

import pytest
import yaml
from dotmac_deployment_foundation.controller_identity import (
    ControllerSshFingerprintV1,
)
from dotmac_deployment_foundation.engine.run import CommandResult
from dotmac_deployment_foundation.errors import (
    DeploymentFoundationError,
    LockUnavailableError,
    PreconditionFailed,
    SpecError,
    StepFailed,
)
from dotmac_deployment_foundation.lease import HostLease, release_path
from dotmac_deployment_foundation.lease_release import (
    RELEASE_DUPLICATE,
    CleanupDisposition,
    HostClosure,
    HostLeaseReleaseV1,
    ReleasingPrincipal,
    TerminalOutcome,
    TerminalRefusal,
    lease_digest,
    write_release,
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

#: A REAL OpenSSH fingerprint — `SHA256:` and 43 characters of unpadded base64,
#: as `ssh-keygen -lf` emits. It read `"sha256:" + "ab" * 32`, the content-digest
#: shape the old regex required and the one shape a key fingerprint never has.
FINGERPRINT_TEXT = "SHA256:T1kdK/6QTzzwU1EienO6nUgk8wu9UpjqB8BatKbndSE"
FINGERPRINT = ControllerSshFingerprintV1.parse(FINGERPRINT_TEXT, field="controller")
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
        # The CLI hands a string; `run` parses it before it reads the descriptor.
        "controller_identity": FINGERPRINT_TEXT,
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
    # `run` parses `--controller-identity` before it opens the descriptor, so by
    # the time any terminal record is built the parsed value is in hand. A
    # fixture that left it unset would exercise a state the runner cannot be in
    # once it holds a lease.
    ctx.controller_identity = FINGERPRINT
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
    """The symmetric half: a clean cleanup does not launder an uncertified host.

    `host_state_uncertified` says nobody can say what state the machine is in. A
    purged cleanup is unrestricted on its own axis, so a writer consulting only
    the cleanup would answer `reusable` here — and the cleanup axis is still
    ASKED and still recorded, because "the state is uncertified" and "something
    the lease created is still there" are different facts.
    """
    ctx = _context(host_mutated=True)
    ctx.record_cleanup(
        runner.CleanupAct("withdraw", CleanupDisposition.PURGED, "all successful")
    )
    release = runner.build_release(
        ctx,
        _args(tmp_path),
        TerminalOutcome(refusal=TerminalRefusal.HOST_STATE_UNCERTIFIED),
    )
    assert release.closure is HostClosure.INSPECTION_REQUIRED
    # The refusal constrained the CLOSURE; it did not excuse the cleanup axis
    # from answering. Reusability stays the intersection of the two.
    assert release.cleanup is CleanupDisposition.PURGED


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


# ── one write, once — through the store's owner ─────────────────────────────
#
# This runner no longer publishes. `lease.py` owns the store, so create-only is
# re-derived HERE through the path the runner actually takes rather than assumed
# to have survived the move — the guarantee is the reason the record exists, and
# a swap that quietly traded it for a checked one would look identical in a diff.
#
# READ THIS BEFORE TRUSTING THE TESTS BELOW. They are SEQUENTIAL. A sequential
# proof establishes that a second write is refused when the first has already
# finished; it says NOTHING about two runs racing, which is the case the store
# exists for — a shared host, agents contending for one target, and a workflow
# that does not cancel a run in progress. A `path.exists()` guard passes every
# test in this section and still loses the race. The race proof lives with the
# publish primitive in `test_deployment_foundation_lease_persistence.py`, where
# both implementations are run under one barrier and the rejected one is kept as
# a negative control. What these tests establish is that the runner reaches that
# primitive at all.


def test_a_second_release_refuses_and_leaves_the_first_intact(
    tmp_path: pathlib.Path, proven: None
) -> None:
    args = _args(tmp_path)
    first = runner.build_release(
        _context(), args, TerminalOutcome(receipt_digest="sha256:" + "11" * 32)
    )
    second = runner.build_release(
        _context(), args, TerminalOutcome(receipt_digest="sha256:" + "22" * 32)
    )
    stored = write_release(first, target=args.target, directory=args.lease_dir)
    with pytest.raises(PreconditionFailed) as exc:
        write_release(second, target=args.target, directory=args.lease_dir)
    assert exc.value.code == RELEASE_DUPLICATE
    document = json.loads(stored.read_text())
    assert document["outcome"]["receipt_digest"] == "sha256:" + "11" * 32
    assert not list(stored.parent.glob(".*partial"))


def test_that_refusal_is_not_vacuous(tmp_path: pathlib.Path, proven: None) -> None:
    """The near-miss: a DIFFERENT target's release still writes."""
    args = _args(tmp_path)
    release = runner.build_release(
        _context(), args, TerminalOutcome(receipt_digest="sha256:" + "33" * 32)
    )
    write_release(release, target="host-a", directory=args.lease_dir)
    stored = write_release(release, target="host-b", directory=args.lease_dir)
    assert stored.exists()


def test_the_runner_carries_no_publisher_of_its_own() -> None:
    """The defect that reached two branches at once: two writers for one store.

    Each lane built one correctly for a seam nobody had named. The package's own
    sweep watches both file sets now; this is the same question asked from the
    side that got it wrong, so the runner fails here first rather than in another
    lane's suite.
    """
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    publishes = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "link"
        and getattr(getattr(node.func, "value", None), "id", None) == "os"
    ]
    assert publishes == [], (
        f"the runner publishes with os.link at {publishes}. "
        "`lease.write_store_record_once` is the release writer; a second one "
        "here is two answers to one question and the store has one owner"
    )
    assert "write_release(" in RUNNER.read_text(encoding="utf-8"), (
        "the runner no longer calls the package's writer either, so the record "
        "reaches the store by no route at all"
    )


def test_that_publisher_check_bites(tmp_path: pathlib.Path) -> None:
    """Sensitivity: the assertion above passes over a clean tree today."""
    planted = tmp_path / "runner.py"
    planted.write_text(
        "import os\ndef write_create_only(p, d):\n    os.link(p.with_suffix('.t'), p)\n"
    )
    tree = ast.parse(planted.read_text())
    hits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "link"
        and getattr(getattr(node.func, "value", None), "id", None) == "os"
    ]
    assert len(hits) == 1


def test_a_duplicate_release_does_not_escape_record_terminal(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """`write_release` refuses with `PreconditionFailed`, which is a contract.

    It is a `DeploymentFoundationError`, so a handler that caught only `OSError`
    would let it escape a function that promises never to raise — and on the
    receipt path it would unwind into `main`, classify as unnameable, and report
    a fully green rehearsal as a refusal with no release.
    """
    args = _args(tmp_path)
    outcome = TerminalOutcome(receipt_digest="sha256:" + "44" * 32)
    write_release(
        runner.build_release(_context(), args, outcome),
        target=args.target,
        directory=args.lease_dir,
    )
    digest = runner.record_terminal(_context(), args, outcome)
    assert digest == ""
    evidence = json.loads(pathlib.Path(args.terminal_evidence_out).read_text())
    assert any("already exists" in note for note in evidence["notes"])


def test_the_workspace_copy_is_only_taken_after_the_store_write_succeeds(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """The store is the ledger; the copy is evidence of what it holds.

    If the store refused, there is nothing to copy — and that absence is the
    correct outcome rather than a gap to paper over with a parallel write.
    """
    args = _args(tmp_path)
    outcome = TerminalOutcome(receipt_digest="sha256:" + "55" * 32)
    write_release(
        runner.build_release(_context(), args, outcome),
        target=args.target,
        directory=args.lease_dir,
    )
    runner.record_terminal(_context(), args, outcome)
    assert not pathlib.Path(args.release_out).exists()


def test_the_copy_IS_taken_when_the_store_write_succeeds(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """The near-miss for the test above: a writer that never copied would pass it."""
    args = _args(tmp_path)
    digest = runner.record_terminal(
        _context(), args, TerminalOutcome(receipt_digest="sha256:" + "66" * 32)
    )
    assert digest
    stored = release_path(args.target, directory=args.lease_dir)
    assert json.loads(pathlib.Path(args.release_out).read_text()) == json.loads(
        stored.read_text()
    )


# ── absence means held ──────────────────────────────────────────────────────


def test_a_refusal_below_this_lane_BEFORE_mutation_still_has_no_member() -> None:
    """The precondition half, unchanged and deliberately so.

    A `SpecError` from `ProductDeploymentSpec.load` and a `PreconditionFailed`
    from `load_lease` are raised before the runner has contacted the host at all.
    They must NOT borrow `host_state_uncertified`, which asserts something about
    a machine, so they keep answering `None`: no release, and the host stays
    HELD. Whether they should instead carry `precondition_unfit` is an open
    question the ruling did not settle, and answering it by inference is how a
    member that promises "untouched and safely releasable" gets attached to a
    run nobody characterised.
    """
    assert (
        runner.classify_refusal(StepFailed("exposure", "boom"), host_mutated=False)
        is None
    )
    assert (
        runner.classify_refusal(PreconditionFailed("no lease"), host_mutated=False)
        is None
    )
    assert (
        runner.classify_refusal(SpecError("bad descriptor"), host_mutated=False) is None
    )
    assert (
        runner.classify_refusal(DeploymentFoundationError("bare"), host_mutated=False)
        is None
    )


@pytest.mark.parametrize(
    "exc",
    [
        # THE case Michael ruled on: the compose apply failed ON THE HOST.
        StepFailed("apply_compose", "docker compose up exited 1"),
        # `errors.py` documents this as "a gate refused before anything was
        # mutated", and `ExposureTransaction.run` raises it AFTER applying the
        # stack, rewriting both filter chains and rolling back. The type cannot
        # discriminate; the position can.
        PreconditionFailed("the applied exposure did not verify"),
        # `build_receipt` raises this after the whole transaction.
        SpecError("a receipt is missing an item"),
        LockUnavailableError("another deployment holds the lock"),
        DeploymentFoundationError("bare"),
    ],
)
def test_a_refusal_below_this_lane_AFTER_mutation_is_host_state_uncertified(
    exc: DeploymentFoundationError,
) -> None:
    """The gap closed rather than named.

    A `StepFailed` from a failed compose apply had NO member in the closed
    vocabulary, so a failed apply left the host mutated with no record and no
    closure — reachable on any run where the apply fails on the host. Every
    refusal here is raised at a point where mutation may have begun, and every
    one of them now says the true thing: nobody has certified what state the
    machine is in.
    """
    assert (
        runner.classify_refusal(exc, host_mutated=True)
        is TerminalRefusal.HOST_STATE_UNCERTIFIED
    )


def test_this_lanes_own_refusals_keep_their_own_member_after_mutation() -> None:
    """The near-miss for the test above.

    A positional rule that swallowed every refusal past the first mutation would
    turn `evidence_unreadable` and `probe_refused` — both of which are raised
    after the apply by design — into `host_state_uncertified`, and the
    vocabulary would collapse to two members. The type is consulted FIRST.
    """
    assert (
        runner.classify_refusal(runner.EvidenceUnreadable("x"), host_mutated=True)
        is TerminalRefusal.EVIDENCE_UNREADABLE
    )
    assert (
        runner.classify_refusal(runner.ProbeRefused("x"), host_mutated=True)
        is TerminalRefusal.PROBE_REFUSED
    )


@pytest.mark.parametrize(
    ("kind", "member"),
    [
        (runner.ResultRecordedTwice, TerminalRefusal.RECEIPT_INCONSISTENT),
        (runner.EvidenceUnreadable, TerminalRefusal.EVIDENCE_UNREADABLE),
        (runner.EvidenceIncomplete, TerminalRefusal.EVIDENCE_INCOMPLETE),
        (runner.ProbeRefused, TerminalRefusal.PROBE_REFUSED),
        (runner.PreconditionUnfit, TerminalRefusal.PRECONDITION_UNFIT),
        (
            runner.HostStateUncertified,
            TerminalRefusal.HOST_STATE_UNCERTIFIED,
        ),
    ],
)
def test_every_refusal_this_lane_raises_is_mapped(
    kind: type[DeploymentFoundationError], member: TerminalRefusal
) -> None:
    """The near-miss for the test above: these are named, and none of them is
    None — including with no mutation attempted, where the positional rule
    answers `None` and would otherwise mask a missing type mapping."""
    assert runner.classify_refusal(kind("x"), host_mutated=False) is member


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
    # Asked with no mutation attempted, so the positional rule cannot answer for
    # it: an unmapped class of THIS lane's own must still show up as unmapped.
    assert runner.classify_refusal(Unmapped("x"), host_mutated=False) is None


def test_a_failed_apply_releases_the_host_as_needing_inspection(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """THE shape the ruling exists for, end to end through the writer.

    A `StepFailed` from `apply_compose` unwinds out of `run` with the host
    mutated. Before this change it had no member, `record_terminal` wrote no
    release, and the host stayed HELD with nothing saying why. Now it is
    `host_state_uncertified`, the closure lands on `inspection_required` — never
    `reusable` — and the cleanup axis still ANSWERS: `not_attempted`, which is
    true, because the apply failed long before `run_cleanup` is reached.
    """
    member = runner.classify_refusal(
        StepFailed("apply_compose", "docker compose up exited 1"), host_mutated=True
    )
    assert member is TerminalRefusal.HOST_STATE_UNCERTIFIED
    ctx = _context(host_mutated=True)
    release = runner.build_release(
        ctx, _args(tmp_path), TerminalOutcome(refusal=member)
    )
    assert release.closure is HostClosure.INSPECTION_REQUIRED
    assert release.cleanup is CleanupDisposition.NOT_ATTEMPTED
    assert release.outcome.refusal is TerminalRefusal.HOST_STATE_UNCERTIFIED
    # And the record names the key that touched the host, by the same type the
    # lease carries, so the destroy gate compares digests rather than text.
    assert release.controller_identity_fingerprint == FINGERPRINT


# ── site 134, settled by POSITION rather than by line number ───────────────
#
# `TerminalRefusal` used to file the inside-vantage probe under two members at
# once: `PRECONDITION_UNFIT`'s docstring claimed it, and `EVIDENCE_UNREADABLE`'s
# said a reader subprocess exiting non-zero is the same fact as malformed output.
# Both could not stand.
#
# The ruling splits it by POSITION — a missing harness, argument or jump key
# detected before host contact is `precondition_unfit`; the probe subprocess
# failing after mutation is `evidence_unreadable`; `probe_refused` applies only
# when a probe actually ran and refused. That is a claim about the CODE, so it is
# checked against the code.


def _run_function() -> ast.FunctionDef:
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )


def _first_call_line(function: ast.FunctionDef, name: str) -> int:
    lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == name
    ]
    assert lines, f"{name} is not called in `run` at all"
    return min(lines)


def _first_mutation_line(function: ast.FunctionDef) -> int:
    """Where `run` declares the host may have been mutated from here on.

    A single assignment, asserted to be single: two of them would mean two
    answers to "has anything been touched", and the earlier one would be the one
    a reader trusted while the later one was the one that ran.
    """
    lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(ast.unparse(target) == "ctx.host_mutated" for target in node.targets)
        and ast.unparse(node.value) == "True"
    ]
    assert len(lines) == 1, f"expected one `ctx.host_mutated = True`, found {lines}"
    return lines[0]


@pytest.mark.parametrize(
    ("call", "why"),
    [
        (
            "ControllerSshFingerprintV1.parse",
            "whether `--controller-identity` is a key fingerprint is decidable "
            "from the argument alone",
        ),
        ("private_port", "a question about the descriptor"),
        ("inside_source_set", "a question about the descriptor"),
        (
            "require_inside_probe_harness",
            "a missing harness, argument or jump key is a fact about the " "INVOCATION",
        ),
    ],
)
def test_every_precondition_unfit_site_is_asked_BEFORE_first_mutation(
    call: str, why: str
) -> None:
    """`precondition_unfit` asserts the host was never touched. Asked after the
    apply that sentence is FALSE about the machine, and a release carrying it
    would tell a destroyer the host was untouched when it was not.

    Three of these were once reached only after the compose stack had been
    applied and both filter chains rewritten. This is the check that says so.
    """
    function = _run_function()
    assert _first_call_line(function, call) < _first_mutation_line(function), (
        f"`{call}` is reached after `ctx.host_mutated = True`, and it refuses "
        f"with `precondition_unfit` — {why}, so it belongs before the host is "
        "touched or the member it raises says something false"
    )


@pytest.mark.parametrize(
    ("call", "member"),
    [
        ("inside_vantage.collect", "evidence_unreadable"),
        ("build_receipt", "host_state_uncertified, positionally"),
        ("transaction.run", "host_state_uncertified, positionally"),
    ],
)
def test_the_after_mutation_sites_really_are_after_it(call: str, member: str) -> None:
    """The other half, and the reason it is not enough to check one direction.

    A test that only proved the precondition sites come first would pass over a
    `run` that had moved everything before the mutation — including the probe
    that must be taken while the stack is UP. `inside_vantage.collect` refuses
    with `evidence_unreadable` precisely BECAUSE it is reached long after the
    apply, where "the host was never touched" cannot be claimed.
    """
    function = _run_function()
    assert _first_call_line(function, call) > _first_mutation_line(function), (
        f"`{call}` now runs before the first mutation, so it no longer maps to "
        f"{member} — revisit TerminalRefusal rather than the assertion"
    )


def test_the_inside_vantage_region_is_translated_to_evidence_unreadable() -> None:
    """`probe_refused` applies ONLY when a probe actually ran and refused.

    `lane3_inside_vantage.collect` raises a bare `DeploymentFoundationError` from
    two sites and nothing outside it can tell them apart, so the whole region is
    translated at the call — and to `EvidenceUnreadable`, never `ProbeRefused`.
    """
    function = _run_function()
    # Containment, not a line window: the call must be INSIDE the translating
    # `with` block, and a window would answer the same for a call that merely
    # sits near one.
    enclosing = [
        ast.unparse(node.items[0].context_expr)
        for node in ast.walk(function)
        if isinstance(node, ast.With)
        and node.items
        and ast.unparse(node.items[0].context_expr).startswith("refusal_of(")
        and any(
            isinstance(inner, ast.Call)
            and ast.unparse(inner.func) == "inside_vantage.collect"
            for statement in node.body
            for inner in ast.walk(statement)
        )
    ]
    assert enclosing == ["refusal_of(EvidenceUnreadable)"], (
        f"the inside-vantage probe is translated as {enclosing}. It is reached "
        "after the apply, so `PreconditionUnfit` would claim an untouched host "
        "that was touched, and `ProbeRefused` would claim a probe ran when the "
        "harness may never have been invoked"
    )


def _synthetic_run(body: str) -> ast.FunctionDef:
    source = "def run(args, ctx):\n" + textwrap.indent(textwrap.dedent(body), "    ")
    tree = ast.parse(source)
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )


def test_the_position_guard_would_see_a_precondition_moved_after_the_mutation() -> None:
    """Sensitivity. The assertions above pass over a tree that is already
    correct, so they prove nothing about their own ability to fail — and the
    defect they target is the one that actually shipped: three
    `precondition_unfit` sites reached only after the compose stack was applied.
    """
    planted = _synthetic_run(
        """
        ctx.host_mutated = True
        report = transaction.run()
        require_inside_probe_harness(args, target_v6="::1", port=1)
        """
    )
    assert _first_call_line(planted, "require_inside_probe_harness") > (
        _first_mutation_line(planted)
    )

    near_miss = _synthetic_run(
        """
        require_inside_probe_harness(args, target_v6="::1", port=1)
        ctx.host_mutated = True
        report = transaction.run()
        """
    )
    assert _first_call_line(near_miss, "require_inside_probe_harness") < (
        _first_mutation_line(near_miss)
    )


def test_the_translation_guard_reads_CONTAINMENT_not_proximity() -> None:
    """Sensitivity for the translation check, in both directions.

    The planted defect: the probe wrapped in `refusal_of(PreconditionUnfit)`,
    which would claim an untouched host after the apply. The near miss: an
    `EvidenceUnreadable` block that merely PRECEDES the call — a proximity check
    would credit it, and containment does not.
    """

    def _enclosing(function: ast.FunctionDef) -> list[str]:
        return [
            ast.unparse(node.items[0].context_expr)
            for node in ast.walk(function)
            if isinstance(node, ast.With)
            and node.items
            and ast.unparse(node.items[0].context_expr).startswith("refusal_of(")
            and any(
                isinstance(inner, ast.Call)
                and ast.unparse(inner.func) == "inside_vantage.collect"
                for statement in node.body
                for inner in ast.walk(statement)
            )
        ]

    planted = _synthetic_run(
        """
        with refusal_of(PreconditionUnfit):
            probe = inside_vantage.collect(harness)
        """
    )
    assert _enclosing(planted) == ["refusal_of(PreconditionUnfit)"]

    near_miss = _synthetic_run(
        """
        with refusal_of(EvidenceUnreadable):
            something_else()
        probe = inside_vantage.collect(harness)
        """
    )
    assert _enclosing(near_miss) == []


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


def test_the_release_lands_beside_its_lease_and_nowhere_else(
    tmp_path: pathlib.Path, proven: None
) -> None:
    """The runner derives no path of its own. A second location is a second ledger.

    With no copy destination configured the record still reaches the store, so
    the store is not contingent on an artifact convenience.
    """
    args = _args(tmp_path, release_out="")
    assert runner.record_terminal(
        _context(), args, TerminalOutcome(receipt_digest="sha256:" + "77" * 32)
    )
    assert release_path(args.target, directory=args.lease_dir).exists()
    assert not hasattr(runner, "release_path"), (
        "the runner derives the store path again; `lease.release_path` is the "
        "one answer to where a release lives"
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
