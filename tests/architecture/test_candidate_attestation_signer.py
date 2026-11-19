"""``scripts/candidate_attestation_signer.py`` is publish-only; prove it.

S3 (ruling Q5-B): the script has ``render`` / ``assemble`` / ``verify`` and NO
key path. The signature in these tests comes from a synthetic Ed25519 key that
lives only in this test module and plays the ceremony's transit key. The
custody tests below (no key path in the script, no secret in any workflow,
publish-only workflow) are the successors of the old signing-secret tests.

Original framing: ``scripts/candidate_attestation_signer.py`` was a producer.


`trusted_host_source.py`'s ``verify_attestation_pair`` is a fail-closed
verifier with no producer anywhere in this repository until this change. A
producer whose output nothing validates is the actual failure mode ADR-0070's
2026-09-08 amendment warns about, so this module's first job is a POSITIVE
control: assemble a well-formed pair (this script's real candidate envelope,
plus a synthetic test-only installed envelope built only to exercise the other
half of the verifier) and show `verify_attestation_pair` accepts it.

Its second job is the required NEGATIVE control: take that same accepted
envelope, mutate one field of its subject, and show the verifier now refuses —
proving the envelope is actually checked, not merely well-typed.

Its third job is the custody-separation proof this producer can make on its
own evidence: signing both roles with one key is refused independent of any
supplied trust policy (`SAME_KEY_SIGNED_BOTH`), and this script's own source
carries no reference to the installed-host role's purpose, subject type, or
the verifier entry point — each backed by a sensitivity plant, per ADR-0018 /
AGENTS.md rule 23.

## What the synthetic "installed" envelope in this file is not

It is a same-process, same-process-only Ed25519 key generated inside this
test module, used exactly once, to build the OTHER HALF of a pair so
`verify_attestation_pair` has two inputs to compare. It is not, and must never
be read as, a reference host-attestation implementation — that lane is owned
elsewhere. Its ONLY job here is to let this module prove that the candidate
envelope this repository's real signer produces behaves correctly inside the
real verifier.
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import importlib.util
import json
import re
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from dotmac_deployment_foundation.attestation_store import (
    attestation_envelope_document,
    write_attestation_record,
)
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.trusted_host_source import (
    AUDIENCE_MISMATCH,
    INSTALLED_OBSERVATION_PURPOSE,
    ROOT_BINDING_MISMATCH,
    ROOT_NOT_VALID,
    ROOT_PURPOSE_MISMATCH,
    SAME_KEY_SIGNED_BOTH,
    SIGNATURE_INVALID,
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationTrustRootV2,
    CandidateAttestationSubjectV2,
    InstalledHostAttestationSubjectV2,
    verify_attestation_pair,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "candidate_attestation_signer.py"
WORKFLOW = (
    PROJECT_ROOT / ".github" / "workflows" / "foundation-candidate-attestation.yml"
)

#: The retired signing-key secret's exact name, restated here rather than
#: imported: after S3 NO workflow may reference it.
RETIRED_SIGNING_SECRET_NAME = "FOUNDATION_CANDIDATE_SIGNING_ED25519_PRIVATE_KEY_PEM"


def _module():
    spec = importlib.util.spec_from_file_location(
        "_candidate_attestation_signer", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution — a module that later grows a
    # `@dataclass(slots=True)` would otherwise fail here in a way that reads as
    # a bug in the script rather than in this loader (see the fix for #674).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SIGNER = _module()


# ── fixtures ─────────────────────────────────────────────────────────────


def _receipt() -> dict[str, Any]:
    return {
        "schema": "CandidateArtifact.v1",
        "facility": "dotmac-deployment-foundation",
        "version": "0.4.0a2",
        "repository": "michaelayoade/dotmac_starter_mt",
        "source_sha": "a" * 40,
        "run_id": "11111111",
        "artifact_id": "22222222",
        "filename": "dotmac_deployment_foundation-0.4.0a2-py3-none-any.whl",
        "size_bytes": 12345,
        "sha256": "b" * 64,
        "sdist": {
            "filename": "dotmac_deployment_foundation-0.4.0a2.tar.gz",
            "size_bytes": 6789,
            "sha256": "c" * 64,
        },
        "expires_at": "2099-01-01T00:00:00Z",
        "retention_requested_days": 90,
        "artifact_size_bytes": 12345,
        "published": False,
        "tagged": False,
    }


class _ReferenceEd25519Verifier:
    """A pure crypto primitive, used only to exercise the pair verifier.

    This is not a trust decision: it answers only "does this bit pattern
    verify against this key", with no notion of which key ought to be
    trusted — that judgment is entirely `trust_policy`'s, supplied by the
    caller of `verify_attestation_pair` in every case, including here.
    """

    def verify(
        self, *, public_key: bytes, algorithm: str, message: bytes, signature: str
    ) -> bool:
        if algorithm != "ed25519":
            return False
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                base64.b64decode(signature), message
            )
        except InvalidSignature:
            return False
        return True


def _root_for(
    private_key: Ed25519PrivateKey,
    envelope: AttestationEnvelopeV2,
    *,
    now: datetime,
) -> AttestationTrustRootV2:
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return AttestationTrustRootV2(
        public_key_fingerprint=envelope.public_key_fingerprint,
        public_key_base64=base64.b64encode(raw).decode("ascii"),
        issuer=envelope.issuer,
        key_id=envelope.key_id,
        purpose=envelope.purpose,
        custody_domain=envelope.custody_domain,
        algorithm=envelope.algorithm,
        trust_root_version=envelope.trust_root_version,
        not_before=SIGNER._canonical_instant(now - timedelta(days=1)),
        not_after=SIGNER._canonical_instant(now + timedelta(days=1)),
    )


CEREMONY_ISSUER = "urn:dotmac:foundation:candidate-release-ceremony:a2"
ENROLMENT_UUID = "3f2b8a3e-4c1d-4e0a-9b7a-0d6c5e4f3a21"


def _public_raw(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _render(
    receipt: dict[str, Any],
    private_key: Ed25519PrivateKey,
    *,
    now: datetime,
    lifetime: timedelta = timedelta(hours=1),
) -> AttestationEnvelopeV2:
    return SIGNER.render_unsigned_envelope(
        receipt=receipt,
        key_id="candidate-release-v1",
        public_key_fingerprint=SIGNER.fingerprint_of(_public_raw(private_key)),
        trust_root_version=ENROLMENT_UUID,
        issued_at=now,
        expires_at=now + lifetime,
        observation_id=str(uuid.uuid4()),
    )


def _ceremony_signature(private_key: Ed25519PrivateKey, unsigned: Any) -> str:
    """Stands in for the ceremony's transit sign: signs the rendered bytes."""
    return base64.b64encode(private_key.sign(unsigned.signed_bytes())).decode("ascii")


