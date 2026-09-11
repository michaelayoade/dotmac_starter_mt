"""Verify trusted helper bytes, then consume them in the same process."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any, Final

ACTION_ROOT: Final = Path(__file__).resolve().parent
if str(ACTION_ROOT) not in sys.path:
    sys.path.insert(0, str(ACTION_ROOT))

from verify import (  # noqa: E402
    TRUSTED_CONTRACT_REVISION,
    TrustedSourceAcquisitionError,
    find_source_drift,
)

EXPECTED_STATUS: Final = {
    "academy": "satisfied",
    "erp": "deferred_runtime_debt",
    "sub": "satisfied",
}
EXPECTED_ERP_DEBT: Final = {
    ("packages/dotmac-files/src/dotmac_files/service.py", 1),
    ("packages/dotmac-tax/src/dotmac_tax/service.py", 5),
}


def _workspace() -> Path:
    raw = os.environ.get("GITHUB_WORKSPACE")
    if not raw:
        raise TrustedSourceAcquisitionError("GITHUB_WORKSPACE is unset")
    workspace = Path(raw).resolve()
    if not workspace.is_dir():
        raise TrustedSourceAcquisitionError(
            f"GITHUB_WORKSPACE is not a directory: {workspace}"
        )
    return workspace


def _require_expected_result(result: Any) -> None:
    evaluations = tuple(result.evaluations)
    by_product = {item.product: item for item in evaluations}
    if set(by_product) != set(EXPECTED_STATUS):
        raise RuntimeError(f"gate evaluated wrong product set: {sorted(by_product)!r}")
    if not result.evidence_verified:
        raise RuntimeError("gate did not verify all three product evidence records")
    actual_status = {
        product: item.compatibility_status for product, item in by_product.items()
    }
    if actual_status != EXPECTED_STATUS:
        raise RuntimeError(f"gate compatibility statuses changed: {actual_status!r}")
    erp_debt = {
        (item.source_path, item.sites) for item in by_product["erp"].deferred_debt
    }
    if erp_debt != EXPECTED_ERP_DEBT:
        raise RuntimeError(f"ERP deferred debt changed: {sorted(erp_debt)!r}")
    if any(
        item.runtime_exposure is None or item.runtime_exposure.unknown != 0
        for item in evaluations
    ):
        raise RuntimeError("a product retains unknown runtime exposure")
    if result.compatibility_satisfied:
        raise RuntimeError("the current three-product gate must preserve ERP debt")
    if result.adoption_status != "not_evaluated":
        raise RuntimeError("compatibility evaluation must not claim adoption")


def main() -> int:
    workspace = _workspace()
    try:
        drift = find_source_drift(workspace)
    except TrustedSourceAcquisitionError as exc:
        print(f"TRUSTED SOURCE ACQUISITION FAILED: {exc}")
        return 2
    if drift:
        print(
            "TRUSTED SOURCE REFUSED: current helper bytes differ from "
            f"{TRUSTED_CONTRACT_REVISION}: {list(drift)!r}"
        )
        return 1

    # No candidate-controlled step occurs between the byte proof above and
    # this import. The pinned action owns both verification and evaluation.
    if str(workspace) not in sys.path:
        sys.path.insert(0, str(workspace))
    gate = importlib.import_module("tools.composition_contract.compatibility_gate")
    clones = {
        "academy": workspace / ".compat-gate-clones/dotmac_academy_app",
        "erp": workspace / ".compat-gate-clones/dotmac_erp",
        "sub": workspace / ".compat-gate-clones/dotmac_sub",
    }
    result = gate.evaluate_gate(gate.load_bindings(), clones=clones)
    _require_expected_result(result)
    print(result.explain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
