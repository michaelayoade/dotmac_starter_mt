"""S2: `scripts/lane3_host_admission.py` (key-init / pop / assemble / present).

Synthetic keys only. The Control parse rules are restated here from Control
`host_admission.py` (0.1.0a17), because Control is not importable from this
repository; that restatement is the conformance vector.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import lane3_host_admission as ha
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotmac_deployment_foundation.trusted_host_source import (
    AttestationEnvelopeV2,
    attestation_envelope_digest,
)

from tests.unit import lane3_attestation_support as support

SCRIPT = support.SCRIPTS / "lane3_host_admission.py"
UID = os.getuid()
KEY_ID = "presentation-key-1"
STORE_TIME = "2026-11-19T10:05:00Z"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    directory = tmp_path / "home"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


@pytest.fixture
def key_path(home: Path) -> Path:
    path = home / "presentation.key"
    ha.key_init(path, owner_uid=UID)
    return path


def _pair() -> tuple[AttestationEnvelopeV2, AttestationEnvelopeV2]:
    candidate = support.candidate_envelope()
    return candidate, support.installed_envelope(candidate)


def _unsigned(home: Path, name: str = "unsigned.json") -> Path:
    candidate, installed = _pair()
    document = ha.assemble_unsigned(
        dispatch_materials={"attempt_id": support.DISPATCH_ID},
        candidate=candidate.canonical_document(),
        installed=installed.canonical_document(),
        expected_candidate_digest=str(attestation_envelope_digest(candidate)),
        expected_installed_digest=str(attestation_envelope_digest(installed)),
    )
    return support.write_json(home / name, document)


def _present(home: Path, key_path: Path, **overrides: Any) -> dict[str, str]:
    arguments: dict[str, Any] = {
        "key_path": key_path,
        "key_id": KEY_ID,
        "store_time": STORE_TIME,
        "out": home / "host-admission.json",
        "admission_input": _unsigned(home),
        "owner_uid": UID,
    }
    arguments.update(overrides)
    return ha.present(**arguments)


def _refusal(call: Any, code: str) -> None:
    with pytest.raises(ha.Refused) as refusal:
        call()
    assert refusal.value.code == code


def _document(home: Path) -> dict[str, Any]:
    return json.loads((home / "host-admission.json").read_text(encoding="utf-8"))


def _control_accepts(presentation: dict[str, Any], public_raw: bytes) -> None:
    """Control's `HostAdmissionPresentationV1.parse` + signature + lifetime rules."""
    assert set(presentation) == {"statement", "signature"}
    statement = presentation["statement"]
    assert set(statement) == {
        "schema",
        "version",
        "purpose",
        "audience",
        "presentation_id",
        "key_id",
        "dispatch_id",
        "candidate_attestation_envelope_digest",
        "installed_attestation_envelope_digest",
        "issued_at",
        "expires_at",
    }
    assert statement["schema"] == "dotmac.control.host-admission-presentation"
    assert type(statement["version"]) is int and statement["version"] == 1
    assert statement["purpose"] == "dotmac.control.host-admission-presentation.v1"
    assert statement["audience"] == "dotmac.deployment-control"
    for name in (
        "candidate_attestation_envelope_digest",
        "installed_attestation_envelope_digest",
    ):
        value = statement[name]
        assert len(value) == 71 and value.startswith("sha256:")
        assert all(c in "0123456789abcdef" for c in value[7:])
    parsed = {}
    for name in ("issued_at", "expires_at"):
        value = statement[name]
        assert value.endswith("Z")
        moment = datetime.fromisoformat(value[:-1] + "+00:00")
        assert moment.isoformat().replace("+00:00", "Z") == value
        parsed[name] = moment
    assert (
        timedelta(0)
        < parsed["expires_at"] - parsed["issued_at"]
        <= timedelta(minutes=5)
    )
    signature = presentation["signature"]
    assert signature and "=" not in signature and signature == signature.strip()
    raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    assert base64.urlsafe_b64encode(raw).decode().rstrip("=") == signature
    canonical = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    Ed25519PublicKey.from_public_bytes(public_raw).verify(raw, canonical)


# ── key-init ────────────────────────────────────────────────────────────────


