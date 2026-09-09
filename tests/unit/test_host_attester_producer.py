"""`scripts/host_attester.py`: the target-host attester's pure, crypto-free
producer core.

Loaded the same way `test_release_facility_candidate_bytes.py` loads
`release_facility.py`: by file location, registered in `sys.modules` before
execution (the exact fix `dfaea9d7`'s sibling commit `d2a2e9e0` made to this
repository's own test suite — a module executed before registration cannot
resolve its own `from __future__ import annotations` postponed evaluation
correctly under some import orders).

Every guard below is shown refusing on a planted defect as well as accepting
correct input — a check that has never been observed refusing is
indistinguishable from one that cannot refuse.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import hmac
import importlib.util
import inspect
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.host_source import (
    CandidateReceipt,
    InstalledArtifact,
    require_host_source,
)
from dotmac_deployment_foundation.trusted_host_source import (
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationTrustRootV2,
    verify_attestation_pair,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"


def _load_host_attester():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "host_attester", SCRIPTS / "host_attester.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HOST_ATTESTER = _load_host_attester()

NOW = datetime(2026, 9, 8, tzinfo=UTC)
HOST_KEY = b"host-attester-incarnation-key"
CANDIDATE_KEY = b"starter-release-workflow-key"
HOST_FP = "sha256:" + hashlib.sha256(HOST_KEY).hexdigest()
CANDIDATE_FP = "sha256:" + hashlib.sha256(CANDIDATE_KEY).hexdigest()
ALGORITHM = "ed25519"


@dataclasses.dataclass(frozen=True)
class _HostSourceLookalike:
    """PLANT: every attribute `HostSource` has, but never proven by
    `require_host_source`."""

    distribution: str = "dotmac-deployment-foundation"
    version: str = "0.4.0a2"
    artifact_digest: Digest = dataclasses.field(
        default_factory=lambda: Digest.parse("a" * 64, where="test")
    )
    source_revision: str = "c" * 40
    repository: str = "dotmac/foundation"
    run_id: str = "123"
    artifact_id: str = "456"
    read_from: str = "fabricated"


class _HmacSigner:
    """A stand-in `HostAttesterSigner`. Not real Ed25519 — proves the ENVELOPE
    SHAPE and the pipeline wiring, never a specific algorithm's cryptography."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def sign(self, *, algorithm: str, message: bytes) -> str:
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def public_key_material(self) -> bytes:
        return self._key


class _HmacVerifier:
    keys: ClassVar[dict[str, bytes]] = {HOST_FP: HOST_KEY, CANDIDATE_FP: CANDIDATE_KEY}

    def verify(
        self, *, public_key: bytes, algorithm: str, message: bytes, signature: str
    ) -> bool:
        return hmac.compare_digest(
            signature, hmac.new(public_key, message, hashlib.sha256).hexdigest()
        )


def _root(fp: str, purpose: str, domain: str) -> AttestationTrustRootV2:
    import base64

    key = _HmacVerifier.keys[fp]
    return AttestationTrustRootV2(
        public_key_fingerprint=fp,
        public_key_base64=base64.b64encode(key).decode(),
        issuer="test-issuer",
        key_id="rotatable-label",
        purpose=purpose,
        custody_domain=domain,
        algorithm=ALGORITHM,
        trust_root_version="control-v1",
        not_before="2026-01-01T00:00:00Z",
        not_after="2027-01-01T00:00:00Z",
    )


def _policy(host_identity: str) -> AttestationTrustPolicy:
    return AttestationTrustPolicy(
        (_root(CANDIDATE_FP, CANDIDATE_ATTESTATION_PURPOSE, "starter-release"),),
        (_root(HOST_FP, INSTALLED_OBSERVATION_PURPOSE, "target-local-host"),),
        "starter-release-workflow",
        host_identity,
    )


