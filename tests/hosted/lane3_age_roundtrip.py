"""Hosted-only real age transport qualification using disposable synthetic keys.

This establishes interoperability, not private-proof or rehearsal acceptance.
No plaintext archive or decrypted payload is written to disk.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def require_hosted() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("RUNNER_OS") != "Linux"
    ):
        raise SystemExit("REFUSED: real age qualification requires GitHub-hosted Linux")


# Refuse even discovery/import outside the dedicated hosted workload.
require_hosted()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import lane3_evidence_bundle as bundle  # noqa: E402
import lane3_evidence_finalization as final  # noqa: E402

AGE = "/usr/bin/age"
KEYGEN = "/usr/bin/age-keygen"
ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"}
PROBE = b'{"synthetic_probe":true}\n'
CALLS = b'{"synthetic_calls":[]}\n'


def safe_test(method):
    """Keep unexpected library/process exceptions from echoing captured values."""

    @functools.wraps(method)
    def wrapped(self):
        try:
            method(self)
        except Exception:
            raise AssertionError(
                "synthetic age transport qualification failed"
            ) from None

    return wrapped


def process(arguments: list[str], data: bytes | None = None):
    # Every argument comes from source constants or the disposable fixture.
    try:
        return subprocess.run(  # noqa: S603
            arguments,
            input=data,
            capture_output=True,
            shell=False,
            check=False,
            timeout=30,
            env=ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AssertionError("synthetic age process unavailable") from None


class AgeRoundtrip(unittest.TestCase):
    def setUp(self):
        try:
            self.temporary = tempfile.TemporaryDirectory(prefix="lane3-age-synthetic-")
            self.addCleanup(self.temporary.cleanup)
            self.root = Path(self.temporary.name)
            self.root.chmod(0o700)
            if stat.S_IMODE(self.root.stat().st_mode) != 0o700:
                raise ValueError
            self.key = self.root / "identity.txt"
            self.recipient = self.generate_identity(self.key)
            self.tool_digest = (
                "sha256:" + hashlib.sha256(Path(AGE).read_bytes()).hexdigest()
            )
            self.policy = self.root / "policy.json"
            self.write_policy()
            policy_patch = patch.object(final, "_POLICY", self.policy)
            policy_patch.start()
            self.addCleanup(policy_patch.stop)
            self.output = self.root / "evidence.age"
        except Exception:
            raise AssertionError("synthetic age fixture setup failed") from None

    def generate_identity(self, path: Path) -> str:
        generated = process([KEYGEN, "--output", str(path)])
        if generated.returncode != 0:
            raise AssertionError("synthetic identity generation failed")
        path.chmod(0o600)
        derived = process([KEYGEN, "-y", str(path)])
        if derived.returncode != 0:
            raise AssertionError("synthetic public recipient derivation failed")
        return derived.stdout.decode("ascii").strip()

    def write_policy(self, **changes):
        document = {
            "age_sha256": self.tool_digest,
            "recipient": self.recipient,
            "schema": "lane3.evidence-encryption-policy.v1",
            "state": "enrolled",
        }
        document.update(changes)
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        self.policy.write_bytes(canonical.encode())
        self.policy.chmod(0o600)

    def encrypt(self) -> bytes:
        return final.finalize_evidence(
            probe_evidence=PROBE,
            exposure_calls=CALLS,
            context=final.prepare_encryption(),
            output=self.output,
        )

    def decrypt(self, ciphertext: bytes, identity: Path):
        return process([AGE, "--decrypt", "--identity", str(identity)], ciphertext)

    def require_refusal(self, action, reason: str):
        try:
            action()
        except final.FinalizationRefused as error:
            self.assertTrue(str(error) == reason, "unexpected fixed refusal code")
        else:
            self.fail("expected transport refusal")

    @safe_test
    def test_real_roundtrip_exact_archive_and_ciphertext_binding(self):
        ciphertext = self.encrypt()
        readback = self.output.read_bytes()
        self.assertTrue(ciphertext == readback, "ciphertext readback mismatch")
        self.assertTrue(
            hashlib.sha256(ciphertext).digest() == hashlib.sha256(readback).digest(),
            "ciphertext digest binding mismatch",
        )
        self.assertTrue(
            stat.S_IMODE(self.output.stat().st_mode) == 0o600,
            "ciphertext output permissions mismatch",
        )
        decrypted = self.decrypt(ciphertext, self.key)
        self.assertTrue(decrypted.returncode == 0, "real age decryption refused")
        checked_manifest, checked_payloads = final.read_plaintext_evidence_archive(
            decrypted.stdout
        )
        self.assertTrue(
            checked_payloads
            == {"probe-evidence.json": PROBE, "exposure-calls.json": CALLS},
            "bounded archive intake payload mismatch",
        )
        self.assertTrue(decrypted.stdout[257:263] == b"ustar\0", "expected USTAR")
        with tarfile.open(fileobj=io.BytesIO(decrypted.stdout), mode="r:") as archive:
            members = archive.getmembers()
            self.assertTrue(
                [member.name for member in members]
                == ["manifest.json", "probe-evidence.json", "exposure-calls.json"],
                "archive member names or order mismatch",
            )
            self.assertTrue(
                all(
                    member.isfile()
                    and member.mode == 0o600
                    and member.mtime == member.uid == member.gid == 0
                    and member.uname == member.gname == ""
                    and not member.pax_headers
                    for member in members
                ),
                "archive metadata mismatch",
            )
            payloads = {
                member.name: archive.extractfile(member).read() for member in members
            }
        manifest = payloads.pop("manifest.json")
        self.assertTrue(
            (checked_manifest, checked_payloads) == (manifest, payloads),
            "bounded intake and independent archive inspection mismatch",
        )
        self.assertTrue(
            payloads == {"probe-evidence.json": PROBE, "exposure-calls.json": CALLS},
            "decrypted synthetic payload mismatch",
        )
        bundle.require_plaintext_bundle(manifest, payloads)
        self.assertTrue(
            bundle.parse_manifest(manifest)
            == {
                name: (hashlib.sha256(raw).hexdigest(), len(raw))
                for name, raw in payloads.items()
            },
            "manifest digest or byte-length join mismatch",
        )

    @safe_test
    def test_encrypted_body_corruption_refused(self):
        ciphertext = self.encrypt()
        # The final byte is encrypted body/authentication data, beyond the header.
        corrupted = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        self.assertTrue(
            self.decrypt(corrupted, self.key).returncode != 0,
            "real age accepted corrupted ciphertext",
        )

    @safe_test
    def test_independent_wrong_identity_refused(self):
        ciphertext = self.encrypt()
        wrong = self.root / "wrong-identity.txt"
        wrong_recipient = self.generate_identity(wrong)
        self.assertTrue(
            wrong_recipient != self.recipient, "identities must be independent"
        )
        self.assertTrue(
            self.decrypt(ciphertext, wrong).returncode != 0,
            "real age accepted an independent wrong identity",
        )

    @safe_test
    def test_wrong_measured_tool_pin_refused_before_output(self):
        changed = "0" if self.tool_digest[-1] != "0" else "1"
        self.write_policy(age_sha256=self.tool_digest[:-1] + changed)
        self.require_refusal(self.encrypt, "encryption-tool-pin-mismatch")
        self.assertTrue(not self.output.exists(), "pin refusal created ciphertext")

    @safe_test
    def test_malformed_recipient_refused_before_output(self):
        self.write_policy(recipient="age1" + "q" * 58)
        self.require_refusal(self.encrypt, "encryption-policy-invalid")
        self.assertTrue(
            not self.output.exists(), "recipient refusal created ciphertext"
        )

    @safe_test
    def test_exclusive_output_preserves_existing_ciphertext(self):
        ciphertext = self.encrypt()
        original = self.output.stat()
        self.require_refusal(self.encrypt, "encryption-output-exists")
        current = self.output.stat()
        self.assertTrue(
            (original.st_dev, original.st_ino) == (current.st_dev, current.st_ino),
            "exclusive output identity changed",
        )
        self.assertTrue(
            self.output.read_bytes() == ciphertext, "existing output changed"
        )


if __name__ == "__main__":
    unittest.main()