def test_key_init_creates_a_0400_key_and_prints_only_the_public_half(
    home: Path,
) -> None:
    path = home / "presentation.key"
    printed = ha.key_init(path, owner_uid=UID)
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert set(printed) == {"public_key_b64url", "public_key_fingerprint"}
    raw = base64.urlsafe_b64decode(printed["public_key_b64url"] + "=")
    assert (
        printed["public_key_fingerprint"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    )
    assert "=" not in printed["public_key_b64url"]
    assert "PRIVATE" not in json.dumps(printed)


def test_key_init_never_replaces_a_key(key_path: Path, home: Path) -> None:
    before = key_path.read_bytes()
    _refusal(
        lambda: ha.key_init(key_path, owner_uid=UID), "key-exists-or-not-creatable"
    )
    assert key_path.read_bytes() == before


def test_key_init_refuses_when_not_the_expected_user(home: Path) -> None:
    _refusal(lambda: ha.key_init(home / "k", owner_uid=UID + 1), "key-init-wrong-user")
    assert not (home / "k").exists()


def test_key_init_refuses_an_unsafe_directory_and_a_relative_path(
    tmp_path: Path, home: Path
) -> None:
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    open_dir.chmod(0o755)
    _refusal(lambda: ha.key_init(open_dir / "k", owner_uid=UID), "key-directory-unsafe")
    _refusal(
        lambda: ha.key_init(home / "missing" / "k", owner_uid=UID),
        "key-directory-unsafe",
    )
    _refusal(
        lambda: ha.key_init("relative.key", owner_uid=UID), "key-path-not-absolute"
    )


def test_key_init_refuses_a_symlink_at_the_key_path(home: Path) -> None:
    target = home / "elsewhere"
    target.write_bytes(b"x")
    link = home / "presentation.key"
    link.symlink_to(target)
    _refusal(lambda: ha.key_init(link, owner_uid=UID), "key-exists-or-not-creatable")


# ── load_key ────────────────────────────────────────────────────────────────


def test_load_key_refuses_everything_key_init_does_not_leave(
    home: Path, key_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ha.load_key(key_path, owner_uid=UID)
    key_path.chmod(0o440)
    _refusal(lambda: ha.load_key(key_path, owner_uid=UID), "key-mode-unexpected")
    key_path.chmod(0o600)
    _refusal(lambda: ha.load_key(key_path, owner_uid=UID), "key-mode-unexpected")
    key_path.chmod(0o400)

    _refusal(lambda: ha.load_key(home / "absent", owner_uid=UID), "key-missing")

    link = home / "link.key"
    link.symlink_to(key_path)
    _refusal(lambda: ha.load_key(link, owner_uid=UID), "key-not-a-regular-file")

    garbage = home / "garbage.key"
    garbage.write_bytes(b"not a pem")
    garbage.chmod(0o400)
    _refusal(lambda: ha.load_key(garbage, owner_uid=UID), "key-unreadable")

    monkeypatch.setattr(ha, "_owner_only_dir", lambda *args, **kwargs: None)
    _refusal(lambda: ha.load_key(key_path, owner_uid=UID + 1), "key-owner-unexpected")


def test_load_key_refuses_a_key_that_is_not_ed25519(home: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    path = home / "ec.key"
    path.write_bytes(pem)
    path.chmod(0o400)
    _refusal(lambda: ha.load_key(path, owner_uid=UID), "key-not-ed25519")


# ── pop ─────────────────────────────────────────────────────────────────────


def _challenge(
    key_id: str = KEY_ID, deployment_ref: str = "target-1"
) -> dict[str, str]:
    from dotmac_kernel.licensing import DeploymentPossessionChallenge

    return DeploymentPossessionChallenge(
        challenge_id="challenge-1",
        key_id=key_id,
        deployment_ref=deployment_ref,
        nonce=bytes(range(16, 48)),
        expires_at=datetime(2026, 11, 19, 11, 0, 0, tzinfo=UTC),
    ).to_wire()


def test_pop_answer_verifies_under_kernels_own_verifier(
    key_path: Path, home: Path
) -> None:
    from dotmac_kernel.licensing import (
        DeploymentPossessionChallenge,
        DeploymentVerificationKey,
        verify_possession,
    )

    key = ha.load_key(key_path, owner_uid=UID)
    answer = ha.pop_response(
        key, challenge=_challenge(), key_id=KEY_ID, deployment_ref="target-1"
    )
    public = key.public_key().public_bytes_raw()
    verified = verify_possession(
        DeploymentPossessionChallenge.from_wire(_challenge()),
        answer,
        key=DeploymentVerificationKey(
            key_id=KEY_ID,
            public_key_b64=support.b64url(public),
            deployment_ref="target-1",
        ),
        now=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
    )
    assert verified.key_id == KEY_ID


def test_pop_refuses_a_challenge_for_another_key_or_deployment(key_path: Path) -> None:
    key = ha.load_key(key_path, owner_uid=UID)
    _refusal(
        lambda: ha.pop_response(
            key,
            challenge=_challenge("other-key"),
            key_id=KEY_ID,
            deployment_ref="target-1",
        ),
        "challenge-not-for-this-key",
    )
    _refusal(
        lambda: ha.pop_response(
            key, challenge=_challenge(), key_id=KEY_ID, deployment_ref="target-2"
        ),
        "challenge-not-for-this-key",
    )
    _refusal(
        lambda: ha.pop_response(
            key, challenge={"schema": "nope"}, key_id=KEY_ID, deployment_ref="target-1"
        ),
        "challenge-malformed",
    )
    _refusal(
        lambda: ha.pop_response(
            key, challenge=_challenge(), key_id="", deployment_ref="target-1"
        ),
        "key-id-invalid",
    )


# ── assemble ────────────────────────────────────────────────────────────────


def _assemble(**overrides: Any) -> dict[str, Any]:
    candidate, installed = _pair()
    arguments: dict[str, Any] = {
        "dispatch_materials": {"attempt_id": support.DISPATCH_ID},
        "candidate": candidate.canonical_document(),
        "installed": installed.canonical_document(),
        "expected_candidate_digest": str(attestation_envelope_digest(candidate)),
        "expected_installed_digest": str(attestation_envelope_digest(installed)),
    }
    arguments.update(overrides)
    return ha.assemble_unsigned(**arguments)


def test_assemble_builds_the_closed_unsigned_document() -> None:
    document = _assemble()
    candidate, installed = _pair()
    assert set(document) == {"attempt_id", "candidate", "installed"}
    assert document["attempt_id"] == support.DISPATCH_ID
    assert document["candidate"] == candidate.canonical_document()
    assert document["installed"] == installed.canonical_document()


def test_assemble_reads_the_attempt_id_from_the_dispatch_materials_only() -> None:
    document = _assemble(
        dispatch_materials={
            "attempt_id": support.DISPATCH_ID,
            "authorization_envelope": {},
            "dispatch_envelope": {},
        }
    )
    assert document["attempt_id"] == support.DISPATCH_ID


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"dispatch_materials": {}}, "attempt-id-invalid"),
        ({"dispatch_materials": {"attempt_id": "not-a-uuid"}}, "attempt-id-invalid"),
        (
            {"dispatch_materials": {"attempt_id": support.DISPATCH_ID.upper()}},
            "attempt-id-invalid",
        ),
        (
            {"expected_candidate_digest": "sha256:" + "0" * 64},
            "candidate-digest-mismatch",
        ),
        (
            {"expected_installed_digest": "sha256:" + "0" * 64},
            "installed-digest-mismatch",
        ),
        ({"expected_candidate_digest": "nope"}, "expected-digest-invalid"),
        (
            {"expected_installed_digest": "SHA256:" + "0" * 64},
            "expected-digest-invalid",
        ),
        ({"candidate": {"schema": "x"}}, "candidate-envelope-malformed"),
        ({"installed": {}}, "installed-envelope-malformed"),
    ],
)
def test_assemble_refusals(overrides: dict[str, Any], code: str) -> None:
    _refusal(lambda: _assemble(**overrides), code)


