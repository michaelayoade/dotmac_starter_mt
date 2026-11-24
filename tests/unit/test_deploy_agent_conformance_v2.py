"""dotmac-deploy-agent: conformance to Control's `contract/target_api_v2.json`.

A delta over v1 (v1 unchanged). Vendored byte-identical beside v1 and pinned. Every
group the agent can replay through its own functions is replayed: the agent
observation envelope (PoP-signed), domain separation, the observation kind rules,
the `deploy_binding` job/lease/image/naming shapes and the job params render. The
lease route bodies and the renew ledger belong to the lease client (#1146) and are
not replayed here; the server-side parse refusals (`refused_parse`) are Control's.
PROVISIONAL: the sha may change once more (the reconcile marker).
"""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotmac_deploy_agent import observation_v2 as o2
from dotmac_deploy_agent import wire
from dotmac_deploy_agent.canon import canonical_json, digest
from dotmac_deploy_agent.jobs import check_plan_binding, job_env, plan_jobs
from dotmac_deploy_agent.refusal import Refusal

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent"
)
VENDORED = SOURCE / "contract/target_api_v2.json"
#: Control a18 `contract/target_api_v2.json` (branch control-a18-20261122 at dfe7133,
#: 298742 bytes). A new Control sha replaces this value and the vendored bytes together.
TARGET_API_V2_SHA256 = (
    "4d847889d516d0613e0214c7b24c718ec9828c661be8bd0170b24a8e3da9be33"
)


def load() -> dict[str, object]:
    raw = VENDORED.read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest() == TARGET_API_V2_SHA256
    ), "the vendored v2 contract differs from the pinned Control file"
    return json.loads(raw)


def test_v2_is_vendored_byte_identical_and_pinned_beside_an_unchanged_v1() -> None:
    document = load()
    assert document["contract"] == "dotmac.target_api/2"
    assert document["extends"]["contract"] == "dotmac.target_api/1"  # type: ignore[index]
    v1 = (SOURCE / "contract/target_api_v1.json").read_bytes()
    assert (
        hashlib.sha256(v1).hexdigest()
        == "2e02f2ca295ee531d819d8c09aa7887ed4801c6272e60dd8b2c151d21929a907"
    )


def signer_for(key: dict[str, object]) -> wire.PopSigner:
    return wire.PopSigner.from_seed(str(key["key_id"]), str(key["seed_b64u"]).encode())


def public_key(key: dict[str, object]) -> Ed25519PublicKey:
    raw = wire.decode_b64url(str(key["public_b64u"]), length=32)
    assert raw is not None
    return Ed25519PublicKey.from_public_bytes(raw)


def test_the_observation_constants_match() -> None:
    ao = load()["agent_observation"]
    assert ao["schema"] == o2.SCHEMA  # type: ignore[index]
    assert {k: list(v) for k, v in o2.KIND_OUTCOMES.items()} == ao["kind_outcomes"]  # type: ignore[index]
    assert ao["limits"] == {  # type: ignore[index]
        "max_details_bytes": o2.MAX_DETAILS_BYTES,
        "max_details_depth": o2.MAX_DETAILS_DEPTH,
        "max_envelope_bytes": o2.MAX_ENVELOPE_BYTES,
    }
    assert sorted(load()["constants"]["observation_kinds"]) == sorted(o2.KIND_OUTCOMES)  # type: ignore[index]
    key = ao["key"]  # type: ignore[index]
    assert signer_for(key).public_key_b64url == key["public_b64u"]


def test_every_ok_observation_is_reproduced_byte_for_byte_and_signature_for_signature() -> (
    None
):
    ao = load()["agent_observation"]
    signer = signer_for(ao["key"])  # type: ignore[index]
    for vector in ao["ok"]:  # type: ignore[index]
        statement = vector["statement"]
        assert o2.signed_bytes(statement).hex() == vector["signed_bytes_hex"]
        envelope = o2.seal(statement, signer)
        assert envelope["signature"] == vector["signature_b64u"]
        assert canonical_json(envelope).decode() == vector["wire_utf8"]
        # a reconcile's report id also carries observed_at: every observation of an
        # expired dispatch is its own report
        suffix = (
            f":{statement['observed_at']}" if statement["kind"] == "reconcile" else ""
        )
        assert (
            vector["report_id"]
            == f"agent:{statement['kind']}:{statement['dispatch_id']}{suffix}"
        )
        public_key(ao["key"]).verify(
            wire.decode_b64url(vector["signature_b64u"], length=64) or b"",
            bytes.fromhex(vector["signed_bytes_hex"]),
        )  # type: ignore[index]


@pytest.mark.parametrize("internal", ["completed", "failed", "refused", "reconciled"])
def test_the_agents_own_record_converts_to_a_statement_the_vectors_accept(
    internal: str,
) -> None:
    ao = load()["agent_observation"]
    for vector in ao["ok"]:  # type: ignore[index]
        s = vector["statement"]
        mapping = o2._RECONCILE_OUTCOME if s["kind"] == "reconcile" else o2._OUTCOME
        if mapping.get(internal) != s["outcome"]:
            continue
        record = {
            "kind": s["kind"],
            "dispatch_id": s["dispatch_id"],
            "target_ref": s["target_ref"],
            "outcome": internal,
            "reason": s["reason"] or None,
            "details": s["details"],
            "observed_at": s["observed_at"],
        }
        assert o2.statement_from(record, key_id=s["key_id"]) == s


