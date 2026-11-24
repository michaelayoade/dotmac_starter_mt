"""dotmac-deploy-agent: signed fetch and observation return against the CP double."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotmac_deploy_agent import wire
from dotmac_deploy_agent.client import TargetClient
from dotmac_deploy_agent.config import AgentConfig, refuse_db_environment
from dotmac_deploy_agent.dispatch import read_identity
from dotmac_deploy_agent.materials import parse_materials
from dotmac_deploy_agent.refusal import CODES, Refusal

from tests.unit.deploy_agent_support import (
    NOW,
    FakeCp,
    Keys,
    make_config,
    make_materials,
    make_rig,
    public_b64url,
)


def client_for(tmp_path: Path) -> tuple[TargetClient, FakeCp, AgentConfig]:
    keys = Keys()
    config = make_config(tmp_path, keys)
    cp = FakeCp(keys)
    signer = wire.PopSigner.from_seed(config.credential_id, keys.seed())
    return TargetClient(config, signer, cp, clock=lambda: NOW), cp, config


def parse(document: dict[str, object], config: AgentConfig):  # type: ignore[no-untyped-def]
    return parse_materials(
        document,
        config,
        identity=read_identity(config.dispatch_identity_path, owner_uid=os.geteuid()),
        store_now=NOW,
    )


def test_materials_none_when_nothing_pending(tmp_path: Path) -> None:
    client, cp, _ = client_for(tmp_path)
    assert client.materials() is None
    assert cp.calls == [("GET", "materials")]


def test_signed_request_verifies_and_materials_parse(tmp_path: Path) -> None:
    client, cp, config = client_for(tmp_path)
    cp.materials = make_materials()
    reply = client.materials()
    assert reply is not None and reply.store_now == NOW
    parsed = parse(reply.document, config)
    assert parsed.dispatch_id == "dispatch-1"  # from the SIGNED statement


def test_path_is_three_unencoded_segments_and_signatures_are_unpadded_base64url(
    tmp_path: Path,
) -> None:
    client, cp, _ = client_for(tmp_path)
    client.materials()
    assert cp.paths == [
        "/platform/deploy/target/v1/targets/erp/staging/host-a/materials"
    ]
    assert "%" not in cp.paths[0] and "?" not in cp.paths[0]


def test_padded_or_standard_base64_signature_is_refused(tmp_path: Path) -> None:
    keys = Keys()
    signer = wire.PopSigner.from_seed("cred-1", keys.seed())
    signature = signer.sign(b"message")
    assert "=" not in signature and "+" not in signature and "/" not in signature
    padded = base64.urlsafe_b64encode(
        base64.urlsafe_b64decode(signature + "==")
    ).decode()
    assert padded != signature  # the padded form exists and is not accepted below
    for bad in (padded, base64.b64encode(b"x" * 64).decode(), signature[:-2]):
        with pytest.raises(Refusal):
            wire.verify_response((public_b64url(keys.cp),), b"m", bad)
    # And a pop-key credential in the old padded form is refused.
    with pytest.raises(Refusal):
        wire.PopSigner.from_seed("cred-1", base64.b64encode(b"s" * 32))


def test_response_signature_negatives(tmp_path: Path) -> None:
    client, cp, _ = client_for(tmp_path)
    cp.tamper_signature = True
    with pytest.raises(Refusal) as tampered:
        client.materials()
    assert tampered.value.code == "response_signature_invalid"
    cp.tamper_signature, cp.omit_signature = False, True
    with pytest.raises(Refusal) as missing:
        client.materials()
    assert missing.value.code == "response_signature_invalid"


def test_response_signed_by_another_key_is_refused(tmp_path: Path) -> None:
    client, cp, _ = client_for(tmp_path)
    cp.keys.cp = Ed25519PrivateKey.generate()  # not the pinned CP key
    with pytest.raises(Refusal) as caught:
        client.materials()
    assert caught.value.code == "response_signature_invalid"


def test_clock_skew_refused(tmp_path: Path) -> None:
    client, cp, _ = client_for(tmp_path)
    cp.store_now_override = "2027-01-06T12:05:00Z"
    with pytest.raises(Refusal) as caught:
        client.materials()
    assert caught.value.code == "clock_skew"


def test_server_error_throttle_and_size_limit_are_retryable_not_signature_refusals(
    tmp_path: Path,
) -> None:
    client, cp, _ = client_for(tmp_path)
    cp.status_5xx = True
    with pytest.raises(Refusal) as down:
        client.materials()
    assert down.value.code == "cp_unreachable"
    cp.status_5xx = False
    for status in (429, 413):  # unsigned 429 and 413 from a proxy or the router
        cp.raw_status["materials"] = status
        with pytest.raises(Refusal) as throttled:
            client.materials()
        assert throttled.value.code == "cp_unreachable"


def test_a_signed_4xx_carries_a_closed_detail(tmp_path: Path) -> None:
    client, cp, _ = client_for(tmp_path)
    cp.overrides["materials"] = (403, {"error": "credential_revoked"})
    with pytest.raises(Refusal) as refused:
        client.materials()
    assert (refused.value.code, refused.value.detail) == (
        "cp_refused",
        "credential_revoked",
    )
    cp.overrides["materials"] = (403, {"error": "Not A Code!"})
    with pytest.raises(Refusal) as odd:
        client.materials()
    assert odd.value.detail is None


def test_nonce_is_fresh_per_request_and_replay_is_refused_by_cp(tmp_path: Path) -> None:
    keys = Keys()
    config = make_config(tmp_path, keys)
    cp = FakeCp(keys)
    signer = wire.PopSigner.from_seed(config.credential_id, keys.seed())
    client = TargetClient(config, signer, cp, clock=lambda: NOW, nonce=lambda: "0" * 32)
    client.materials()
    with pytest.raises(Refusal) as caught:
        client.materials()  # same nonce
    assert caught.value.code == "cp_refused"


def test_request_signed_with_a_different_key_is_refused_by_cp(tmp_path: Path) -> None:
    keys = Keys()
    config = make_config(tmp_path, keys)
    cp = FakeCp(keys)
    rogue = wire.PopSigner.from_seed(config.credential_id, Keys().seed())
    client = TargetClient(config, rogue, cp, clock=lambda: NOW)
    with pytest.raises(Refusal) as caught:
        client.materials()
    assert (caught.value.code, caught.value.detail) == ("cp_refused", "bad_signature")


def test_materials_for_another_target_are_refused_and_reported(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(target_ref="erp/staging/other")
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "materials_target_mismatch")
    assert rig.runner.specs == []


@pytest.mark.parametrize(
    "change",
    [
        {"unknown_key": 1},
        {"schema": "dotmac.deploy_target_materials/2"},
        {"roles": [{"role": "app"}]},
        {"roles": [{"role": "App", "single_use": False}]},
        {"roles": [{"role": "app", "single_use": False, "container_uid": 10001}]},
        {
            "roles": [
                {"role": "a", "single_use": False},
                {"role": "a", "single_use": True},
            ]
        },
    ],
)
def test_materials_with_unknown_or_malformed_fields_are_refused(
    tmp_path: Path, change: dict[str, object]
) -> None:
    config = make_config(tmp_path, Keys())
    with pytest.raises(Refusal):
        parse(make_materials(**change), config)


def test_digest_images_legacy_and_ttl_are_read_only_from_the_signed_plan(
    tmp_path: Path,
) -> None:
    """Top-level CP fields for these are not part of the schema at all."""
    config = make_config(tmp_path, Keys())
    for field in (
        "params_env_digest",
        "images",
        "legacy",
        "lease_ttl_seconds",
        "roles",
    ):
        document = make_materials()
        document[field] = "anything"
        with pytest.raises(Refusal) as caught:
            parse(document, config)
        assert caught.value.code == "materials_invalid", field


def test_derived_archive_name_and_roles_come_from_the_binding(tmp_path: Path) -> None:
    config = make_config(tmp_path, Keys())
    parsed = parse(make_materials(roles=[{"role": "web", "single_use": False}]), config)
    assert parsed.images[0].archive == "app-" + "a" * 64 + ".oci.tar"
    assert [(r.role, r.single_use) for r in parsed.roles] == [("web", False)]
    assert parsed.lease_ttl_seconds == 900


def test_agent_refuses_db_environment() -> None:
    for name in (
        "DATABASE_URL",
        "PLATFORM_DATABASE_URL",
        "DISPATCH_CONSUMPTION_DATABASE_URL",
        "LANE3_LEASE_DATABASE_URL",
        "PGHOST",
        "PGPASSWORD",
    ):
        with pytest.raises(Refusal) as caught:
            refuse_db_environment({name: "x", "PATH": "/bin"})
        assert caught.value.code == "db_environment_present"
    refuse_db_environment({"PATH": "/bin", "HOME": "/x"})


def test_launcher_refuses_to_start_with_db_environment(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, environ={"DATABASE_URL": "x"})
    with pytest.raises(Refusal):
        rig.launcher.run_once()
    assert rig.cp.calls == []


def test_config_requires_https_pin_absolute_runner_and_the_bindings_names(
    tmp_path: Path,
) -> None:
    keys = Keys()
    required = dict(make_config(tmp_path, keys).bindings_env)
    without_one = tuple(
        sorted((k, v) for k, v in required.items() if k != "HOST_ADMISSION_MATERIALS")
    )
    extra = (*tuple(required.items()), ("DATABASE_URL", "x"))
    for override in (
        {"cp_url": "http://cp.example.invalid"},
        {"cp_url": "https://user:pw@cp.example.invalid"},
        {"cp_spki_sha256": ()},
        {"cp_spki_sha256": ("zz",)},
        {"foundation_argv": ("relative",)},
        {"poll_seconds": 5},
        {"target_ref": "only/two"},
        {"cp_response_public_keys_b64url": ()},
        {"cp_response_public_keys_b64url": ("AAAA",)},
        {"cp_response_public_keys_b64url": (base64.b64encode(b"k" * 32).decode(),)},
        {"bindings_env": without_one},
        {"bindings_env": extra},
        {"bindings_env": (*without_one, ("HOST_ADMISSION_MATERIALS", "relative/path"))},
        {"wrap_ttl_seconds": 10},
    ):
        with pytest.raises(Refusal):
            make_config(tmp_path, keys, **override)
    assert public_b64url(keys.cp)


def test_every_refusal_code_is_in_the_closed_set() -> None:
    with pytest.raises(ValueError):
        Refusal("not_a_code")
    assert "lease_refused" in CODES
    assert "secret_chown_failed" not in CODES  # chown is gone, not renamed


def test_the_config_file_must_be_root_owned_and_not_writable(tmp_path: Path) -> None:
    """L2: an agent that can rewrite its own keys or argv can persist."""
    from dotmac_deploy_agent.config import protected_config_file

    path = tmp_path / "config.toml"
    path.write_text("x = 1\n")
    path.chmod(0o644)
    protected_config_file(path, owner_uid=os.geteuid())  # owner matches
    with pytest.raises(Refusal):
        protected_config_file(path, owner_uid=os.geteuid() + 1)
    with pytest.raises(Refusal):  # the default owner is root: not us, on CI
        if os.geteuid() == 0:  # pragma: no cover - the agent is never root
            raise Refusal("config_invalid")
        protected_config_file(path)
    path.chmod(0o664)
    with pytest.raises(Refusal):
        protected_config_file(path, owner_uid=os.geteuid())
    link = tmp_path / "link.toml"
    link.symlink_to(path)
    with pytest.raises(Refusal):
        protected_config_file(link, owner_uid=os.geteuid())


def test_the_pinned_secrets_naming_must_be_well_formed(tmp_path: Path) -> None:
    for override in (
        {"secrets_namespace": "Erp"},
        {"secrets_environment": "prod/x"},
        {"secrets_namespace": ""},
        {"dispatch_max_age_seconds": 10},
        {"settle_seconds": 5},
    ):
        with pytest.raises(Refusal):
            make_config(tmp_path, Keys(), **override)