def test_assemble_refuses_an_installed_attestation_for_another_dispatch() -> None:
    candidate = support.candidate_envelope()
    installed = support.installed_envelope(candidate, observation_id="another-dispatch")
    _refusal(
        lambda: _assemble(
            installed=installed.canonical_document(),
            expected_installed_digest=str(attestation_envelope_digest(installed)),
        ),
        "installed-observation-is-not-the-dispatch",
    )


def test_assemble_refuses_an_installed_attestation_that_binds_another_candidate() -> (
    None
):
    candidate = support.candidate_envelope()
    other = support.candidate_envelope(version="0.4.0a3")
    installed = support.installed_envelope(other)
    _refusal(
        lambda: _assemble(
            candidate=candidate.canonical_document(),
            installed=installed.canonical_document(),
            expected_installed_digest=str(attestation_envelope_digest(installed)),
        ),
        "installed-does-not-bind-the-candidate",
    )


def test_assemble_refuses_swapped_roles_and_a_shared_key() -> None:
    candidate, installed = _pair()
    _refusal(
        lambda: _assemble(
            candidate=installed.canonical_document(),
            installed=candidate.canonical_document(),
            expected_candidate_digest=str(attestation_envelope_digest(installed)),
            expected_installed_digest=str(attestation_envelope_digest(candidate)),
        ),
        "candidate-purpose-unexpected",
    )
    same_key = support.installed_envelope(candidate, key=support.CANDIDATE_KEY)
    _refusal(
        lambda: _assemble(
            installed=same_key.canonical_document(),
            expected_installed_digest=str(attestation_envelope_digest(same_key)),
        ),
        "pair-shares-a-signing-key",
    )


