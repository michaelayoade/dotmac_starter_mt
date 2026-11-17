"""E7 / T4: lease custody stays on the launcher; the release is only ever written
from a terminal report the launcher could reconcile with what it knows itself."""

from __future__ import annotations

import json
import pathlib
import sys
import types
from typing import Any

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_launcher_lease as custody  # noqa: E402
from dotmac_deployment_foundation.lease_release import (  # noqa: E402
    CleanupDisposition,
    TerminalRefusal,
)
from lane3_lease_store import LeaseStanding  # noqa: E402

from tests.unit.lane3_store_support import FakeStore, release_for  # noqa: E402

MACHINE = "0123456789abcdef0123456789abcdef"
DIGEST = "sha256:" + "a" * 64


def terminal_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "receipt_digest": DIGEST,
        "refusal": "",
        "host_mutated": True,
        "lease_in_hand": True,
        "cleanup": CleanupDisposition.NOT_ATTEMPTED.value,
        "cleanup_acts": [
            {
                "act": "provocation_disarm",
                "disposition": CleanupDisposition.NOT_ATTEMPTED.value,
                "detail": "nothing was offered to the chain",
                "commands": [
                    {"command": "echo 'a b'", "exit_code": 0, "output": "a b"}
                ],
            }
        ],
        "machine_id": MACHINE,
        "notes": ["a note"],
        "exit_status": 0,
    }
    data.update(overrides)
    return data


def _custody(store, **overrides):
    arguments = {
        "store": store,
        "host_id": "host-a",
        "authorization_run": "run-1",
        "controller_identity": "fp-launch-key",
        "ttl_seconds": 600,
    }
    arguments.update(overrides)
    return custody.take_custody(**arguments)


@pytest.mark.parametrize("store", [None, "/var/leases", object()])
def test_the_lease_store_is_a_required_interface_not_a_path(store) -> None:
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _custody(store)
    assert refusal.value.code == "store-lacks-interface"


def test_a_store_without_database_time_is_refused_before_anything_is_taken() -> None:
    store = FakeStore(authority="runner")
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _custody(store)
    assert refusal.value.code == "store-lacks-database-time"
    assert not store._rows


def test_custody_is_taken_exclusively_from_the_store() -> None:
    store = FakeStore()
    held = _custody(store)
    assert held.lease.controller_identity_fingerprint == "fp-launch-key"
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _custody(store, authorization_run="run-2")
    assert refusal.value.code == "lease-held-by-another-run"


def test_a_lease_naming_another_controller_is_refused() -> None:
    class Other(FakeStore):
        def acquire_exclusive(self, **kwargs):
            held = super().acquire_exclusive(**kwargs)
            held.lease.controller_identity_fingerprint = "someone-else"
            return held

    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _custody(Other())
    assert refusal.value.code == "lease-names-another-controller"


def test_a_staging_directory_that_others_can_read_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    open_dir.chmod(0o755)
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        custody.stage_readonly_copy(None, open_dir)  # type: ignore[arg-type]
    assert refusal.value.code == "staging-directory-unsafe"


def test_a_staging_directory_with_content_is_refused(tmp_path: pathlib.Path) -> None:
    used = tmp_path / "used"
    used.mkdir(mode=0o700)
    used.chmod(0o700)
    (used / "stale.json").write_text("{}")
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        custody.stage_readonly_copy(None, used)  # type: ignore[arg-type]
    assert refusal.value.code == "staging-directory-not-empty"


def test_a_complete_terminal_report_parses() -> None:
    terminal = custody.parse_terminal(terminal_data())
    assert terminal.receipt_digest == DIGEST
    assert terminal.refusal is None
    assert terminal.cleanup is CleanupDisposition.NOT_ATTEMPTED
    assert terminal.acts[0].commands[0].argv == ("echo", "a b")


def test_a_refusal_member_parses_into_the_foundation_vocabulary() -> None:
    terminal = custody.parse_terminal(
        terminal_data(
            receipt_digest="",
            refusal=TerminalRefusal.HOST_STATE_UNCERTIFIED.value,
        )
    )
    assert terminal.refusal is TerminalRefusal.HOST_STATE_UNCERTIFIED


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"refusal": "made_up"}, "terminal-refusal"),
        (
            {"refusal": TerminalRefusal.HOST_STATE_UNCERTIFIED.value},
            "terminal-receipt-and-refusal",
        ),
        ({"host_mutated": 1}, "terminal-flag"),
        ({"lease_in_hand": "yes"}, "terminal-flag"),
        ({"machine_id": "XYZ"}, "terminal-machine-id"),
        ({"cleanup_acts": "none"}, "terminal-act-shape"),
        ({"cleanup_acts": [{"act": "x"}]}, "terminal-act-shape"),
        ({"notes": "n"}, "terminal-notes"),
        ({"exit_status": 999}, "terminal-exit-status"),
        ({"exit_status": True}, "terminal-exit-status"),
        ({"cleanup": "tidy"}, "terminal-disposition"),
        ({"cleanup": CleanupDisposition.PURGED.value}, "terminal-cleanup-inconsistent"),
    ],
)
def test_a_malformed_or_self_contradicting_report_is_refused(
    override: dict[str, Any], code: str
) -> None:
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        custody.parse_terminal(terminal_data(**override))
    assert refusal.value.code == code


