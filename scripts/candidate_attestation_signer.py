#!/usr/bin/env python3
"""Produce a signed ``TrustedHostAttestation.v2`` candidate envelope.

ADR-0070's 2026-09-08 amendment ("six admission decisions, none of them
implemented yet") rules that evidence subject is Foundation's own
``CandidateArtifact.v1`` (decision 1), that the exact built bytes are signed
once and never recomputed from a rebuild (decision 2), and that the signing
identity is the protected Starter release-workflow identity, distinct from the
target-local host-attestation workload
(``packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation/trusted_host_source.py``
module docstring). This script is that producer.

It reads an already-committed-shape ``CandidateArtifact.v1`` receipt (the
schema ``scripts/foundation_candidate.py`` writes) and an Ed25519 private key
supplied as PEM bytes, and emits a ``TrustedHostAttestation.v2`` envelope whose
subject is a ``CandidateAttestationSubjectV2`` built from that exact receipt —
never from a rebuilt or recomputed digest.

## What this script is not

It does not build, sign, or read anything belonging to the installed-host
role. It imports nothing from a host-attestation module (there is none in this
package to import), accepts no ``--host-*`` argument, and can never construct
an ``InstalledHostAttestationSubjectV2`` or an envelope whose purpose is
``INSTALLED_OBSERVATION_PURPOSE`` — the purpose string is a hardcoded constant
imported from the package, not a CLI input. It performs no trust-root
registration: Control owns binding fingerprints, purposes and custody domains
(module docstring; ADR-0070's 2026-09-07 amendment).

It does not commit its own output to the repository. A human commits the
signed envelope (and the bound receipt) under ``docs/inventories/`` in a
follow-up, reviewed change, the same shape every existing
``foundation-candidate-<version>.json`` file already went through. This
script's ``sign`` command writes one file for a workflow-local path that
becomes a build artifact; nothing here touches version control.

## Writing the envelope: a thin adapter, not a second writer

This script does not canonicalize or atomically write the envelope itself.
``dotmac_deployment_foundation.attestation_store.write_attestation_record``
does — the package's own writer for ``TrustedHostAttestation.v2`` records,
which reuses ``lease.write_store_record_once``'s atomic-once mechanism rather
than a second, independently hand-rolled ``json.dumps(..., sort_keys=True)``.
`trusted_host_source.py` stays I/O-free by its own stated design; the writer
therefore lives in a sibling module, not there. This script's only remaining
job after signing is to hand the package the finished envelope.

## Custody

The private key is never a CLI literal or a repository value. ``sign`` reads
it from a file path (materialised by the calling workflow step from a
protected GitHub Environment secret whose canonical source is an OpenBao
pointer — see the workflow for the exact secret name and pointer) and the
file is expected to be removed by the caller once this process exits. Nothing
in this module writes the key material anywhere but into memory for the
duration of one signature.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)
from dotmac_deployment_foundation.attestation_store import write_attestation_record
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.trusted_host_source import (
    ATTESTATION_SCHEMA,
    CANDIDATE_ATTESTATION_PURPOSE,
    AttestationEnvelopeV2,
    CandidateAttestationSubjectV2,
)

CANDIDATE_ARTIFACT_SCHEMA = "CandidateArtifact.v1"


def _canonical_instant(value: datetime) -> str:
    """Fixed-width UTC RFC3339, matching ``trusted_host_source``'s
    ``_UTC_RFC3339`` regex exactly (no fractional seconds, always ``Z``)."""
    if value.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_candidate_artifact_receipt(path: Path) -> dict[str, Any]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("schema") != CANDIDATE_ARTIFACT_SCHEMA:
        raise SystemExit(
            f"{path}: not a {CANDIDATE_ARTIFACT_SCHEMA} receipt "
            f"(schema={receipt.get('schema')!r}). The candidate attestation "
            "subject binds against Foundation's own CandidateArtifact.v1 "
            "record, never a rebuilt or recomputed digest (ADR-0070 "
            "2026-09-08, decision 1)."
        )
    return receipt


def candidate_subject_from_receipt(
    receipt: dict[str, Any],
) -> CandidateAttestationSubjectV2:
    """Build the exact subject the verifier expects, from the exact receipt.

    Every field is read from the committed ``CandidateArtifact.v1`` receipt —
    none is recomputed by re-hashing a file, re-reading the wheel, or asking
    ``gh api`` again. Signing a recomputed value would open exactly the gap
    decision 2 forbids: the receipt is the frozen fact, and this function's
    only job is to reshape it into the subject the verifier parses.
    """
    return CandidateAttestationSubjectV2(
        package=str(receipt["facility"]),
        version=str(receipt["version"]),
        wheel_sha256=Digest.parse(
            f"sha256:{receipt['sha256']}", where="receipt.sha256"
        ),
        source_revision=str(receipt["source_sha"]),
        repository=str(receipt["repository"]),
        run_id=str(receipt["run_id"]),
        artifact_id=str(receipt["artifact_id"]),
    )


def _fingerprint(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def sign_candidate_attestation(
    *,
    receipt: dict[str, Any],
    private_key: Ed25519PrivateKey,
    issuer: str,
    key_id: str,
    custody_domain: str,
    trust_root_version: str,
    audience: str,
    issued_at: datetime,
    expires_at: datetime,
    observation_id: str | None = None,
) -> AttestationEnvelopeV2:
    """Produce one signed ``AttestationEnvelopeV2`` over the receipt's subject.

    Purpose is always ``CANDIDATE_ATTESTATION_PURPOSE`` — it is never a
    parameter, so there is no code path by which this function could emit an
    installed-host envelope.
    """
    subject = candidate_subject_from_receipt(receipt)
    public_key = private_key.public_key()
    fingerprint = _fingerprint(public_key)
    observation_id = observation_id or str(uuid.uuid4())

    unsigned = AttestationEnvelopeV2(
        schema=ATTESTATION_SCHEMA,
        purpose=CANDIDATE_ATTESTATION_PURPOSE,
        issuer=issuer,
        key_id=key_id,
        algorithm="ed25519",
        public_key_fingerprint=fingerprint,
        custody_domain=custody_domain,
        trust_root_version=trust_root_version,
        issued_at=_canonical_instant(issued_at),
        expires_at=_canonical_instant(expires_at),
        audience=audience,
        observation_id=observation_id,
        subject=subject.canonical_document(),
        # Placeholder: excluded from `signed_bytes()`, discarded below.
        signature="unsigned",
    )
    to_sign = unsigned.signed_bytes()
    raw_signature = private_key.sign(to_sign)
    signature = base64.b64encode(raw_signature).decode("ascii")
    return dataclasses.replace(unsigned, signature=signature)


def cmd_sign(args: argparse.Namespace) -> int:
    receipt = load_candidate_artifact_receipt(Path(args.receipt))
    key_bytes = Path(args.private_key_file).read_bytes()
    private_key = load_pem_private_key(key_bytes, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SystemExit(
            "the supplied private key is not Ed25519 — "
            f"{ATTESTATION_SCHEMA} declares algorithm=ed25519 and this "
            "producer signs no other algorithm"
        )
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=args.validity_minutes)
    envelope = sign_candidate_attestation(
        receipt=receipt,
        private_key=private_key,
        issuer=args.issuer,
        key_id=args.key_id,
        custody_domain=args.custody_domain,
        trust_root_version=args.trust_root_version,
        audience=args.audience,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    # The package owns this document's wire form and its atomic-once write —
    # see `attestation_store.py`. This script is a thin adapter over it: it
    # signs, and hands the package the exact envelope to publish.
    write_attestation_record(Path(args.out), envelope)
    print(
        f"signed {envelope.purpose} for {receipt['facility']} "
        f"{receipt['version']} (fingerprint {envelope.public_key_fingerprint})"
    )
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(
                "## Candidate attestation — signed, NOT admitted\n\n"
                "This envelope is verifier input only. No executor accepts it "
                "yet (ADR-0070, 2026-09-08 amendment, decision 5): the first "
                "admission occurs only inside the protected artifact-rehearsal "
                "workflow.\n\n"
                "| fact | value |\n|---|---|\n"
                f"| purpose | `{envelope.purpose}` |\n"
                f"| package | `{receipt['facility']}` |\n"
                f"| version | `{receipt['version']}` |\n"
                f"| fingerprint | `{envelope.public_key_fingerprint}` |\n"
                f"| observation_id | `{envelope.observation_id}` |\n"
                f"| issued_at | `{envelope.issued_at}` |\n"
                f"| expires_at | `{envelope.expires_at}` |\n"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sign = sub.add_parser(
        "sign", help="sign a CandidateArtifact.v1 receipt into a v2 envelope"
    )
    sign.set_defaults(handler=cmd_sign)
    sign.add_argument("--receipt", required=True)
    sign.add_argument("--private-key-file", required=True)
    sign.add_argument("--issuer", required=True)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--custody-domain", required=True)
    sign.add_argument("--trust-root-version", required=True)
    sign.add_argument("--audience", required=True)
    sign.add_argument("--validity-minutes", type=int, default=180)
    sign.add_argument("--out", required=True)
    sign.add_argument("--summary", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
