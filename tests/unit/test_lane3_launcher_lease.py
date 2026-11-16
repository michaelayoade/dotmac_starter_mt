"""E7 / T4: lease custody stays on the launcher; the release is only ever written
from a terminal report the launcher could reconcile with what it knows itself."""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_launcher_lease as custody  # noqa: E402
from dotmac_deployment_foundation.lease_release import (  # noqa: E402
    CleanupDisposition,
    TerminalRefusal,
)

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


def test_the_lease_store_is_a_required_input_with_no_default() -> None:
    for store in ("", "   ", None):
        with pytest.raises(custody.LeaseCustodyRefused) as refusal:
            custody.take_custody(
                target="host-a",
                lease_store=store,  # type: ignore[arg-type]
                authorization_run="run-1",
                controller_identity=object(),  # type: ignore[arg-type]
                now=None,  # type: ignore[arg-type]
            )
        assert refusal.value.code == "lease-store-required"


def test_a_missing_lease_is_not_held(tmp_path: pathlib.Path) -> None:
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        custody.take_custody(
            target="host-a",
            lease_store=str(tmp_path),
            authorization_run="run-1",
            controller_identity=object(),  # type: ignore[arg-type]
            now=None,  # type: ignore[arg-type]
        )
    assert refusal.value.code == "lease-not-held"


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
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, Any, Any]]:
    calls: list[tuple[Any, Any, Any]] = []

    def fake(ctx: Any, args: Any, outcome: Any) -> str:
        calls.append((ctx, args, outcome))
        return "release-digest"

    monkeypatch.setattr(custody.controller_half, "record_terminal", fake)
    return calls


def _release(data: dict[str, Any], **overrides: Any) -> str:
    arguments: dict[str, Any] = {
        "lease": object(),
        "controller_identity": object(),
        "expected_machine_id": MACHINE,
        "verified_receipt_digest": DIGEST,
        "release_args": object(),
    }
    arguments.update(overrides)
    return custody.release_for(custody.parse_terminal(data), **arguments)


def test_a_verified_receipt_releases_with_the_receipt_outcome(recorded) -> None:
    assert _release(terminal_data()) == "release-digest"
    ctx, _, outcome = recorded[0]
    assert outcome.receipt_digest == DIGEST
    assert ctx.lease is not None
    assert ctx.vm_installation_id == MACHINE


def test_a_receipt_digest_the_launcher_did_not_verify_writes_nothing(recorded) -> None:
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _release(terminal_data(), verified_receipt_digest="sha256:" + "b" * 64)
    assert refusal.value.code == "terminal-receipt-unverified"
    assert not recorded


def test_another_machine_writes_nothing(recorded) -> None:
    with pytest.raises(custody.LeaseCustodyRefused) as refusal:
        _release(terminal_data(), expected_machine_id="f" * 32)
    assert refusal.value.code == "terminal-machine-mismatch"
    assert not recorded


def test_a_named_refusal_releases_with_that_member(recorded) -> None:
    member = TerminalRefusal.HOST_STATE_UNCERTIFIED
    _release(terminal_data(receipt_digest="", refusal=member.value))
    assert recorded[0][2].refusal is member


def test_an_unnameable_refusal_passes_no_outcome(recorded) -> None:
    _release(terminal_data(receipt_digest="", lease_in_hand=False))
    ctx, _, outcome = recorded[0]
    assert outcome is None
    assert ctx.lease is None