# ── present ─────────────────────────────────────────────────────────────────


def test_present_writes_a_document_control_would_accept(
    home: Path, key_path: Path
) -> None:
    summary = _present(home, key_path)
    document = _document(home)
    candidate, installed = _pair()
    assert set(document) == {"attempt_id", "presentation", "candidate", "installed"}
    assert document["attempt_id"] == support.DISPATCH_ID
    assert document["candidate"] == candidate.canonical_document()
    assert document["installed"] == installed.canonical_document()
    _control_accepts(
        document["presentation"],
        ha.load_key(key_path, owner_uid=UID).public_key().public_bytes_raw(),
    )
    statement = document["presentation"]["statement"]
    assert statement["dispatch_id"] == support.DISPATCH_ID
    assert statement["key_id"] == KEY_ID
    assert statement["candidate_attestation_envelope_digest"] == str(
        attestation_envelope_digest(candidate)
    )
    assert statement["installed_attestation_envelope_digest"] == str(
        attestation_envelope_digest(installed)
    )
    # store time minus 2 s, then 300 s
    assert statement["issued_at"] == "2026-11-19T10:04:58Z"
    assert statement["expires_at"] == "2026-11-19T10:09:58Z"
    assert summary["issued_at"] == statement["issued_at"]
    raw = (home / "host-admission.json").read_bytes()
    assert summary["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert stat.S_IMODE((home / "host-admission.json").stat().st_mode) == 0o600
    assert not list(home.glob(".*.partial"))


def test_present_is_create_only(home: Path, key_path: Path) -> None:
    _present(home, key_path)
    before = (home / "host-admission.json").read_bytes()
    _refusal(lambda: _present(home, key_path), "output-exists")
    assert (home / "host-admission.json").read_bytes() == before


def test_present_never_follows_a_symlink_at_the_output(
    home: Path, key_path: Path
) -> None:
    target = home / "victim"
    target.write_text("keep")
    (home / "host-admission.json").symlink_to(target)
    _refusal(lambda: _present(home, key_path), "output-exists")
    assert target.read_text() == "keep"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"store_time": "2026-11-19T10:05:00"}, "store-time-invalid"),
        ({"store_time": "2026-11-19T10:05:00.250Z"}, "store-time-invalid"),
        ({"store_time": "2026-11-19T10:05:00+00:00"}, "store-time-invalid"),
        ({"key_id": ""}, "key-id-invalid"),
        ({"key_id": "has space"}, "key-id-invalid"),
        ({"admission_input": None}, "admission-input-required"),
    ],
)
def test_present_refusals(
    home: Path, key_path: Path, overrides: dict[str, Any], code: str
) -> None:
    _refusal(lambda: _present(home, key_path, **overrides), code)
    assert not (home / "host-admission.json").exists()


def test_present_refuses_a_missing_or_unsafe_key(home: Path, key_path: Path) -> None:
    _refusal(lambda: _present(home, home / "absent.key"), "key-missing")
    key_path.chmod(0o444)
    _refusal(lambda: _present(home, key_path), "key-mode-unexpected")


def test_present_refuses_an_unsafe_output_directory(
    tmp_path: Path, key_path: Path, home: Path
) -> None:
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    open_dir.chmod(0o755)
    _refusal(
        lambda: _present(home, key_path, out=open_dir / "host-admission.json"),
        "output-directory-unsafe",
    )
    _refusal(
        lambda: _present(home, key_path, out="host-admission.json"),
        "output-not-absolute",
    )