def test_an_unknown_act_disposition_is_refused() -> None:
    acts = terminal_data()["cleanup_acts"]
    acts[0]["disposition"] = "tidy"
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        custody.parse_terminal(terminal_data(cleanup_acts=acts))
    assert refusal.value.code == "terminal-disposition"


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """`build_release` stands in for the OIDC-proven builder; what it was asked
    to build is recorded on the context it was given."""
    contexts: list[Any] = []

    def fake_build(ctx: Any, args: Any, outcome: Any) -> Any:
        contexts.append((ctx, outcome))
        lease = ctx.lease
        # Matches the held lease, as the real builder's record does.
        return release_for(
            FakeStore(),
            types.SimpleNamespace(lease=lease),
            lease_digest=lease.digest,
            released_by=types.SimpleNamespace(subject=lease.workload_principal),
            controller_identity_fingerprint=lease.controller_identity_fingerprint,
        )

    monkeypatch.setattr(custody.controller_half, "build_release", fake_build)
    return contexts


def _release(data: dict[str, Any], store=None, **overrides: Any) -> str:
    store = FakeStore() if store is None else store
    held = _custody(store)
    arguments: dict[str, Any] = {
        "store": store,
        "held": held,
        "controller_identity": "fp-launch-key",
        "expected_machine_id": MACHINE,
        "verified_receipt_digest": DIGEST,
        "ready_seen": False,
        "release_args": object(),
    }
    arguments.update(overrides)
    _release.store = store  # type: ignore[attr-defined]
    _release.held = held  # type: ignore[attr-defined]
    return custody.release_for(custody.parse_terminal(data), **arguments)


def test_a_verified_receipt_releases_into_the_same_store(written) -> None:
    assert _release(terminal_data()) == "release-digest"
    ctx, outcome = written[0]
    assert outcome.receipt_digest == DIGEST
    assert ctx.lease is not None
    assert ctx.vm_installation_id == MACHINE
    ((record, mutated),) = _release.store.releases  # type: ignore[attr-defined]
    assert record == "RELEASE-RECORD"
    assert mutated is True


def test_a_receipt_digest_the_launcher_did_not_verify_writes_nothing(written) -> None:
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _release(terminal_data(), verified_receipt_digest="sha256:" + "b" * 64)
    assert refusal.value.code == "terminal-receipt-unverified"
    assert not written
    assert not _release.store.releases  # type: ignore[attr-defined]


def test_another_machine_writes_nothing(written) -> None:
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _release(terminal_data(), expected_machine_id="f" * 32)
    assert refusal.value.code == "terminal-machine-mismatch"
    assert not written


def test_a_named_refusal_releases_with_that_member(written) -> None:
    member = TerminalRefusal.HOST_STATE_UNCERTIFIED
    _release(terminal_data(receipt_digest="", refusal=member.value))
    assert written[0][1].refusal is member


def test_an_unnameable_refusal_writes_no_release_and_the_lease_is_left_to_expire(
    written,
) -> None:
    store = FakeStore()
    result = _release(
        terminal_data(receipt_digest="", lease_in_hand=False), store=store
    )
    assert result == ""
    assert not written
    assert not store.releases
    store.advance(10_000)
    assert store.standing("host-a") is LeaseStanding.EXPIRED_MUTATED


def test_a_target_that_reports_no_mutation_is_not_believed_after_ready(
    written,
) -> None:
    """Once `ready` was seen the host is not provably untouched, whatever the
    target says about itself: the store is told `host_mutated=True`."""
    _release(terminal_data(host_mutated=False), ready_seen=True)
    assert written[0][0].host_mutated is True
    assert _release.store.releases[0][1] is True  # type: ignore[attr-defined]


def test_without_ready_the_targets_own_report_stands(written) -> None:
    _release(terminal_data(host_mutated=False), ready_seen=False)
    assert written[0][0].host_mutated is False
    assert _release.store.releases[0][1] is False  # type: ignore[attr-defined]


