"""CLI for the SHA-pinned product observation verification action."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Executing this file by its action-relative path makes its directory, rather
# than the checked-out contract root, sys.path[0]. Resolve imports from the
# file's immutable location, never from the caller's working directory.
CONTRACT_ROOT = Path(__file__).resolve().parents[2]
if str(CONTRACT_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTRACT_ROOT))

from tools.composition_contract.observations import (  # noqa: E402
    CANONICAL_RECORD_PATH,
    ObservationAcquisitionError,
    ObservationRefusal,
    load_and_verify_product_checkout_envelope,
)
from tools.composition_contract.specs import PRODUCT_OBSERVATION_SPECS  # noqa: E402

EXPECTED_ACTION_REPOSITORY = "michaelayoade/dotmac_starter_mt"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True, choices=PRODUCT_OBSERVATION_SPECS)
    parser.add_argument("--record-path", default=CANONICAL_RECORD_PATH)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--action-repository", required=True)
    parser.add_argument("--action-ref", required=True)
    parser.add_argument("--caller-repository", required=True)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    spec = PRODUCT_OBSERVATION_SPECS[arguments.product]
    if arguments.action_repository != EXPECTED_ACTION_REPOSITORY:
        raise ObservationRefusal(
            "composition verification must execute from "
            f"{EXPECTED_ACTION_REPOSITORY!r}, got {arguments.action_repository!r}"
        )
    if not _COMMIT.fullmatch(arguments.action_ref):
        raise ObservationRefusal(
            "composition verification action must be pinned by an exact "
            "40-character lowercase commit"
        )
    expected_caller = f"michaelayoade/{spec.repository}"
    if arguments.caller_repository != expected_caller:
        raise ObservationRefusal(
            f"product key {arguments.product!r} belongs to {expected_caller!r}, "
            f"not {arguments.caller_repository!r}"
        )
    if arguments.record_path != CANONICAL_RECORD_PATH:
        raise ObservationRefusal(f"record path is fixed at {CANONICAL_RECORD_PATH!r}")
    verified = load_and_verify_product_checkout_envelope(
        spec=spec,
        product_clone=arguments.workspace,
        trusted_contract_revision=arguments.action_ref,
        record_path=CANONICAL_RECORD_PATH,
    )
    print(
        "composition observations verified: "
        f"product={verified.product_id} "
        f"revision={verified.product_revision} "
        f"observations={len(verified.observations)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ObservationRefusal as exc:
        print(f"composition observation refusal: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except ObservationAcquisitionError as exc:
        print(f"composition observation acquisition failure: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