def test_present_refuses_an_unsigned_input_that_carries_a_presentation(
    home: Path, key_path: Path
) -> None:
    unsigned = json.loads(_unsigned(home).read_text())
    unsigned["presentation"] = {}
    path = support.write_json(home / "widened.json", unsigned)
    _refusal(
        lambda: _present(home, key_path, admission_input=path),
        "admission-input-malformed",
    )


def test_present_refuses_an_input_whose_pair_does_not_bind(
    home: Path, key_path: Path
) -> None:
    unsigned = json.loads(_unsigned(home).read_text())
    other = support.candidate_envelope(version="0.4.0a3")
    unsigned["candidate"] = other.canonical_document()
    path = support.write_json(home / "swapped.json", unsigned)
    _refusal(
        lambda: _present(home, key_path, admission_input=path),
        "installed-does-not-bind-the-candidate",
    )


# ── present --refresh ───────────────────────────────────────────────────────


def test_refresh_replaces_the_file_atomically_with_a_later_presentation(
    home: Path, key_path: Path
) -> None:
    _present(home, key_path)
    first = _document(home)
    out = home / "host-admission.json"
    _present(
        home,
        key_path,
        refresh=True,
        admission_input=None,
        store_time="2026-11-19T10:05:40Z",
    )
    second = _document(home)
    public = ha.load_key(key_path, owner_uid=UID).public_key().public_bytes_raw()
    _control_accepts(second["presentation"], public)
    assert second["attempt_id"] == first["attempt_id"]
    assert second["candidate"] == first["candidate"]
    assert second["installed"] == first["installed"]
    assert second["presentation"]["statement"]["issued_at"] == "2026-11-19T10:05:38Z"
    assert (
        second["presentation"]["statement"]["presentation_id"]
        != first["presentation"]["statement"]["presentation_id"]
    )
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert not list(home.glob(".*.partial"))


def test_refresh_refuses_a_missing_file(home: Path, key_path: Path) -> None:
    _refusal(
        lambda: _present(home, key_path, refresh=True, admission_input=None),
        "admission-input-unreadable",
    )


def test_refresh_refuses_a_store_time_that_is_not_later(
    home: Path, key_path: Path
) -> None:
    _present(home, key_path)
    before = (home / "host-admission.json").read_bytes()
    for later in (STORE_TIME, "2026-11-19T10:00:00Z"):
        _refusal(
            lambda later=later: _present(
                home, key_path, refresh=True, admission_input=None, store_time=later
            ),
            "refresh-store-time-not-later",
        )
    assert (home / "host-admission.json").read_bytes() == before


def test_refresh_refuses_a_file_signed_by_another_key(
    home: Path, key_path: Path
) -> None:
    _present(home, key_path)
    other = home / "other.key"
    ha.key_init(other, owner_uid=UID)
    _refusal(
        lambda: _present(
            home,
            other,
            refresh=True,
            admission_input=None,
            store_time="2026-11-19T10:06:00Z",
        ),
        "presentation-signature-invalid",
    )


def test_refresh_refuses_a_tampered_or_substituted_document(
    home: Path, key_path: Path
) -> None:
    _present(home, key_path)
    out = home / "host-admission.json"
    original = json.loads(out.read_text())

    def refresh() -> None:
        _present(
            home,
            key_path,
            refresh=True,
            admission_input=None,
            store_time="2026-11-19T10:06:00Z",
        )

    # the statement edited after signing
    tampered = json.loads(json.dumps(original))
    tampered["presentation"]["statement"]["dispatch_id"] = "another"
    out.write_text(json.dumps(tampered))
    _refusal(refresh, "presentation-signature-invalid")

    # another validly signed installed envelope under the old presentation
    candidate = support.candidate_envelope()
    other_installed = support.installed_envelope(
        candidate, observation_id=support.DISPATCH_ID, host_id="host-b"
    )
    swapped = json.loads(json.dumps(original))
    swapped["installed"] = other_installed.canonical_document()
    out.write_text(json.dumps(swapped))
    _refusal(refresh, "previous-presentation-differs")

    # a different key id
    out.write_text(json.dumps(original))
    _refusal(
        lambda: _present(
            home,
            key_path,
            key_id="another-key-id",
            refresh=True,
            admission_input=None,
            store_time="2026-11-19T10:06:00Z",
        ),
        "previous-presentation-differs",
    )

    # a malformed previous presentation
    broken = json.loads(json.dumps(original))
    broken["presentation"] = {"statement": {}}
    out.write_text(json.dumps(broken))
    _refusal(refresh, "previous-presentation-malformed")

    # a document missing its presentation entirely is an input error, not a refresh
    unsigned = {k: v for k, v in original.items() if k != "presentation"}
    out.write_text(json.dumps(unsigned))
    _refusal(refresh, "admission-input-malformed")


