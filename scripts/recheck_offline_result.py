"""Re-authenticate the build-selected public proof at the publication boundary.

Expected coordinates come only from the fixed build job outputs. This does
not reselect rehearsal API runs; it rechecks current policy and expiry for
that authenticated decision. No candidate SDK or private data is imported.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from lane3_offline_result import (
    OfflineResultCoordinates,
    OfflineResultRefused,
    OfflineResultUnavailable,
)
from require_rehearsal import _require_authenticated_offline_result

EXPECTED_ENV = {
    "repository_id": "FOUNDATION_EXPECTED_REPOSITORY_ID",
    "run_id": "FOUNDATION_EXPECTED_RUN_ID",
    "run_attempt": "FOUNDATION_EXPECTED_RUN_ATTEMPT",
    "receipt_digest": "FOUNDATION_EXPECTED_RECEIPT_DIGEST",
    "ciphertext_digest": "FOUNDATION_EXPECTED_CIPHERTEXT_DIGEST",
    "candidate_artifact_digest": "FOUNDATION_EXPECTED_CANDIDATE_ARTIFACT_DIGEST",
    "release_revision": "FOUNDATION_EXPECTED_RELEASE_REVISION",
}


def expected_coordinates() -> OfflineResultCoordinates:
    values = {field: os.environ.get(name, "") for field, name in EXPECTED_ENV.items()}
    if not all(values.values()):
        raise OfflineResultUnavailable("offline_result.expected_unavailable")
    numbers: dict[str, int] = {}
    for field in ("repository_id", "run_id", "run_attempt"):
        raw = values[field]
        try:
            integer = int(raw)
        except ValueError:
            raise OfflineResultRefused("offline_result.expected_invalid") from None
        if not 1 <= integer <= 2**63 - 1 or str(integer) != raw:
            raise OfflineResultRefused("offline_result.expected_invalid")
        numbers[field] = integer
    revision = values["release_revision"]
    if (
        len(revision) != 40
        or any(character not in "0123456789abcdef" for character in revision)
        or revision != os.environ.get("GITHUB_SHA", "")
    ):
        raise OfflineResultRefused("offline_result.expected_revision")
    return OfflineResultCoordinates(
        repository_id=numbers["repository_id"],
        run_id=numbers["run_id"],
        run_attempt=numbers["run_attempt"],
        receipt_digest=values["receipt_digest"],
        ciphertext_digest=values["ciphertext_digest"],
        candidate_artifact_digest=values["candidate_artifact_digest"],
        release_revision=revision,
    )


def main() -> int:
    try:
        _require_authenticated_offline_result(expected_coordinates())
    except OfflineResultRefused:
        print("REFUSED: offline_result_recheck_refused", file=sys.stderr)
        return 1
    except Exception:
        print("INDETERMINATE: offline_result_recheck_unavailable", file=sys.stderr)
        return 2
    print("offline_result_recheck=verified")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
