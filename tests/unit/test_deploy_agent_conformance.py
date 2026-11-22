"""dotmac-deploy-agent: conformance to Control's `contract/target_api_v1.json` (ruling R1).

Deployment Control owns the target API bytes: wire messages, headers, path shape,
materials v1, env names, render and secret-shape vectors. The agent vendors that
file BYTE-IDENTICAL under `src/dotmac_deploy_agent/contract/` and pins its sha256
to the value in the Control a18 CHANGELOG. A change is a `_v2` file: a Control
release, then the CP pin and this copy in one train.

Every vector group is replayed through the agent's OWN functions. Three groups
have no agent counterpart and say so: `paths.refused` is replayed through the
agent's path builder (it refuses what it cannot build), and `secret_ref_vectors`
does not apply because the agent no longer holds a `secret_ref` at all (Control
derives it; the agent receives roles).
"""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from dotmac_deploy_agent import wire
from dotmac_deploy_agent.canon import digest
from dotmac_deploy_agent.config import BINDINGS_ENV_OPTIONAL, BINDINGS_ENV_REQUIRED
from dotmac_deploy_agent.dispatch import plan_digest_of
from dotmac_deploy_agent.materials import (
    FORBIDDEN_KEYS,
    MATERIALS_KEYS,
    ImageSpec,
    check_shape,
    expected_app_image,
)
from dotmac_deploy_agent.params import Param, canonical_render
from dotmac_deploy_agent.refusal import Refusal
from dotmac_deploy_agent.runner import RunSpec, runner_environment

from tests.unit.deploy_agent_support import Keys, make_config

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent"
)
VENDORED = SOURCE / "contract/target_api_v1.json"
#: The sha256 Control a18 records in its CHANGELOG for `contract/target_api_v1.json`
#: (dotmac_deployment_control PR #76 at ab738548, 53737 bytes).
TARGET_API_V1_SHA256 = (
    "354e587a8f6902ae14653ee6ff0282544fcfd5c77d19d74cc2b99500bc3ac0b7"
)

REFUSAL_FOR_RENDER = {
    "secret_named_material": "params_secret_shaped",
    "secret_shaped": "params_secret_shaped",
    "claimed_twice": "params_value_invalid",
    "value_invalid": "params_value_invalid",
}
REFUSAL_FOR_VALUE = {
    "empty": "params_value_invalid",
    "alterable": "params_value_invalid",
    "secret_shaped": "params_secret_shaped",
}


def load() -> dict[str, object]:
    if not VENDORED.is_file():
        pytest.fail(
            "the vendored contract is missing: copy Control's "
            "contract/target_api_v1.json byte-identical to " + str(VENDORED)
        )
    return json.loads(VENDORED.read_bytes())


def join(parts: list[str]) -> str:
    return "".join(parts)


def test_the_vendored_contract_is_byte_identical_to_the_pinned_release() -> None:
    raw = VENDORED.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == TARGET_API_V1_SHA256
    assert len(raw) == 53737
    assert load()["contract"] == "dotmac.target_api/1"


def test_constants_match() -> None:
    c = load()["constants"]
    assert c["base_path"] == wire.BASE_PATH  # type: ignore[index]
    assert c["headers"] == {  # type: ignore[index]
        "credential_id": wire.H_CREDENTIAL,
        "issued_at": wire.H_ISSUED_AT,
        "nonce": wire.H_NONCE,
        "response_signature": wire.H_RESPONSE_SIGNATURE,
        "signature": wire.H_SIGNATURE,
        "target_ref": wire.H_TARGET,
    }
    assert c["request_purpose"] == wire.REQUEST_PURPOSE  # type: ignore[index]
    assert c["response_purpose"] == wire.RESPONSE_PURPOSE  # type: ignore[index]
    assert c["max_skew_seconds"] == wire.MAX_SKEW_SECONDS  # type: ignore[index]
    assert c["key_encoding"] == "base64url-unpadded"  # type: ignore[index]
    assert set(c["bindings_env_names"]) == set(BINDINGS_ENV_REQUIRED) | set(
        BINDINGS_ENV_OPTIONAL
    )  # type: ignore[index]
    spec = RunSpec(
        "d", "a/b/c", "a", "sha256:" + "0" * 64, Path("/p"), Path("/c"),
        (), "/creds", Path("/l"), "lease",
    )  # fmt: skip
    deploy = {n for n in runner_environment(spec) if n.startswith("DOTMAC_DEPLOY_")}
    assert deploy | {"CREDENTIALS_DIRECTORY"} == set(c["deploy_env_names"])  # type: ignore[index]


