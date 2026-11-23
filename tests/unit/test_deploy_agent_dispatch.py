"""dotmac-deploy-agent: act only on a Control-signed, plan-bound dispatch (ruling R1)."""
# ruff: noqa: E501

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotmac_deploy_agent.dispatch import fingerprint_of, read_identity
from dotmac_deploy_agent.materials import parse_materials
from dotmac_deploy_agent.refusal import Refusal

from tests.unit.deploy_agent_support import (
    DISPATCH_KEY,
    NOW,
    Keys,
    make_config,
    make_materials,
    make_rig,
    write_identity,
)


def parse(tmp_path: Path, document: dict[str, object]):  # type: ignore[no-untyped-def]
    config = make_config(tmp_path, Keys())
    identity = read_identity(config.dispatch_identity_path, owner_uid=os.geteuid())
    return parse_materials(document, config, identity=identity, store_now=NOW)


def refused(tmp_path: Path, document: dict[str, object]) -> str:
    with pytest.raises(Refusal) as caught:
        parse(tmp_path, document)
    return caught.value.code


def test_a_signed_plan_bound_dispatch_is_accepted(tmp_path: Path) -> None:
    parsed = parse(tmp_path, make_materials())
    assert parsed.dispatch_id == "dispatch-1"
    assert parsed.plan_digest.startswith("sha256:")


def test_a_dispatch_signed_by_another_key_is_refused(tmp_path: Path) -> None:
    document = make_materials(sign_with=Ed25519PrivateKey.generate())
    assert refused(tmp_path, document) == "dispatch_signature_invalid"


def test_a_statement_changed_after_signing_is_refused(tmp_path: Path) -> None:
    document = make_materials()
    document["dispatch_envelope"]["statement"]["operation"] = "restore"  # type: ignore[index]
    assert refused(tmp_path, document) == "dispatch_signature_invalid"


def test_a_plan_snapshot_that_is_not_the_signed_plan_is_refused(tmp_path: Path) -> None:
    document = make_materials()
    snapshot = copy.deepcopy(document["plan_snapshot"])
    snapshot["deploy_binding"]["lease_ttl_seconds"] = 3600  # type: ignore[index]
    document["plan_snapshot"] = snapshot
    # The shape check cross-checks the signed plan digest against the snapshot.
    assert refused(tmp_path, document) == "materials_invalid"
    # Swapping in a different, internally valid plan fares no better.
    other = make_materials(lease_ttl_seconds=120)
    document["plan_snapshot"] = other["plan_snapshot"]
    assert refused(tmp_path, document) == "materials_invalid"


def test_the_params_digest_images_legacy_and_ttl_come_only_from_the_signed_plan(
    tmp_path: Path,
) -> None:
    legacy = {
        "policy_version": 3,
        "sha256": "sha256:" + "7" * 64,
        "names": ["A_NAME"],
        "expires_at": "2027-02-01T00:00:00Z",
    }
    parsed = parse(
        tmp_path,
        make_materials(
            lease_ttl_seconds=1200, legacy=legacy, active_binding_names=["X"]
        ),
    )
    assert parsed.lease_ttl_seconds == 1200
    assert parsed.legacy is not None and parsed.legacy.version == 3
    assert parsed.legacy.names == ("A_NAME",)
    assert parsed.active_binding_names == ("X",)
    assert parsed.images[0].service == "app"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: e.pop("signature"),
        lambda e: e.__setitem__("signature", ""),
        lambda e: e.__setitem__("signature", e["signature"] + "="),
        lambda e: e.__setitem__("extra", 1),
        lambda e: e["statement"].pop("plan_digest"),
        lambda e: e["statement"].__setitem__("surprise", 1),
        lambda e: e.__setitem__("statement", "not a mapping"),
    ],
)
def test_a_malformed_or_unsigned_envelope_is_refused(tmp_path: Path, mutation) -> None:  # type: ignore[no-untyped-def]
    document = make_materials()
    mutation(document["dispatch_envelope"])
    assert refused(tmp_path, document) in {
        "dispatch_envelope_invalid",
        "dispatch_signature_invalid",
        "materials_invalid",
    }


def test_wrong_purpose_key_id_or_fingerprint_is_refused(tmp_path: Path) -> None:
    assert (
        refused(tmp_path, make_materials(statement={"purpose": "authorization"}))
        == "dispatch_envelope_invalid"
    )
    assert (
        refused(tmp_path, make_materials(statement={"key_id": "someone-else"}))
        == "dispatch_signature_invalid"
    )
    assert (
        refused(
            tmp_path,
            make_materials(statement={"public_key_fingerprint": "sha256:" + "0" * 64}),
        )
        == "dispatch_signature_invalid"
    )