def test_a_statement_changed_after_signing_or_signed_under_another_key_id_does_not_verify() -> (
    None
):
    ao = load()["agent_observation"]
    for vector in ao["refused_verify"]:  # type: ignore[index]
        wire_doc = json.loads(vector["wire_utf8"])
        signature = wire.decode_b64url(wire_doc["signature"], length=64) or b""
        if vector["name"] == "a statement changed after signing":
            with pytest.raises(InvalidSignature):
                public_key(ao["key"]).verify(
                    signature, o2.signed_bytes(wire_doc["statement"])
                )  # type: ignore[index]
        else:  # signed under a key id Control does not know: the key id is inside the signed bytes
            assert wire_doc["statement"]["key_id"] != ao["key"]["key_id"]  # type: ignore[index]


def test_details_outside_control_limits_are_replaced_by_a_marker_never_reshaped() -> (
    None
):
    assert o2.shaped_details({"a": [{"b": [{"c": 1}]}]}) == {
        "details_dropped": "shape"
    }  # depth 5
    assert o2.shaped_details({"a": 1.5}) == {"details_dropped": "shape"}
    assert o2.shaped_details({"a": "x" * 20000}) == {"details_dropped": "size"}
    assert o2.shaped_details({"a": [1, "x", None, True]}) == {"a": [1, "x", None, True]}


def test_a_record_that_cannot_be_expressed_is_a_value_error() -> None:
    base = {
        "kind": "deploy",
        "dispatch_id": "d",
        "target_ref": "a/b/c",
        "outcome": "completed",
        "reason": None,
        "details": {},
        "observed_at": "2027-01-06T12:00:00Z",
    }
    for change in (
        {"kind": "other"},
        {"outcome": "nonsense"},
        {"reason": " padded "},
        {"details": []},
    ):
        with pytest.raises(ValueError):
            o2.statement_from({**base, **change}, key_id="cred-1")
    assert o2.statement_from(base, key_id="cred-1")["reason"] == ""


def test_the_observation_and_request_byte_domains_never_parse_or_verify_as_each_other() -> (
    None
):
    ds = load()["domain_separation"]
    ao = load()["agent_observation"]
    key = public_key(ao["key"])  # type: ignore[index]
    observed = ds["observation_signed_bytes_utf8"].encode()  # type: ignore[index]
    request = ds["request_message_utf8"].encode()  # type: ignore[index]
    assert observed.startswith(b"{") and request.startswith(
        wire.REQUEST_PURPOSE.encode()
    )
    wire_doc = json.loads(ds["observation_wire_utf8"])  # type: ignore[index]
    signature = wire.decode_b64url(wire_doc["signature"], length=64) or b""
    key.verify(signature, observed)
    with pytest.raises(InvalidSignature):
        key.verify(signature, request)
    assert o2.signed_bytes(wire_doc["statement"]) == observed


def test_the_agent_reports_a_kind_the_plan_operation_allows() -> None:
    vectors = load()["observation_vectors"]
    assert vectors["kinds"] == ["deploy", "reconcile", "job"]  # type: ignore[index]
    for ok in vectors["ok"]:  # type: ignore[index]
        assert (
            ok["kind"] == "reconcile"
            or o2.kind_for_operation(ok["operation"]) == ok["kind"]
        )
    for refused in vectors["refused"]:  # type: ignore[index]
        if refused["operation"] in ("deploy", "rollback", "run_job"):
            assert o2.kind_for_operation(refused["operation"]) != refused["kind"]


# -- deploy_binding ---------------------------------------------------------
def test_the_binding_examples_pass_the_agents_own_checks_and_the_job_render_is_reproduced() -> (
    None
):
    binding = load()["binding"]
    assert (
        check_plan_binding(binding["serving"]["snapshot"]["deploy_binding"], "deploy")
        is None
    )  # type: ignore[index]
    job_binding = binding["job"]["snapshot"]["deploy_binding"]  # type: ignore[index]
    job = check_plan_binding(job_binding, "run_job")
    assert job is not None and job.name == "import_execute" and job.role == "import"
    assert [j.name for j in plan_jobs({"deploy_binding": job_binding})] == [
        "import_execute"
    ]  # definitions, extra keys ignored
    render = binding["job_params_render"]  # type: ignore[index]
    assert job_env(job) == render["render_utf8"].encode()
    assert (
        job.params_env_digest
        == render["params_env_digest"]
        == digest(render["render_utf8"].encode())
    )
    assert dict(job.env) == render["params"]


def test_every_refused_binding_is_refused_by_the_agent() -> None:
    refused = load()["binding"]["refused"]  # type: ignore[index]
    # every refused vector Control publishes, however many: never a pinned count
    assert len(refused) >= 27
    assert {"job_inputs", "job_shape", "lease_split", "naming_required"} <= {
        item["code"] for item in refused
    }
    for item in refused:
        with pytest.raises(Refusal):
            check_plan_binding(item["snapshot"]["deploy_binding"], item["operation"])


def test_details_depth_counts_containers_not_scalars_like_control() -> None:
    nested = {"a": [{"b": ["leaf"]}]}  # dict(1) > list(2) > dict(3) > list(4) > scalar
    assert o2.shaped_details(nested) == nested
    too_deep = {"a": [{"b": [["x"]]}]}  # a list at depth 5
    assert o2.shaped_details(too_deep) == {"details_dropped": "shape"}
