"""dotmac-deploy-agent: conformance to Control's `contract/target_api_v1.json` (ruling R1).

Deployment Control owns the target API bytes: wire messages, headers, path shape,
materials v1, the env names, the render and secret-shape vectors. The agent
vendors that file BYTE-IDENTICAL under `src/dotmac_deploy_agent/contract/` and
pins its sha256 to the value in the Control a18 CHANGELOG. A change is a `_v2`
file: a Control release, then the CP pin and this copy in one train.

Until Control a18 lands that file this test FAILS, on purpose and loudly: an agent
whose wire bytes are checked against nothing must not look green.
"""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from dotmac_deploy_agent import wire
from dotmac_deploy_agent.params import Param, canonical_render, secret_shaped
from dotmac_deploy_agent.refusal import Refusal

VENDORED = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent/contract/target_api_v1.json"
)
#: The sha256 recorded in Control a18's CHANGELOG for `contract/target_api_v1.json`.
#: None until that release exists; setting it is part of vendoring the file.
TARGET_API_V1_SHA256: str | None = None

MISSING = (
    "the vendored contract is missing: copy Control a18's "
    "`contract/target_api_v1.json` byte-identical to "
    "packages/dotmac-deploy-agent/src/dotmac_deploy_agent/contract/target_api_v1.json "
    "and set TARGET_API_V1_SHA256 from the a18 CHANGELOG (Starter Control PR #76)"
)


def load() -> dict[str, object]:
    if not VENDORED.is_file():
        pytest.fail(MISSING)
    raw = VENDORED.read_bytes()
    if TARGET_API_V1_SHA256 is None:
        pytest.fail("TARGET_API_V1_SHA256 is not pinned: " + MISSING)
    assert (
        hashlib.sha256(raw).hexdigest() == TARGET_API_V1_SHA256
    ), "the vendored contract differs from the pinned Control release"
    return json.loads(raw)


def vectors(kind: str) -> list[dict[str, object]]:
    document = load()
    found = [v for v in document.get("vectors", []) if v.get("kind") == kind]  # type: ignore[union-attr]
    assert found, f"the vendored contract carries no {kind!r} vectors"
    return found


def test_the_vendored_contract_is_present_and_pinned() -> None:
    load()


def test_request_message_vectors() -> None:
    for vector in vectors("request_message"):
        given = vector["input"]
        message = wire.request_message(**given)  # type: ignore[arg-type]
        assert message.decode() == vector["expected"]


def test_response_message_vectors() -> None:
    for vector in vectors("response_message"):
        message = wire.response_message(**vector["input"])  # type: ignore[arg-type]
        assert message.decode() == vector["expected"]


def test_canonical_render_vectors() -> None:
    for vector in vectors("canonical_render"):
        params = [Param(**p) for p in vector["input"]["params"]]  # type: ignore[index]
        render = canonical_render(params, vector["input"]["app_image"])  # type: ignore[index]
        assert render.decode() == vector["expected"]


def test_secret_shape_vectors_neither_side_refuses_what_the_other_accepts() -> None:
    for vector in vectors("secret_shape"):
        value = str(vector["input"])
        try:
            canonical_render(
                [Param("p", "SOME_VALUE", "string", value)], "img@sha256:" + "0" * 64
            )
            refused = False
        except Refusal:
            refused = True
        assert refused == (vector["expected"] == "refuse"), value
        assert secret_shaped(value) == (vector["expected"] == "refuse") or not refused