def test_a_dispatch_for_another_host_product_or_environment_is_refused(
    tmp_path: Path,
) -> None:
    for target in ("erp/staging/other", "sub/staging/host-a", "erp/prod/host-a"):
        assert (
            refused(tmp_path, make_materials(target_ref=target))
            == "materials_target_mismatch"
        )


def test_a_statement_that_disagrees_with_the_document_is_refused(
    tmp_path: Path,
) -> None:
    for change in (
        {"target_ref": "erp/staging/other"},
        {"product_code": "sub"},
        {"environment": "prod"},
        {"dispatch_id": "dispatch-2"},
    ):
        assert (
            refused(tmp_path, make_materials(statement=change)) == "materials_invalid"
        )


def test_a_dispatch_issued_in_the_future_is_refused(tmp_path: Path) -> None:
    document = make_materials(statement={"issued_at": "2027-01-06T13:00:00Z"})
    assert refused(tmp_path, document) == "dispatch_envelope_invalid"


def test_nothing_acts_before_the_dispatch_verifies(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(
        sign_with=Ed25519PrivateKey.generate(),
        roles=[{"role": "web", "single_use": False}],
    )
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "dispatch_signature_invalid")
    # No lease, no transfer, no secret minted, no run: signed input or nothing.
    assert rig.cp.lease_log == [] and rig.transfer.calls == []
    assert rig.bao.paths == [] and rig.runner.specs == []
    assert (
        rig.cp.observations[0]["dispatch_id"] == "dispatch-1"
    )  # refusal evidence only


def test_the_pinned_identity_rules(tmp_path: Path) -> None:
    path = tmp_path / "ids" / "dispatch.json"
    write_identity(path)
    uid = os.geteuid()
    read_identity(path, owner_uid=uid)

    def broken(mutate) -> None:  # type: ignore[no-untyped-def]
        document = json.loads(path.read_text())
        mutate(document)
        path.write_text(json.dumps(document))
        with pytest.raises(Refusal) as caught:
            read_identity(path, owner_uid=uid)
        assert caught.value.code == "dispatch_identity_invalid"
        write_identity(path)

    broken(lambda d: d.__setitem__("purpose", "authorization"))
    broken(lambda d: d.__setitem__("algorithm", "rsa"))
    broken(lambda d: d.__setitem__("schema", "other"))
    broken(lambda d: d.__setitem__("extra", 1))
    broken(lambda d: d.__setitem__("public_key_fingerprint", "sha256:" + "0" * 64))
    broken(lambda d: d.__setitem__("public_key_b64url", d["public_key_b64url"] + "="))

    path.chmod(0o664)  # writable by the group
    with pytest.raises(Refusal):
        read_identity(path, owner_uid=uid)
    write_identity(path)
    with pytest.raises(Refusal):  # not owned by the required owner
        read_identity(path, owner_uid=uid + 1)
    link = tmp_path / "ids" / "link.json"
    link.symlink_to(path)
    with pytest.raises(Refusal):
        read_identity(link, owner_uid=uid)
    path.parent.chmod(0o775)  # a directory others can write into
    with pytest.raises(Refusal):
        read_identity(path, owner_uid=uid)
    with pytest.raises(Refusal):
        read_identity(tmp_path / "ids" / "absent.json", owner_uid=uid)


def test_the_fingerprint_is_sha256_of_the_raw_key() -> None:
    raw = DISPATCH_KEY.public_key().public_bytes_raw()
    assert fingerprint_of(raw).startswith("sha256:") and len(fingerprint_of(raw)) == 71


def test_a_dispatch_older_than_the_bound_is_refused_as_stale(tmp_path: Path) -> None:
    """M3: CP can re-serve an old signed dispatch; staleness has a past bound too."""
    old = make_materials(
        statement={"issued_at": "2027-01-06T05:00:00Z"}
    )  # 7 h before NOW
    assert refused(tmp_path, old) == "dispatch_stale"
    fresh = make_materials(
        statement={"issued_at": "2027-01-06T07:00:00Z"}
    )  # 5 h: inside 6 h
    parse(tmp_path, fresh)


def test_a_replayed_dispatch_is_refused_before_anything_is_minted_or_moved(
    tmp_path: Path,
) -> None:
    from tests.unit.deploy_agent_support import make_rig

    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(roles=[{"role": "web", "single_use": False}])
    from tests.unit.deploy_agent_support import prepare_role

    prepare_role(rig, "web")
    assert rig.launcher.run_once().outcome == "completed"
    minted = len(rig.bao.paths)
    transfers = len(rig.transfer.calls)
    leases = len(rig.cp.lease_log)
    again = rig.launcher.run_once()  # CP serves the SAME signed dispatch again
    assert (again.outcome, again.reason) == ("refused", "dispatch_replayed")
    assert len(rig.bao.paths) == minted and len(rig.transfer.calls) == transfers
    assert len(rig.cp.lease_log) == leases  # no lease either
    assert len(rig.runner.specs) == 1