def _host_source(
    *, distribution: str = "dotmac-deployment-foundation", version: str = "0.4.0a2"
):
    receipt = CandidateReceipt(
        facility=distribution,
        version=version,
        artifact_digest=Digest.parse("a" * 64, where="test"),
        source_revision="c" * 40,
        repository="dotmac/foundation",
        run_id="123",
        artifact_id="456",
    )
    installed = InstalledArtifact(
        distribution=distribution,
        version=version,
        artifact_digest=Digest.parse("a" * 64, where="test"),
        installed_content_digest=Digest.parse("b" * 64, where="test"),
        read_from="test direct_url.json archive_info.hashes.sha256",
    )
    return require_host_source(
        receipt=receipt,
        installed=installed,
        distribution=distribution,
        source_tree_digest=lambda: "d" * 64,
    )


def _candidate_envelope(host_source, *, host_identity: str):
    subject = HOST_ATTESTER.candidate_subject_from_host_source(host_source)
    envelope = AttestationEnvelopeV2(
        "TrustedHostAttestation.v2",
        CANDIDATE_ATTESTATION_PURPOSE,
        "test-issuer",
        "rotatable-label",
        ALGORITHM,
        CANDIDATE_FP,
        "starter-release",
        "control-v1",
        "2026-09-08T00:00:00Z",
        "2026-09-08T00:10:00Z",
        "starter-release-workflow",
        "candidate-observation",
        subject.canonical_document(),
        "placeholder",
    )
    signature = hmac.new(
        CANDIDATE_KEY, envelope.signed_bytes(), hashlib.sha256
    ).hexdigest()
    return dataclasses.replace(envelope, signature=signature)


def _build(host_source, *, signer=None):
    return HOST_ATTESTER.build_installed_attestation(
        host_source=host_source,
        expected_host_identity="host:canonical-a",
        signer=signer if signer is not None else _HmacSigner(HOST_KEY),
        issuer="test-issuer",
        key_id="rotatable-label",
        algorithm=ALGORITHM,
        custody_domain="target-local-host",
        trust_root_version="control-v1",
        observation_id="host-observation",
        issued_at=datetime(2026, 9, 8, tzinfo=UTC),
        expires_at=datetime(2026, 9, 8, 0, 10, tzinfo=UTC),
    )


# ── candidate_subject_from_host_source ──────────────────────────────────────


def test_candidate_subject_from_host_source_reflects_every_bound_field() -> None:
    host_source = _host_source()
    subject = HOST_ATTESTER.candidate_subject_from_host_source(host_source)
    assert subject.package == host_source.distribution
    assert subject.version == host_source.version
    assert subject.wheel_sha256 == host_source.artifact_digest
    assert subject.source_revision == host_source.source_revision
    assert subject.repository == host_source.repository
    assert subject.run_id == host_source.run_id
    assert subject.artifact_id == host_source.artifact_id


def test_candidate_subject_from_host_source_refuses_a_lookalike_object() -> None:
    """PLANT: an object with every attribute `HostSource` has, but that never
    passed through `require_host_source`."""
    with pytest.raises(SpecError, match="HostSource"):
        HOST_ATTESTER.candidate_subject_from_host_source(
            _HostSourceLookalike()  # type: ignore[arg-type]
        )


# ── build_installed_attestation: the full pipeline ──────────────────────────