def _sign_candidate(
    receipt: dict[str, Any], private_key: Ed25519PrivateKey, *, now: datetime
) -> AttestationEnvelopeV2:
    unsigned = _render(receipt, private_key, now=now)
    return SIGNER.assemble_envelope(
        unsigned, _ceremony_signature(private_key, unsigned)
    )


def _sign_installed(
    candidate_envelope: AttestationEnvelopeV2,
    candidate_subject: CandidateAttestationSubjectV2,
    private_key: Ed25519PrivateKey,
    *,
    expected_host_identity: str,
    now: datetime,
) -> AttestationEnvelopeV2:
    """A same-process TEST-ONLY installed envelope — see module docstring."""
    candidate_digest = Digest.of(
        json.dumps(
            candidate_subject.canonical_document(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )
    installed_subject = InstalledHostAttestationSubjectV2(
        host_identity=expected_host_identity,
        package=candidate_subject.package,
        version=candidate_subject.version,
        wheel_sha256=candidate_subject.wheel_sha256,
        candidate_subject_digest=candidate_digest,
    )
    unsigned = AttestationEnvelopeV2(
        schema=candidate_envelope.schema,
        purpose=INSTALLED_OBSERVATION_PURPOSE,
        issuer="test-only://host-attestation-fixture",
        key_id="test-only-host-key",
        algorithm="ed25519",
        public_key_fingerprint=SIGNER.fingerprint_of(_public_raw(private_key)),
        custody_domain="dotmac.foundation.installed-host.test-only",
        trust_root_version="v1",
        issued_at=SIGNER._canonical_instant(now),
        expires_at=SIGNER._canonical_instant(now + timedelta(hours=1)),
        audience=expected_host_identity,
        observation_id=str(uuid.uuid4()),
        subject=installed_subject.canonical_document(),
        signature="unsigned",
    )
    raw_signature = private_key.sign(unsigned.signed_bytes())
    return dataclasses.replace(
        unsigned, signature=base64.b64encode(raw_signature).decode("ascii")
    )


# ── positive control: the verifier accepts a well-formed pair ─────────────


def test_verify_attestation_pair_accepts_the_signed_candidate_envelope() -> None:
    receipt = _receipt()
    now = datetime.now(UTC)
    candidate_key = Ed25519PrivateKey.generate()
    installed_key = Ed25519PrivateKey.generate()

    candidate_envelope = _sign_candidate(receipt, candidate_key, now=now)
    candidate_subject = SIGNER.candidate_subject_from_receipt(receipt)
    installed_envelope = _sign_installed(
        candidate_envelope,
        candidate_subject,
        installed_key,
        expected_host_identity="test-target-host-01",
        now=now,
    )

    policy = AttestationTrustPolicy(
        candidate_roots=(_root_for(candidate_key, candidate_envelope, now=now),),
        installed_roots=(_root_for(installed_key, installed_envelope, now=now),),
        candidate_audience="dotmac-deployment-foundation",
        installed_audience="test-target-host-01",
    )

    # Does not raise.
    verify_attestation_pair(
        candidate=candidate_envelope,
        installed=installed_envelope,
        verifier=_ReferenceEd25519Verifier(),
        trust_policy=policy,
        expected_host_identity="test-target-host-01",
        expected_observation_id=installed_envelope.observation_id,
        expected_package="dotmac-deployment-foundation",
        verification_context_digest="sha256:" + "ab" * 32,
        now=now,
    )


def test_the_signed_envelope_round_trips_through_the_package_writer() -> None:
    receipt = _receipt()
    now = datetime.now(UTC)
    candidate_key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(receipt, candidate_key, now=now)
    mapping = attestation_envelope_document(envelope)
    # Round-trips through JSON exactly as the package writer publishes it and
    # a later consumer would parse it back.
    reparsed = AttestationEnvelopeV2.from_mapping(json.loads(json.dumps(mapping)))
    assert reparsed.signature == envelope.signature
    assert reparsed.subject_mapping() == envelope.subject_mapping()
    assert reparsed.purpose == "dotmac.foundation.candidate-artifact.v2"


def test_script_writes_through_the_package_owned_writer_not_its_own_json_dumps(
    tmp_path: Path,
) -> None:
    """The thin-adapter proof: the script's own source contains no
    ``json.dumps(..., sort_keys=True)`` call, and ``render`` / ``assemble``
    publish bytes ``attestation_store.write_attestation_record`` produced."""
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    own_canonicalizing_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
        == "dumps"
        and any(kw.arg == "sort_keys" for kw in node.keywords)
    ]
    assert not own_canonicalizing_calls

    receipt = _receipt()
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(receipt, key, now=now)
    out = tmp_path / "candidate-attestation.json"
    assert write_attestation_record(out, envelope) == out
    assert json.loads(out.read_text(encoding="utf-8")) == (
        attestation_envelope_document(envelope)
    )
    with pytest.raises(FileExistsError):
        write_attestation_record(out, envelope)


# ── required negative control: mutate one subject field, verifier refuses ──


def test_mutating_one_subject_field_after_signing_is_refused() -> None:
    receipt = _receipt()
    now = datetime.now(UTC)
    candidate_key = Ed25519PrivateKey.generate()
    installed_key = Ed25519PrivateKey.generate()

    candidate_envelope = _sign_candidate(receipt, candidate_key, now=now)
    candidate_subject = SIGNER.candidate_subject_from_receipt(receipt)
    installed_envelope = _sign_installed(
        candidate_envelope,
        candidate_subject,
        installed_key,
        expected_host_identity="test-target-host-01",
        now=now,
    )
    policy = AttestationTrustPolicy(
        candidate_roots=(_root_for(candidate_key, candidate_envelope, now=now),),
        installed_roots=(_root_for(installed_key, installed_envelope, now=now),),
        candidate_audience="dotmac-deployment-foundation",
        installed_audience="test-target-host-01",
    )

    # Sanity: the unmutated pair verifies (repeats the positive control so a
    # reader of this test alone can see the baseline it is mutating away
    # from).
    verify_attestation_pair(
        candidate=candidate_envelope,
        installed=installed_envelope,
        verifier=_ReferenceEd25519Verifier(),
        trust_policy=policy,
        expected_host_identity="test-target-host-01",
        expected_observation_id=installed_envelope.observation_id,
        expected_package="dotmac-deployment-foundation",
        verification_context_digest="sha256:" + "ab" * 32,
        now=now,
    )

    # ONE field of the SIGNED subject, changed after the signature was
    # produced. The signature still authenticates the ORIGINAL bytes, so a
    # verifier that recomputes `signed_bytes()` over the mutated subject must
    # find the signature no longer matches.
    mutated_subject = dict(candidate_envelope.subject_mapping())
    mutated_subject["version"] = "9.9.9-tampered"
    tampered = dataclasses.replace(candidate_envelope, subject=mutated_subject)

    with pytest.raises(PreconditionFailed) as excinfo:
        verify_attestation_pair(
            candidate=tampered,
            installed=installed_envelope,
            verifier=_ReferenceEd25519Verifier(),
            trust_policy=policy,
            expected_host_identity="test-target-host-01",
            expected_observation_id=installed_envelope.observation_id,
            expected_package="dotmac-deployment-foundation",
            verification_context_digest="sha256:" + "ab" * 32,
            now=now,
        )
    assert excinfo.value.code == SIGNATURE_INVALID


# ── custody separation: structural, not conventional ───────────────────────


def test_reusing_the_same_key_for_both_roles_is_refused_independent_of_policy() -> None:
    """The verifier's own backstop: one key can never author both roles.

    This is the structural guarantee this producer's own key-separation relies
    on even if a future operational mistake ever pointed both lanes' secrets
    at the same OpenBao path: `verify_attestation_pair` refuses before
    consulting any supplied trust policy at all.
    """
    receipt = _receipt()
    now = datetime.now(UTC)
    shared_key = Ed25519PrivateKey.generate()

    candidate_envelope = _sign_candidate(receipt, shared_key, now=now)
    candidate_subject = SIGNER.candidate_subject_from_receipt(receipt)
    installed_envelope = _sign_installed(
        candidate_envelope,
        candidate_subject,
        shared_key,
        expected_host_identity="test-target-host-01",
        now=now,
    )
    # A policy built from two UNRELATED keys — deliberately not matching
    # either envelope's actual fingerprint. `verify_attestation_pair` checks
    # `candidate.public_key_fingerprint == installed.public_key_fingerprint`
    # as its very first act, before it ever inspects `trust_policy` at all
    # (source order in `trusted_host_source.py`), so this policy's roots are
    # never reached — which is exactly what "independent of policy" means
    # here, and why this policy is deliberately unrelated to the shared key.
    other_candidate_key = Ed25519PrivateKey.generate()
    other_installed_key = Ed25519PrivateKey.generate()
    dummy_candidate_envelope = _sign_candidate(receipt, other_candidate_key, now=now)
    dummy_installed_envelope = _sign_installed(
        dummy_candidate_envelope,
        candidate_subject,
        other_installed_key,
        expected_host_identity="test-target-host-01",
        now=now,
    )
    policy = AttestationTrustPolicy(
        candidate_roots=(
            _root_for(other_candidate_key, dummy_candidate_envelope, now=now),
        ),
        installed_roots=(
            _root_for(other_installed_key, dummy_installed_envelope, now=now),
        ),
        candidate_audience="dotmac-deployment-foundation",
        installed_audience="test-target-host-01",
    )

    with pytest.raises(PreconditionFailed) as excinfo:
        verify_attestation_pair(
            candidate=candidate_envelope,
            installed=installed_envelope,
            verifier=_ReferenceEd25519Verifier(),
            trust_policy=policy,
            expected_host_identity="test-target-host-01",
            expected_observation_id=installed_envelope.observation_id,
            expected_package="dotmac-deployment-foundation",
            verification_context_digest="sha256:" + "ab" * 32,
            now=now,
        )
    assert excinfo.value.code == SAME_KEY_SIGNED_BOTH


# ── sensitivity proof: the signer script itself cannot emit a host envelope ─

#: Names that would let this script construct or read an installed-host role
#: envelope. Their absence is what makes "this script cannot emit a host
#: attestation" checkable rather than aspirational — the same discipline
#: `test_candidate_lane_cannot_publish.py` applies to publish verbs.
FORBIDDEN_HOST_REFERENCES = (
    "InstalledHostAttestationSubjectV2",
    "INSTALLED_OBSERVATION_PURPOSE",
    "verify_attestation_pair",
    "expected_host_identity",
)


def _host_reference_violations(source: str) -> list[str]:
    """AST-based, deliberately: a raw substring search would also flag this
    module's OWN docstring, which names these identifiers in prose to explain
    their absence, and it would treat `expected_host_identity_v2` as a hit on
    `expected_host_identity` when it is a different name entirely. Only
    actual `Name`/`Attribute`/import identifiers count — a docstring's string
    contents produce no such nodes."""
    tree = ast.parse(source)
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                identifiers.add(alias.name.rsplit(".", 1)[-1])
                if alias.asname:
                    identifiers.add(alias.asname)
    return [name for name in FORBIDDEN_HOST_REFERENCES if name in identifiers]


def test_the_signer_script_contains_no_installed_host_reference() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    violations = _host_reference_violations(source)
    assert not violations, (
        f"{SCRIPT.name} references installed-host-role names {violations} — "
        "the candidate signer must be structurally incapable of emitting a "
        "host-role envelope, not merely undocumented for doing so. (The "
        "module docstring is allowed to NAME these identifiers in prose; "
        "this sweep is AST-based and only counts real code references.)"
    )


def test_the_host_reference_sweep_is_observed_failing_on_a_planted_violation() -> None:
    """ADR-0018 sensitivity proof: a detector with no observed failure is an
    assumption wearing a test's clothes, not a guard."""
    real_source = SCRIPT.read_text(encoding="utf-8")
    assert not _host_reference_violations(real_source)

    # The plant is real CODE (a bare name reference), not prose — a text-only
    # plant would not distinguish this AST sweep from the substring search it
    # deliberately replaced.
    planted = real_source + "\nverify_attestation_pair\n"
    violations = _host_reference_violations(planted)
    assert violations == ["verify_attestation_pair"]

    # Near-miss: a DIFFERENT identifier that merely contains one of the
    # forbidden names as a substring must not be flagged — this is exactly
    # the false positive a plain substring search would have produced, and is
    # why the sweep is AST-based and matches whole identifiers only.
    near_miss = real_source + "\nexpected_host_identity_v2 = 1\n"
    assert not _host_reference_violations(near_miss)


# ── custody: no workflow may reference any secret for the candidate lane ───


def _workflow_files() -> dict[str, str]:
    directory = PROJECT_ROOT / ".github" / "workflows"
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(directory.glob("*.yml"))
    }