def test_base64url_vectors() -> None:
    group = load()["encoding"]["base64url"]  # type: ignore[index]
    for vector in group["ok"]:
        raw = bytes.fromhex(vector["hex"])
        assert wire.b64url_decode(vector["text"]) == raw
        assert wire.encode_b64url(raw) == vector["text"]
    for text in group["refused"]:
        assert wire.b64url_decode(text) is None, text


def test_nonce_stamp_skew_vectors() -> None:
    encoding = load()["encoding"]  # type: ignore[index]
    for nonce in encoding["nonces"]["ok"]:
        assert wire.check_nonce(nonce) == nonce
    for nonce in encoding["nonces"]["refused"]:
        with pytest.raises(Refusal):
            wire.check_nonce(nonce)
    for stamp in encoding["stamps"]["ok"]:
        wire.parse_stamp(stamp)
    for stamp in encoding["stamps"]["refused"]:
        with pytest.raises(Refusal):
            wire.parse_stamp(stamp)
    for vector in load()["stamp_vectors"]:  # type: ignore[union-attr]
        assert (
            wire.utc_stamp(datetime.fromisoformat(vector["input"]))
            == vector["expected"]
        )
    for vector in encoding["skew"]:
        if vector["ok"]:
            wire.check_skew(vector["issued_at"], vector["store_now"])
        else:
            with pytest.raises(Refusal) as caught:
                wire.check_skew(vector["issued_at"], vector["store_now"])
            assert caught.value.code == "clock_skew"


def test_unsigned_status_vectors() -> None:
    for status, expected in load()["encoding"]["unsigned_status"].items():  # type: ignore[index]
        assert wire.classify_unsigned_response(int(status)) == expected


def test_path_and_route_vectors() -> None:
    paths = load()["paths"]  # type: ignore[index]
    assert paths["base"] == wire.BASE_PATH
    for vector in paths["ok"]:
        if vector["route"] in wire.ROUTES:
            assert (
                wire.target_path(vector["target_ref"], vector["route"])
                == vector["path"]
            )
        else:  # committed, consume, resolve: the bindings' routes, never the agent's
            assert vector["route"] in {"committed", "consume", "resolve"}
    prefix = f"{wire.BASE_PATH}/targets/"
    for vector in paths["refused"]:
        parts = vector["path"][len(prefix) :].split("/")
        built = None
        try:
            built = wire.target_path("/".join(parts[:3]), "/".join(parts[3:]))
        except Refusal:
            pass
        assert built != vector["path"], vector[
            "path"
        ]  # what Control refuses, the agent cannot build
    agent_routes = {
        r["route"]: r["method"] for r in paths["routes"] if r["caller"] == "agent"
    }
    assert agent_routes == wire.ROUTES
    for vector in paths["wrong_verbs"]:
        with pytest.raises(Refusal):
            wire.check_route(vector["method"], vector["route"])


def test_target_ref_grammar_follows_control(tmp_path: Path) -> None:
    keys = Keys()
    for index, ref in enumerate(
        ("dotmac-erp-v2/production/host-a", "a/b/c", "dotmac.erp_v2/prod-1/h.1")
    ):
        make_config(tmp_path / f"ok{index}", keys, target_ref=ref)
    for index, ref in enumerate(
        ("Dotmac/production/host-a", "a/b", "a//c", "-a/b/c", "a/b/c/d")
    ):
        with pytest.raises(Refusal):
            make_config(tmp_path / f"bad{index}", keys, target_ref=ref)