def test_a_release_the_workload_cannot_prove_is_not_written(
    written, monkeypatch
) -> None:
    def refuse(*_: Any) -> Any:
        raise custody.controller_half.PrincipalUnprovable("no token")

    monkeypatch.setattr(custody.controller_half, "build_release", refuse)
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _release(terminal_data())
    assert refusal.value.code == "release-not-writable"
    assert not _release.store.releases  # type: ignore[attr-defined]


def test_an_expired_lease_is_never_released_as_untouched(written) -> None:
    store = FakeStore()
    held = _custody(store)
    store.advance(10_000)
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        custody.release_for(
            custody.parse_terminal(terminal_data(host_mutated=False)),
            store=store,
            held=held,
            controller_identity="fp-launch-key",
            expected_machine_id=MACHINE,
            verified_receipt_digest=DIGEST,
            ready_seen=False,
            release_args=object(),
        )
    assert refusal.value.code == "lease-expired"
    assert store.standing("host-a") is LeaseStanding.EXPIRED_MUTATED


# ── M2: a mutated host can never close as untouched ─────────────────────────


def test_a_precondition_refusal_after_ready_degrades_to_state_uncertified(
    written,
) -> None:
    data = terminal_data(
        receipt_digest="", refusal=TerminalRefusal.PRECONDITION_UNFIT.value
    )
    _release(data, ready_seen=True)
    assert written[0][1].refusal is TerminalRefusal.HOST_STATE_UNCERTIFIED


def test_a_precondition_refusal_before_ready_keeps_its_member(written) -> None:
    data = terminal_data(
        receipt_digest="",
        refusal=TerminalRefusal.PRECONDITION_UNFIT.value,
        host_mutated=False,
    )
    _release(data, ready_seen=False)
    assert written[0][1].refusal is TerminalRefusal.PRECONDITION_UNFIT


def test_a_precondition_refusal_the_target_says_mutated_degrades_too(written) -> None:
    data = terminal_data(
        receipt_digest="",
        refusal=TerminalRefusal.PRECONDITION_UNFIT.value,
        host_mutated=True,
    )
    _release(data, ready_seen=False)
    assert written[0][1].refusal is TerminalRefusal.HOST_STATE_UNCERTIFIED


def test_other_refusal_members_are_not_rewritten(written) -> None:
    member = TerminalRefusal.EVIDENCE_UNREADABLE
    _release(terminal_data(receipt_digest="", refusal=member.value), ready_seen=True)
    assert written[0][1].refusal is member


# ── M4: terminal records for EVERY outcome ──────────────────────────────────


def test_the_record_names_are_the_ones_the_existing_upload_step_publishes() -> None:
    workflow = (
        pathlib.Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "exposure-rehearsal.yml"
    ).read_text(encoding="utf-8")
    assert f"            {custody.RELEASE_FILE}\n" in workflow
    assert f"            {custody.EVIDENCE_FILE}\n" in workflow


def test_the_evidence_record_is_written_even_with_no_release(
    tmp_path: pathlib.Path,
) -> None:
    paths = custody.write_terminal_records(
        tmp_path, evidence={"outcome": "no_terminal"}, release_record=None
    )
    assert [p.name for p in paths] == [custody.EVIDENCE_FILE]
    assert json.loads((tmp_path / custody.EVIDENCE_FILE).read_text()) == {
        "outcome": "no_terminal"
    }
    assert not (tmp_path / custody.RELEASE_FILE).exists()


def test_a_release_leaves_both_records(tmp_path: pathlib.Path) -> None:
    record = types.SimpleNamespace(as_document=lambda: {"release": "doc"})
    paths = custody.write_terminal_records(
        tmp_path, evidence={"outcome": "released"}, release_record=record
    )
    assert {p.name for p in paths} == {custody.EVIDENCE_FILE, custody.RELEASE_FILE}
    assert json.loads((tmp_path / custody.RELEASE_FILE).read_text()) == {
        "release": "doc"
    }


def test_the_records_are_create_only_and_private(tmp_path: pathlib.Path) -> None:
    custody.write_terminal_records(tmp_path, evidence={"a": 1}, release_record=None)
    path = tmp_path / custody.EVIDENCE_FILE
    assert (path.stat().st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError):
        custody.write_terminal_records(tmp_path, evidence={"a": 2}, release_record=None)
    assert json.loads(path.read_text()) == {"a": 1}


def test_release_with_record_returns_the_record_it_wrote(written) -> None:
    store = FakeStore()
    held = _custody(store)
    digest, record = custody.release_with_record(
        custody.parse_terminal(terminal_data()),
        store=store,
        held=held,
        controller_identity="fp-launch-key",
        expected_machine_id=MACHINE,
        verified_receipt_digest=DIGEST,
        ready_seen=False,
        release_args=object(),
    )
    assert digest == "release-digest"
    assert record is store.releases[0][0]