def _secret_users(documents: dict[str, str], secret_name: str) -> list[str]:
    return sorted(name for name, text in documents.items() if secret_name in text)


def test_no_workflow_references_the_retired_candidate_signing_secret() -> None:
    users = _secret_users(_workflow_files(), RETIRED_SIGNING_SECRET_NAME)
    assert users == [], (
        f"{RETIRED_SIGNING_SECRET_NAME} is referenced by {users}; the candidate "
        "key is a ceremony transit key and no private key may exist in GitHub "
        "(ruling Q5-B)"
    )


def test_the_secret_sweep_is_observed_failing_on_a_planted_user() -> None:
    documents = _workflow_files()
    assert _secret_users(documents, RETIRED_SIGNING_SECRET_NAME) == []
    planted = dict(documents)
    planted["some-other-lane.yml"] = f"secrets.{RETIRED_SIGNING_SECRET_NAME}"
    assert _secret_users(planted, RETIRED_SIGNING_SECRET_NAME) == [
        "some-other-lane.yml"
    ]
    near_miss = dict(documents)
    near_miss["unrelated-lane.yml"] = "secrets.SOME_OTHER_SIGNING_KEY_PEM"
    assert _secret_users(near_miss, RETIRED_SIGNING_SECRET_NAME) == []


def test_the_candidate_workflow_references_no_secret_at_all() -> None:
    document = _workflow_document()
    assert "secrets." not in yaml.safe_dump(document)
    assert "PRIVATE_KEY" not in yaml.safe_dump(document)


