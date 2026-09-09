"""`scripts/host_attester_install.py`: download, verify, install, observe, sign.

Three plants are required by the brief this lane repairs, and all three live
here because all three properties belong to this file:

1. **The canonical signed wheel filename is carried through, never rebuilt.**
   A filename reconstructed from `{facility}-{version}.whl` alone is missing
   the mandatory `{python tag}-{abi tag}-{platform tag}` wheel tags, and real
   pip refuses it outright (`InvalidWheelFilename`) before looking at a
   single byte. The prior test suite injected a fake installer, so pip's own
   filename validation never ran and this defect shipped undetected.
2. **No caller-supplied installed digest or metadata.** The install-and-
   observe pipeline is ONE function; there is no parameter anywhere on it
   through which a caller can hand in an `InstalledArtifact`, a digest, or a
   metadata reader in place of `read_installed_artifact` actually running
   against the interpreter that just installed the wheel.
3. **No caller-selected candidate trust root.** The authority that decides
   whether a downloaded file's digest is trusted comes from
   `verify_candidate_attestation`, authenticating a SIGNED envelope against a
   Control-resolved `AttestationTrustPolicy` — never a path, an environment
   variable, or a bare unverified value the invoker controls.

Loaded by file location, registered in `sys.modules` before execution — the
same idiom `test_release_facility_candidate_bytes.py` already uses, and the
same fix commit `d2a2e9e0` made after a helper named like a test was collected
by pytest's default `test*` glob and broke on missing fixtures. Every helper
here that is not itself a test keeps a leading underscore for exactly that
reason.

The full REAL-pip success path (a genuine wheel, a genuine `pip install`,
re-observed through the interpreter that ran it) is deliberately NOT
exercised here: this repository's own dev install of
`dotmac-deployment-foundation` is editable, which `host_source.py` itself
refuses to read an artifact digest from, and no test in this suite may shell
out to pip or create a venv. That proof lives in CI, modelled on the
artifact-rehearsal shape, per the brief's item 3.
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import hashlib
import hmac
import importlib.util
import inspect
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import dotmac_deployment_foundation.host_source as host_source_module
import pytest
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.host_source import InstalledArtifact
from dotmac_deployment_foundation.trusted_host_source import (
    CANDIDATE_ATTESTATION_PURPOSE,
    INSTALLED_OBSERVATION_PURPOSE,
    AttestationEnvelopeV2,
    AttestationTrustPolicy,
    AttestationTrustRootV2,
    CandidateAttestationSubjectV2,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# `host_attester` must be importable under its own name before
# `host_attester_install` executes its `from host_attester import ...`.
_load("host_attester")
INSTALL = _load("host_attester_install")

NOW = datetime(2026, 9, 9, tzinfo=UTC)
CANDIDATE_KEY = b"starter-release-workflow-key"
CANDIDATE_FP = "sha256:" + hashlib.sha256(CANDIDATE_KEY).hexdigest()
OUTSIDE_KEY = b"an-attacker-controlled-key"
OUTSIDE_FP = "sha256:" + hashlib.sha256(OUTSIDE_KEY).hexdigest()
#: The host incarnation key `_call` signs with — its trust-root fingerprint
#: below is DERIVED from these same bytes, never hand-written, so the two
#: values can never again be authored independently, which is exactly what
#: `AttestationTrustRootV2.__post_init__` refuses.
HOST_KEY = b"host-attester-incarnation-key"
HOST_FP = "sha256:" + hashlib.sha256(HOST_KEY).hexdigest()
ALGORITHM = "ed25519"

FACILITY = "dotmac-deployment-foundation"
VERSION = "0.4.0a2"
CORRECT_BYTES = b"correct candidate wheel bytes"
WRONG_BYTES = b"a substituted wheel with different bytes entirely"
CORRECT_DIGEST = Digest.parse(hashlib.sha256(CORRECT_BYTES).hexdigest(), where="test")

#: The real, tag-carrying filename the publisher produced.
VALID_FILENAME = f"{FACILITY.replace('-', '_')}-{VERSION}-py3-none-any.whl"
#: The exact invalid shape defect 1 generated — no python/abi/platform tags.
#: Real pip's `InvalidWheelFilename` refuses this before looking at bytes.
INVALID_LEGACY_FILENAME = f"{FACILITY.replace('-', '_')}-{VERSION}.whl"


class _HmacVerifier:
    keys: ClassVar[dict[str, bytes]] = {
        CANDIDATE_FP: CANDIDATE_KEY,
        OUTSIDE_FP: OUTSIDE_KEY,
    }

    def verify(
        self, *, public_key: bytes, algorithm: str, message: bytes, signature: str
    ) -> bool:
        return hmac.compare_digest(
            signature, hmac.new(public_key, message, hashlib.sha256).hexdigest()
        )


class _HmacSigner:
    def __init__(self, key: bytes) -> None:
        self._key = key

    def sign(self, *, algorithm: str, message: bytes) -> str:
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()


def _root(fp: str, key: bytes, purpose: str, domain: str) -> AttestationTrustRootV2:
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


def _policy() -> AttestationTrustPolicy:
    return AttestationTrustPolicy(
        (
            _root(
                CANDIDATE_FP,
                CANDIDATE_KEY,
                CANDIDATE_ATTESTATION_PURPOSE,
                "starter-release",
            ),
        ),
        (
            _root(
                HOST_FP,
                HOST_KEY,
                INSTALLED_OBSERVATION_PURPOSE,
                "target-local-host",
            ),
        ),
        "starter-release-workflow",
        "host:canonical-a",
    )


def _subject(*, wheel_sha256: Digest = CORRECT_DIGEST) -> CandidateAttestationSubjectV2:
    return CandidateAttestationSubjectV2(
        FACILITY,
        VERSION,
        wheel_sha256,
        "c" * 40,
        "dotmac/foundation",
        "123",
        "456",
    )


def _candidate_envelope(
    *,
    fp: str = CANDIDATE_FP,
    key: bytes = CANDIDATE_KEY,
    subject: CandidateAttestationSubjectV2 | None = None,
) -> AttestationEnvelopeV2:
    subject = subject if subject is not None else _subject()
    envelope = AttestationEnvelopeV2(
        "TrustedHostAttestation.v2",
        CANDIDATE_ATTESTATION_PURPOSE,
        "test-issuer",
        "rotatable-label",
        ALGORITHM,
        fp,
        "starter-release",
        "control-v1",
        "2026-09-09T00:00:00Z",
        "2026-09-09T00:10:00Z",
        "starter-release-workflow",
        "candidate-observation",
        subject.canonical_document(),
        "placeholder",
    )
    signature = hmac.new(key, envelope.signed_bytes(), hashlib.sha256).hexdigest()
    return dataclasses.replace(envelope, signature=signature)


def _call(
    *,
    download_url: str,
    workdir: Path,
    candidate: AttestationEnvelopeV2 | None = None,
    downloader=None,
    installer=None,
) -> AttestationEnvelopeV2:
    return INSTALL.download_verify_install_and_attest(
        candidate=candidate if candidate is not None else _candidate_envelope(),
        verifier=_HmacVerifier(),
        trust_policy=_policy(),
        now=NOW,
        download_url=download_url,
        workdir=workdir,
        expected_host_identity="host:canonical-a",
        signer=_HmacSigner(HOST_KEY),
        issuer="test-issuer",
        key_id="rotatable-label",
        algorithm=ALGORITHM,
        public_key_fingerprint=HOST_FP,
        custody_domain="target-local-host",
        trust_root_version="control-v1",
        observation_id="host-observation",
        issued_at=NOW,
        expires_at=NOW,
        downloader=downloader,
        installer=installer,
    )


# ── plant 1: the canonical wheel filename is carried through ───────────────


def test_the_old_reconstructed_filename_is_refused_by_the_new_filename_gate(
    tmp_path: Path,
) -> None:
    """PLANT: reproduce exactly the invalid shape the prior defect produced
    (`{facility}-{version}.whl`, no python/abi/platform tags — the one real
    pip's `InvalidWheelFilename` refuses). The new gate must catch it BEFORE
    a byte is downloaded, which is exactly what let it ship before: the old
    suite injected a fake installer, so nothing ever asked pip to parse the
    name."""

    def _never_called_download(url: str, dest: Path) -> Path:
        raise AssertionError("download must not run once the filename is invalid")

    def _never_called_install(wheel: Path, *, python=None) -> None:
        raise AssertionError("install must not run once the filename is invalid")

    with pytest.raises(PreconditionFailed) as raised:
        _call(
            download_url=f"https://example.invalid/{INVALID_LEGACY_FILENAME}",
            workdir=tmp_path,
            downloader=_never_called_download,
            installer=_never_called_install,
        )
    assert raised.value.code == INSTALL.INVALID_WHEEL_FILENAME


def test_the_canonical_filename_is_preserved_end_to_end(tmp_path: Path) -> None:
    """The real, tag-carrying filename the publisher produced is carried
    through to the local destination path handed to the installer — never
    regenerated from facility+version."""
    installer_calls: list[Path] = []

    def _fake_download(url: str, dest: Path) -> Path:
        dest.write_bytes(CORRECT_BYTES)
        return dest

    def _fake_install(wheel: Path, *, python=None) -> None:
        installer_calls.append(wheel)

    fake_installed = InstalledArtifact(
        distribution=FACILITY,
        version=VERSION,
        artifact_digest=CORRECT_DIGEST,
        installed_content_digest=Digest.parse("b" * 64, where="test"),
        read_from="test direct_url.json archive_info.hashes.sha256",
    )

    def _fake_read_installed_artifact(
        distribution: str, **_: object
    ) -> InstalledArtifact:
        assert distribution == FACILITY
        return fake_installed

    original = host_source_module.read_installed_artifact
    host_source_module.read_installed_artifact = _fake_read_installed_artifact
    try:
        envelope = _call(
            download_url=f"https://example.invalid/dist/{VALID_FILENAME}",
            workdir=tmp_path,
            downloader=_fake_download,
            installer=_fake_install,
        )
    finally:
        host_source_module.read_installed_artifact = original

    assert installer_calls == [tmp_path / VALID_FILENAME]
    assert isinstance(envelope, AttestationEnvelopeV2)
    assert envelope.purpose == INSTALLED_OBSERVATION_PURPOSE
    assert envelope.subject_mapping()["wheel_sha256"] == str(CORRECT_DIGEST)


def test_a_filename_naming_a_different_package_or_version_is_refused(
    tmp_path: Path,
) -> None:
    """PLANT: a syntactically valid wheel filename that names the WRONG
    package/version than the authenticated candidate subject. The download
    location is not the trust boundary, but a mismatch here means real pip
    would install something other than what was authenticated."""

    def _never_called_download(url: str, dest: Path) -> Path:
        raise AssertionError("download must not run on a filename/subject mismatch")

    with pytest.raises(PreconditionFailed) as raised:
        _call(
            download_url="https://example.invalid/dist/some_other_package-9.9.9-py3-none-any.whl",
            workdir=tmp_path,
            downloader=_never_called_download,
        )
    assert raised.value.code == INSTALL.INVALID_WHEEL_FILENAME


# ── plant 2: digest verified before install, using the AUTHENTICATED digest ─


def test_digest_mismatch_refuses_before_install(tmp_path: Path) -> None:
    installer_calls: list[Path] = []

    def _fake_download(url: str, dest: Path) -> Path:
        dest.write_bytes(WRONG_BYTES)  # a substituted wheel
        return dest

    def _fake_install(wheel: Path, *, python=None) -> None:
        installer_calls.append(wheel)

    with pytest.raises(PreconditionFailed) as raised:
        _call(
            download_url=f"https://example.invalid/dist/{VALID_FILENAME}",
            workdir=tmp_path,
            downloader=_fake_download,
            installer=_fake_install,
        )
    assert raised.value.code == INSTALL.DOWNLOAD_DIGEST_MISMATCH
    assert installer_calls == [], (
        "install must never run once the digest disagrees — the whole point "
        "of decision 3 is refusing BEFORE install, not after"
    )


# ── plant 3: no caller-selected candidate trust root ────────────────────────


def test_a_non_envelope_candidate_is_refused(tmp_path: Path) -> None:
    """PLANT: something other than an `AttestationEnvelopeV2` stands in for
    the candidate. `verify_candidate_attestation` itself refuses this; this
    proves the wiring reaches that refusal rather than swallowing it."""
    with pytest.raises(SpecError):
        _call(
            download_url=f"https://example.invalid/dist/{VALID_FILENAME}",
            workdir=tmp_path,
            candidate={"facility": FACILITY, "sha256": "a" * 64},  # type: ignore[arg-type]
        )


def test_a_key_outside_the_resolved_roots_is_refused(tmp_path: Path) -> None:
    """PLANT: a validly-shaped, validly-signed envelope, signed by a key that
    is simply not one of the Control-resolved candidate roots."""
    with pytest.raises(PreconditionFailed) as raised:
        _call(
            download_url=f"https://example.invalid/dist/{VALID_FILENAME}",
            workdir=tmp_path,
            candidate=_candidate_envelope(fp=OUTSIDE_FP, key=OUTSIDE_KEY),
        )
    assert raised.value.code == "trusted-host-source-key-not-trusted"


def _forbidden_trust_selectors(source: str) -> list[str]:
    """Every way a caller could be given a say in WHICH trust material is
    used. Checked structurally over the AST plus two textual greps ast
    cannot express as cleanly (module-level `__main__` guard, `os.environ`
    attribute access).
    """
    findings: list[str] = []
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            findings.append(f"reads an environment variable ({node.attr})")
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name == "add_argument":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        lowered = arg.value.lower()
                        if any(
                            token in lowered
                            for token in ("receipt", "candidate", "root", "trust")
                        ):
                            findings.append(
                                "defines a CLI option naming a trust input: "
                                f"{arg.value!r}"
                            )
        if isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
            ):
                findings.append(
                    'defines an `if __name__ == "__main__":` CLI entry point'
                )
    return findings


def test_host_attester_install_offers_no_caller_selected_trust_root() -> None:
    source = (SCRIPTS / "host_attester_install.py").read_text(encoding="utf-8")
    assert _forbidden_trust_selectors(source) == []


def test_the_trust_selector_detector_flags_an_environment_variable_plant() -> None:
    """SENSITIVITY PLANT: a decoy source that reads
    `os.environ["CANDIDATE_RECEIPT_PATH"]` must be caught, or the clean result
    above proves nothing."""
    decoy = (
        "import os\n"
        "def resolve_receipt():\n"
        "    return os.environ['CANDIDATE_RECEIPT_PATH']\n"
    )
    assert _forbidden_trust_selectors(decoy) != []


def test_the_trust_selector_detector_flags_a_cli_receipt_path_plant() -> None:
    """SENSITIVITY PLANT: a decoy source with an `argparse` option naming a
    receipt path must be caught."""
    decoy = (
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--receipt-path')\n"
    )
    assert _forbidden_trust_selectors(decoy) != []


def test_the_trust_selector_detector_flags_a_bare_main_entry_point_plant() -> None:
    """SENSITIVITY PLANT: a decoy `if __name__ == "__main__":` block must be
    caught, even with no argument naming a trust input directly — a CLI
    entry point is itself the shape this module refuses to offer."""
    decoy = 'if __name__ == "__main__":\n    pass\n'
    assert _forbidden_trust_selectors(decoy) != []


# ── sensitivity proof: no parameter can carry an installed digest/metadata ──


def _signature_names(func) -> set[str]:
    return set(inspect.signature(func).parameters)


def _names_leak_a_substitutable_observation(names: set[str]) -> bool:
    """The check both the plant and the real function are run through."""
    forbidden_tokens = ("installed", "metadata", "digest", "sha256")
    allowed = {"candidate"}  # names a trust INPUT, not an installed observation
    return any(
        token in name.lower()
        for name in names
        if name not in allowed
        for token in forbidden_tokens
    )


def test_download_verify_install_and_attest_offers_no_installed_substitution() -> None:
    real_names = _signature_names(INSTALL.download_verify_install_and_attest)
    assert not _names_leak_a_substitutable_observation(real_names)
    assert "installed" not in real_names
    assert "metadata" not in real_names


def test_the_substitution_detector_flags_a_planted_installed_parameter() -> None:
    """SENSITIVITY PLANT for the check above: a decoy function that DOES take
    an installed artifact/digest directly must be caught by the same
    detector, or the detector proves nothing about the real function
    passing."""

    def _decoy(*, candidate, installed=None) -> None:  # pragma: no cover
        raise NotImplementedError

    assert _names_leak_a_substitutable_observation(_signature_names(_decoy))


def test_read_installed_artifact_is_called_with_no_metadata_argument(
    tmp_path: Path,
) -> None:
    """The re-observation step calls `read_installed_artifact` with no
    `metadata` keyword at all — proving there is no seam through which even
    an in-process caller of the underlying module could substitute an
    answer from THIS module's own call site."""
    calls: list[dict[str, object]] = []

    def _fake_download(url: str, dest: Path) -> Path:
        dest.write_bytes(CORRECT_BYTES)
        return dest

    def _fake_install(wheel: Path, *, python=None) -> None:
        pass

    def _fake_read_installed_artifact(
        distribution: str, **kwargs: object
    ) -> InstalledArtifact:
        calls.append({"distribution": distribution, **kwargs})
        return InstalledArtifact(
            distribution=distribution,
            version=VERSION,
            artifact_digest=CORRECT_DIGEST,
            installed_content_digest=Digest.parse("b" * 64, where="test"),
            read_from="test direct_url.json archive_info.hashes.sha256",
        )

    original = host_source_module.read_installed_artifact
    host_source_module.read_installed_artifact = _fake_read_installed_artifact
    try:
        _call(
            download_url=f"https://example.invalid/dist/{VALID_FILENAME}",
            workdir=tmp_path,
            downloader=_fake_download,
            installer=_fake_install,
        )
    finally:
        host_source_module.read_installed_artifact = original

    assert calls == [{"distribution": FACILITY}]
