#!/usr/bin/env python3
"""Prove `host_attester_install.py`'s install-and-observe path against REAL pip.

Run this with the interpreter of a clean virtualenv that ALREADY has
`dotmac-deployment-foundation` installed from the exact wheel under test —
`ci.yml`'s `host-attester-install-artifact` job does exactly that, modelled on
the `ui-artifact` job's shape: build the wheel, install it into a clean venv
(never the repo's own editable checkout), run this script with THAT venv's
python.

## Why this cannot be a unit test

The defect this proof exists to catch (a wheel filename missing the mandatory
`{python tag}-{abi tag}-{platform tag}` tags) is invisible to any test that
injects a fake installer: a fake installer never asks pip to parse a
filename, so it cannot refuse one. `tests/unit/test_host_attester_install.py`
proves this repository's OWN filename validator (`_canonical_wheel_filename`)
refuses the same shape — necessary, but self-referential: a validator that
agrees with itself proves nothing about whether real pip agrees with it. This
script is the second, independent oracle.

## The two properties, and why each is load-bearing

1. `assert_malformed_wheel_filename_rejected_by_real_pip` — takes a COPY of
   the real wheel, renames it to the exact shape the prior defect generated
   (`{distribution}-{version}.whl`, no tags), and shells `pip install
   --dry-run` at it DIRECTLY. This repository's own filename regex is never
   consulted in this function at all — the refusal is pip's own
   `InvalidWheelFilename`, or this check fails.

2. `assert_install_and_observe_round_trips_through_real_pip` — calls
   `download_verify_install_and_attest` for real: a genuine `pip install
   --no-deps --force-reinstall <wheel>` subprocess (`install_exact_wheel`'s
   real default runner, not the test injection seam), then a genuine
   `read_installed_artifact()` read of the PEP 610 `direct_url.json` that
   SAME `pip install` just wrote in THIS interpreter's own site-packages —
   the function accepts no `metadata`/`installed` argument at all, so the
   only way this can produce a matching signed observation is if the
   interpreter's own installer metadata agrees with the wheel's real sha256.
   `_assert_running_from_the_installed_artifact` guards against the proof
   silently measuring an editable checkout instead, the same failure mode
   `verify_ui_release_artifact.py` guards against for `dotmac-ui`.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import importlib.util
import subprocess
import sys
import sysconfig
from datetime import UTC, datetime
from pathlib import Path

#: Pip's own wording has drifted across versions; every variant observed in
#: this repository's supported pip range is listed so the check does not
#: depend on one exact phrasing, while still requiring the refusal to name
#: the FILENAME rather than some unrelated condition.
_INVALID_WHEEL_FILENAME_TOKENS = (
    "not a valid wheel filename",
    "invalid wheel filename",
    "invalidwheelfilename",
)


def _fail(message: str) -> None:
    raise SystemExit(f"host-attester install-and-observe proof FAILED: {message}")


def _load_scripts_module(name: str, scripts_dir: Path):
    spec = importlib.util.spec_from_file_location(name, scripts_dir / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_running_from_the_installed_artifact() -> None:
    """The subject must be the INSTALLED package, never a source checkout.

    `host_source.py` itself refuses to read an artifact digest from an
    editable install — this repository's own dev checkout is editable — so a
    proof run against it would not exercise the real path at all; it would
    fail for an unrelated reason and look like coverage.
    """
    import dotmac_deployment_foundation

    origin = getattr(dotmac_deployment_foundation, "__file__", None)
    if origin is None:
        _fail("dotmac_deployment_foundation has no __file__; it is not installed")
    package_dir = Path(str(origin)).resolve().parent
    candidates = {
        Path(path).resolve()
        for key in ("purelib", "platlib")
        if (path := sysconfig.get_paths().get(key))
    }
    if not candidates:
        _fail("this interpreter reports no site-packages path")
    if not any(package_dir.is_relative_to(root) for root in candidates):
        _fail(
            f"dotmac_deployment_foundation was imported from {package_dir}, "
            "which is not inside this interpreter's site-packages "
            f"({sorted(str(c) for c in candidates)}). This proof would be "
            "measuring a source checkout, never the installed artifact."
        )


def assert_malformed_wheel_filename_rejected_by_real_pip(
    wheel: Path, tmp_dir: Path
) -> None:
    """Real pip is the oracle here — this repository's own regex is not
    imported or called anywhere in this function."""
    parts = wheel.name.split("-")
    distribution, version = parts[0], parts[1]
    decoy = tmp_dir / f"{distribution}-{version}.whl"
    decoy.write_bytes(wheel.read_bytes())

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--dry-run", str(decoy)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        _fail(
            f"real pip ACCEPTED {decoy.name} — a filename missing the "
            "mandatory python/abi/platform tags must be refused, or this "
            "whole proof is checking nothing"
        )
    combined = (result.stdout + result.stderr).lower()
    if not any(token in combined for token in _INVALID_WHEEL_FILENAME_TOKENS):
        _fail(
            f"real pip refused {decoy.name}, but not for the expected "
            f"reason (stdout/stderr: {combined!r}). This must fail on the "
            "FILENAME, not on some unrelated condition"
        )
    print(f"  real pip OK        refused {decoy.name} (invalid wheel filename)")


def assert_install_and_observe_round_trips_through_real_pip(
    wheel: Path, scripts_dir: Path
) -> None:
    _load_scripts_module("host_attester", scripts_dir)
    install = _load_scripts_module("host_attester_install", scripts_dir)
    from dotmac_deployment_foundation.digest import Digest
    from dotmac_deployment_foundation.trusted_host_source import (
        CANDIDATE_ATTESTATION_PURPOSE,
        INSTALLED_OBSERVATION_PURPOSE,
        AttestationEnvelopeV2,
        AttestationTrustPolicy,
        AttestationTrustRootV2,
        CandidateAttestationSubjectV2,
    )

    wheel_digest = Digest.parse(
        hashlib.sha256(wheel.read_bytes()).hexdigest(), where=str(wheel)
    )
    distribution = "dotmac-deployment-foundation"
    version = wheel.name.split("-")[1]

    key = b"artifact-rehearsal-fake-candidate-key"
    fp = "sha256:" + hashlib.sha256(key).hexdigest()
    algorithm = "hmac-sha256-rehearsal-only"

    class _HmacVerifier:
        def verify(
            self, *, public_key: bytes, algorithm: str, message: bytes, signature: str
        ) -> bool:
            return hmac.compare_digest(
                signature, hmac.new(public_key, message, hashlib.sha256).hexdigest()
            )

    class _HmacSigner:
        def sign(self, *, algorithm: str, message: bytes) -> str:
            return hmac.new(key, message, hashlib.sha256).hexdigest()

    def _root(
        purpose: str, domain: str, root_fp: str, root_key: bytes
    ) -> AttestationTrustRootV2:
        return AttestationTrustRootV2(
            public_key_fingerprint=root_fp,
            public_key_base64=base64.b64encode(root_key).decode(),
            issuer="artifact-rehearsal",
            key_id="rehearsal-key",
            purpose=purpose,
            custody_domain=domain,
            algorithm=algorithm,
            trust_root_version="rehearsal-v1",
            not_before="2020-01-01T00:00:00Z",
            not_after="2100-01-01T00:00:00Z",
        )

    #: `AttestationTrustPolicy` requires a non-empty `installed_roots`
    #: collection even though `download_verify_install_and_attest` never
    #: verifies against it (it only SIGNS the installed half — see the
    #: function's own docstring). `AttestationTrustRootV2.__post_init__`
    #: still validates every root's fingerprint against its OWN key material
    #: unconditionally, whether or not that root is ever consulted — so this
    #: placeholder's fingerprint must be genuinely derived from its own key,
    #: not an arbitrary constant, or construction itself fails before either
    #: property under test gets to run.
    unused_host_key = b"unused-host-key-never-verified-against"
    unused_host_fp = "sha256:" + hashlib.sha256(unused_host_key).hexdigest()
    trust_policy = AttestationTrustPolicy(
        (_root(CANDIDATE_ATTESTATION_PURPOSE, "starter-release", fp, key),),
        (
            _root(
                INSTALLED_OBSERVATION_PURPOSE,
                "target-local-host",
                unused_host_fp,
                unused_host_key,
            ),
        ),
        "starter-release-workflow",
        "host:artifact-rehearsal",
    )

    subject = CandidateAttestationSubjectV2(
        distribution, version, wheel_digest, "a" * 40, "dotmac/foundation", "0", "0"
    )
    envelope = AttestationEnvelopeV2(
        "TrustedHostAttestation.v2",
        CANDIDATE_ATTESTATION_PURPOSE,
        "artifact-rehearsal",
        "rehearsal-key",
        algorithm,
        fp,
        "starter-release",
        "rehearsal-v1",
        "2026-01-01T00:00:00Z",
        "2100-01-01T00:00:00Z",
        "starter-release-workflow",
        "artifact-rehearsal-observation",
        subject.canonical_document(),
        "placeholder",
    )
    signature = hmac.new(key, envelope.signed_bytes(), hashlib.sha256).hexdigest()
    envelope = dataclasses.replace(envelope, signature=signature)

    # A SEPARATE destination directory from the wheel's own location.
    # `download_exact_wheel` opens `workdir / <filename>` with `open(...,
    # "wb")` — which TRUNCATES that path — after already opening the source
    # via `file://`. If the destination and the source resolved to the same
    # path, that truncation would corrupt the very bytes still being read
    # from underneath the open read handle, producing a spurious digest
    # mismatch that would misreport a harness bug as a pipeline defect. The
    # download itself is still real: a genuine `file://` fetch of the exact
    # candidate wheel, into a genuinely distinct file, still real pip
    # installed and re-observed below.
    download_workdir = wheel.parent / "download-proof"
    download_workdir.mkdir(exist_ok=True)

    now = datetime(2026, 6, 1, tzinfo=UTC)
    result = install.download_verify_install_and_attest(
        candidate=envelope,
        verifier=_HmacVerifier(),
        trust_policy=trust_policy,
        now=now,
        download_url=f"file://{wheel.resolve()}",
        workdir=download_workdir,
        expected_host_identity="host:artifact-rehearsal",
        signer=_HmacSigner(),
        issuer="artifact-rehearsal",
        key_id="rehearsal-key",
        algorithm=algorithm,
        public_key_fingerprint=unused_host_fp,
        custody_domain="target-local-host",
        trust_root_version="rehearsal-v1",
        observation_id="artifact-rehearsal-installed",
        issued_at=now,
        expires_at=datetime(2100, 1, 1, tzinfo=UTC),
    )

    installed_subject = result.subject_mapping()
    if installed_subject["wheel_sha256"] != str(wheel_digest):
        _fail(
            "the signed installed-observation names a different digest than "
            f"the real wheel's own sha256 ({wheel_digest}) — the pipeline "
            "did not actually observe what pip installed"
        )
    if installed_subject["package"] != distribution:
        _fail("the signed installed-observation names the wrong distribution")
    print(
        "  install-and-observe OK  real pip installed and re-observed "
        f"{distribution} {version} ({wheel_digest})"
    )


def main() -> int:
    if len(sys.argv) != 2:
        _fail("usage: verify_host_attester_install_artifact.py <path-to-wheel>")
    wheel = Path(sys.argv[1]).resolve()
    if not wheel.is_file():
        _fail(f"{wheel} is not a file")

    _assert_running_from_the_installed_artifact()

    scripts_dir = Path(__file__).resolve().parent
    tmp_dir = wheel.parent / "malformed-filename-proof"
    tmp_dir.mkdir(exist_ok=True)

    print(f"verifying host-attester install-and-observe against REAL pip: {wheel.name}")
    assert_malformed_wheel_filename_rejected_by_real_pip(wheel, tmp_dir)
    assert_install_and_observe_round_trips_through_real_pip(wheel, scripts_dir)
    print("host-attester install-and-observe proof OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