# ── the script has no key path at all ──────────────────────────────────────

#: Identifiers that would give the script a way to hold or use a private key.
KEY_PATH_IDENTIFIERS = (
    "Ed25519PrivateKey",
    "load_pem_private_key",
    "load_der_private_key",
    "private_key",
    "sign",
    "private_bytes",
    "load_ssh_private_key",
    "subprocess",
    "urllib",
    "http",
)


def _identifiers(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.FunctionDef):
            found.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
            for alias in node.names:
                found.add(alias.name.rsplit(".", 1)[-1])
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.Call):
            # getattr(x, "sign") reaches an attribute without naming it.
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                found.add(node.args[1].value)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("--"):
                found.add(node.value)
    return found


def _key_path_violations(source: str) -> list[str]:
    identifiers = _identifiers(source)
    return [
        name
        for name in (*KEY_PATH_IDENTIFIERS, "--private-key-file", "--key-file")
        if name in identifiers
    ]


def test_the_script_has_no_key_path() -> None:
    assert _key_path_violations(SCRIPT.read_text(encoding="utf-8")) == []


def test_the_key_path_sweep_is_observed_failing_on_planted_code() -> None:
    real = SCRIPT.read_text(encoding="utf-8")
    assert _key_path_violations(real + "\nprivate_key.sign(b'x')\n") == [
        "private_key",
        "sign",
    ]
    assert _key_path_violations(real + "\nfrom x import load_pem_private_key\n") == [
        "load_pem_private_key"
    ]
    assert _key_path_violations(
        real + "\nparser.add_argument('--private-key-file')\n"
    ) == ["--private-key-file"]
    assert _key_path_violations(real + "\nimport subprocess\n") == ["subprocess"]
    assert _key_path_violations(real + "\nimport urllib.request\n") == ["urllib"]
    assert _key_path_violations(real + "\nfrom http import client\n") == ["http"]
    assert _key_path_violations(real + "\nfrom x import load_ssh_private_key\n") == [
        "load_ssh_private_key"
    ]
    assert _key_path_violations(real + '\ngetattr(k, "sign")\n') == ["sign"]
    # Near miss: whole identifiers only.
    assert _key_path_violations(real + "\nsignature_bytes = 1\n") == []