def test_a_dispatch_that_only_reached_a_refusal_before_delivery_may_be_retried(
    tmp_path: Path,
) -> None:
    """A lease refusal happens before the first irreversible step, so the same
    dispatch is not consumed by it."""
    from tests.unit.deploy_agent_support import make_rig

    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials()
    rig.cp.overrides["lease/acquire"] = (409, {"error": "lease-held"})
    assert rig.launcher.run_once().reason == "lease_refused"
    del rig.cp.overrides["lease/acquire"]
    assert rig.launcher.run_once().outcome == "completed"


def test_a_pinned_dispatch_identity_whose_seed_is_published_is_refused(
    tmp_path: Path,
) -> None:
    """L3: the vector file publishes two private seeds; their keys sign nothing."""
    import json as _json

    from tests.unit.deploy_agent_support import write_identity

    path = tmp_path / "ids" / "dispatch.json"
    write_identity(path)
    document = _json.loads(path.read_text())
    document["public_key_b64url"] = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"
    from dotmac_deploy_agent.canon import sha256_hex
    from dotmac_deploy_agent.wire import decode_b64url

    raw = decode_b64url(document["public_key_b64url"], length=32) or b""
    document["public_key_fingerprint"] = "sha256:" + sha256_hex(raw)  # self-consistent
    path.write_text(_json.dumps(document))
    with pytest.raises(Refusal) as caught:
        read_identity(path, owner_uid=os.geteuid())
    assert caught.value.code == "dispatch_identity_invalid"


def test_an_older_dispatch_refused_earlier_cannot_run_after_a_newer_one_executed(
    tmp_path: Path,
) -> None:
    """R2-L2: the per-id journal cannot order dispatches; the high-water mark on
    (execution_sequence, attempt_no) can."""
    from tests.unit.deploy_agent_support import make_rig

    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(dispatch_id="dispatch-1")  # seq 1
    rig.cp.overrides["lease/acquire"] = (409, {"error": "lease-held"})
    assert rig.launcher.run_once().reason == "lease_refused"  # refused, not executed
    del rig.cp.overrides["lease/acquire"]
    rig.cp.materials = make_materials(dispatch_id="dispatch-2")  # seq 2
    assert rig.launcher.run_once().outcome == "completed"
    minted = (len(rig.bao.paths), len(rig.transfer.calls), len(rig.cp.lease_log))
    rig.cp.materials = make_materials(dispatch_id="dispatch-1")  # the older one returns
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "dispatch_superseded")
    assert (
        len(rig.bao.paths),
        len(rig.transfer.calls),
        len(rig.cp.lease_log),
    ) == minted
    assert len(rig.runner.specs) == 1
    assert rig.launcher.executed.high_water() == (2, 1)


def test_a_later_attempt_of_the_same_sequence_is_newer_and_an_earlier_one_is_not(
    tmp_path: Path,
) -> None:
    from tests.unit.deploy_agent_support import make_rig

    rig = make_rig(tmp_path)
    rig.cp.materials = make_materials(
        dispatch_id="dispatch-5", statement={"attempt_no": 2}
    )
    assert rig.launcher.run_once().outcome == "completed"
    rig.cp.materials = make_materials(
        dispatch_id="dispatch-5b", statement={"execution_sequence": 5, "attempt_no": 1}
    )
    assert rig.launcher.run_once().reason == "dispatch_superseded"
    rig.cp.materials = make_materials(
        dispatch_id="dispatch-5c", statement={"execution_sequence": 5, "attempt_no": 3}
    )
    assert rig.launcher.run_once().outcome == "completed"


def test_a_corrupt_high_water_mark_refuses_rather_than_resetting(
    tmp_path: Path,
) -> None:
    from tests.unit.deploy_agent_support import make_rig

    rig = make_rig(tmp_path)
    (rig.config.state_dir / "high_water.json").write_text("{not json")
    rig.cp.materials = make_materials()
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == ("refused", "dispatch_journal_invalid")
    assert rig.runner.specs == []


def test_a_malformed_sequence_in_the_signed_statement_is_refused(
    tmp_path: Path,
) -> None:
    for bad in (0, -1, "1", True):
        document = make_materials(statement={"execution_sequence": bad})
        assert refused(tmp_path, document) == "dispatch_envelope_invalid"
