#!/usr/bin/env python3
"""Fail closed when a PR or main push rewrites accepted module wheel evidence.

The caller supplies an immutable 40-hex base commit, never a moving ref. The
base ledger must exist and parse; initial ledger bootstrap needs its own
explicitly reviewed acceptance path, not an implicit empty-history fallback.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from write_release_record import (
    ReleaseRecordError,
    require_module_release_verifications_append_only,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = "docs/inventories/module-release-verifications.json"
LEGACY = "docs/inventories/module-release-legacy-unverified.json"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def check_append_only_base(base: str) -> None:
    if not SHA.fullmatch(base):
        raise ReleaseRecordError("base must be one immutable full commit SHA")
    shallow = _git("rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        raise ReleaseRecordError("module release base evidence is shallow/unavailable")
    resolved = _git("rev-parse", "--verify", f"{base}^{{commit}}")
    if resolved.returncode != 0 or resolved.stdout.strip() != base:
        raise ReleaseRecordError(f"module release base {base} is unresolvable")
    ancestry = _git("merge-base", "--is-ancestor", base, "HEAD")
    if ancestry.returncode != 0:
        raise ReleaseRecordError(f"module release base {base} is not a HEAD ancestor")
    before = _git("show", f"{base}:{LEDGER}")
    if before.returncode != 0:
        raise ReleaseRecordError(
            f"module release base {base} has no accepted {LEDGER}; "
            "bootstrap requires explicit review"
        )
    after = _git("show", f"HEAD:{LEDGER}")
    if after.returncode != 0:
        raise ReleaseRecordError(f"HEAD has no {LEDGER}")
    require_module_release_verifications_append_only(before.stdout, after.stdout)

    # The 120-row pre-cutover baseline is frozen against the immutable base —
    # not merely against a test constant a PR can edit in the same diff. A
    # test constant only proves "this PR agrees with itself"; comparing
    # against the base revision proves the file a reviewer already accepted
    # is unchanged, byte for byte, including whitespace-only reformatting
    # that would otherwise pass an equality-of-meaning check.
    legacy_before = _git("show", f"{base}:{LEGACY}")
    if legacy_before.returncode != 0:
        raise ReleaseRecordError(
            f"module release base {base} has no accepted {LEGACY}; "
            "bootstrap requires explicit review"
        )
    legacy_after = _git("show", f"HEAD:{LEGACY}")
    if legacy_after.returncode != 0:
        raise ReleaseRecordError(f"HEAD has no {LEGACY}")
    if legacy_after.stdout != legacy_before.stdout:
        raise ReleaseRecordError(
            f"{LEGACY} differs from its accepted base revision {base}; the "
            "pre-cutover unverified baseline is frozen and must stay "
            "byte-identical to the immutable base — it is never edited by a PR"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    try:
        check_append_only_base(args.base)
    except ReleaseRecordError as exc:
        print(f"module release append-only REFUSED: {exc}", file=sys.stderr)
        return 2
    print("module release verification history is append-only")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