def test_the_script_exposes_exactly_render_assemble_verify() -> None:
    parser = SIGNER.build_parser()
    choices = next(
        action.choices
        for action in parser._actions
        if getattr(action, "choices", None) is not None
    )
    assert sorted(choices) == ["assemble", "render", "verify"]


# ── workflow structure: protected, non-publishing, non-self-committing ─────


def _run_scripts(document: dict[str, Any]) -> list[str]:
    scripts: list[str] = []
    for job in (document.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                scripts.append(step["run"])
    return scripts


def _workflow_document() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


#: Verbs that would make this lane a second, self-committing publisher of the
#: record it is supposed to leave to a separate reviewed change.
SELF_COMMIT_VERBS = ("git commit", "git push", "gh pr create", "gh release")


def test_the_publish_workflow_keeps_its_protected_environment_gate() -> None:
    document = _workflow_document()
    jobs = document.get("jobs") or {}
    assert jobs, "the workflow declares no jobs"
    environments = {job.get("environment") for job in jobs.values()}
    assert environments == {"foundation-candidate-signing"}
    # Distinct from the publish credential boundary every release lane reuses.
    assert "registry-release" not in environments


def test_the_publish_workflow_never_writes_contents_or_self_commits() -> None:
    document = _workflow_document()
    assert document.get("permissions", {}).get("contents") == "read"
    for name, job in (document.get("jobs") or {}).items():
        permissions = job.get("permissions") or {}
        assert permissions.get("contents", "read") == "read", (
            f"job {name!r} widens contents permission; this lane must never "
            "self-commit its own attestation receipt"
        )
    scripts = "\n".join(_run_scripts(document))
    violations = [verb for verb in SELF_COMMIT_VERBS if verb in scripts]
    assert not violations, (
        f"the signing workflow contains self-commit verb(s) {violations}; a "
        "separate, reviewed record change owns committing the receipt"
    )


def test_the_self_commit_sweep_is_observed_failing_on_a_planted_git_push() -> None:
    document = _workflow_document()
    scripts = "\n".join(_run_scripts(document))
    assert not any(verb in scripts for verb in SELF_COMMIT_VERBS)

    planted_scripts = scripts + "\ngit push origin main\n"
    assert any(verb in planted_scripts for verb in SELF_COMMIT_VERBS)


# ── the candidate subject is built from the receipt, never recomputed ──────


def test_candidate_subject_reads_every_field_from_the_receipt_verbatim() -> None:
    receipt = _receipt()
    subject = SIGNER.candidate_subject_from_receipt(receipt)
    assert subject.package == receipt["facility"]
    assert subject.version == receipt["version"]
    assert str(subject.wheel_sha256) == f"sha256:{receipt['sha256']}"
    assert subject.source_revision == receipt["source_sha"]
    assert subject.repository == receipt["repository"]
    assert subject.run_id == receipt["run_id"]
    assert subject.artifact_id == receipt["artifact_id"]


def test_refuses_a_receipt_that_is_not_candidate_artifact_v1() -> None:
    receipt = _receipt()
    receipt["schema"] = "SomethingElse.v1"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(receipt, handle)
        path = Path(handle.name)
    try:
        with pytest.raises(SystemExit):
            SIGNER.load_candidate_artifact_receipt(path)
    finally:
        path.unlink()


def test_purpose_and_custody_are_hardcoded_never_render_parameters() -> None:
    import inspect

    parameters = inspect.signature(SIGNER.render_unsigned_envelope).parameters
    for name in ("purpose", "issuer", "custody_domain", "audience", "algorithm"):
        assert name not in parameters, (
            f"render_unsigned_envelope must not accept {name}: it is a constant "
            "of the ceremony-signed candidate role"
        )


# ── S3: render / assemble / verify behaviour and every refusal ─────────────


def _write(path: Path, envelope: AttestationEnvelopeV2) -> Path:
    write_attestation_record(path, envelope)
    return path


def _pin(private_key: Ed25519PrivateKey) -> dict[str, str]:
    raw = _public_raw(private_key)
    return {
        "public_key_b64": base64.b64encode(raw).decode("ascii"),
        "key_id": "candidate-release-v1",
        "pinned_fingerprint": SIGNER.fingerprint_of(raw),
        "trust_root_version": ENROLMENT_UUID,
    }


def _verify(envelope, receipt, key, *, now, **override):
    args = {**_pin(key), **override}
    return SIGNER.verify_published_envelope(
        envelope=envelope, receipt=receipt, now=now, **args
    )


def test_render_carries_the_ceremony_constants_including_the_f_a_custody_domain() -> (
    None
):
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    unsigned = _render(_receipt(), key, now=now)
    assert unsigned.custody_domain == "candidate_release_signer"
    assert unsigned.issuer == CEREMONY_ISSUER
    assert unsigned.audience == "dotmac-deployment-foundation"
    assert unsigned.trust_root_version == ENROLMENT_UUID
    assert unsigned.purpose == "dotmac.foundation.candidate-artifact.v2"
    assert unsigned.signature == "unsigned"
    assert unsigned.issued_at == SIGNER._canonical_instant(now)
    # The rendered signed_bytes exclude the signature, so signing them and
    # assembling cannot change what was signed.
    signed = _sign_candidate(_receipt(), key, now=now)
    assert b"unsigned" not in unsigned.signed_bytes()
    assert signed.signed_bytes() != b""


def test_assemble_then_verify_accepts_and_returns_the_receipt_subject() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=now)
    subject = _verify(envelope, _receipt(), key, now=now)
    assert subject == SIGNER.candidate_subject_from_receipt(_receipt())


