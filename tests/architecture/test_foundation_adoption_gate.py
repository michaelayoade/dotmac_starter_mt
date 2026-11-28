"""The DR3_DR5 adoption-clearance gate: DrillClearance.v1 and the production scope.

Starter #1150 lets Foundation 0.4.0a2 publish with DR3_DR5 WAIVED under the
policy's ``adoption_conditions``. This gate is the read side: no production-scope
candidate envelope exists without a Michael-signed DrillClearance.v1 for the
exact a2 wheel. Every refusal is planted on its own against a clearance that is
otherwise accepted, and the accepting case is shown first, so a gate that
refused everything could not pass.

No key is enrolled in the repository. The tests generate throwaway keys in a
temporary directory and point the module at them; the shipped, EMPTY signer file
and the shipped, EMPTY oracle table are asserted separately.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import foundation_adoption_gate as adoption  # noqa: E402
import foundation_publication_gate as gate  # noqa: E402

SIGNER_SCRIPT = REPO / "scripts" / "candidate_attestation_signer.py"
WORKFLOW = REPO / ".github" / "workflows" / "foundation-candidate-attestation.yml"
FACILITY = "dotmac-deployment-foundation"
VERSION = "0.4.0a2"
COND_ID = "DR3_DR5_STAGING_DRILL"
WHEEL = "ab" * 32
SOURCE = "c" * 40
MANIFEST = "d" * 64
FAULTS = ("db_outage_at_migrate", "network_loss", "timeout_sigterm")
#: The test oracle. The shipped table is EMPTY; these are placeholders that only
#: prove the comparison, not the drill recipe owner's codes.
ORACLE = {fault: f"refused_{fault}" for fault in FAULTS}
NOW = datetime(2026, 12, 1, 12, 0, 0)
ISSUED = "2026-11-30T11:00:00Z"
RECORDED = "2026-11-30T10:00:00Z"
#: Pinned from the reviewed bytes: this slice must not change the policy or the
#: publication gate (the gate script is a release-authority input).
_POLICY_SHA = "b17bd3bb38ab382192cd3096d9ff603f3c168ffb635bfcdb22b2ee6f699abaca"
_GATE_SHA = "c9634b274a57dc243bba65b0b9c4c8d2928ebf62b8e8e7360da1f2befa732dec"
PINNED_BYTES = {
    "docs/inventories/foundation-publication-gates.json": _POLICY_SHA,
    "scripts/foundation_publication_gate.py": _GATE_SHA,
}


def _run(args, *, data=None, cwd=None) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        args, input=data, capture_output=True, check=True, cwd=cwd
    ).stdout


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _fault(name: str, index: int) -> dict:
    return {
        "fault": name,
        "dispatch_id": f"dispatch-{index}",
        "plan_digest": _digest(f"plan-{name}"),
        "injected_phase": "migrate",
        "admission": {
            "candidate_attestation_envelope_digest": _digest("cand"),
            "installed_attestation_envelope_digest": _digest("inst"),
            "installed_wheel_sha256": f"sha256:{WHEEL}",
        },
        "expected_outcome": ORACLE[name],
        "observed_outcome": ORACLE[name],
        "foundation_receipt_sha256": _digest(f"fr-{name}"),
        "agent_observation_envelope_sha256": _digest(f"ao-{name}"),
        "recovery_receipt_sha256": _digest(f"rr-{name}"),
        "receipt_sha256": _digest(f"r-{name}"),
        "reviewed_by": "reader",
        "recorded_at": RECORDED,
    }


def good_document() -> dict:
    return {
        "schema": adoption.SCHEMA,
        "facility": FACILITY,
        "version": VERSION,
        "condition_id": COND_ID,
        "register_ref": 2155,
        "candidate": {
            "artifact_sha256": f"sha256:{WHEEL}",
            "wheel_sha256": f"sha256:{WHEEL}",
            "source_sha": SOURCE,
            "gate2_manifest_sha256": f"sha256:{MANIFEST}",
        },
        "environment": "seabone-staging",
        "drill_target": {
            "target_ref": "example/staging/seabone",
            "control_target_id": "target-1",
            "host_id": "host-1",
            "bao_environment": "staging",
        },
        "faults": [_fault(name, i) for i, name in enumerate(FAULTS)],
        "issued_at": ISSUED,
    }


def canonical(document: dict) -> bytes:
    return adoption.canonical_result_bytes(document)


class World:
    """A throwaway signer, receipt, publication row and clearance directory."""

    def __init__(self, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.key = root / "key"
        _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)])
        self.blob = (root / "key.pub").read_text().split()[1]
        self.inventories = root / "inventories"
        (self.inventories / "foundation-adoption-clearances").mkdir(parents=True)
        self.signers = root / "allowed_signers"
        self.revoked = root / "revoked_keys"
        self.write_signers("20200101000000Z", "20990101000000Z")
        self.revoked.write_text("")
        (self.inventories / f"foundation-candidate-{VERSION}.json").write_text(
            json.dumps(
                {
                    "schema": "CandidateArtifact.v1",
                    "facility": FACILITY,
                    "version": VERSION,
                    "sha256": WHEEL,
                    "source_sha": SOURCE,
                }
            )
        )
        self.write_records(self.good_row())
        monkeypatch.setattr(adoption, "INVENTORIES", self.inventories)
        monkeypatch.setattr(
            adoption,
            "CLEARANCE_DIR",
            self.inventories / "foundation-adoption-clearances",
        )
        monkeypatch.setattr(adoption, "SIGNERS_PATH", self.signers)
        monkeypatch.setattr(adoption, "REVOKED_PATH", self.revoked)
        monkeypatch.setattr(
            adoption, "RECORDS_PATH", self.inventories / "publication-records.json"
        )
        monkeypatch.setattr(adoption, "EXPECTED_OUTCOMES", dict(ORACLE))
        monkeypatch.setattr(gate, "_utcnow", lambda: NOW)

    def write_signers(
        self, after: str, before: str, namespace: str | None = None
    ) -> None:
        self.signers.write_text(
            f"{adoption.PRINCIPAL} "
            f'namespaces="{namespace or adoption.NAMESPACE}",'
            f'valid-after="{after}",valid-before="{before}" '
            f"ssh-ed25519 {self.blob}\n"
        )

    def good_row(self) -> dict:
        return {
            "distribution": FACILITY,
            "version": VERSION,
            "tag": f"{FACILITY}-v{VERSION}",
            "publication_gate": gate.GATE_2,
            "gate2_manifest_sha256": f"sha256:{MANIFEST}",
            "evidence": [
                {
                    "id": "DR3_DR5",
                    "status": "WAIVED",
                    "waiver": {"register_ref": 2155, "condition_id": COND_ID},
                }
            ],
        }

    def write_records(self, *rows: dict) -> None:
        (self.inventories / "publication-records.json").write_text(
            json.dumps({"schema": adoption.RECORDS_SCHEMA, "records": list(rows)})
        )

    def sign(self, payload: bytes, namespace: str | None = None) -> bytes:
        sigfile = self.root / "payload.sig"
        sigfile.unlink(missing_ok=True)
        (self.root / "payload").write_bytes(payload)
        _run(
            [
                "ssh-keygen",
                "-Y",
                "sign",
                "-f",
                str(self.key),
                "-n",
                namespace or adoption.NAMESPACE,
                str(self.root / "payload"),
            ]
        )
        data = (self.root / "payload.sig").read_bytes()
        (self.root / "payload.sig").unlink()
        return data

    def commit(self, document: dict | None = None) -> tuple[bytes, bytes]:
        clearance = canonical(document or good_document())
        signature = self.sign(clearance)
        json_path, sig_path = adoption.clearance_paths(FACILITY, VERSION, COND_ID)
        json_path.write_bytes(clearance)
        sig_path.write_bytes(signature)
        return clearance, signature


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    return World(tmp_path, monkeypatch)


def refusal(callable_, *args, **kwargs) -> str:
    with pytest.raises(gate.GateRefused) as caught:
        callable_(*args, **kwargs)
    return str(caught.value)


# ── the accepting case, then the shipped state ─────────────────────────────


def test_a_good_clearance_is_accepted_and_adoptable(world: World) -> None:
    clearance, signature = world.commit()
    assert adoption.verify_clearance(FACILITY, VERSION, clearance, signature)
    adoption.require_adoptable(FACILITY, VERSION)


def test_the_shipped_signer_file_is_empty_and_refuses_everything() -> None:
    assert adoption.SIGNERS_PATH.read_bytes() == b""
    assert adoption.REVOKED_PATH.read_bytes() == b""
    assert adoption.SIGNERS_PATH.name == "foundation-adoption-clearance.allowed_signers"
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, b"x\n", b"y")
        == "clearance_signer_unenrolled"
    )


def test_the_shipped_clearance_directory_holds_no_clearance() -> None:
    names = sorted(p.name for p in adoption.CLEARANCE_DIR.iterdir())
    assert names == [".gitkeep"]
    # So the real repository refuses production adoption of a2 today.
    message = refusal(adoption.require_adoptable, FACILITY, VERSION)
    assert message == f"adoption_condition_unsatisfied:{COND_ID}"


def test_the_shipped_oracle_table_is_empty() -> None:
    assert adoption.EXPECTED_OUTCOMES == {}


def test_an_empty_signer_file_refuses_even_a_well_signed_clearance(
    world: World, monkeypatch
) -> None:
    clearance, signature = world.commit()
    world.signers.write_text("")
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance_signer_unenrolled"
    )
    message = refusal(adoption.require_adoptable, FACILITY, VERSION)
    assert message.startswith(f"adoption_condition_unsatisfied:{COND_ID}")


def test_an_empty_oracle_refuses_a_well_signed_clearance(
    world: World, monkeypatch
) -> None:
    clearance, signature = world.commit()
    monkeypatch.setattr(adoption, "EXPECTED_OUTCOMES", {})
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance_oracle_unpinned"
    )


@pytest.mark.parametrize("name", sorted(PINNED_BYTES))
def test_the_policy_and_gate_bytes_are_unchanged_by_this_slice(name: str) -> None:
    assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == PINNED_BYTES[name]


def test_a_version_with_no_blocking_condition_is_adoptable() -> None:
    adoption.require_adoptable(FACILITY, "0.9.9")
    adoption.require_adoptable("another-facility", VERSION)


# ── signature ──────────────────────────────────────────────────────────────


def test_an_unsigned_clearance_is_refused(world: World) -> None:
    clearance = canonical(good_document())
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, b"")
        == "clearance_signature.size"
    )
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, b"junk")
        == "clearance_signature_invalid"
    )


def test_a_signature_over_other_bytes_is_refused(world: World) -> None:
    clearance, signature = world.commit()
    other = canonical({**good_document(), "issued_at": "2026-11-30T09:00:00Z"})
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, other, signature)
        == "clearance_signature_invalid"
    )


@pytest.mark.parametrize(
    "namespace", [gate.NAMESPACE, "foundation-rehearsal-verification-v1@dotmac.io"]
)
def test_a_signature_in_another_namespace_is_refused(world: World, namespace) -> None:
    clearance = canonical(good_document())
    signature = world.sign(clearance, namespace)
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance_signature_invalid"
    )


def test_a_signer_enrolled_for_another_namespace_is_refused(world: World) -> None:
    clearance = canonical(good_document())
    signature = world.sign(clearance)
    world.write_signers("20200101000000Z", "20990101000000Z", gate.NAMESPACE)
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance_signer.options"
    )


def test_an_expired_signer_enrolment_refuses_the_clearance(world: World) -> None:
    clearance = canonical(good_document())
    signature = world.sign(clearance)
    world.write_signers("20200101000000Z", "20210101000000Z")
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance_signature_invalid"
    )


def test_a_clearance_issued_in_the_future_is_refused(world: World) -> None:
    document = good_document()
    document["issued_at"] = "2027-01-01T00:00:00Z"
    clearance = canonical(document)
    signature = world.sign(clearance)
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance.not_yet_valid"
    )


def test_a_revoked_signer_is_refused(world: World) -> None:
    clearance, signature = world.commit()
    world.revoked.write_text(f"ssh-ed25519 {world.blob}\n")
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance_signer_revoked"
    )


# ── bindings: each planted alone against an otherwise accepted clearance ────


def _signed(world: World, mutate) -> tuple[bytes, bytes]:
    document = good_document()
    mutate(document)
    clearance = canonical(document)
    return clearance, world.sign(clearance)


def _refused_with(world: World, mutate) -> str:
    clearance, signature = _signed(world, mutate)
    return refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)


def test_a_wrong_wheel_sha_is_refused(world: World) -> None:
    def mutate(document):
        document["candidate"]["wheel_sha256"] = "sha256:" + "ee" * 32

    assert _refused_with(world, mutate) == "clearance.candidate.wheel_mismatch"


def test_a_wrong_artifact_sha_is_refused(world: World) -> None:
    def mutate(document):
        document["candidate"]["artifact_sha256"] = "sha256:" + "ee" * 32

    assert _refused_with(world, mutate) == "clearance.candidate.artifact_mismatch"


def test_a_wrong_source_sha_is_refused(world: World) -> None:
    def mutate(document):
        document["candidate"]["source_sha"] = "e" * 40

    assert _refused_with(world, mutate) == "clearance.candidate.source_mismatch"


def test_a_wrong_gate2_manifest_is_refused(world: World) -> None:
    def mutate(document):
        document["candidate"]["gate2_manifest_sha256"] = "sha256:" + "ee" * 32

    assert _refused_with(world, mutate) == "clearance.candidate.manifest_mismatch"


def test_a_wrong_environment_is_refused(world: World) -> None:
    def mutate(document):
        document["environment"] = "production"

    assert _refused_with(world, mutate) == "clearance.environment"


def test_a_wrong_bao_environment_is_refused(world: World) -> None:
    def mutate(document):
        document["drill_target"]["bao_environment"] = "prod"

    assert _refused_with(world, mutate) == "clearance.drill_target.bao_environment"


def test_a_missing_fault_is_refused(world: World) -> None:
    def mutate(document):
        document["faults"].pop()

    assert _refused_with(world, mutate) == "clearance.faults"


def test_a_replaced_fault_is_refused_as_missing(world: World) -> None:
    def mutate(document):
        document["faults"][2] = copy.deepcopy(document["faults"][0])
        document["faults"][2]["dispatch_id"] = "dispatch-9"

    assert _refused_with(world, mutate) == "clearance.fault.duplicate"


def test_an_unknown_fault_is_refused(world: World) -> None:
    def mutate(document):
        document["faults"][2]["fault"] = "cosmic_ray"

    assert _refused_with(world, mutate) == "clearance.fault.unknown"


def test_a_dispatch_reused_across_faults_is_refused(world: World) -> None:
    def mutate(document):
        document["faults"][1]["dispatch_id"] = document["faults"][0]["dispatch_id"]

    assert _refused_with(world, mutate) == "clearance.fault.dispatch_reused"


def test_an_attestation_wheel_mismatch_is_refused(world: World) -> None:
    def mutate(document):
        document["faults"][1]["admission"]["installed_wheel_sha256"] = (
            "sha256:" + "ee" * 32
        )

    assert _refused_with(world, mutate) == "clearance.fault.installed_wheel_mismatch"


def test_an_unexpected_outcome_is_refused(world: World) -> None:
    def mutate(document):
        document["faults"][0]["expected_outcome"] = "anything"

    assert _refused_with(world, mutate) == "clearance.fault.expected_outcome"


def test_an_observed_outcome_that_differs_is_refused(world: World) -> None:
    def mutate(document):
        document["faults"][0]["observed_outcome"] = "succeeded"

    assert _refused_with(world, mutate) == "clearance.fault.observed_outcome"


def test_a_fault_recorded_after_issue_is_refused(world: World) -> None:
    def mutate(document):
        document["faults"][0]["recorded_at"] = "2026-11-30T12:00:00Z"

    assert _refused_with(world, mutate) == "clearance.fault.recorded_after_issue"


def test_a_wrong_condition_is_refused(world: World) -> None:
    def mutate(document):
        document["condition_id"] = "OTHER"

    assert _refused_with(world, mutate) == "clearance.condition"


def test_a_wrong_register_ref_is_refused(world: World) -> None:
    def mutate(document):
        document["register_ref"] = 1

    assert _refused_with(world, mutate) == "clearance.register_ref"


def test_a_wrong_version_is_refused(world: World) -> None:
    def mutate(document):
        document["version"] = "0.4.0a3"

    assert _refused_with(world, mutate) == "clearance.identity"


def test_an_extra_or_missing_key_is_refused(world: World) -> None:
    def mutate(document):
        document["note"] = "x"

    assert _refused_with(world, mutate) == "clearance.schema"


def test_a_noncanonical_encoding_is_refused(world: World) -> None:
    pretty = json.dumps(good_document(), indent=1).encode() + b"\n"
    signature = world.sign(pretty)
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, pretty, signature)
        == "clearance.noncanonical"
    )


def test_a_missing_publication_record_is_refused(world: World) -> None:
    clearance, signature = world.commit()
    world.write_records()
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance_publication_record_missing"
    )


def test_a_publication_record_that_does_not_waive_the_condition_is_refused(
    world: World,
) -> None:
    clearance, signature = world.commit()
    row = world.good_row()
    row["evidence"][0]["waiver"]["condition_id"] = "OTHER"
    world.write_records(row)
    assert (
        refusal(adoption.verify_clearance, FACILITY, VERSION, clearance, signature)
        == "clearance.candidate.waiver_mismatch"
    )


# ── require-adoptable ──────────────────────────────────────────────────────


def test_require_adoptable_refuses_without_a_committed_clearance(world: World) -> None:
    assert (
        refusal(adoption.require_adoptable, FACILITY, VERSION)
        == f"adoption_condition_unsatisfied:{COND_ID}"
    )


def test_require_adoptable_refuses_a_committed_but_invalid_clearance(
    world: World,
) -> None:
    document = good_document()
    document["environment"] = "production"
    world.commit(document)
    message = refusal(adoption.require_adoptable, FACILITY, VERSION)
    assert message == f"adoption_condition_unsatisfied:{COND_ID}:clearance.environment"


def test_require_adoptable_refuses_a_clearance_filed_under_another_condition(
    world: World,
) -> None:
    # The clearance bytes are valid, but the policy condition they are filed
    # for is not the one the document names: the document is refused.
    document = good_document()
    document["condition_id"] = "OTHER"
    world.commit(document)
    message = refusal(adoption.require_adoptable, FACILITY, VERSION)
    assert message == f"adoption_condition_unsatisfied:{COND_ID}:clearance.condition"


def test_a_malformed_policy_is_indeterminate_never_adoptable(monkeypatch) -> None:
    monkeypatch.setattr(gate, "POLICY_PATH", REPO / "does" / "not" / "exist.json")
    with pytest.raises(gate.GateIndeterminate):
        adoption.require_adoptable(FACILITY, VERSION)


def test_cli_exit_codes(world: World, capsys, monkeypatch) -> None:
    argv = [
        "require-adoptable",
        FACILITY,
        "--version",
        VERSION,
        "--scope",
        "production",
    ]
    assert adoption.main(argv) == 1
    world.commit()
    assert adoption.main(argv) == 0
    monkeypatch.setattr(gate, "POLICY_PATH", REPO / "does" / "not" / "exist.json")
    assert adoption.main(argv) == 2
    capsys.readouterr()


def test_no_scope_other_than_production_is_accepted_by_the_cli() -> None:
    with pytest.raises(SystemExit):
        adoption.build_parser().parse_args(
            ["require-adoptable", FACILITY, "--version", VERSION, "--scope", "x"]
        )
    with pytest.raises(SystemExit):
        adoption.build_parser().parse_args(
            ["require-adoptable", FACILITY, "--version", VERSION]
        )


def test_the_production_audience_names_no_single_operation() -> None:
    assert (
        adoption.production_audience(FACILITY) == f"{FACILITY}/{gate.CONDITION_BLOCKS}"
    )
    source = (REPO / "scripts" / "foundation_adoption_gate.py").read_text()
    assert "--scope production_governed_deploy" not in source
    assert adoption.SCOPE_PRODUCTION == "production"


# ── the candidate signer's scope ───────────────────────────────────────────


def _signer():
    spec = importlib.util.spec_from_file_location("_signer_scope", SIGNER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SIGNER = _signer()
ENROLMENT_UUID = "11111111-2222-4333-8444-555555555555"
OBSERVATION = "66666666-7777-4888-8999-aaaaaaaaaaaa"
#: Computed from the signer BEFORE the --scope flag existed, over the fixed
#: inputs below. The default path must keep producing exactly these bytes.
GOLDEN_DEFAULT_SIGNED_BYTES_SHA256 = (
    "b0c1becac4d8cf83810bb24ef86228c4a88762b821d2210a6db2ca1ad75a6f0a"
)


def _receipt() -> dict:
    return {
        "schema": "CandidateArtifact.v1",
        "facility": FACILITY,
        "version": VERSION,
        "repository": "example/repo",
        "source_sha": "a" * 40,
        "run_id": "11111111",
        "artifact_id": "22222222",
        "sha256": "b" * 64,
    }


def _fixed_unsigned(**extra):
    return SIGNER.render_unsigned_envelope(
        receipt=_receipt(),
        key_id="k1",
        public_key_fingerprint="sha256:" + "d" * 64,
        trust_root_version=ENROLMENT_UUID,
        issued_at=datetime(2026, 11, 28, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2026, 11, 29, 0, 0, 0, tzinfo=UTC),
        observation_id=OBSERVATION,
        **extra,
    )


def test_the_default_scope_signed_bytes_are_byte_identical_to_before() -> None:
    unsigned = _fixed_unsigned()
    assert unsigned.audience == "dotmac-deployment-foundation"
    assert (
        hashlib.sha256(unsigned.signed_bytes()).hexdigest()
        == GOLDEN_DEFAULT_SIGNED_BYTES_SHA256
    )


def test_the_default_scope_flag_is_candidate_and_changes_nothing(tmp_path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(_receipt()))
    outputs = {}
    for label, extra in (("omitted", []), ("explicit", ["--scope", "candidate"])):
        out = tmp_path / f"{label}.json"
        sb = tmp_path / f"{label}.bytes"
        assert (
            SIGNER.main(
                [
                    "render",
                    "--receipt",
                    str(receipt),
                    "--key-id",
                    "k1",
                    "--public-key-fingerprint",
                    "sha256:" + "d" * 64,
                    "--trust-root-version",
                    ENROLMENT_UUID,
                    "--issued-at",
                    "2026-11-28T00:00:00Z",
                    "--expires-at",
                    "2026-11-29T00:00:00Z",
                    "--observation-id",
                    OBSERVATION,
                    "--out-envelope",
                    str(out),
                    "--out-signed-bytes",
                    str(sb),
                    *extra,
                ]
            )
            == 0
        )
        outputs[label] = (out.read_bytes(), sb.read_bytes())
    assert outputs["omitted"] == outputs["explicit"]
    assert (
        hashlib.sha256(outputs["omitted"][1]).hexdigest()
        == GOLDEN_DEFAULT_SIGNED_BYTES_SHA256
    )


def test_the_default_scope_never_imports_the_adoption_gate() -> None:
    sys.modules.pop("foundation_adoption_gate", None)
    SIGNER.require_scope_cleared("candidate", FACILITY, VERSION)
    assert (
        SIGNER.audience_for_scope("candidate", FACILITY)
        == "dotmac-deployment-foundation"
    )
    assert "foundation_adoption_gate" not in sys.modules
    sys.modules["foundation_adoption_gate"] = adoption


def test_scopes_are_exactly_candidate_and_production() -> None:
    assert SIGNER.SCOPES == ("candidate", "production")
    for command in ("render", "assemble", "verify"):
        parser = SIGNER.build_parser()
        args = {
            "render": [
                "--receipt",
                "r",
                "--key-id",
                "k",
                "--public-key-fingerprint",
                "f",
                "--trust-root-version",
                "t",
                "--issued-at",
                "i",
                "--expires-at",
                "e",
                "--out-envelope",
                "o",
                "--out-signed-bytes",
                "b",
            ],
            "assemble": ["--unsigned", "u", "--signature-file", "s", "--out", "o"],
            "verify": [
                "--envelope",
                "e",
                "--receipt",
                "r",
                "--public-key-base64",
                "p",
                "--key-id",
                "k",
                "--public-key-fingerprint",
                "f",
                "--trust-root-version",
                "t",
            ],
        }[command]
        assert parser.parse_args([command, *args]).scope == "candidate"
        with pytest.raises(SystemExit):
            parser.parse_args([command, *args, "--scope", "production_governed_deploy"])


def test_production_render_is_refused_without_a_clearance(
    world: World, tmp_path
) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(_receipt()))
    with pytest.raises(SystemExit) as caught:
        SIGNER.main(
            [
                "render",
                "--receipt",
                str(receipt),
                "--key-id",
                "k1",
                "--public-key-fingerprint",
                "sha256:" + "d" * 64,
                "--trust-root-version",
                ENROLMENT_UUID,
                "--issued-at",
                "2026-11-28T00:00:00Z",
                "--expires-at",
                "2026-11-29T00:00:00Z",
                "--out-envelope",
                str(tmp_path / "u.json"),
                "--out-signed-bytes",
                str(tmp_path / "b"),
                "--scope",
                "production",
            ]
        )
    assert "adoption_condition_unsatisfied" in str(caught.value)
    assert not (tmp_path / "u.json").exists()
    assert not (tmp_path / "b").exists()


def _ceremony(tmp_path, key, scope):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(_receipt()))
    raw = key.public_key().public_bytes_raw()
    fingerprint = SIGNER.fingerprint_of(raw)
    now = datetime.now(UTC)
    base = [
        "--receipt",
        str(receipt),
        "--key-id",
        "k1",
        "--public-key-fingerprint",
        fingerprint,
        "--trust-root-version",
        ENROLMENT_UUID,
    ]
    unsigned, sbytes = tmp_path / f"u-{scope}.json", tmp_path / f"b-{scope}"
    rc = SIGNER.main(
        [
            "render",
            *base,
            "--issued-at",
            SIGNER._canonical_instant(now - timedelta(minutes=1)),
            "--expires-at",
            SIGNER._canonical_instant(now + timedelta(hours=1)),
            "--out-envelope",
            str(unsigned),
            "--out-signed-bytes",
            str(sbytes),
            "--scope",
            scope,
        ]
    )
    assert rc == 0
    sig = tmp_path / f"s-{scope}"
    sig.write_text(base64.b64encode(key.sign(sbytes.read_bytes())).decode())
    final = tmp_path / f"f-{scope}.json"
    assert (
        SIGNER.main(
            [
                "assemble",
                "--unsigned",
                str(unsigned),
                "--signature-file",
                str(sig),
                "--out",
                str(final),
                "--scope",
                scope,
            ]
        )
        == 0
    )
    verify = [
        "verify",
        "--envelope",
        str(final),
        "--receipt",
        str(receipt),
        "--public-key-base64",
        base64.b64encode(raw).decode(),
        "--key-id",
        "k1",
        "--public-key-fingerprint",
        fingerprint,
        "--trust-root-version",
        ENROLMENT_UUID,
    ]
    return final, verify


def test_production_scope_round_trips_only_after_clearance(
    world: World, tmp_path
) -> None:
    world.commit()
    key = Ed25519PrivateKey.generate()
    final, verify = _ceremony(tmp_path, key, "production")
    document = json.loads(final.read_text())
    assert document["audience"] == f"{FACILITY}/{gate.CONDITION_BLOCKS}"
    assert SIGNER.main([*verify, "--scope", "production"]) == 0
    # An envelope for one audience is not accepted under the other scope.
    with pytest.raises(SystemExit):
        SIGNER.main(verify)


def test_the_default_envelope_is_not_accepted_in_production_scope(
    world: World, tmp_path
) -> None:
    world.commit()
    key = Ed25519PrivateKey.generate()
    final, verify = _ceremony(tmp_path, key, "candidate")
    assert json.loads(final.read_text())["audience"] == "dotmac-deployment-foundation"
    assert SIGNER.main(verify) == 0
    with pytest.raises(SystemExit):
        SIGNER.main([*verify, "--scope", "production"])


def test_production_verify_and_assemble_refuse_without_a_clearance(
    world: World, tmp_path
) -> None:
    world.commit()
    key = Ed25519PrivateKey.generate()
    final, verify = _ceremony(tmp_path, key, "production")
    json_path, _ = adoption.clearance_paths(FACILITY, VERSION, COND_ID)
    json_path.unlink()
    with pytest.raises(SystemExit) as caught:
        SIGNER.main([*verify, "--scope", "production"])
    assert "adoption_condition_unsatisfied" in str(caught.value)
    with pytest.raises(SystemExit) as caught:
        SIGNER.main(
            [
                "assemble",
                "--unsigned",
                str(tmp_path / "u-production.json"),
                "--signature-file",
                str(tmp_path / "s-production"),
                "--out",
                str(tmp_path / "again.json"),
                "--scope",
                "production",
            ]
        )
    assert "adoption_condition_unsatisfied" in str(caught.value)


def test_production_assemble_refuses_an_ordinary_audience_envelope(
    world: World, tmp_path
) -> None:
    world.commit()
    key = Ed25519PrivateKey.generate()
    _ceremony(tmp_path, key, "candidate")
    with pytest.raises(SystemExit) as caught:
        SIGNER.main(
            [
                "assemble",
                "--unsigned",
                str(tmp_path / "u-candidate.json"),
                "--signature-file",
                str(tmp_path / "s-candidate"),
                "--out",
                str(tmp_path / "x.json"),
                "--scope",
                "production",
            ]
        )
    assert "production audience" in str(caught.value)


# ── the workflow ───────────────────────────────────────────────────────────


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_workflow_scope_input_defaults_to_candidate() -> None:
    inputs = _workflow()[True]["workflow_dispatch"]["inputs"]
    scope = inputs["scope"]
    assert scope["default"] == "candidate"
    assert scope["required"] is False
    assert scope["options"] == ["candidate", "production"]
    assert "clearance" not in " ".join(inputs), "clearance is never caller-supplied"


def test_the_production_clearance_step_gates_before_verify_and_only_in_production() -> (
    None
):
    steps = _workflow()["jobs"]["publish"]["steps"]
    names = [step.get("name", step.get("uses", "")) for step in steps]
    gate_index = next(
        i for i, n in enumerate(names) if n.startswith("Require the adoption")
    )
    verify_index = next(i for i, n in enumerate(names) if n.startswith("Verify the"))
    upload_index = next(i for i, n in enumerate(names) if n.startswith("Upload"))
    assert gate_index < verify_index < upload_index
    step = steps[gate_index]
    assert step["if"] == "${{ inputs.scope == 'production' }}"
    assert "continue-on-error" not in step
    assert "foundation_adoption_gate.py require-adoptable" in step["run"]
    assert "--scope production" in step["run"]
    verify = steps[verify_index]
    assert '--scope "${SCOPE}"' in verify["run"]
    assert verify["env"]["SCOPE"] == "${{ inputs.scope }}"
    assert "if" not in verify
