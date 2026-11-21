"""E9 offline orchestrator joins, with synthetic keys only (hosted CI).

Every join refusal has a planted case. No production key, host or address.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import pathlib
import struct
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.engine.run import DeploymentOutcome
from dotmac_deployment_foundation.execution_plan import (
    HostPrestateV1,
    render_execution_plan,
)
from dotmac_deployment_foundation.execution_plan_v2 import render_execution_plan_v2
from dotmac_deployment_foundation.execution_plan_v3 import render_execution_plan_v3
from dotmac_deployment_foundation.policy import build_firewall_plan
from dotmac_deployment_foundation.rehearsal import (
    REQUIRED_ITEMS,
    ExecutionRunBindingV1,
    RequirementResult,
    RequirementStatus,
    build_receipt_v2,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATION_SCHEMA,
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    AttestationEnvelopeV2,
    CandidateAttestationSubjectV2,
    attestation_envelope_digest,
    candidate_subject_digest,
)

from tests.unit.foundation_v3_support import CONTROLLER, WHEEL, grant_for_plan
from tests.unit.lane3_e10_support import MACHINE_ID, SSH_FINGERPRINT
from tests.unit.test_lane3_exposure_evidence import _observation

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_cp6_export as cp6  # noqa: E402
import lane3_host_admission as ha  # noqa: E402
import lane3_offline_orchestrator as orchestrator  # noqa: E402
from lane3_evidence_finalization import _archive  # noqa: E402
from lane3_exposure_evidence import serialize_exposure_calls  # noqa: E402
from lane3_exposure_recording import ExposureCallRecord  # noqa: E402
from lane3_offline_result import (  # noqa: E402
    NAMESPACE,
    PRINCIPAL,
    parse_policy,
    parse_result,
)
from lane3_plan_v3 import exposure_reconciliations_for_spec  # noqa: E402

REVISION = "c" * 40
TARGET_ID = "3f2b8c1e-7a44-4d0b-9c55-0e1d2a3b4c5d"
DISPATCH_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
HOST_ID = "fleet-host-1"
ENROL = "6f1c2f4e-52a8-4d0e-9a1b-2f0c7e6d9b11"
CAND_ENROL = "0b6c7d0e-3a41-4f7e-8d2e-6a5a0e6a7c22"
RUN = ExecutionRunBindingV1(repository_id=1397614141, run_id=4242, run_attempt=1)
EXPECTED_PACKAGE = "dotmac-deployment-foundation"
T = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)
FAKE_CIPHERTEXT = b"age-encryption.org/v1\n" + b"synthetic ciphertext"


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def ts(moment: datetime) -> str:
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    if moment.microsecond:
        text += f".{moment.microsecond:06d}"
    return text + "Z"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def canonical(document: Any) -> bytes:
    return (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def public_raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes_raw()


def sign_envelope(
    key: Ed25519PrivateKey, **fields: Any
) -> tuple[AttestationEnvelopeV2, bytes]:
    unsigned = AttestationEnvelopeV2(
        schema=ATTESTATION_SCHEMA,
        algorithm="ed25519",
        public_key_fingerprint=digest(public_raw(key)),
        signature="unsigned",
        **fields,
    )
    signature = base64.b64encode(key.sign(unsigned.signed_bytes())).decode()
    envelope = dataclasses.replace(unsigned, signature=signature)
    return envelope, json.dumps(envelope.canonical_document()).encode()


def ssh_public_blob(key: Ed25519PrivateKey) -> str:
    raw = public_raw(key)
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + raw
    return base64.b64encode(blob).decode()


def make_policy(now: datetime):
    after, before = now - timedelta(days=1), now + timedelta(days=1)
    options = (
        f'namespaces="{NAMESPACE}",valid-after="{after:%Y%m%d%H%M%SZ}",'
        f'valid-before="{before:%Y%m%d%H%M%SZ}"'
    )
    signers = (
        f"{PRINCIPAL} {options} ssh-ed25519 "
        f"{ssh_public_blob(Ed25519PrivateKey.generate())}\n"
    ).encode()
    document = {
        "schema": "lane3.offline-verifier-policy.v1",
        "state": "enrolled",
        "namespace": NAMESPACE,
        "principal": PRINCIPAL,
        "audience": "dotmac.foundation.publication",
        "ssh_keygen_sha256": digest(b"tool"),
        "verifier_source_revision": "b" * 40,
        "verifier_profile_digest": digest(b"profile"),
        "allowed_signers_digest": digest(signers),
        "revoked_keys_digest": digest(b""),
        "max_result_age_seconds": 86400,
        "future_clock_tolerance_seconds": 300,
    }
    return parse_policy(canonical(document), signers, b"")


class World:
    """One consistent rehearsal; tests mutate one piece and call `run()`."""

    def __init__(self) -> None:
        self.cp_key = Ed25519PrivateKey.generate()
        self.cand_key = Ed25519PrivateKey.generate()
        self.inst_key = Ed25519PrivateKey.generate()
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.policy = make_policy(self.now)

        spec = ProductDeploymentSpec.loads(
            (REPO / "scripts/exposure-rehearsal/product.toml").read_text("utf-8"),
            source="<orchestrator-fixture>",
        )
        self.spec = spec
        descriptor_digest = str(spec.to_canonical_document().sha256_digest())
        base = render_execution_plan(
            spec,
            build_plan(spec),
            target="rehearsal-target",
            operation="deploy",
            descriptor_digest=descriptor_digest,
            prestate=HostPrestateV1.first_deploy(),
            application_profile_digest="",
        )
        self.inst_fp = digest(public_raw(self.inst_key))
        self.plan = render_execution_plan_v3(
            render_execution_plan_v2(
                base, exposure_reconciliations=exposure_reconciliations_for_spec(spec)
            ),
            candidate_wheel_digest=WHEEL,
            target_id=TARGET_ID,
            controller_ssh_fingerprint=CONTROLLER,
            host_id=HOST_ID,
            host_incarnation=self.inst_fp,
            host_enrolment_ref=ENROL,
        )
        records = [ExposureCallRecord(0, "observe", observation=_observation())]
        for item in self.plan.exposure_reconciliations:
            records.append(
                ExposureCallRecord(
                    len(records),
                    "replace_rules",
                    family=item.family,
                    chain=item.chain,
                    rules=tuple(
                        rule
                        for rule in build_firewall_plan(spec)
                        if rule.family == item.family
                    ),
                )
            )
        self.archive = _archive(
            b'{"probe":"synthetic"}', serialize_exposure_calls(records)
        )
        self.ciphertext = FAKE_CIPHERTEXT
        self.decrypt = lambda _ciphertext: self.archive

        grant = grant_for_plan(
            spec, self.plan, receipt_overrides={"dispatch_id": DISPATCH_ID}
        )
        outcome = DeploymentOutcome(
            plan=build_plan(spec),
            succeeded=True,
            execution_plan_digest=self.plan.digest(),
            descriptor_digest=descriptor_digest,
            execution_sequence=grant.execution_sequence,
            attempt_no=grant.attempt_no,
        )
        self.receipt = build_receipt_v2(
            foundation_revision=REVISION,
            foundation_artifact_digest=WHEEL,
            descriptor_digest=descriptor_digest,
            execution_plan=self.plan,
            grant=grant,
            execution_outcome=outcome,
            fixture_digest="sha256:" + "d" * 64,
            evidence_bundle_digest=digest(self.ciphertext),
            controller_identity=CONTROLLER,
            lease_id="lease-1",
            probe_vantage_ref="inside-vantage@7",
            execution_run=RUN,
            started_at="2026-11-19T10:02:00+00:00",
            finished_at="2026-11-19T10:09:00+00:00",
            results=[
                RequirementResult(
                    code=item.code,
                    status=RequirementStatus.EXECUTED_PASSED,
                    detail=f"{item.title} - measured",
                    evidence=(f"bundle:{item.code}",),
                )
                for item in REQUIRED_ITEMS
            ],
        )
        self.dispatch_id = self.receipt.content["control_dispatch"]["dispatch_id"]
        coordinate = self.receipt.content["control_dispatch"]

        subject = {
            "package": EXPECTED_PACKAGE,
            "version": "0.4.0a2",
            "wheel_sha256": WHEEL,
            "source_revision": REVISION,
            "repository": "example-org/example-repo",
            "run_id": "1",
            "artifact_id": "2",
        }
        candidate, self.candidate_bytes = sign_envelope(
            self.cand_key,
            purpose=CANDIDATE_ATTESTATION_PURPOSE,
            issuer="urn:example:candidate-ceremony",
            key_id="cand-key",
            custody_domain="candidate_release_signer",
            trust_root_version=CAND_ENROL,
            issued_at="2026-11-18T00:00:00Z",
            expires_at="2026-12-31T00:00:00Z",
            audience=orchestrator.CANDIDATE_AUDIENCE,
            observation_id="candidate-1",
            subject=subject,
        )
        installed, self.installed_bytes = sign_envelope(
            self.inst_key,
            purpose=INSTALLED_OBSERVATION_PURPOSE,
            issuer="urn:example:installed-ceremony",
            key_id="inst-key",
            custody_domain="host_attester",
            trust_root_version=ENROL,
            issued_at="2026-11-19T09:59:00Z",
            expires_at="2026-11-19T12:00:00Z",
            audience=HOST_ID,
            observation_id=self.dispatch_id,
            subject={
                "host_identity": HOST_ID,
                "package": EXPECTED_PACKAGE,
                "version": "0.4.0a2",
                "wheel_sha256": WHEEL,
                "candidate_subject_digest": str(
                    candidate_subject_digest(
                        CandidateAttestationSubjectV2.from_mapping(subject)
                    )
                ),
            },
        )
        self.installed_digest = str(attestation_envelope_digest(installed))
        self.candidate_digest = str(attestation_envelope_digest(candidate))
        # S2's own document shape; the presentation is signed with a synthetic
        # presentation key whose public half is the PINNED input of the run.
        self.pres_key = Ed25519PrivateKey.generate()
        self.pres_pub = b64url(public_raw(self.pres_key))
        # Presentation live window is [store_time - 2s, store_time + 298s]; the
        # default ledger time is T + 5m.
        self.store_time = T + timedelta(minutes=5)
        self.admission: dict[str, Any] = {
            "attempt_id": self.dispatch_id,
            "presentation": None,
            "candidate": candidate.canonical_document(),
            "installed": installed.canonical_document(),
        }
        self.resign_admission()

        self.cp_identity = orchestrator.PinnedIdentity(
            "cp-dispatch-key", "ed25519", b64url(public_raw(self.cp_key))
        )
        root_common = {
            "algorithm": "ed25519",
            "not_before": "2026-11-01T00:00:00Z",
            "not_after": "2027-01-01T00:00:00Z",
            "enrolled_at": "2026-11-18T00:00:00Z",
        }
        self.export: dict[str, Any] = {
            "schema": cp6.EXPORT_SCHEMA,
            "dispatch_id": self.dispatch_id,
            "host_id": HOST_ID,
            "control": {
                "dispatch": self._dispatch_block(coordinate),
                "ledger": {
                    "key": self.dispatch_id,
                    "scope": cp6.CONSUME_SCOPE,
                    "status": cp6.CONSUME_STATUS,
                    "fingerprint": "0" * 64,
                    "created_at": ts(T + timedelta(minutes=5, microseconds=123456)),
                    "execution_sequence": coordinate["execution_sequence"],
                    "attempt_no": coordinate["attempt_no"],
                },
            },
            "cp_lane3": {
                "e10_receipt_digest": "sha256:" + "0" * 64,
                "e10_receipt": {
                    "receipt_ref": "e10-0001",
                    "vm_alias": "vm-a",
                    "host_id": HOST_ID,
                    "machine_id": MACHINE_ID,
                    "ssh_host_key_fingerprint": SSH_FINGERPRINT,
                    "vm_slot": None,
                    "incarnation_public_key_b64url": b64url(public_raw(self.inst_key)),
                    "incarnation_fingerprint": self.inst_fp,
                    "enrolment_uuid": ENROL,
                    "key_custody_pointer": "bao://transit-a2-hi-vm-a-1/keys/incarnation",
                    "recorded_by": "ceremony",
                    "recorded_at": ts(T - timedelta(minutes=30)),
                    "seq": 1,
                },
                "destruction": {
                    "receipt_ref": "e10-0001",
                    "destroyed_at": ts(T),
                    "route": "transit_mount_disabled",
                    "dispatch_id": self.dispatch_id,
                    "installed_attestation_digest": self.installed_digest,
                    "evidence_digest": digest(b"evidence"),
                    "recorded_by": "ceremony",
                },
                "lease": {
                    "lease_id": "lease-row-1",
                    "host_id": HOST_ID,
                    "authorization_run": "lease-1",
                    "e10_receipt_ref": "e10-0001",
                    "acquired_at": ts(T + timedelta(minutes=1)),
                    "expires_at": ts(T + timedelta(minutes=61)),
                },
                "release": {
                    "lease_id": "lease-row-1",
                    "released_at": ts(T + timedelta(minutes=10)),
                    "host_mutated": True,
                },
                "candidate_root": {
                    **root_common,
                    "enrolment_uuid": CAND_ENROL,
                    "custody_domain": "candidate_release_signer",
                    "issuer": "urn:example:candidate-ceremony",
                    "key_id": "cand-key",
                    "purpose": CANDIDATE_ATTESTATION_PURPOSE,
                    "public_key_b64url": b64url(public_raw(self.cand_key)),
                    "revoked_at": None,
                },
                "installed_root": {
                    **root_common,
                    "enrolment_uuid": ENROL,
                    "custody_domain": "host_attester",
                    "issuer": "urn:example:installed-ceremony",
                    "key_id": "inst-key",
                    "purpose": INSTALLED_OBSERVATION_PURPOSE,
                    "public_key_b64url": b64url(public_raw(self.inst_key)),
                    "revoked_at": ts(T + timedelta(minutes=11)),
                },
            },
        }
        self.kwargs: dict[str, Any] = {}
        self.cp["e10_receipt_digest"] = cp6.receipt_digest(self.cp["e10_receipt"])
        self.refresh_ledger()

    def _dispatch_block(self, coordinate: dict[str, Any]) -> dict[str, Any]:
        statement = {
            "schema": "dotmac.deployment-dispatch",
            "version": 1,
            "purpose": cp6.DISPATCH_PURPOSE,
            "dispatch_id": self.dispatch_id,
            "authorization_id": "authorization-1",
            "authorization_plan_id": "plan-1",
            "authorization_control_version": "0.1.0a17",
            "authorization_key_id": "authorization-key",
            "authorization_public_key_fingerprint": "sha256:" + "3" * 64,
            "authorization_envelope_digest": "sha256:" + "1" * 64,
            "execution_sequence": coordinate["execution_sequence"],
            "attempt_no": coordinate["attempt_no"],
            "rollout_ref": "rollout-1",
            "target_id": self.plan.target_id,
            "target_ref": self.plan.target,
            "product_code": "product-1",
            "environment": "rehearsal",
            "operation": "deploy",
            "release_ref": "release-1",
            "authorized_images": [],
            "plan_digest": "sha256:" + "5" * 64,
            "descriptor_digest": self.plan.descriptor_digest,
            "execution_plan_digest": self.plan.digest(),
            "issued_at": "2026-11-19T09:58:00Z",
            "control_version": "0.1.0a17",
            "key_id": "cp-dispatch-key",
            "algorithm": "ed25519",
            "public_key_fingerprint": self.cp_identity.fingerprint,
        }
        block = {"statement": statement, "signature": "", "envelope_digest": ""}
        self.resign(block)
        return block

    def resign(self, block: dict[str, Any] | None = None, **changes: Any) -> None:
        """Re-sign the exported dispatch statement after `changes`."""
        block = block if block is not None else self.export["control"]["dispatch"]
        block["statement"].update(changes)
        block["signature"] = b64url(
            self.cp_key.sign(cp6.canonical_json(block["statement"]))
        )
        block["envelope_digest"] = cp6.dispatch_envelope_digest(
            block["statement"], block["signature"]
        )

    def resign_admission(self) -> None:
        """Re-sign the presentation over the CURRENT attempt id and envelopes."""
        self.admission["presentation"] = ha.build_presentation(
            self.pres_key,
            key_id="presentation-key-1",
            attempt_id=self.admission["attempt_id"],
            candidate=AttestationEnvelopeV2.from_mapping(self.admission["candidate"]),
            installed=AttestationEnvelopeV2.from_mapping(self.admission["installed"]),
            store_time=self.store_time,
            presentation_id="presentation-1",
        )

    def admission_facts(self):
        return orchestrator.read_host_admission(
            json.dumps(self.admission).encode(),
            presentation_public_key_b64url=self.pres_pub,
        )

    def variant(self, name: str) -> dict[str, Any]:
        """A different, validly signed envelope in the `name` slot."""
        old = AttestationEnvelopeV2.from_mapping(self.admission[name])
        key = self.cand_key if name == "candidate" else self.inst_key
        envelope, _raw = sign_envelope(
            key,
            purpose=old.purpose,
            issuer=old.issuer,
            key_id=old.key_id,
            custody_domain=old.custody_domain,
            trust_root_version=old.trust_root_version,
            issued_at=old.issued_at,
            expires_at=old.expires_at,
            audience=old.audience,
            observation_id=old.observation_id + "-other",
            subject=old.subject,
        )
        return envelope.canonical_document()

    def refresh_ledger(self) -> None:
        """Recompute the ledger fingerprint from the CURRENT admission digests."""
        self.resign_admission()
        facts = self.admission_facts()
        self.export["control"]["ledger"]["fingerprint"] = cp6.ledger_fingerprint(
            dispatch_envelope_digest=self.export["control"]["dispatch"][
                "envelope_digest"
            ],
            candidate_attestation_envelope_digest=facts.candidate_attestation_digest,
            installed_attestation_envelope_digest=facts.installed_attestation_digest,
        )

    # -- accessors for planted mutations
    @property
    def cp(self) -> dict[str, Any]:
        return self.export["cp_lane3"]

    def verify_values(self, *, refresh_digest: bool = True) -> dict[str, Any]:
        row = self.cp["e10_receipt"]
        if refresh_digest:
            self.cp["e10_receipt_digest"] = cp6.receipt_digest(row)
        values: dict[str, Any] = {
            "receipt_bytes": self.receipt.canonical_bytes(),
            "ciphertext": self.ciphertext,
            "decrypt": self.decrypt,
            "run": RUN,
            "release_revision": REVISION,
            "candidate_artifact_digest": WHEEL,
            "execution_plan": self.plan,
            "descriptor": self.spec,
            "export_bytes": canonical(self.export),
            "expected_e10_receipt_digest": cp6.receipt_digest(row),
            "pinned_cp_identities": [self.cp_identity],
            "pinned_candidate_root_fingerprint": digest(public_raw(self.cand_key)),
            "candidate_envelope_bytes": self.candidate_bytes,
            "installed_envelope_bytes": self.installed_bytes,
            "host_admission_bytes": json.dumps(self.admission).encode(),
            "presentation_public_key_b64url": self.pres_pub,
            "policy": self.policy,
            "now": self.now,
        }
        values.update(self.kwargs)
        return values

    def run(self, *, refresh_digest: bool = True, **overrides: Any):
        values = self.verify_values(refresh_digest=refresh_digest)
        values.update(overrides)
        return orchestrator.verify_offline(**values)


@pytest.fixture
def world() -> World:
    return World()


def refused(world: World, code: str, **overrides: Any) -> None:
    with pytest.raises(orchestrator.JoinRefused) as caught:
        world.run(**overrides)
    assert str(caught.value) == f"offline-join-{code}"


# --- the positive path and the emitted result ---------------------------------


def test_every_join_passes_and_emits_a_result_the_consumer_parses(world: World) -> None:
    outcome = world.run()
    parsed = parse_result(outcome.result_bytes)
    assert parsed.coordinates == outcome.coordinates
    assert outcome.coordinates.repository_id == RUN.repository_id
    assert outcome.coordinates.run_id == RUN.run_id
    assert outcome.coordinates.run_attempt == RUN.run_attempt
    assert outcome.coordinates.receipt_digest == world.receipt.sha256_digest()
    assert (
        outcome.coordinates.ciphertext_digest
        == world.receipt.content["evidence_bundle_digest"]
    )
    assert outcome.coordinates.candidate_artifact_digest == WHEEL
    assert outcome.coordinates.release_revision == REVISION
    assert parsed.policy_digest == world.policy.sha256_digest()
    assert outcome.result_bytes.endswith(b"\n")
    assert set(outcome.input_digests) >= {"cp6_export", "receipt", "ciphertext"}


def test_an_unreleased_lease_uses_its_expiry_as_the_bracket_end(world: World) -> None:
    world.cp["release"] = None
    world.export["control"]["ledger"]["created_at"] = ts(T + timedelta(minutes=50))
    world.store_time = T + timedelta(minutes=50)
    world.resign_admission()
    world.cp["installed_root"]["revoked_at"] = ts(T + timedelta(minutes=55))
    world.run()


def test_the_result_is_unsigned_and_unenrolled_policy_refuses(world: World) -> None:
    unenrolled = parse_policy(
        canonical(
            {
                "schema": "lane3.offline-verifier-policy.v1",
                "state": "unenrolled",
                "namespace": NAMESPACE,
                "principal": PRINCIPAL,
                "audience": "dotmac.foundation.publication",
                "ssh_keygen_sha256": None,
                "verifier_source_revision": None,
                "verifier_profile_digest": None,
                "allowed_signers_digest": digest(b""),
                "revoked_keys_digest": digest(b""),
                "max_result_age_seconds": 86400,
                "future_clock_tolerance_seconds": 300,
            }
        ),
        b"",
        b"",
    )
    refused(world, "policy-unenrolled", policy=unenrolled)


# --- receipt and ciphertext ---------------------------------------------------


def test_wrong_run_is_refused(world: World) -> None:
    refused(
        world,
        "receipt-run-mismatch",
        run=ExecutionRunBindingV1(RUN.repository_id, RUN.run_id + 1, 1),
    )


def test_wrong_revision_is_refused(world: World) -> None:
    refused(world, "receipt-not-published", release_revision="e" * 40)


def test_wrong_artifact_digest_is_refused(world: World) -> None:
    refused(
        world,
        "receipt-artifact-mismatch",
        candidate_artifact_digest="sha256:" + "1" * 64,
    )


def test_a_receipt_that_is_not_v2_is_refused(world: World) -> None:
    refused(world, "receipt-invalid", receipt_bytes=b'{"schema":"nope"}')


def test_ciphertext_digest_mismatch_is_refused_before_decrypt(world: World) -> None:
    called = []
    refused(
        world,
        "ciphertext-digest-mismatch",
        ciphertext=FAKE_CIPHERTEXT + b"x",
        decrypt=lambda c: called.append(c) or world.archive,
    )
    assert called == []


def test_a_plaintext_ciphertext_is_refused(world: World) -> None:
    refused(world, "ciphertext-invalid", ciphertext=b"plain")


def test_a_failing_decryptor_is_refused(world: World) -> None:
    def boom(_c: bytes) -> bytes:
        raise RuntimeError("private text must not surface")

    refused(world, "decrypt-failed", decrypt=boom)


def test_a_tampered_archive_fails_the_bundle_join(world: World) -> None:
    refused(world, "bundle-inconsistent", decrypt=lambda _c: world.archive[:-1] + b"x")


def test_a_wrong_plan_is_refused(world: World) -> None:
    other = render_execution_plan_v3(
        world.plan.base,
        candidate_wheel_digest=WHEEL,
        target_id="target-2",
        controller_ssh_fingerprint=CONTROLLER,
        host_id=HOST_ID,
        host_incarnation=world.inst_fp,
        host_enrolment_ref=ENROL,
    )
    refused(world, "receipt-plan-mismatch", execution_plan=other)


# --- Control join -------------------------------------------------------------


def test_unpinned_dispatch_signer_is_refused(world: World) -> None:
    stranger = orchestrator.PinnedIdentity(
        "cp-dispatch-key", "ed25519", b64url(public_raw(Ed25519PrivateKey.generate()))
    )
    refused(world, "dispatch-signer-unpinned", pinned_cp_identities=[stranger])
    refused(world, "dispatch-signer-unpinned", pinned_cp_identities=[])


def test_wrong_dispatch_signer_key_is_refused(world: World) -> None:
    # The statement names the pinned identity but another key signed it.
    block = world.export["control"]["dispatch"]
    forged = Ed25519PrivateKey.generate().sign(cp6.canonical_json(block["statement"]))
    block["signature"] = b64url(forged)
    refused(world, "dispatch-signature-invalid")


def test_tampered_signed_dispatch_statement_is_refused(world: World) -> None:
    world.export["control"]["dispatch"]["statement"]["operation"] = "rollback"
    refused(world, "dispatch-signature-invalid")


def test_a_standard_base64_signature_is_not_the_wire_form(world: World) -> None:
    block = world.export["control"]["dispatch"]
    raw = base64.urlsafe_b64decode(
        block["signature"] + "=" * (-len(block["signature"]) % 4)
    )
    block["signature"] = base64.b64encode(raw).decode()
    refused(world, "export-invalid")


def test_dispatch_naming_another_id_is_refused(world: World) -> None:
    world.resign(dispatch_id="11111111-2222-4333-8444-555555555555")
    refused(world, "dispatch-id-mismatch")


def test_dispatch_for_another_plan_is_refused(world: World) -> None:
    world.resign(execution_plan_digest="sha256:" + "9" * 64)
    refused(world, "dispatch-plan-mismatch")


@pytest.mark.parametrize("field", ["target_id", "target_ref"])
def test_dispatch_for_another_target_is_refused(world: World, field: str) -> None:
    world.resign(
        **{
            field: "other-target"
            if field == "target_ref"
            else "11111111-2222-4333-8444-555555555555"
        }
    )
    refused(world, "dispatch-target-mismatch")


@pytest.mark.parametrize("field", ["execution_sequence", "attempt_no"])
def test_signed_coordinates_must_match_the_receipt(world: World, field: str) -> None:
    world.resign(**{field: 99})
    refused(world, "dispatch-coordinate-mismatch")


def test_a_dispatch_with_another_purpose_is_refused(world: World) -> None:
    world.resign(purpose="authorization")
    refused(world, "dispatch-purpose")


def test_a_statement_naming_an_unpinned_key_id_is_refused(world: World) -> None:
    world.resign(key_id="other-key")
    refused(world, "dispatch-signer-unpinned")


def test_a_statement_naming_another_key_fingerprint_is_refused(world: World) -> None:
    world.resign(public_key_fingerprint="sha256:" + "7" * 64)
    refused(world, "dispatch-signer-unpinned")


def test_export_for_another_dispatch_is_refused(world: World) -> None:
    world.export["dispatch_id"] = "11111111-2222-4333-8444-555555555555"
    refused(world, "export-dispatch-mismatch")


def test_dispatch_envelope_digest_mismatch_is_refused(world: World) -> None:
    world.export["control"]["dispatch"]["envelope_digest"] = digest(b"other")
    refused(world, "dispatch-digest-mismatch")


def test_ledger_key_not_the_dispatch_id_is_refused(world: World) -> None:
    world.export["control"]["ledger"]["key"] = "dispatch-other"
    refused(world, "ledger-key-mismatch")


def test_ledger_coordinates_must_match_the_signed_statement(world: World) -> None:
    world.export["control"]["ledger"]["attempt_no"] = 9
    refused(world, "ledger-coordinate-mismatch")


@pytest.mark.parametrize(
    ("field", "value"), [("scope", "other.scope"), ("status", "failed")]
)
def test_ledger_row_scope_and_status_are_joined(
    world: World, field: str, value: str
) -> None:
    world.export["control"]["ledger"][field] = value
    refused(world, "ledger-row-mismatch")


def test_ledger_fingerprint_is_joined_to_the_three_digests(world: World) -> None:
    world.export["control"]["ledger"]["fingerprint"] = "1" * 64
    refused(world, "ledger-fingerprint-mismatch")


def test_ledger_fingerprint_is_bare_hex(world: World) -> None:
    world.export["control"]["ledger"]["fingerprint"] = "sha256:" + "1" * 64
    refused(world, "export-invalid")


def test_the_ledger_fingerprint_function_matches_control_a17() -> None:
    # Control `compute_host_admission_consumption_fingerprint`, spelled out.
    payload = {
        "schema": "dotmac.control.host-admission-consumption",
        "version": 1,
        "dispatch_envelope_digest": "sha256:" + "a" * 64,
        "candidate_attestation_envelope_digest": "sha256:" + "b" * 64,
        "installed_attestation_envelope_digest": "sha256:" + "c" * 64,
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert (
        cp6.ledger_fingerprint(
            dispatch_envelope_digest="sha256:" + "a" * 64,
            candidate_attestation_envelope_digest="sha256:" + "b" * 64,
            installed_attestation_envelope_digest="sha256:" + "c" * 64,
        )
        == expected
    )


@pytest.mark.parametrize(
    "created",
    [
        T + timedelta(minutes=1) - timedelta(seconds=1),  # before acquisition
        T + timedelta(minutes=10, seconds=1),  # after release
    ],
)
def test_ledger_time_outside_the_bracket_is_refused(world: World, created) -> None:
    world.export["control"]["ledger"]["created_at"] = ts(created)
    refused(world, "ledger-outside-bracket")


@pytest.mark.parametrize("minutes", [61, 62])
def test_expiry_end_of_the_bracket_is_strict(world: World, minutes: int) -> None:
    world.cp["release"] = None
    world.export["control"]["ledger"]["created_at"] = ts(T + timedelta(minutes=minutes))
    refused(world, "ledger-outside-bracket")


def test_a_release_after_expiry_is_refused(world: World) -> None:
    world.cp["release"]["released_at"] = ts(T + timedelta(minutes=62))
    refused(world, "release-after-expiry")


def test_bracket_bounds_are_inclusive(world: World) -> None:
    world.export["control"]["ledger"]["created_at"] = ts(T + timedelta(minutes=1))
    world.store_time = T + timedelta(minutes=1)
    world.resign_admission()
    world.cp["destruction"]["destroyed_at"] = ts(T + timedelta(minutes=1))
    world.run()


def test_lease_for_another_authorization_run_is_refused(world: World) -> None:
    world.cp["lease"]["authorization_run"] = "lease-other"
    refused(world, "lease-mismatch")


def test_release_for_another_lease_row_is_refused(world: World) -> None:
    world.cp["release"]["lease_id"] = "lease-row-other"
    refused(world, "lease-mismatch")


def test_host_admission_attempt_must_be_the_dispatch_id(world: World) -> None:
    world.admission["attempt_id"] = "11111111-2222-4333-8444-555555555555"
    world.resign_admission()
    refused(world, "host-admission-attempt-mismatch")


def test_a_release_that_did_not_mutate_is_refused(world: World) -> None:
    world.cp["release"]["host_mutated"] = False
    refused(world, "release-not-mutated")


# --- E10 join -----------------------------------------------------------------


def test_destruction_after_acquire_is_refused(world: World) -> None:
    world.cp["destruction"]["destroyed_at"] = ts(T + timedelta(minutes=2))
    refused(world, "e10-destroyed-after-acquire")


def test_acquire_after_ledger_is_refused_by_the_e10_join_directly() -> None:
    # The bracket refuses first through `verify_offline`, so exercise the E10
    # join's own ordering check on its own.
    world = World()
    world.cp["lease"]["acquired_at"] = ts(T + timedelta(minutes=6))
    export = cp6.parse_cp6_export(canonical(world.export))
    with pytest.raises(orchestrator.JoinRefused) as caught:
        orchestrator._join_e10(
            export,
            plan=world.plan,
            expected_receipt_digest=cp6.receipt_digest(world.cp["e10_receipt"]),
            admission=world.admission_facts(),
        )
    assert str(caught.value) == "offline-join-e10-acquired-after-ledger"
    refused(world, "ledger-outside-bracket")


def test_enrolment_after_destruction_is_refused(world: World) -> None:
    world.cp["installed_root"]["enrolled_at"] = ts(T + timedelta(minutes=1))
    refused(world, "e10-enrolled-after-destruction")


def test_installed_root_with_the_wrong_custody_domain_is_refused(world: World) -> None:
    world.cp["installed_root"]["custody_domain"] = "candidate_release_signer"
    refused(world, "e10-root-mismatch")


def test_installed_digest_mismatch_with_host_admission_is_refused(world: World) -> None:
    world.admission["installed"] = world.variant("installed")
    world.refresh_ledger()
    refused(world, "e10-installed-digest-mismatch")


def test_destruction_digest_not_the_admission_digest_is_refused(world: World) -> None:
    world.cp["destruction"]["installed_attestation_digest"] = digest(b"other")
    refused(world, "e10-installed-digest-mismatch")


def test_root_not_revoked_is_refused(world: World) -> None:
    world.cp["installed_root"]["revoked_at"] = None
    refused(world, "e10-root-not-revoked")


@pytest.mark.parametrize("offset", [timedelta(minutes=-1), timedelta(0)])
def test_root_revoked_before_or_at_the_ledger_is_refused(world: World, offset) -> None:
    ledger = datetime.fromisoformat(
        world.export["control"]["ledger"]["created_at"][:-1] + "+00:00"
    )
    world.cp["installed_root"]["revoked_at"] = ts(ledger + offset)
    refused(world, "e10-root-revoked-before-ledger")


def test_e10_receipt_digest_mismatch_is_refused(world: World) -> None:
    refused(
        world, "e10-receipt-digest-mismatch", expected_e10_receipt_digest=digest(b"x")
    )


def test_exported_receipt_digest_must_match_the_recomputation(world: World) -> None:
    world.cp["e10_receipt"]["vm_alias"] = "vm-b"
    refused(world, "e10-receipt-export-digest-mismatch", refresh_digest=False)


def test_e10_receipt_row_edited_after_the_pin_is_refused(world: World) -> None:
    pinned = cp6.receipt_digest(world.cp["e10_receipt"])
    world.cp["e10_receipt"]["vm_alias"] = "vm-b"
    refused(world, "e10-receipt-digest-mismatch", expected_e10_receipt_digest=pinned)


def test_destruction_for_another_receipt_is_refused(world: World) -> None:
    world.cp["destruction"]["receipt_ref"] = "e10-0002"
    refused(world, "e10-receipt-ref-mismatch")


def test_destruction_for_another_dispatch_is_refused(world: World) -> None:
    world.cp["destruction"]["dispatch_id"] = "dispatch-other"
    refused(world, "e10-destruction-dispatch-mismatch")


def test_a_destroy_only_incarnation_is_refused(world: World) -> None:
    world.cp["destruction"]["dispatch_id"] = None
    world.cp["destruction"]["installed_attestation_digest"] = None
    refused(world, "e10-destruction-dispatch-mismatch")


def test_wrong_destruction_route_is_refused(world: World) -> None:
    world.cp["destruction"]["route"] = "claimed"
    refused(world, "e10-destruction-route")


def test_plan_not_bound_to_the_receipt_incarnation_is_refused(world: World) -> None:
    other = Ed25519PrivateKey.generate()
    row = world.cp["e10_receipt"]
    row["incarnation_public_key_b64url"] = b64url(public_raw(other))
    row["incarnation_fingerprint"] = digest(public_raw(other))
    refused(world, "e10-plan-binding-mismatch")


def test_installed_root_for_another_key_is_refused(world: World) -> None:
    world.cp["installed_root"]["public_key_b64url"] = b64url(
        public_raw(Ed25519PrivateKey.generate())
    )
    refused(world, "e10-root-mismatch")


# --- candidate join -----------------------------------------------------------


def test_candidate_root_not_the_pinned_one_is_refused(world: World) -> None:
    refused(
        world,
        "candidate-root-unpinned",
        pinned_candidate_root_fingerprint=digest(b"another root"),
    )


def test_candidate_envelope_signed_by_another_key_is_refused(world: World) -> None:
    forged, forged_bytes = sign_envelope(
        Ed25519PrivateKey.generate(),
        **{
            name: getattr(
                AttestationEnvelopeV2.from_mapping(json.loads(world.candidate_bytes)),
                name,
            )
            for name in (
                "purpose",
                "issuer",
                "key_id",
                "custody_domain",
                "trust_root_version",
                "issued_at",
                "expires_at",
                "audience",
                "observation_id",
                "subject",
            )
        },
    )
    world.admission["candidate"] = forged.canonical_document()
    world.refresh_ledger()
    refused(world, "pair-key-not-trusted", candidate_envelope_bytes=forged_bytes)


def test_candidate_envelope_not_the_admitted_one_is_refused(world: World) -> None:
    world.admission["candidate"] = world.variant("candidate")
    world.refresh_ledger()
    refused(world, "host-admission-digest-mismatch")


def test_installed_envelope_with_a_corrupted_signature_is_refused(world: World) -> None:
    document = json.loads(world.installed_bytes)
    signature = bytearray(base64.b64decode(document["signature"]))
    signature[0] ^= 1
    document["signature"] = base64.b64encode(bytes(signature)).decode()
    corrupted = json.dumps(document).encode()
    envelope = AttestationEnvelopeV2.from_mapping(document)
    world.admission["installed"] = envelope.canonical_document()
    world.cp["destruction"]["installed_attestation_digest"] = str(
        attestation_envelope_digest(envelope)
    )
    world.refresh_ledger()
    refused(world, "pair-signature-invalid", installed_envelope_bytes=corrupted)


def test_installed_envelope_for_another_dispatch_is_refused(world: World) -> None:
    envelope, raw = sign_envelope(
        world.inst_key,
        purpose=INSTALLED_OBSERVATION_PURPOSE,
        issuer="urn:example:installed-ceremony",
        key_id="inst-key",
        custody_domain="host_attester",
        trust_root_version=ENROL,
        issued_at="2026-11-19T09:59:00Z",
        expires_at="2026-11-19T12:00:00Z",
        audience=HOST_ID,
        observation_id="dispatch-other",
        subject=AttestationEnvelopeV2.from_mapping(
            json.loads(world.installed_bytes)
        ).subject,
    )
    world.admission["installed"] = envelope.canonical_document()
    world.cp["destruction"]["installed_attestation_digest"] = str(
        attestation_envelope_digest(envelope)
    )
    world.refresh_ledger()
    refused(world, "pair-observation-id-mismatch", installed_envelope_bytes=raw)


def _candidate_with(world: World, **subject_changes: Any) -> tuple[str, bytes]:
    old = AttestationEnvelopeV2.from_mapping(json.loads(world.candidate_bytes))
    envelope, raw = sign_envelope(
        world.cand_key,
        purpose=old.purpose,
        issuer=old.issuer,
        key_id=old.key_id,
        custody_domain=old.custody_domain,
        trust_root_version=old.trust_root_version,
        issued_at=old.issued_at,
        expires_at=old.expires_at,
        audience=old.audience,
        observation_id=old.observation_id,
        subject={**old.subject, **subject_changes},
    )
    return envelope.canonical_document(), raw


def test_candidate_wheel_must_be_the_plans_wheel(world: World) -> None:
    admitted, raw = _candidate_with(world, wheel_sha256="sha256:" + "8" * 64)
    world.admission["candidate"] = admitted
    world.refresh_ledger()
    refused(world, "candidate-wheel-mismatch", candidate_envelope_bytes=raw)


def test_a_revoked_candidate_root_is_refused(world: World) -> None:
    world.cp["candidate_root"]["revoked_at"] = ts(T + timedelta(minutes=5))
    refused(world, "candidate-root-revoked")


def test_a_candidate_root_revoked_after_the_ledger_still_verifies(world: World) -> None:
    world.cp["candidate_root"]["revoked_at"] = ts(T + timedelta(minutes=30))
    world.run()


def test_candidate_root_custody_domain_is_pinned(world: World) -> None:
    world.cp["candidate_root"]["custody_domain"] = "host_attester"
    refused(world, "candidate-root-custody")


def test_a_candidate_root_enrolled_after_the_ledger_is_refused(world: World) -> None:
    world.cp["candidate_root"]["enrolled_at"] = ts(T + timedelta(minutes=10))
    refused(world, "candidate-root-enrolled-late")


# --- export and host-admission parsing ---------------------------------------


def test_a_noncanonical_export_is_refused(world: World) -> None:
    refused(
        world,
        "export-invalid",
        export_bytes=json.dumps(world.export, indent=1).encode(),
    )


def test_an_export_with_an_unknown_member_is_refused(world: World) -> None:
    world.export["extra"] = 1
    refused(world, "export-invalid")


def test_an_export_with_duplicate_keys_is_refused(world: World) -> None:
    raw = canonical(world.export).replace(
        b'"host_id":"fleet-host-1"', b'"host_id":"x","host_id":"fleet-host-1"', 1
    )
    refused(world, "export-invalid", export_bytes=raw)


def test_a_float_in_the_export_is_refused(world: World) -> None:
    world.cp["e10_receipt"]["seq"] = 1.5
    with pytest.raises(cp6.Cp6ExportRefused):
        cp6.parse_cp6_export(canonical(world.export))


def test_a_non_utc_time_is_refused(world: World) -> None:
    world.cp["destruction"]["destroyed_at"] = "2026-11-19T10:00:00+01:00"
    refused(world, "export-invalid")


def test_null_together_rule_for_destruction(world: World) -> None:
    world.cp["destruction"]["dispatch_id"] = None
    refused(world, "export-invalid")


def test_host_admission_must_be_closed(world: World) -> None:
    world.admission["extra"] = 1
    refused(world, "host-admission-malformed")


def test_host_admission_envelope_must_be_an_envelope(world: World) -> None:
    world.admission["candidate"] = "sha256:XYZ"
    refused(world, "host-admission-candidate-envelope-malformed")


def test_age_decryptor_refuses_a_pin_mismatch_and_a_missing_tool(tmp_path) -> None:
    tool = tmp_path / "age"
    tool.write_bytes(b"#!/bin/sh\n")
    decrypt = orchestrator.age_decryptor(
        identity_path=tmp_path / "id", age_path=str(tool), age_sha256=digest(b"other")
    )
    with pytest.raises(orchestrator.JoinRefused) as caught:
        decrypt(FAKE_CIPHERTEXT)
    assert str(caught.value) == "offline-join-age-pin-mismatch"
    missing = orchestrator.age_decryptor(
        identity_path=tmp_path / "id",
        age_path=str(tmp_path / "absent"),
        age_sha256=digest(b"x"),
    )
    with pytest.raises(orchestrator.JoinRefused) as caught:
        missing(FAKE_CIPHERTEXT)
    assert str(caught.value) == "offline-join-age-unavailable"


# --- the emitted result is authenticated by the consumer's own code ----------


def test_signed_result_authenticates_with_the_consumers_verifier(tmp_path) -> None:
    ssh_keygen = pathlib.Path("/usr/bin/ssh-keygen")
    if not ssh_keygen.is_file():
        pytest.skip("OpenSSH is required for the signature round trip")
    key = tmp_path / "synthetic"
    subprocess.run(  # noqa: S603 -- CI-only synthetic key
        [str(ssh_keygen), "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        timeout=20,
    )
    public = key.with_suffix(".pub").read_bytes().split()
    world = World()
    after, before = world.now - timedelta(days=1), world.now + timedelta(days=1)
    signers = (
        (
            f'{PRINCIPAL} namespaces="{NAMESPACE}",valid-after="{after:%Y%m%d%H%M%SZ}",'
            f'valid-before="{before:%Y%m%d%H%M%SZ}" '
        ).encode()
        + b" ".join(public[:2])
        + b"\n"
    )
    document = {
        "schema": "lane3.offline-verifier-policy.v1",
        "state": "enrolled",
        "namespace": NAMESPACE,
        "principal": PRINCIPAL,
        "audience": "dotmac.foundation.publication",
        "ssh_keygen_sha256": digest(ssh_keygen.read_bytes()),
        "verifier_source_revision": "b" * 40,
        "verifier_profile_digest": digest(b"profile"),
        "allowed_signers_digest": digest(signers),
        "revoked_keys_digest": digest(b""),
        "max_result_age_seconds": 86400,
        "future_clock_tolerance_seconds": 300,
    }
    policy = parse_policy(canonical(document), signers, b"")
    outcome = world.run(policy=policy)
    signature = orchestrator.sign_result(
        outcome, key_path=key, policy=policy, now=world.now
    )
    assert signature.startswith(b"-----BEGIN SSH SIGNATURE-----")


def test_sign_result_takes_only_a_verification_object(tmp_path) -> None:
    world = World()
    outcome = world.run()
    with pytest.raises(orchestrator.JoinRefused) as caught:
        orchestrator.sign_result(
            outcome.result_bytes,
            key_path=tmp_path / "key",
            policy=world.policy,
            now=world.now,
        )
    assert str(caught.value) == "offline-join-sign-input-invalid"


def test_sign_result_checks_the_tool_before_the_key_is_used(tmp_path) -> None:
    world = World()  # the policy pins a tool digest no real ssh-keygen has
    outcome = world.run()
    absent_key = tmp_path / "never-used"
    with pytest.raises(orchestrator.JoinRefused) as caught:
        orchestrator.sign_result(
            outcome, key_path=absent_key, policy=world.policy, now=world.now
        )
    assert str(caught.value) in {
        "offline-join-sign-tool-refused",
        "offline-join-sign-unavailable",
    }
    assert not absent_key.exists()


def test_sign_result_refuses_a_result_that_no_longer_binds(tmp_path) -> None:
    world = World()
    outcome = world.run()
    stale = world.now + timedelta(days=2)
    with pytest.raises(orchestrator.JoinRefused) as caught:
        orchestrator.sign_result(
            outcome, key_path=tmp_path / "k", policy=world.policy, now=stale
        )
    assert str(caught.value) == "offline-join-sign-self-check"


@pytest.mark.parametrize("field", ["dispatch_id", "target_id"])
def test_statement_ids_must_be_canonical_uuids(world: World, field: str) -> None:
    world.resign(**{field: "not-a-uuid"})
    refused(world, "export-invalid")


def test_top_level_dispatch_id_must_be_a_canonical_uuid(world: World) -> None:
    world.export["dispatch_id"] = world.dispatch_id.upper()
    refused(world, "export-invalid")


def test_host_admission_attempt_id_must_be_a_canonical_uuid(world: World) -> None:
    world.admission["attempt_id"] = "attempt-1"
    refused(world, "host-admission-attempt-id-invalid")


def test_plan_digest_is_compared_exactly(world: World) -> None:
    world.resign(execution_plan_digest=str(world.plan.digest()).removeprefix("sha256:"))
    refused(world, "export-invalid")


# --- S2 reader: presentation signature, pinned key ---------------------------


def test_presentation_signed_by_another_key_is_refused(world: World) -> None:
    world.pres_key = Ed25519PrivateKey.generate()
    world.resign_admission()
    refused(world, "host-admission-presentation-signature-invalid")


def test_a_tampered_presentation_statement_is_refused(world: World) -> None:
    world.admission["presentation"]["statement"]["presentation_id"] = "presentation-2"
    refused(world, "host-admission-presentation-signature-invalid")


def test_presentation_dispatch_must_be_the_attempt_id(world: World) -> None:
    world.admission["attempt_id"] = "11111111-2222-4333-8444-555555555555"
    refused(world, "host-admission-presentation-dispatch-mismatch")


def test_presentation_digests_must_be_those_of_the_carried_envelopes(
    world: World,
) -> None:
    world.admission["installed"] = world.variant("installed")
    refused(world, "host-admission-presentation-digest-mismatch")


def test_the_pinned_presentation_key_must_be_well_formed(world: World) -> None:
    refused(
        world,
        "host-admission-presentation-key-invalid",
        presentation_public_key_b64url="AAAA",
    )


def test_the_pinned_presentation_key_must_be_the_signing_one(world: World) -> None:
    refused(
        world,
        "host-admission-presentation-signature-invalid",
        presentation_public_key_b64url=b64url(public_raw(Ed25519PrivateKey.generate())),
    )


def test_host_admission_bytes_must_be_json(world: World) -> None:
    refused(world, "host-admission-unreadable", host_admission_bytes=b"not json")


def test_an_expired_presentation_is_refused(world: World) -> None:
    # issued = store - 2s, expires = issued + 300s, well before the ledger row.
    world.store_time = T + timedelta(minutes=5) - timedelta(seconds=310)
    world.resign_admission()
    refused(world, "host-admission-presentation-not-fresh")


def test_the_presentation_expiry_instant_itself_is_refused(world: World) -> None:
    ledger = T + timedelta(minutes=5)
    world.export["control"]["ledger"]["created_at"] = ts(ledger)
    world.store_time = ledger - timedelta(seconds=298)  # expires_at == ledger
    world.resign_admission()
    refused(world, "host-admission-presentation-not-fresh")


def test_a_not_yet_issued_presentation_is_refused(world: World) -> None:
    world.store_time = T + timedelta(minutes=10)
    world.resign_admission()
    refused(world, "host-admission-presentation-not-fresh")


def test_a_presentation_issued_exactly_at_the_ledger_is_accepted(world: World) -> None:
    world.export["control"]["ledger"]["created_at"] = ts(T + timedelta(minutes=5))
    world.store_time = T + timedelta(minutes=5, seconds=2)
    world.resign_admission()
    world.run()


def test_deeply_nested_host_admission_is_a_refusal_not_a_crash(world: World) -> None:
    nested = b"[" * 100000 + b"]" * 100000
    with pytest.raises(orchestrator.JoinRefused) as caught:
        orchestrator.read_host_admission(
            nested, presentation_public_key_b64url=world.pres_pub
        )
    assert str(caught.value).startswith("offline-join-host-admission-")
    refused(world, "host-admission-unreadable", host_admission_bytes=nested)


def test_a_non_s2_exception_from_the_reader_is_a_refusal(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise ValueError("private text must not be quoted")

    monkeypatch.setattr(orchestrator, "parse_host_admission", boom)
    refused(world, "host-admission-unreadable")