def test_assemble_signs_nothing_and_refuses_a_bad_signature_shape() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    unsigned = _render(_receipt(), key, now=now)
    for bad in ("not base64 !!", base64.b64encode(b"short").decode("ascii")):
        with pytest.raises(SystemExit, match="signature"):
            SIGNER.assemble_envelope(unsigned, bad)
    signed = SIGNER.assemble_envelope(unsigned, _ceremony_signature(key, unsigned))
    with pytest.raises(SystemExit, match="already signed"):
        SIGNER.assemble_envelope(signed, _ceremony_signature(key, unsigned))


def test_verify_refuses_an_unsigned_envelope() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    with pytest.raises(SystemExit, match="unsigned"):
        _verify(_render(_receipt(), key, now=now), _receipt(), key, now=now)


def test_verify_refuses_a_signature_by_another_key() -> None:
    now = datetime.now(UTC)
    pinned = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    unsigned = _render(_receipt(), pinned, now=now)
    forged = SIGNER.assemble_envelope(unsigned, _ceremony_signature(other, unsigned))
    with pytest.raises(SystemExit, match=re.escape(SIGNATURE_INVALID)):
        _verify(forged, _receipt(), pinned, now=now)


def test_verify_refuses_a_public_key_that_is_not_the_pinned_fingerprint() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=now)
    with pytest.raises(
        SystemExit, match="public key does not match the pinned fingerprint"
    ):
        _verify(
            envelope,
            _receipt(),
            key,
            now=now,
            public_key_b64=_pin(other)["public_key_b64"],
        )


def test_verify_refuses_an_envelope_fingerprint_that_is_not_the_pin() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=now)
    with pytest.raises(
        SystemExit, match="envelope fingerprint differs from the pinned"
    ):
        _verify(
            envelope,
            _receipt(),
            key,
            now=now,
            pinned_fingerprint=_pin(other)["pinned_fingerprint"],
            public_key_b64=_pin(other)["public_key_b64"],
        )


@pytest.mark.parametrize(
    "override",
    [
        {"key_id": "some-other-key"},
        {"trust_root_version": "11111111-2222-4333-8444-555555555555"},
    ],
)
def test_verify_refuses_a_binding_that_differs_from_the_pin(
    override: dict[str, str],
) -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=now)
    with pytest.raises(SystemExit, match=re.escape(ROOT_BINDING_MISMATCH)):
        _verify(envelope, _receipt(), key, now=now, **override)


