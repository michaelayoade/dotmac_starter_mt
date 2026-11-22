"""dotmac-deploy-agent: signed fetch and observation return against the CP double."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotmac_deploy_agent import wire
from dotmac_deploy_agent.client import TargetClient
from dotmac_deploy_agent.config import AgentConfig, refuse_db_environment
from dotmac_deploy_agent.materials import parse_materials
from dotmac_deploy_agent.refusal import CODES, Refusal

from tests.unit.deploy_agent_support import (
    NOW,
    FakeCp,
    Keys,
    make_config,
    make_materials,
    make_rig,
    public_b64,
)


def client_for(tmp_path: Path) -> tuple[TargetClient, FakeCp, AgentConfig]:
    keys = Keys()
    config = make_config(tmp_path, keys)
    cp = FakeCp(keys)
    signer = wire.PopSigner.from_seed(config.credential_id, keys.seed())
    return TargetClient(config, signer, cp, clock=lambda: NOW), cp, config


def test_materials_none_when_nothing_pending(tmp_path: Path) -> None:
    client, cp, _ = client_for(tmp_path)
    assert client.materials() is None
    assert cp.calls == [("GET", "materials")]


def test_signed_request_verifies_and_materials_parse(tmp_path: Path) -> None:
    client, cp, config = client_for(tmp_path)
    cp.materials = make_materials()
    reply = client.materials()
    assert reply is not None and reply.store_now == NOW
    parsed = parse_materials(reply.document, config)
    assert parsed.dispatch_id == "dispatch-1"


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


def test_server_error_is_unreachable_and_4xx_carries_a_closed_detail(
    tmp_path: Path,
) -> None:
    client, cp, _ = client_for(tmp_path)
    cp.status_5xx = True
    with pytest.raises(Refusal) as down:
        client.materials()
    assert down.value.code == "cp_unreachable"
    cp.status_5xx = False
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
    assert refused.value.detail != odd.value.detail and odd.value.detail is None


def test_nonce_is_fresh_per_request_and_replay_is_refused_by_cp(tmp_path: Path) -> None:
    keys = Keys()
    config = make_config(tmp_path, keys)
    cp = FakeCp(keys)
    signer = wire.PopSigner.from_seed(config.credential_id, keys.seed())
    client = TargetClient(config, signer, cp, clock=lambda: NOW, nonce=lambda: "fixed")
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
        {"params_env_digest": "nope"},
        {"bindings": [{"role": "app"}]},
    ],
)
def test_materials_with_unknown_or_malformed_fields_are_refused(
    tmp_path: Path, change: dict[str, object]
) -> None:
    keys = Keys()
    config = make_config(tmp_path, keys)
    document = make_materials(**change)
    with pytest.raises(Refusal):
        parse_materials(document, config)


def test_secret_ref_must_point_at_this_product_role_and_name(tmp_path: Path) -> None:
    keys = Keys()
    config = make_config(tmp_path, keys)

    def doc(ref: str) -> dict[str, object]:
        return make_materials(
            bindings=[
                {
                    "role": "app",
                    "container_uid": None,
                    "single_use": False,
                    "names": [{"name": "DB_URL", "secret_ref": ref}],
                }
            ]
        )

    parse_materials(doc("bao://staging/erp/kv/app/DB_URL"), config)
    for bad in (
        "bao://staging/other/kv/app/DB_URL",
        "bao://prod/erp/kv/app/DB_URL",
        "bao://staging/erp/kv/other/DB_URL",
        "bao://staging/erp/kv/app/OTHER",
        "https://example.invalid/x",
    ):
        with pytest.raises(Refusal):
            parse_materials(doc(bad), config)


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


def test_config_requires_https_pin_and_absolute_runner(tmp_path: Path) -> None:
    keys = Keys()
    for override in (
        {"cp_url": "http://cp.example.invalid"},
        {"cp_url": "https://user:pw@cp.example.invalid"},
        {"cp_spki_sha256": ()},
        {"cp_spki_sha256": ("zz",)},
        {"foundation_argv": ("relative",)},
        {"poll_seconds": 5},
        {"target_ref": "only/two"},
        {"cp_response_public_keys_b64": ()},
        {"cp_response_public_keys_b64": ("AAAA",)},
    ):
        with pytest.raises(Refusal):
            make_config(tmp_path, keys, **override)
    assert public_b64(keys.cp)


def test_every_refusal_code_is_in_the_closed_set() -> None:
    with pytest.raises(ValueError):
        Refusal("not_a_code")
    assert "lease_refused" in CODES