def test_refresh_input_must_be_the_output(home: Path, key_path: Path) -> None:
    _present(home, key_path)
    _refusal(
        lambda: _present(
            home,
            key_path,
            refresh=True,
            admission_input=_unsigned(home, "elsewhere.json"),
            store_time="2026-11-19T10:06:00Z",
        ),
        "refresh-input-must-be-the-output",
    )


# ── signature independence ──────────────────────────────────────────────────


def test_a_presentation_does_not_verify_under_any_other_key(
    home: Path, key_path: Path
) -> None:
    _present(home, key_path)
    presentation = _document(home)["presentation"]
    with pytest.raises(InvalidSignature):
        _control_accepts(presentation, support.raw_public(support.OTHER_KEY))


def test_the_signed_bytes_are_controls_canonical_json(
    home: Path, key_path: Path
) -> None:
    _present(home, key_path)
    presentation = _document(home)["presentation"]
    statement = presentation["statement"]
    assert list(statement) == sorted(statement), "stored in canonical key order"
    expected = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    signature = presentation["signature"]
    raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    public = ha.load_key(key_path, owner_uid=UID).public_key()
    public.verify(raw, expected)


# ── source guards and CLI ───────────────────────────────────────────────────


def test_the_script_names_no_incarnation_key_or_transit_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "transit" not in source.replace("transit signature", "")
    assert "incarnation" not in source.replace("NOT the incarnation key", "")


def test_the_script_never_reads_the_clock() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None)
            assert name not in {"now", "utcnow", "today", "time"}


def test_cli_end_to_end_and_refusal_output(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = home / "presentation.key"
    assert ha.main(["key-init", "--key-path", str(key), "--owner-uid", str(UID)]) == 0
    public = json.loads(capsys.readouterr().out)
    assert set(public) == {"public_key_b64url", "public_key_fingerprint"}
    assert ha.main(["key-init", "--key-path", str(key), "--owner-uid", str(UID)]) == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == "refused: key-exists-or-not-creatable"
    assert captured.out == ""

    candidate, installed = _pair()
    files = {
        "dispatch.json": {
            "attempt_id": support.DISPATCH_ID,
            "authorization_envelope": {},
        },
        "candidate.json": candidate.canonical_document(),
        "installed.json": installed.canonical_document(),
    }
    for name, document in files.items():
        support.write_json(home / name, document)
    unsigned = home / "unsigned.json"
    assert (
        ha.main(
            [
                "assemble",
                "--dispatch-materials", str(home / "dispatch.json"),
                "--candidate", str(home / "candidate.json"),
                "--installed", str(home / "installed.json"),
                "--candidate-digest", str(attestation_envelope_digest(candidate)),
                "--installed-digest", str(attestation_envelope_digest(installed)),
                "--out", str(unsigned),
            ]
        )
        == 0
    )  # fmt: skip
    capsys.readouterr()
    out = home / "host-admission.json"
    common = [
        "--key-path", str(key),
        "--owner-uid", str(UID),
        "--key-id", KEY_ID,
        "--out", str(out),
    ]  # fmt: skip
    assert (
        ha.main(
            [
                "present",
                *common,
                "--store-time",
                STORE_TIME,
                "--admission-input",
                str(unsigned),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        ha.main(
            ["present", *common, "--store-time", "2026-11-19T10:05:30Z", "--refresh"]
        )
        == 0
    )
    refreshed = json.loads(capsys.readouterr().out)
    assert refreshed["issued_at"] == "2026-11-19T10:05:28Z"
    _control_accepts(
        json.loads(out.read_text())["presentation"],
        base64.urlsafe_b64decode(public["public_key_b64url"] + "="),
    )
