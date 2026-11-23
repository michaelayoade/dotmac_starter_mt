"""dotmac-deploy-agent: M11 legacy env bridge, agent side (DESIGN-rev3 2.8)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from dotmac_deploy_agent.canon import digest
from dotmac_deploy_agent.legacy import check_source, parse_names, prepare_legacy
from dotmac_deploy_agent.materials import LegacyPolicy
from dotmac_deploy_agent.refusal import Refusal

from tests.unit.deploy_agent_support import NOW, make_materials, make_rig

CONTENT = b"LEGACY_ONE=1\nLEGACY_TWO=2\n"


@dataclass
class Fixture:
    allowed: Path
    source: Path
    creds: Path
    deploy_dir: Path
    run_dir: Path
    environ: dict[str, str]
    policy: LegacyPolicy


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    allowed = tmp_path / "etc-legacy"
    allowed.mkdir(mode=0o755)
    allowed.chmod(0o755)
    source = allowed / "erp-staging-host-a.env"
    source.write_bytes(CONTENT)
    source.chmod(0o600)
    creds = tmp_path / "creds"
    creds.mkdir(mode=0o700)
    (creds / "legacy").write_bytes(CONTENT)
    (creds / "pop-key").write_bytes(b"unused-by-these-tests")
    run_dir = tmp_path / "deploy-run"
    run_dir.mkdir(mode=0o700)
    deploy_dir = tmp_path / "app"
    deploy_dir.mkdir()
    policy = LegacyPolicy(
        1, ("LEGACY_ONE", "LEGACY_TWO"), digest(CONTENT), NOW + timedelta(days=30)
    )
    return Fixture(
        allowed,
        source,
        creds,
        deploy_dir,
        run_dir,
        {"CREDENTIALS_DIRECTORY": str(creds)},
        policy,
    )


def prepare(f: Fixture, **over: object) -> Path:
    args: dict[str, object] = {
        "source": f.source,
        "deploy_dir": f.deploy_dir,
        "deploy_run_dir": f.run_dir,
        "environ": f.environ,
        "cp_now": NOW,
        "param_materials": {"APP_PORT"},
        "binding_names": set(),
        "allowed_dir": f.allowed,
        "required_uid": os.geteuid(),
    }
    args.update(over)
    return prepare_legacy(f.policy, **args)  # type: ignore[arg-type]


def test_legacy_copy_is_0400_and_matches_policy(fixture: Fixture) -> None:
    target = prepare(fixture)
    assert target.read_bytes() == CONTENT
    assert target.stat().st_mode & 0o777 == 0o400


def test_legacy_refuses_other_sha(fixture: Fixture) -> None:
    (fixture.creds / "legacy").write_bytes(CONTENT + b"EXTRA=1\n")
    with pytest.raises(Refusal) as caught:
        prepare(fixture)
    assert caught.value.code == "legacy_sha_mismatch"
    assert not (fixture.run_dir / "legacy.env").exists()


def test_legacy_names_equal_policy(fixture: Fixture) -> None:
    other = b"LEGACY_ONE=1\nSOMETHING_ELSE=2\n"
    (fixture.creds / "legacy").write_bytes(other)
    fixture.policy = LegacyPolicy(
        1, fixture.policy.names, digest(other), fixture.policy.expires_at
    )
    with pytest.raises(Refusal) as caught:
        prepare(fixture)
    assert caught.value.code == "legacy_names_mismatch"


def test_legacy_refuses_collision_with_param_binding_or_app_image(
    fixture: Fixture,
) -> None:
    for taken in ({"LEGACY_ONE"},):
        with pytest.raises(Refusal) as caught:
            prepare(fixture, param_materials=taken)
        assert caught.value.code == "legacy_name_collision"
    with pytest.raises(Refusal) as caught:
        prepare(fixture, binding_names={"LEGACY_TWO"})
    assert caught.value.code == "legacy_name_collision"
    content = b"APP_IMAGE=x\n"
    (fixture.creds / "legacy").write_bytes(content)
    fixture.policy = LegacyPolicy(
        1, ("APP_IMAGE",), digest(content), NOW + timedelta(days=1)
    )
    with pytest.raises(Refusal) as caught:
        prepare(fixture)
    assert caught.value.code == "legacy_name_collision"


def test_legacy_expired_refused_agent(fixture: Fixture) -> None:
    fixture.policy = LegacyPolicy(
        1, fixture.policy.names, fixture.policy.sha256, NOW - timedelta(seconds=1)
    )
    with pytest.raises(Refusal) as caught:
        prepare(fixture)
    assert caught.value.code == "legacy_expired"
    # Expiry is judged on CP time, so a host clock cannot extend it.
    with pytest.raises(Refusal):
        prepare(fixture, cp_now=NOW)


@pytest.mark.parametrize(
    "case",
    [
        "outside",
        "dotenv",
        "not_env",
        "symlink",
        "mode",
        "uid",
        "parent_writable",
        "missing",
        "deploy_dotenv",
    ],
)
def test_legacy_path_rules(fixture: Fixture, tmp_path: Path, case: str) -> None:
    kwargs: dict[str, object] = {}
    source = fixture.source
    code = "legacy_path_invalid"
    if case == "outside":
        source = tmp_path / "elsewhere.env"
        source.write_bytes(CONTENT)
    elif case == "dotenv":
        source = fixture.allowed / ".env"
        source.write_bytes(CONTENT)
    elif case == "not_env":
        source = fixture.allowed / "x.txt"
        source.write_bytes(CONTENT)
    elif case == "deploy_dotenv":
        source = fixture.deploy_dir / ".env"
        source.write_bytes(CONTENT)
    elif case == "symlink":
        real = fixture.allowed / "real.env"
        real.write_bytes(CONTENT)
        real.chmod(0o600)
        link = fixture.allowed / "link.env"
        link.symlink_to(real)
        source, code = link, "legacy_source_unsafe"
    elif case == "mode":
        fixture.source.chmod(0o640)
        code = "legacy_source_unsafe"
    elif case == "uid":
        kwargs["required_uid"] = os.geteuid() + 1
        code = "legacy_source_unsafe"
    elif case == "parent_writable":
        fixture.allowed.chmod(0o775)
        code = "legacy_source_unsafe"
    elif case == "missing":
        fixture.source.unlink()
        code = "legacy_source_unsafe"
    with pytest.raises(Refusal) as caught:
        prepare(fixture, source=source, **kwargs)
    assert caught.value.code == code
    assert not (fixture.run_dir / "legacy.env").exists()


def test_check_source_never_reads_the_file(fixture: Fixture) -> None:
    fixture.source.chmod(0o200)  # write-only: any read attempt would raise
    # mode is wrong for 0600, but the point is only that lstat decides
    with pytest.raises(Refusal):
        check_source(
            fixture.source,
            deploy_dir=fixture.deploy_dir,
            allowed_dir=fixture.allowed,
            required_uid=os.geteuid(),
        )


def test_legacy_only_via_credentials_dir(fixture: Fixture) -> None:
    # No credentials directory: the agent does not fall back to the source path.
    with pytest.raises(Refusal) as caught:
        prepare(fixture, environ={})
    assert caught.value.code == "legacy_credential_missing"
    # A directory without the credential: same.
    empty = fixture.creds.parent / "empty"
    empty.mkdir()
    with pytest.raises(Refusal):
        prepare(fixture, environ={"CREDENTIALS_DIRECTORY": str(empty)})
    # The bytes that are used are the credential's, not the source file's.
    fixture.source.write_bytes(b"DIFFERENT=1\n")
    assert prepare(fixture).read_bytes() == CONTENT


def test_legacy_malformed_and_duplicate_refused() -> None:
    for bad in (b"not a line\n", b"A=1\nA=2\n", b"\xff\xfe"):
        with pytest.raises(Refusal) as caught:
            parse_names(bad)
        assert caught.value.code == "legacy_malformed"
    assert parse_names(b"# c\n\nB=1\nA=2\n") == ("A", "B")


def test_launcher_legacy_end_to_end(
    fixture: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dotmac_deploy_agent.legacy as legacy

    monkeypatch.setattr(legacy, "LEGACY_DIR", fixture.allowed)
    original = legacy.check_source

    def relaxed(source: Path, **kwargs: object) -> None:
        kwargs["allowed_dir"] = fixture.allowed
        kwargs["required_uid"] = os.geteuid()
        original(source, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(legacy, "check_source", relaxed)
    (tmp_path / "rig").mkdir()
    rig = make_rig(
        tmp_path / "rig", environ=fixture.environ, legacy_source_path=fixture.source
    )
    seen: dict[str, bytes | int] = {}

    def effect(spec) -> None:  # type: ignore[no-untyped-def]
        assert spec.legacy_env is not None
        seen["content"] = spec.legacy_env.read_bytes()
        seen["mode"] = spec.legacy_env.stat().st_mode & 0o777

    rig.runner.effect = effect
    rig.cp.materials = make_materials(
        legacy={
            "policy_version": 1,
            "names": list(fixture.policy.names),
            "sha256": fixture.policy.sha256,
            "expires_at": "2027-02-05T12:00:00Z",
        }
    )
    result = rig.launcher.run_once()
    assert result.outcome == "completed", result
    assert seen == {"content": CONTENT, "mode": 0o400}
    assert not (rig.config.deploy_run_dir / "legacy.env").exists()
    details = rig.cp.observations[0]["details"]
    assert details["legacy"]["names"] == ["LEGACY_ONE", "LEGACY_TWO"]  # type: ignore[index]
    assert b"=1" not in repr(rig.cp.observations).encode()  # names only, never values