def test_request_vectors_through_the_agents_signer_and_builders() -> None:
    document = load()
    request_keys = document["keys"]["request"]  # type: ignore[index]
    signer_factory = wire.PopSigner.from_seed
    for vector in document["request_vectors"]:  # type: ignore[union-attr]
        message = wire.request_message(
            method=vector["method"],
            path=vector["path"],
            body=vector["body_utf8"].encode(),
            credential_id=vector["credential_id"],
            target_ref=vector["target_ref"],
            issued_at=vector["issued_at"],
            nonce=vector["nonce"],
        )
        assert message.decode() == vector["message_utf8"]
        signer = signer_factory(
            vector["credential_id"], request_keys["seed_b64u"].encode()
        )
        assert (
            signer.sign(message) == vector["signature_b64u"]
        )  # Ed25519 is deterministic
        assert (
            wire.request_headers(
                credential_id=vector["credential_id"],
                target_ref=vector["target_ref"],
                issued_at=vector["issued_at"],
                nonce=vector["nonce"],
                signature_b64url=vector["signature_b64u"],
            )
            == vector["headers"]
        )


def test_response_vectors_verify_under_the_pinned_response_key_only() -> None:
    document = load()
    response_key = document["keys"]["response"]["public_b64u"]  # type: ignore[index]
    request_key = document["keys"]["request"]["public_b64u"]  # type: ignore[index]
    for vector in document["response_vectors"]:  # type: ignore[union-attr]
        message = wire.response_message(
            method=vector["method"],
            path=vector["path"],
            request_nonce=vector["request_nonce"],
            status=vector["status"],
            body=vector["body_utf8"].encode(),
        )
        assert message.decode() == vector["message_utf8"]
        wire.verify_response((response_key,), message, vector["signature_b64u"])
        with pytest.raises(Refusal):  # the request key is not the response key
            wire.verify_response((request_key,), message, vector["signature_b64u"])


def test_render_vectors() -> None:
    for vector in load()["render_vectors"]:  # type: ignore[union-attr]
        params = [
            Param(p["name"], p["material"], p["type"], join(p["value_parts"]))
            for p in vector["params"]
        ]
        if "refused" in vector:
            with pytest.raises(Refusal) as caught:
                canonical_render(params, vector["app_image"])
            assert caught.value.code == REFUSAL_FOR_RENDER[vector["refused"]], vector[
                "name"
            ]
            continue
        render = canonical_render(params, vector["app_image"])
        assert render.decode() == vector["render_utf8"], vector["name"]
        assert digest(render) == vector["digest"], vector["name"]


def test_secret_name_vectors() -> None:
    sample = {"string": "x", "path": "/run/x", "int": "1"}
    image = "127.0.0.1:5000/team/app@sha256:" + "1" * 64
    for vector in load()["secret_name_vectors"]:  # type: ignore[union-attr]
        params = [
            Param("p", vector["material"], vector["type"], sample[vector["type"]])
        ]
        if vector["accept"]:
            canonical_render(params, image)
        else:
            with pytest.raises(Refusal):
                canonical_render(params, image)


def test_value_vectors_the_agent_refuses_nothing_control_accepts_and_accepts_nothing_it_refuses() -> (
    None
):
    image = "127.0.0.1:5000/team/app@sha256:" + "1" * 64
    for vector in load()["value_vectors"]:  # type: ignore[union-attr]
        value = join(vector["value_parts"])
        params = [Param("p", "SOME_VALUE", "string", value)]
        if vector["accept"]:
            canonical_render(params, image)
        else:
            with pytest.raises(Refusal) as caught:
                canonical_render(params, image)
            assert caught.value.code == REFUSAL_FOR_VALUE[vector["reason"]], value


def test_materials_vectors() -> None:
    group = load()["materials"]  # type: ignore[index]
    assert group["schema"] == "dotmac.deploy_target_materials/1"
    assert set(group["keys"]) == set(MATERIALS_KEYS)
    assert set(group["removed_keys"]) == set(FORBIDDEN_KEYS)
    check_shape(group["ok"])
    for vector in group["refused"]:
        with pytest.raises(Refusal):
            check_shape(vector["document"])
    snapshot = group["ok"]["plan_snapshot"]
    assert plan_digest_of(snapshot) == group["plan_digest"]
    assert expected_app_image(snapshot) == group["expected_app_image"]
    for vector in group["archive_names"]:
        image = ImageSpec(
            vector["service"], "transfer", "registry.example.test/team/app",
            vector["digest"], "127.0.0.1:5000/team/app", vector["digest"],
        )  # fmt: skip
        assert image.archive == vector["name"]


def test_the_agent_holds_no_secret_ref() -> None:
    """`secret_ref_vectors` is Control's: the agent receives roles, never a ref."""
    for path in SOURCE.glob("*.py"):
        assert "bao://" not in path.read_text(), path.name