def test_build_installed_attestation_pairs_with_a_genuine_candidate() -> None:
    host_source = _host_source()
    installed = _build(host_source)
    candidate = _candidate_envelope(host_source, host_identity="host:canonical-a")

    assert (
        verify_attestation_pair(
            candidate=candidate,
            installed=installed,
            verifier=_HmacVerifier(),
            trust_policy=_policy("host:canonical-a"),
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
        is None
    )


def test_mutated_subject_refusal_carries_the_signature_invalid_code() -> None:
    """NEGATIVE CONTROL: tamper with one field of the emitted envelope's
    subject WITHOUT re-signing, and confirm the pair is genuinely checked —
    not merely accepted because the shapes line up."""
    from dotmac_deployment_foundation.errors import PreconditionFailed

    host_source = _host_source()
    installed = _build(host_source)
    candidate = _candidate_envelope(host_source, host_identity="host:canonical-a")

    tampered_subject = dict(installed.subject_mapping())
    tampered_subject["version"] = "9.9.9-tampered"
    tampered = dataclasses.replace(installed, subject=tampered_subject)

    with pytest.raises(PreconditionFailed) as raised:
        verify_attestation_pair(
            candidate=candidate,
            installed=tampered,
            verifier=_HmacVerifier(),
            trust_policy=_policy("host:canonical-a"),
            expected_host_identity="host:canonical-a",
            now=NOW,
        )
    assert raised.value.code == "trusted-host-source-signature-invalid"


def test_non_host_source_refuses_regardless_of_signer_validity() -> None:
    """PLANT: a caller hands a HostSource lookalike AND a perfectly-working
    signer. The refusal must fire from the `host_source` type check alone —
    proving it does not depend on the signer being broken to be reached."""
    with pytest.raises(SpecError, match="HostSource"):
        HOST_ATTESTER.build_installed_attestation(
            host_source=_HostSourceLookalike(),  # type: ignore[arg-type]
            expected_host_identity="host:canonical-a",
            signer=_HmacSigner(HOST_KEY),  # a genuinely working signer
            issuer="test-issuer",
            key_id="rotatable-label",
            algorithm=ALGORITHM,
            custody_domain="target-local-host",
            trust_root_version="control-v1",
            observation_id="host-observation",
            issued_at=datetime(2026, 9, 8, tzinfo=UTC),
            expires_at=datetime(2026, 9, 8, 0, 10, tzinfo=UTC),
        )


def test_non_conforming_signer_refuses() -> None:
    host_source = _host_source()
    with pytest.raises(SpecError, match="HostAttesterSigner"):
        _build(host_source, signer=object())


# ── the declared fingerprint cannot disagree with the signing key ──────────


def test_the_declared_fingerprint_is_derived_from_the_signers_own_material() -> None:
    """`build_installed_attestation` takes no `public_key_fingerprint`
    parameter at all — there is no expression that could hand this function
    a fingerprint disagreeing with the key `signer.sign()` actually used, the
    same way there is no way to disagree with a value you were never given
    twice (see the module's `candidate_subject_from_host_source` docstring
    for the identical shape applied to the candidate subject).

    This proves the property BEHAVIOURALLY rather than merely by absence of
    a parameter: a signer holding a DIFFERENT key than the fixture default
    (`HOST_KEY`/`HOST_FP` used everywhere else in this file) still produces
    an envelope whose `public_key_fingerprint` is the sha256 of THAT
    signer's own `public_key_material()` — never `HOST_FP`, and never any
    other caller-supplied string, because there is nowhere for one to enter.
    A future refactor that reintroduced an independent
    `public_key_fingerprint` parameter (even one that merely shadowed the
    derived value with an unused default) would break this assertion the
    moment the two keys diverge, which is exactly what this test arranges."""
    host_source = _host_source()
    other_key = b"a-completely-different-incarnation-key-yzyz"
    other_fp = "sha256:" + hashlib.sha256(other_key).hexdigest()

    installed = _build(host_source, signer=_HmacSigner(other_key))

    assert installed.public_key_fingerprint == other_fp
    assert installed.public_key_fingerprint != HOST_FP


def test_a_signer_returning_non_bytes_material_is_refused() -> None:
    """PLANT: a signer whose `public_key_material()` cannot be hashed at
    all — proves the derivation is actually exercised, not skipped."""

    class _BrokenMaterialSigner:
        def sign(self, *, algorithm: str, message: bytes) -> str:
            return hmac.new(HOST_KEY, message, hashlib.sha256).hexdigest()

        def public_key_material(self) -> bytes:
            return "not-bytes"  # type: ignore[return-value]

    host_source = _host_source()
    with pytest.raises(SpecError, match="public_key_material"):
        _build(host_source, signer=_BrokenMaterialSigner())


# ── sensitivity proof: no parameter can carry an installed digest ──────────


def _signature_names(func) -> set[str]:
    return set(inspect.signature(func).parameters)


def _names_leak_an_installed_digest(names: set[str]) -> bool:
    """The check both the plant and the real function are run through."""
    forbidden_tokens = ("digest", "sha256", "wheel_sha", "artifact_digest")
    return any(
        token in name.lower()
        for name in names
        if name != "host_source"
        for token in forbidden_tokens
    )


def test_no_parameter_can_carry_an_installed_digest() -> None:
    real_names = _signature_names(HOST_ATTESTER.build_installed_attestation)
    assert "host_source" in real_names
    assert not _names_leak_an_installed_digest(real_names)


def test_the_leak_detector_itself_flags_a_planted_digest_parameter() -> None:
    """SENSITIVITY PLANT for the check above: a decoy function that DOES take
    an installed digest directly must be caught by the same detector, or the
    detector proves nothing about the real function passing."""

    def _decoy(*, host_source, installed_digest: str) -> None:  # pragma: no cover
        raise NotImplementedError

    assert _names_leak_an_installed_digest(_signature_names(_decoy))


# ── canonicalization: one writer, called, never restated ────────────────────


def test_host_attester_carries_no_canonicalizer_of_its_own() -> None:
    """`trusted_host_source.candidate_subject_digest` is the ONE computation
    that binds an installed observation to its candidate subject —
    `verify_attestation_pair` (imported unmodified from Foundation, above)
    uses this exact function too. A producer-side restatement of
    `json.dumps(doc, sort_keys=True, ...)` here would be a second answer to
    the one question the package already answers: two independent
    implementations of one canonicalization that could silently drift,
    exactly the shape `tests/unit/test_deployment_foundation_recovery_plan.
    py::test_the_TOOLING_canonicalizing_population_has_not_moved` exists to
    catch across every script in this directory. This module must contain no
    such call at all — it must call the package's own writer instead."""
    source = (SCRIPTS / "host_attester.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name == "dumps" and any(kw.arg == "sort_keys" for kw in node.keywords):
            pytest.fail(
                "host_attester.py canonicalizes for itself with "
                "json.dumps(..., sort_keys=...) — it must call "
                "trusted_host_source.candidate_subject_digest instead"
            )
    assert (
        "candidate_subject_digest(" in source
    ), "host_attester.py no longer calls the package's canonicalizer at all"


def test_the_canonicalizer_plant_would_have_been_caught() -> None:
    """SENSITIVITY PLANT: the exact shape this module used to carry — a
    local `json.dumps(doc, sort_keys=True, ...)` call — must be detected by
    the same walk, or the clean result above proves nothing about the
    module's actual history."""
    decoy = (
        "import json\n"
        "def f(d):\n"
        "    return json.dumps(d, sort_keys=True, separators=(',', ':'))\n"
    )
    tree = ast.parse(decoy)
    found = any(
        isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
        == "dumps"
        and any(kw.arg == "sort_keys" for kw in node.keywords)
        for node in ast.walk(tree)
    )
    assert found


def test_build_installed_attestation_calls_the_packages_digest_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the WIRING, not just the absence of a local implementation:
    `build_installed_attestation` must call `trusted_host_source.
    candidate_subject_digest`, not merely happen to produce the same bytes
    some other way."""
    import dotmac_deployment_foundation.trusted_host_source as trusted_host_source

    host_source = _host_source()
    calls: list[object] = []
    real = trusted_host_source.candidate_subject_digest

    def spy(candidate):
        calls.append(candidate)
        return real(candidate)

    monkeypatch.setattr(HOST_ATTESTER, "candidate_subject_digest", spy)
    _build(host_source)
    assert len(calls) == 1