def _resign(
    key: Ed25519PrivateKey, envelope: AttestationEnvelopeV2, **changes: Any
) -> AttestationEnvelopeV2:
    """A validly signed envelope whose content deviates from the role."""
    changed = dataclasses.replace(envelope, signature="unsigned", **changes)
    signature = base64.b64encode(key.sign(changed.signed_bytes())).decode("ascii")
    return dataclasses.replace(changed, signature=signature)


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"issuer": "https://example.invalid/workflow.yml"}, ROOT_BINDING_MISMATCH),
        ({"custody_domain": "dotmac.foundation.candidate"}, ROOT_BINDING_MISMATCH),
        ({"audience": "someone-else"}, AUDIENCE_MISMATCH),
        (
            {"purpose": "dotmac.foundation.installed-observation.v2"},
            ROOT_PURPOSE_MISMATCH,
        ),
    ],
)
def test_verify_refuses_a_validly_signed_envelope_with_a_wrong_role_field(
    changes: dict[str, str], code: str
) -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    envelope = _resign(key, _sign_candidate(_receipt(), key, now=now), **changes)
    with pytest.raises(SystemExit, match=re.escape(code)):
        _verify(envelope, _receipt(), key, now=now)


def test_verify_refuses_an_expired_envelope() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=now)
    with pytest.raises(SystemExit, match=re.escape(ROOT_NOT_VALID)):
        _verify(envelope, _receipt(), key, now=now + timedelta(hours=2))


def test_verify_refuses_an_envelope_issued_in_the_future() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=now)
    with pytest.raises(SystemExit, match=re.escape(ROOT_NOT_VALID)):
        _verify(envelope, _receipt(), key, now=now - timedelta(hours=1))


def test_verify_refuses_a_subject_that_is_not_the_committed_receipt() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=now)
    other = _receipt()
    other["sha256"] = "d" * 64
    with pytest.raises(SystemExit, match="committed CandidateArtifact"):
        _verify(envelope, other, key, now=now)


def test_verify_refuses_a_tampered_subject() -> None:
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=now)
    subject = dict(envelope.subject_mapping())
    subject["version"] = "9.9.9"
    tampered = dataclasses.replace(envelope, subject=subject)
    with pytest.raises(SystemExit, match=re.escape(SIGNATURE_INVALID)):
        _verify(tampered, _receipt(), key, now=now)


def test_render_refuses_a_non_candidate_receipt_and_a_backwards_window(
    tmp_path: Path,
) -> None:
    bad = _receipt()
    bad["schema"] = "Other.v1"
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(bad), encoding="utf-8")
    base = [
        "render",
        "--receipt",
        str(receipt_path),
        "--key-id",
        "k",
        "--public-key-fingerprint",
        "sha256:" + "0" * 64,
        "--trust-root-version",
        ENROLMENT_UUID,
        "--issued-at",
        "2030-01-01T00:00:00Z",
        "--expires-at",
        "2030-01-02T00:00:00Z",
        "--out-envelope",
        str(tmp_path / "u.json"),
        "--out-signed-bytes",
        str(tmp_path / "b"),
    ]
    with pytest.raises(SystemExit, match="CandidateArtifact.v1"):
        SIGNER.main(base)
    receipt_path.write_text(json.dumps(_receipt()), encoding="utf-8")
    backwards = [*base]
    backwards[backwards.index("--expires-at") + 1] = "2029-12-31T00:00:00Z"
    with pytest.raises(SystemExit, match="must follow"):
        SIGNER.main(backwards)
    not_uuid = [*base]
    not_uuid[not_uuid.index("--trust-root-version") + 1] = "v1"
    with pytest.raises(SystemExit, match="enrolment UUID"):
        SIGNER.main(not_uuid)
    loose = [*base]
    loose[loose.index("--issued-at") + 1] = "2030-01-01T00:00:00.5Z"
    with pytest.raises(SystemExit, match="canonical"):
        SIGNER.main(loose)


