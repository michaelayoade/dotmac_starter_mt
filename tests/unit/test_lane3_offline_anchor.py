"""S8b guards: presentation-key anchor and step-13 refresh digest (hosted CI).

Synthetic keys only. Each refusal has a planted case.
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
    public_raw,
    world,  # noqa: F401  (fixture)
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_offline_anchor as anchor_mod  # noqa: E402
import lane3_offline_orchestrator as orchestrator  # noqa: E402

KEY_ID = "presentation-key-1"


def _fingerprint(key: Ed25519PrivateKey) -> str:
    return "sha256:" + hashlib.sha256(public_raw(key)).hexdigest()


def _bytes(w: World) -> bytes:
    return json.dumps(w.admission).encode()


def _anchor(w: World, **changes: Any) -> anchor_mod.PresentationKeyAnchor:
    values: dict[str, Any] = {
        "key_id": KEY_ID,
        "key_fingerprint": _fingerprint(w.pres_key),
        "record_slug": "gate2-record",
        "record_version": 70,
    }
    values.update(changes)
    return anchor_mod.PresentationKeyAnchor(**values)


def _guard(w: World, **overrides: Any):
    data = overrides.pop("host_admission_bytes", _bytes(w))
    values: dict[str, Any] = {
        "presentation_public_key_b64url": w.pres_pub,
        "anchor": _anchor(w),
        "refresh_digest": hashlib.sha256(data).hexdigest(),
    }
    values.update(overrides)
    return anchor_mod.require_admission_anchor(data, **values)


def refused(code: str, w: World, **overrides: Any) -> None:
    with pytest.raises((anchor_mod.AnchorRefused, orchestrator.JoinRefused)) as caught:
        _guard(w, **overrides)
    assert str(caught.value) == code


def test_matching_anchor_and_refresh_digest_pass(world: World) -> None:  # noqa: F811
    facts = _guard(world)
    assert facts.presentation_key_id == KEY_ID
    assert facts.file_sha256 == hashlib.sha256(_bytes(world)).hexdigest()


def test_a_key_that_verifies_but_is_not_the_recorded_anchor_is_refused(
    world: World,  # noqa: F811
) -> None:
    # The `key-init` printout verifies the signature; it is still not Control's
    # recorded credential.
    refused(
        "offline-anchor-key-fingerprint-mismatch",
        world,
        anchor=_anchor(
            world, key_fingerprint=_fingerprint(Ed25519PrivateKey.generate())
        ),
    )


def test_a_wrong_public_key_is_refused_by_the_signature_first(
    world: World,  # noqa: F811
) -> None:
    other = b64url(public_raw(Ed25519PrivateKey.generate()))
    refused(
        "offline-join-host-admission-presentation-signature-invalid",
        world,
        presentation_public_key_b64url=other,
    )


def test_key_id_mismatch_is_refused(world: World) -> None:  # noqa: F811
    refused(
        "offline-anchor-key-id-mismatch",
        world,
        anchor=_anchor(world, key_id="presentation-key-2"),
    )


def test_file_sha256_must_equal_the_step_13_refresh_digest(
    world: World,  # noqa: F811
) -> None:
    refused("offline-anchor-refresh-digest-mismatch", world, refresh_digest="0" * 64)


def test_an_admission_not_the_refreshed_file_is_refused(world: World) -> None:  # noqa: F811
    refresh = hashlib.sha256(_bytes(world)).hexdigest()
    world.store_time = world.store_time + timedelta(seconds=1)
    world.resign_admission()
    refused("offline-anchor-refresh-digest-mismatch", world, refresh_digest=refresh)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"source": "key-init"}, "source-invalid"),
        ({"record_slug": ""}, "provenance-invalid"),
        ({"record_slug": "Bad Slug"}, "provenance-invalid"),
        ({"record_version": 0}, "provenance-invalid"),
        ({"record_version": True}, "provenance-invalid"),
        ({"key_id": ""}, "key-id-invalid"),
        ({"key_fingerprint": "abc"}, "fingerprint-invalid"),
    ],
)
def test_the_anchor_and_its_provenance_are_closed(
    world: World,  # noqa: F811
    changes: dict[str, Any],
    code: str,
) -> None:
    refused(f"offline-anchor-{code}", world, anchor=_anchor(world, **changes))


def test_an_anchor_object_of_another_type_is_refused(world: World) -> None:  # noqa: F811
    refused("offline-anchor-required", world, anchor={"key_id": KEY_ID})


@pytest.mark.parametrize("digest", ["", "sha256:" + "0" * 64, "A" * 64, "0" * 63])
def test_the_refresh_digest_must_be_bare_lowercase_hex(
    world: World,  # noqa: F811
    digest: str,
) -> None:
    refused("offline-anchor-refresh-digest-invalid", world, refresh_digest=digest)


def test_unreadable_admission_is_a_refusal(world: World) -> None:  # noqa: F811
    refused(
        "offline-join-host-admission-unreadable",
        world,
        host_admission_bytes=b"not json",
    )


# --- the anchored entry point --------------------------------------------------


def _anchored(w: World, **overrides: Any):
    verify_kwargs = {
        key: value
        for key, value in w.verify_values().items()
        if key not in {"presentation_public_key_b64url", "host_admission_bytes"}
    }
    data = _bytes(w)
    values: dict[str, Any] = {
        "anchor": _anchor(w),
        "refresh_digest": hashlib.sha256(data).hexdigest(),
        "host_admission_bytes": data,
        "presentation_public_key_b64url": w.pres_pub,
        **verify_kwargs,
    }
    values.update(overrides)
    return anchor_mod.verify_offline_anchored(**values)


def test_the_anchored_entry_point_emits_the_same_result(world: World) -> None:  # noqa: F811
    assert _anchored(world).result_bytes == world.run().result_bytes


def test_the_anchored_entry_point_refuses_before_verify_offline(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**_kwargs: Any) -> None:  # pragma: no cover
        raise AssertionError("verify_offline reached despite a failed guard")

    monkeypatch.setattr(anchor_mod, "verify_offline", boom)
    with pytest.raises(anchor_mod.AnchorRefused):
        _anchored(world, refresh_digest="0" * 64)
    with pytest.raises(anchor_mod.AnchorRefused):
        _anchored(world, anchor=_anchor(world, key_id="other"))


def test_the_anchored_entry_point_still_runs_every_join(world: World) -> None:  # noqa: F811
    with pytest.raises(orchestrator.JoinRefused) as caught:
        _anchored(world, ciphertext=b"not age")
    assert str(caught.value) == "offline-join-ciphertext-invalid"


# --- the CLI -------------------------------------------------------------------


def _cli_args(w: World, path: pathlib.Path, **changes: str) -> list[str]:
    data = _bytes(w)
    path.write_bytes(data)
    values = {
        "--host-admission": str(path),
        "--presentation-public-key": w.pres_pub,
        "--anchor-key-id": KEY_ID,
        "--anchor-key-fingerprint": _fingerprint(w.pres_key),
        "--anchor-record-slug": "gate2-record",
        "--anchor-record-version": "70",
        "--refresh-digest": hashlib.sha256(data).hexdigest(),
    }
    values.update(changes)
    return ["check-admission", *[x for pair in values.items() for x in pair]]


def test_cli_reports_provenance_and_no_key_material(
    world: World,  # noqa: F811
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert anchor_mod.main(_cli_args(world, tmp_path / "ha.json")) == 0
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["result"] == "ok"
    assert report["anchor_record_slug"] == "gate2-record"
    assert report["anchor_record_version"] == 70
    assert report["anchor_source"] == anchor_mod.ANCHOR_SOURCE
    assert world.pres_pub not in out


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"--refresh-digest": "0" * 64}, "offline-anchor-refresh-digest-mismatch"),
        ({"--anchor-key-id": "x"}, "offline-anchor-key-id-mismatch"),
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
    assert anchor_mod.main(_cli_args(world, tmp_path / "ha.json", **override)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == code


def test_cli_refuses_a_relative_path(
    world: World,  # noqa: F811
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _cli_args(world, tmp_path / "ha.json")
    args[args.index("--host-admission") + 1] = "relative.json"
    assert anchor_mod.main(args) == 2
    assert capsys.readouterr().err.strip() == "offline-anchor-path-not-absolute"
