"""``scripts/candidate_attestation_signer.py`` is a producer; prove it is one.

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
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.trusted_host_source import (
    INSTALLED_OBSERVATION_PURPOSE,
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

#: The signing-key secret's exact name, restated here rather than imported —
#: the workflow is data (YAML), not an importable module, so the only way this
#: test can assert "declared in exactly one place" is to name the string
#: independently and grep for it.
SIGNING_SECRET_NAME = "FOUNDATION_CANDIDATE_SIGNING_ED25519_PRIVATE_KEY_PEM"


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


def _sign_candidate(
    receipt: dict[str, Any], private_key: Ed25519PrivateKey, *, now: datetime
) -> AttestationEnvelopeV2:
    return SIGNER.sign_candidate_attestation(
        receipt=receipt,
        private_key=private_key,
        issuer="https://github.com/michaelayoade/dotmac_starter_mt/.github/workflows/foundation-candidate-attestation.yml",
        key_id="candidate-signer-2026-09",
        custody_domain="dotmac.foundation.candidate",
        trust_root_version="v1",
        audience="dotmac-deployment-foundation",
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        observation_id=str(uuid.uuid4()),
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
        public_key_fingerprint=SIGNER._fingerprint(private_key.public_key()),
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
        now=now,
    )


def test_the_signed_envelope_round_trips_through_from_mapping() -> None:
    receipt = _receipt()
    now = datetime.now(UTC)
    candidate_key = Ed25519PrivateKey.generate()
    envelope = _sign_candidate(receipt, candidate_key, now=now)
    mapping = SIGNER.envelope_to_mapping(envelope)
    # Round-trips through JSON exactly as the workflow writes and a later
    # consumer would parse it back.
    reparsed = AttestationEnvelopeV2.from_mapping(json.loads(json.dumps(mapping)))
    assert reparsed.signature == envelope.signature
    assert reparsed.subject_mapping() == envelope.subject_mapping()
    assert reparsed.purpose == "dotmac.foundation.candidate-artifact.v2"


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


# ── sensitivity proof: the signing secret is declared in exactly one place ─


def _workflow_files() -> dict[str, str]:
    directory = PROJECT_ROOT / ".github" / "workflows"
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(directory.glob("*.yml"))
    }


def _secret_users(documents: dict[str, str], secret_name: str) -> list[str]:
    return sorted(name for name, text in documents.items() if secret_name in text)


def test_the_candidate_signing_secret_is_referenced_by_exactly_one_workflow() -> None:
    users = _secret_users(_workflow_files(), SIGNING_SECRET_NAME)
    assert users == ["foundation-candidate-attestation.yml"], (
        f"{SIGNING_SECRET_NAME} is referenced by {users}; the candidate signing "
        "key must be reachable from exactly this one protected workflow"
    )


def test_the_secret_user_sweep_is_observed_failing_on_a_planted_second_user() -> None:
    documents = _workflow_files()
    users = _secret_users(documents, SIGNING_SECRET_NAME)
    assert len(users) == 1

    planted = dict(documents)
    planted["some-other-lane.yml"] = f"secrets.{SIGNING_SECRET_NAME}"
    planted_users = _secret_users(planted, SIGNING_SECRET_NAME)
    assert len(planted_users) == 2, "the sweep must observe the planted second user"

    # Near-miss: a similarly-named but DISTINCT secret must not be counted as
    # a user of this one.
    near_miss = dict(documents)
    near_miss["unrelated-lane.yml"] = "secrets.SOME_OTHER_SIGNING_KEY_PEM"
    assert _secret_users(near_miss, SIGNING_SECRET_NAME) == [
        "foundation-candidate-attestation.yml"
    ]


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


def test_the_signing_workflow_declares_its_own_protected_environment() -> None:
    document = _workflow_document()
    jobs = document.get("jobs") or {}
    assert jobs, "the workflow declares no jobs"
    environments = {job.get("environment") for job in jobs.values()}
    assert environments == {"foundation-candidate-signing"}
    # Distinct from the publish credential boundary every release lane reuses.
    assert "registry-release" not in environments


def test_the_signing_workflow_never_writes_contents_or_self_commits() -> None:
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


def test_purpose_is_hardcoded_never_a_signer_parameter() -> None:
    import inspect

    signature = inspect.signature(SIGNER.sign_candidate_attestation)
    assert "purpose" not in signature.parameters, (
        "sign_candidate_attestation must not accept a purpose parameter — the "
        "candidate purpose is a hardcoded import, which is what makes it "
        "impossible for a caller to redirect this producer at the "
        "installed-host role"
    )
