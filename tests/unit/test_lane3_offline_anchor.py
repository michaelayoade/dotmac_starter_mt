"""S8b: presentation-key anchor, refresh digest and host join (hosted CI).

The checks live in `verify_offline`; synthetic keys only.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from datetime import timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.unit.test_lane3_offline_orchestrator import (
    World,
    b64url,
    digest,
    public_raw,
    world,  # noqa: F401  (fixture)
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_offline_anchor as cli  # noqa: E402
import lane3_offline_orchestrator as orchestrator  # noqa: E402


def anchor_refused(world: World, code: str, **overrides: Any) -> None:  # noqa: F811
    with pytest.raises(orchestrator.AnchorRefused) as caught:
        world.run(**overrides)
    assert str(caught.value) == f"offline-anchor-{code}"


def test_matching_anchor_passes_and_the_provenance_is_recorded(world: World) -> None:  # noqa: F811
    outcome = world.run()
    anchor = world.anchor()
    assert outcome.input_digests["presentation_anchor"] == (
        f"{anchor.record_slug}@70|{anchor.key_id}|{anchor.key_fingerprint}"
        f"|{anchor.host_id}"
    )
    assert outcome.input_digests["refresh_digest"].startswith("sha256:")


def test_the_anchor_is_a_required_argument(world: World) -> None:  # noqa: F811
    values = world.verify_values()
    del values["anchor"]
    with pytest.raises(TypeError):
        orchestrator.verify_offline(**values)
    values = world.verify_values()
    del values["refresh_digest"]
    with pytest.raises(TypeError):
        orchestrator.verify_offline(**values)


def test_attacker_key_resigned_and_supplied_is_refused_by_the_genuine_anchor(
    world: World,  # noqa: F811
) -> None:
    # The substitution: re-sign the presentation with an attacker key, supply the
    # attacker's public key, keep Control's genuine recorded anchor.
    attacker = Ed25519PrivateKey.generate()
    world.pres_key = attacker
    world.resign_admission()
    anchor_refused(
        world,
        "key-fingerprint-mismatch",
        presentation_public_key_b64url=b64url(public_raw(attacker)),
    )


def test_a_key_that_verifies_but_is_not_the_recorded_fingerprint_is_refused(
    world: World,  # noqa: F811
) -> None:
    other = digest(public_raw(Ed25519PrivateKey.generate()))
    anchor_refused(
        world, "key-fingerprint-mismatch", anchor=world.anchor(key_fingerprint=other)
    )


def test_key_id_mismatch_is_refused(world: World) -> None:  # noqa: F811
    anchor_refused(world, "key-id-mismatch", anchor=world.anchor(key_id="other-key"))


def test_file_sha256_must_equal_the_step_13_refresh_digest(
    world: World,  # noqa: F811
) -> None:
    anchor_refused(world, "refresh-digest-mismatch", refresh_digest="0" * 64)


def test_a_resigned_admission_is_not_the_refreshed_file(world: World) -> None:  # noqa: F811
    refresh = hashlib.sha256(json.dumps(world.admission).encode()).hexdigest()
    world.store_time = world.store_time + timedelta(seconds=1)
    world.resign_admission()
    world.refresh_ledger()
    anchor_refused(world, "refresh-digest-mismatch", refresh_digest=refresh)


def test_the_anchor_host_must_be_the_plan_host(world: World) -> None:  # noqa: F811
    # Cross-target: a credential recorded for another target's host.
    anchor_refused(world, "host-mismatch", anchor=world.anchor(host_id="other-host"))


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"record_slug": "key-init"}, "record-slug-invalid"),
        (
            {"record_slug": "dotmac-foundation-gate2-040a2-20261107"},
            "record-slug-invalid",
        ),
        ({"record_version": 0}, "provenance-invalid"),
        ({"record_version": True}, "provenance-invalid"),
        ({"key_id": ""}, "key-id-invalid"),
        ({"host_id": ""}, "host-id-invalid"),
        ({"key_fingerprint": "abc"}, "fingerprint-invalid"),
    ],
)
def test_the_anchor_and_its_provenance_are_closed(
    world: World,  # noqa: F811
    changes: dict[str, Any],
    code: str,
) -> None:
    anchor_refused(world, code, anchor=world.anchor(**changes))


def test_an_anchor_of_another_type_is_refused(world: World) -> None:  # noqa: F811
    anchor_refused(world, "required", anchor={"key_id": "x"})


@pytest.mark.parametrize("value", ["", "sha256:" + "0" * 64, "A" * 64, "0" * 63])
def test_the_refresh_digest_must_be_bare_lowercase_hex(
    world: World,  # noqa: F811
    value: str,
) -> None:
    anchor_refused(world, "refresh-digest-invalid", refresh_digest=value)


# --- the CLI -------------------------------------------------------------------


def _cli_args(w: World, path: pathlib.Path, **changes: str) -> list[str]:
    data = json.dumps(w.admission).encode()
    path.write_bytes(data)
    values = {
        "--host-admission": str(path),
        "--presentation-public-key": w.pres_pub,
        "--anchor-key-id": "presentation-key-1",
        "--anchor-key-fingerprint": w.anchor().key_fingerprint,
        "--anchor-host-id": w.anchor().host_id,
        "--plan-host-id": w.anchor().host_id,
        "--anchor-record-slug": orchestrator.ANCHOR_RECORD_SLUG,
        "--anchor-record-version": "70",
        "--refresh-digest": hashlib.sha256(data).hexdigest(),
    }
    values.update(changes)
    argv = ["check-admission"]
    for flag, value in values.items():
        if flag == "--presentation-public-key":
            # base64url may start with "-"; a separate token would parse as an option.
            argv.append(f"{flag}={value}")
        else:
            argv.extend([flag, value])
    return argv


def test_cli_reports_claimed_provenance_and_no_key_material(
    world: World,  # noqa: F811
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "ha.json"
    assert cli.main(_cli_args(world, path)) == 0
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["claimed_record_version"] == 70
    assert "anchor_source" not in report
    assert world.pres_pub not in out


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"--refresh-digest": "0" * 64}, "offline-anchor-refresh-digest-mismatch"),
        ({"--anchor-key-id": "x"}, "offline-anchor-key-id-mismatch"),
        ({"--plan-host-id": "other"}, "offline-anchor-host-mismatch"),
        (
            {"--anchor-key-fingerprint": "sha256:" + "0" * 64},
            "offline-anchor-key-fingerprint-mismatch",
        ),
    ],
)
def test_cli_refuses_with_a_stable_code(
    world: World,  # noqa: F811
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    override: dict[str, str],
    code: str,
) -> None:
    rc = cli.main(_cli_args(world, tmp_path / "ha.json", **override))
    assert rc == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == code


def _dash_led_presentation_key(world: World) -> None:  # noqa: F811
    """Re-key the world with a presentation key whose base64url starts with '-'."""
    for _ in range(10_000):
        key = Ed25519PrivateKey.generate()
        public = b64url(public_raw(key))
        if public.startswith("-"):
            break
    else:
        pytest.fail("no base64url public key starting with '-' was generated")
    world.pres_key = key
    world.pres_pub = public
    world.resign_admission()


def test_a_dash_led_presentation_key_gets_past_argument_parsing(
    world: World,  # noqa: F811
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _dash_led_presentation_key(world)
    assert world.pres_pub.startswith("-")
    assert cli.main(_cli_args(world, tmp_path / "ha.json")) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["result"] == "ok"


def test_cli_refuses_a_relative_path_and_a_symlink(
    world: World,  # noqa: F811
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real = tmp_path / "ha.json"
    args = _cli_args(world, real)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    for target in ("relative.json", str(link)):
        args[args.index("--host-admission") + 1] = target
        assert cli.main(args) == cli.EXIT_REFUSED
        assert capsys.readouterr().err.strip() == "offline-anchor-path-unreadable"


def test_usage_errors_exit_differently_from_refusals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.EXIT_USAGE != cli.EXIT_REFUSED
    with pytest.raises(SystemExit) as caught:
        cli.main(["check-admission"])
    assert caught.value.code == cli.EXIT_USAGE
    capsys.readouterr()