def test_cli_render_assemble_verify_round_trip(tmp_path: Path, capsys) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_receipt()), encoding="utf-8")
    key = Ed25519PrivateKey.generate()
    pin = _pin(key)
    now = datetime.now(UTC)
    issued = SIGNER._canonical_instant(now - timedelta(minutes=1))
    expires = SIGNER._canonical_instant(now + timedelta(hours=1))
    assert (
        SIGNER.main(
            [
                "render",
                "--receipt",
                str(receipt_path),
                "--key-id",
                pin["key_id"],
                "--public-key-fingerprint",
                pin["pinned_fingerprint"],
                "--trust-root-version",
                ENROLMENT_UUID,
                "--issued-at",
                issued,
                "--expires-at",
                expires,
                "--out-envelope",
                str(tmp_path / "unsigned.json"),
                "--out-signed-bytes",
                str(tmp_path / "signed_bytes"),
            ]
        )
        == 0
    )
    signed_bytes = (tmp_path / "signed_bytes").read_bytes()
    (tmp_path / "sig.b64").write_text(
        base64.b64encode(key.sign(signed_bytes)).decode("ascii"), encoding="utf-8"
    )
    assert (
        SIGNER.main(
            [
                "assemble",
                "--unsigned",
                str(tmp_path / "unsigned.json"),
                "--signature-file",
                str(tmp_path / "sig.b64"),
                "--out",
                str(tmp_path / "final.json"),
            ]
        )
        == 0
    )
    assert (
        SIGNER.main(
            [
                "verify",
                "--envelope",
                str(tmp_path / "final.json"),
                "--receipt",
                str(receipt_path),
                "--public-key-base64",
                pin["public_key_b64"],
                "--key-id",
                pin["key_id"],
                "--public-key-fingerprint",
                pin["pinned_fingerprint"],
                "--trust-root-version",
                ENROLMENT_UUID,
                "--summary",
                str(tmp_path / "summary.md"),
            ]
        )
        == 0
    )
    assert (
        "not admitted" in (tmp_path / "summary.md").read_text(encoding="utf-8").lower()
    )
    out = capsys.readouterr().out
    assert "BEGIN" not in out
    # A malformed envelope file is refused, not crashed on.
    (tmp_path / "junk.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="well-formed"):
        SIGNER.main(
            [
                "verify",
                "--envelope",
                str(tmp_path / "junk.json"),
                "--receipt",
                str(receipt_path),
                "--public-key-base64",
                pin["public_key_b64"],
                "--key-id",
                pin["key_id"],
                "--public-key-fingerprint",
                pin["pinned_fingerprint"],
                "--trust-root-version",
                ENROLMENT_UUID,
            ]
        )


# ── workflow: verify-then-publish only ─────────────────────────────────────


def test_the_workflow_verifies_the_envelope_and_never_signs() -> None:
    document = _workflow_document()
    scripts = "\n".join(_run_scripts(document))
    assert "candidate_attestation_signer.py verify" in scripts
    for forbidden in (" sign ", "--private-key-file", "openssl", "private_key"):
        assert forbidden not in scripts
    inputs = document[True]["workflow_dispatch"]["inputs"]
    assert {"facility", "version", "envelope", "public_key_base64"} <= set(inputs)
    # The pinned public identity comes from repository variables only.
    text = WORKFLOW.read_text(encoding="utf-8")
    for variable in (
        "FOUNDATION_CANDIDATE_SIGNING_KEY_ID",
        "FOUNDATION_CANDIDATE_PUBLIC_KEY_FINGERPRINT",
        "FOUNDATION_CANDIDATE_TRUST_ROOT_VERSION",
    ):
        assert f"vars.{variable}" in text


def test_the_workflow_verifies_before_it_uploads() -> None:
    steps = _workflow_document()["jobs"]["publish"]["steps"]
    names = [step.get("name", step.get("uses", "")) for step in steps]
    verify_index = next(i for i, n in enumerate(names) if n.startswith("Verify the"))
    upload_indexes = [i for i, n in enumerate(names) if n.startswith("Upload")]
    refuse_index = next(i for i, n in enumerate(names) if n.startswith("Refuse"))
    assert upload_indexes and refuse_index < verify_index < min(upload_indexes)
    verify_step = steps[verify_index]
    assert "if" not in verify_step, "the verify step must run unconditionally"
    assert "continue-on-error" not in verify_step
    # Only the file `verify --out` wrote is uploaded.
    upload = steps[upload_indexes[0]]
    assert upload["with"]["path"] == "candidate-attestation.json"
    assert "--out candidate-attestation.json" in verify_step["run"]


def test_the_workflow_interpolates_no_input_into_run_scripts() -> None:
    for script in _run_scripts(_workflow_document()):
        assert "${{ inputs." not in script and "${{inputs." not in script


def _verify_cli(tmp_path: Path, envelope_text: str, key, out: Path | None):
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_receipt()), encoding="utf-8")
    env_path = tmp_path / "in.json"
    env_path.write_text(envelope_text, encoding="utf-8")
    pin = _pin(key)
    argv = [
        "verify",
        "--envelope",
        str(env_path),
        "--receipt",
        str(receipt_path),
        "--public-key-base64",
        pin["public_key_b64"],
        "--key-id",
        pin["key_id"],
        "--public-key-fingerprint",
        pin["pinned_fingerprint"],
        "--trust-root-version",
        ENROLMENT_UUID,
    ]
    if out is not None:
        argv += ["--out", str(out)]
    return SIGNER.main(argv)


def test_verify_out_publishes_the_canonical_verified_record_not_the_input(
    tmp_path: Path,
) -> None:
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=datetime.now(UTC))
    # Same envelope, pretty-printed with extra whitespace: different raw bytes.
    raw = json.dumps(attestation_envelope_document(envelope), indent=4)
    out = tmp_path / "published.json"
    assert _verify_cli(tmp_path, raw, key, out) == 0
    assert out.read_bytes() != raw.encode()
    assert json.loads(out.read_text(encoding="utf-8")) == (
        attestation_envelope_document(envelope)
    )


def test_verify_refuses_duplicate_keys_and_publishes_nothing(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), key, now=datetime.now(UTC))
    raw = json.dumps(attestation_envelope_document(envelope))
    # A decoy: the same key twice, the verified value last.
    decoy = raw.replace('{"schema"', '{"audience": "decoy", "schema"', 1)
    assert decoy != raw
    out = tmp_path / "published.json"
    with pytest.raises(SystemExit, match="well-formed"):
        _verify_cli(tmp_path, decoy, key, out)
    assert not out.exists()


def test_verify_publishes_nothing_when_verification_fails(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(_receipt(), other, now=datetime.now(UTC))
    out = tmp_path / "published.json"
    with pytest.raises(SystemExit):
        _verify_cli(
            tmp_path, json.dumps(attestation_envelope_document(envelope)), key, out
        )
    assert not out.exists()
