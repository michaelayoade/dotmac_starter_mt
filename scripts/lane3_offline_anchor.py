"""E9 CLI (S8b): runs the anchor and refresh-digest checks over host-admission.

The checks themselves live in `verify_offline` (`lane3_offline_orchestrator`):
the presentation key must be Control's ACTIVE E10.6 credential (fingerprint,
key id and host id recorded in the Gate 2 record), never the E10.4 `key-init`
printout, and `host-admission.json`'s sha256 must equal the launcher's step-13
`present --refresh` digest. This module only wraps `check-admission`, which runs
S2's parse plus those same joins without the rest of E9. The full `verify`
subcommand is NOT built (it waits on the trusted plan source decision).

The record slug and version printed are the operator's CLAIM: nothing here reads
Knowledge.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from lane3_host_admission import Refused as HostAdmissionRefused
from lane3_host_admission import _read_regular
from lane3_offline_orchestrator import (
    AnchorRefused,
    JoinRefused,
    PresentationKeyAnchor,
    _check_anchor_shape,
    _join_anchor,
    read_host_admission,
)

__all__ = ["EXIT_REFUSED", "EXIT_USAGE", "main"]

EXIT_REFUSED: Final = 2
EXIT_USAGE: Final = 64


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        print("offline-cli-usage", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="lane3_offline_anchor")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "check-admission",
        help="run the presentation-key anchor and step-13 refresh-digest checks",
    )
    check.add_argument("--host-admission", required=True)
    check.add_argument(
        "--presentation-public-key",
        required=True,
        help="public key (unpadded base64url) of Control's ACTIVE E10.6 credential",
    )
    check.add_argument("--anchor-key-id", required=True)
    check.add_argument("--anchor-key-fingerprint", required=True)
    check.add_argument("--anchor-host-id", required=True)
    check.add_argument("--anchor-record-slug", required=True)
    check.add_argument("--anchor-record-version", required=True, type=int)
    check.add_argument(
        "--plan-host-id",
        required=True,
        help="the trusted plan's host_id the anchor is joined to",
    )
    check.add_argument(
        "--refresh-digest",
        required=True,
        help="file_sha256 printed by the launcher's `present --refresh` (step 13)",
    )
    return parser


class _PlanHost:
    """The only plan fact `check-admission` has: its host id."""

    def __init__(self, host_id: str) -> None:
        self.host_id = host_id


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        anchor = PresentationKeyAnchor(
            key_id=args.anchor_key_id,
            key_fingerprint=args.anchor_key_fingerprint,
            host_id=args.anchor_host_id,
            record_slug=args.anchor_record_slug,
            record_version=args.anchor_record_version,
        )
        anchor = _check_anchor_shape(anchor, args.refresh_digest)
        try:
            data = _read_regular(args.host_admission, "path-unreadable")
        except HostAdmissionRefused:
            raise AnchorRefused("offline-anchor-path-unreadable") from None
        admission = read_host_admission(
            data, presentation_public_key_b64url=args.presentation_public_key
        )
        _join_anchor(
            admission,
            anchor=anchor,
            refresh_digest=args.refresh_digest,
            plan=_PlanHost(args.plan_host_id),  # type: ignore[arg-type]
        )
    except JoinRefused as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_REFUSED
    report = {
        "result": "ok",
        "file_sha256": admission.file_sha256,
        "attempt_id": admission.attempt_id,
        "presentation_key_id": admission.presentation_key_id,
        "presentation_key_fingerprint": admission.presentation_key_fingerprint,
        "claimed_record_slug": anchor.record_slug,
        "claimed_record_version": anchor.record_version,
    }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
