"""E9 entry point guards (S8b obligations): the presentation-key anchor and the
step-13 refresh digest.

Architect ruling, security-relevant. `verify_offline` verifies the presentation
signature under a key the caller supplies. That key must NOT be the E10.4
`key-init` printout (a value the host side produced and printed): it must be the
public key of Control's ACTIVE E10.6 credential, whose `presentation_key_id` and
`presentation_key_fingerprint` are recorded SEPARATELY (a second channel) in the
Gate 2 Knowledge record. Recording to Knowledge is an operator step, not code: the
caller passes the recorded values plus their provenance (record slug, version).

Before `verify_offline` receives any key this module refuses unless

* the presentation's key fingerprint equals the recorded fingerprint;
* the presentation statement's `key_id` equals the recorded key id;
* `host-admission.json`'s `file_sha256` equals the digest the launcher printed at
  step 13 (`present --refresh`).

Every refusal is a stable `offline-anchor-*` code, never supplied text. The
presentation parse itself is S2's (`read_host_admission`); nothing is restated.

The full `verify` subcommand (which needs the trusted plan source) is NOT built:
see the S8b report. `check-admission` runs the two guards alone.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys
from collections.abc import Sequence
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from lane3_host_admission import HostAdmissionDigest
from lane3_offline_orchestrator import (
    JoinRefused,
    OfflineVerification,
    read_host_admission,
    verify_offline,
)

__all__ = [
    "ANCHOR_SOURCE",
    "AnchorRefused",
    "PresentationKeyAnchor",
    "main",
    "require_admission_anchor",
    "verify_offline_anchored",
]

#: The only provenance class accepted: Control's ACTIVE E10.6 credential, read
#: from the Gate 2 record. There is deliberately no "key-init" source.
ANCHOR_SOURCE: Final = "control-e10.6-active-credential"
_FINGERPRINT: Final = re.compile(r"sha256:[0-9a-f]{64}")
_FILE_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_KEY_ID: Final = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_SLUG: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,127}")
_MAX_FILE: Final = 4 * 1024 * 1024


class AnchorRefused(ValueError):
    """An anchor guard failed. The message is a stable code; never any input."""


def _refuse(code: str) -> None:
    raise AnchorRefused(f"offline-anchor-{code}")


@dataclasses.dataclass(frozen=True, slots=True)
class PresentationKeyAnchor:
    """Control's ACTIVE E10.6 presentation credential, as recorded in Gate 2.

    `source` must be `ANCHOR_SOURCE`; `record_slug` and `record_version` say where
    the operator recorded it (carried into the report, never fetched here).
    """

    key_id: str
    key_fingerprint: str
    record_slug: str
    record_version: int
    source: str = ANCHOR_SOURCE


def _require_anchor(anchor: object) -> PresentationKeyAnchor:
    if type(anchor) is not PresentationKeyAnchor:
        _refuse("required")
    assert isinstance(anchor, PresentationKeyAnchor)
    if anchor.source != ANCHOR_SOURCE:
        _refuse("source-invalid")
    if (
        type(anchor.record_slug) is not str
        or not _SLUG.fullmatch(anchor.record_slug)
        or type(anchor.record_version) is not int
        or anchor.record_version < 1
    ):
        _refuse("provenance-invalid")
    if type(anchor.key_id) is not str or not _KEY_ID.fullmatch(anchor.key_id):
        _refuse("key-id-invalid")
    if type(anchor.key_fingerprint) is not str or not _FINGERPRINT.fullmatch(
        anchor.key_fingerprint
    ):
        _refuse("fingerprint-invalid")
    return anchor


def require_admission_anchor(
    host_admission_bytes: bytes,
    *,
    presentation_public_key_b64url: str,
    anchor: PresentationKeyAnchor,
    refresh_digest: str,
) -> HostAdmissionDigest:
    """Run the two guards over `host-admission.json`; return S2's digests.

    Order: provenance shape, refresh digest (cheap, before any parse), then the
    S2 parse under the candidate key, then fingerprint and key id against the
    recorded anchor. A key that does not match the anchor is refused even when it
    verifies the signature (the `key-init` printout does exactly that).
    """
    anchor = _require_anchor(anchor)
    if type(refresh_digest) is not str or not _FILE_SHA256.fullmatch(refresh_digest):
        _refuse("refresh-digest-invalid")
    if type(host_admission_bytes) is not bytes or not host_admission_bytes:
        _refuse("admission-required")
    admission = read_host_admission(
        host_admission_bytes,
        presentation_public_key_b64url=presentation_public_key_b64url,
    )
    if admission.file_sha256 != refresh_digest:
        _refuse("refresh-digest-mismatch")
    if admission.presentation_key_fingerprint != anchor.key_fingerprint:
        _refuse("key-fingerprint-mismatch")
    if admission.presentation_key_id != anchor.key_id:
        _refuse("key-id-mismatch")
    return admission


def verify_offline_anchored(
    *,
    anchor: PresentationKeyAnchor,
    refresh_digest: str,
    host_admission_bytes: bytes,
    presentation_public_key_b64url: str,
    **verify_kwargs: Any,
) -> OfflineVerification:
    """`verify_offline` behind both guards: the only entry point a CLI may use.

    `verify_kwargs` is everything `verify_offline` takes except the presentation
    key, which this function supplies only after the guards pass.
    """
    require_admission_anchor(
        host_admission_bytes,
        presentation_public_key_b64url=presentation_public_key_b64url,
        anchor=anchor,
        refresh_digest=refresh_digest,
    )
    return verify_offline(
        host_admission_bytes=host_admission_bytes,
        presentation_public_key_b64url=presentation_public_key_b64url,
        **verify_kwargs,
    )


# --- CLI ----------------------------------------------------------------------


def _read_file(path: str) -> bytes:
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        _refuse("path-not-absolute")
    try:
        if candidate.is_symlink() or not candidate.is_file():
            _refuse("path-invalid")
        data = candidate.read_bytes()
    except OSError:
        _refuse("path-unreadable")
    if not 0 < len(data) <= _MAX_FILE:
        _refuse("path-invalid")
    return data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lane3_offline_anchor")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "check-admission",
        help="run the presentation-key anchor and step-13 refresh-digest guards",
    )
    check.add_argument("--host-admission", required=True)
    check.add_argument(
        "--presentation-public-key",
        required=True,
        help="public key (unpadded base64url) of Control's ACTIVE E10.6 credential",
    )
    check.add_argument("--anchor-key-id", required=True)
    check.add_argument("--anchor-key-fingerprint", required=True)
    check.add_argument("--anchor-record-slug", required=True)
    check.add_argument("--anchor-record-version", required=True, type=int)
    check.add_argument(
        "--refresh-digest",
        required=True,
        help="file_sha256 printed by the launcher's `present --refresh` (step 13)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        anchor = PresentationKeyAnchor(
            key_id=args.anchor_key_id,
            key_fingerprint=args.anchor_key_fingerprint,
            record_slug=args.anchor_record_slug,
            record_version=args.anchor_record_version,
        )
        admission = require_admission_anchor(
            _read_file(args.host_admission),
            presentation_public_key_b64url=args.presentation_public_key,
            anchor=anchor,
            refresh_digest=args.refresh_digest,
        )
    except (AnchorRefused, JoinRefused) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    report = {
        "result": "ok",
        "file_sha256": admission.file_sha256,
        "attempt_id": admission.attempt_id,
        "presentation_key_id": admission.presentation_key_id,
        "presentation_key_fingerprint": admission.presentation_key_fingerprint,
        "anchor_source": anchor.source,
        "anchor_record_slug": anchor.record_slug,
        "anchor_record_version": anchor.record_version,
    }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
