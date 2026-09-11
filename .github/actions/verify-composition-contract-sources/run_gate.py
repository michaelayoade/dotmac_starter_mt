"""Verify and execute one exact compatibility gate without candidate imports."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Final

ACTION_ROOT: Final = Path(__file__).resolve().parent
TRUSTED_CONTRACT_REVISION: Final = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
GATE_PATH: Final = "tools/composition_contract/compatibility_gate.py"
BINDINGS_PATH: Final = "tests/architecture/compatibility_gate_bindings.json"
GATE_SHA256: Final = "959ea522a1420fcd8eff418a89942c3abbd3168f4eb0bf40186b07262478ad73"
BINDINGS_SHA256: Final = (
    "8467df2722106276159b97b7d2c4f4a9c86b25f9a9f8461e453911b5ff01e23e"
)
EXPECTED_REVISIONS: Final = {
    "academy": "ca1f9058a6483fe52207556fa2d17c56b1e237c7",
    "erp": "dca695a7d59179fe65577ba4e72a709ff0b32cde",
    "sub": "272a897b778899b110c5790fcaf43bbb54efe27c",
}
EXPECTED_STATUS: Final = {
    "academy": "satisfied",
    "erp": "deferred_runtime_debt",
    "sub": "satisfied",
}
EXPECTED_ERP_DEBT: Final = {
    ("packages/dotmac-files/src/dotmac_files/service.py", 1),
    ("packages/dotmac-tax/src/dotmac_tax/service.py", 5),
}
_HELPER_MODULES: Final = (
    ("tools.composition_contract.composition_schema", "composition_schema.py"),
    ("tools.composition_contract.observations", "observations.py"),
    ("tools.composition_contract.specs", "specs.py"),
)


class TrustedRunnerError(RuntimeError):
    """The pinned runner could not establish or execute its exact inputs."""


def _load_file_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise TrustedRunnerError(f"cannot create a loader for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _load_verifier() -> ModuleType:
    """Load the sibling verifier by path, never via PYTHONPATH resolution."""

    return _load_file_module(
        "_trusted_composition_source_verifier", ACTION_ROOT / "verify.py"
    )


def _workspace() -> Path:
    raw = os.environ.get("GITHUB_WORKSPACE")
    if not raw:
        raise TrustedRunnerError("GITHUB_WORKSPACE is unset")
    workspace = Path(raw).resolve()
    if not workspace.is_dir():
        raise TrustedRunnerError(f"GITHUB_WORKSPACE is not a directory: {workspace}")
    return workspace


def _require_sha256(path: Path, expected: str, *, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise TrustedRunnerError(f"{label} is unreadable: {exc}") from exc
    if actual != expected:
        raise TrustedRunnerError(
            f"{label} bytes are not the pinned candidate: expected {expected}, "
            f"got {actual}"
        )


def _strict_json(path: Path) -> Mapping[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise TrustedRunnerError(f"bindings contain duplicate field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedRunnerError(f"bindings are not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise TrustedRunnerError("bindings root is not an object")
    return value


def _require_exact_bindings(path: Path) -> None:
    document = _strict_json(path)
    if set(document) != {"schema", "bindings"}:
        raise TrustedRunnerError("bindings have an unexpected top-level shape")
    if document["schema"] != "kernel-composition-compatibility-bindings.v3":
        raise TrustedRunnerError("bindings schema is not v3")
    rows = document["bindings"]
    if not isinstance(rows, Mapping) or set(rows) != set(EXPECTED_REVISIONS):
        raise TrustedRunnerError("bindings do not name exactly the three products")
    actual: dict[str, str] = {}
    for product in EXPECTED_REVISIONS:
        row = rows[product]
        if not isinstance(row, Mapping) or set(row) != {"revision"}:
            raise TrustedRunnerError(f"{product}: binding row is not closed")
        revision = row["revision"]
        if not isinstance(revision, str):
            raise TrustedRunnerError(f"{product}: revision is not a string")
        actual[product] = revision
    if actual != EXPECTED_REVISIONS:
        raise TrustedRunnerError(f"bindings changed: {actual!r}")


def _safe_package(name: str, path: Path) -> None:
    package = ModuleType(name)
    package.__package__ = name
    package.__path__ = [str(path)]
    sys.modules[name] = package


def _load_verified_gate(workspace: Path) -> ModuleType:
    """Load only pinned files; candidate package initializers never execute."""

    tools_root = workspace / "tools"
    contract_root = tools_root / "composition_contract"
    _safe_package("tools", tools_root)
    _safe_package("tools.composition_contract", contract_root)
    for name, filename in _HELPER_MODULES:
        _load_file_module(name, contract_root / filename)
    return _load_file_module(
        "tools.composition_contract.compatibility_gate", workspace / GATE_PATH
    )


def _require_expected_result(result: Any) -> None:
    evaluations = tuple(result.evaluations)
    by_product = {item.product: item for item in evaluations}
    if set(by_product) != set(EXPECTED_STATUS):
        raise TrustedRunnerError(
            f"gate evaluated wrong product set: {sorted(by_product)!r}"
        )
    if not result.evidence_verified:
        raise TrustedRunnerError("gate did not verify all three product records")
    actual_status = {
        product: item.compatibility_status for product, item in by_product.items()
    }
    if actual_status != EXPECTED_STATUS:
        raise TrustedRunnerError(
            f"gate compatibility statuses changed: {actual_status!r}"
        )
    erp_debt = {
        (item.source_path, item.sites) for item in by_product["erp"].deferred_debt
    }
    if erp_debt != EXPECTED_ERP_DEBT:
        raise TrustedRunnerError(f"ERP deferred debt changed: {sorted(erp_debt)!r}")
    if any(
        item.runtime_exposure is None or item.runtime_exposure.unknown != 0
        for item in evaluations
    ):
        raise TrustedRunnerError("a product retains unknown runtime exposure")
    if result.compatibility_satisfied:
        raise TrustedRunnerError("the current gate must preserve reached ERP debt")
    if result.adoption_status != "not_evaluated":
        raise TrustedRunnerError("compatibility evaluation must not claim adoption")


def main() -> int:
    try:
        workspace = _workspace()
        verifier = _load_verifier()
        drift = verifier.find_source_drift(workspace)
        if drift:
            raise TrustedRunnerError(
                "contract helper bytes differ from "
                f"{TRUSTED_CONTRACT_REVISION}: {list(drift)!r}"
            )
        gate_path = workspace / GATE_PATH
        bindings_path = workspace / BINDINGS_PATH
        _require_sha256(gate_path, GATE_SHA256, label="compatibility gate")
        _require_sha256(bindings_path, BINDINGS_SHA256, label="bindings")
        _require_exact_bindings(bindings_path)
        gate = _load_verified_gate(workspace)
        clones = {
            "academy": workspace / ".compat-gate-clones/dotmac_academy_app",
            "erp": workspace / ".compat-gate-clones/dotmac_erp",
            "sub": workspace / ".compat-gate-clones/dotmac_sub",
        }
        result = gate.evaluate_gate(
            gate.load_bindings(bindings_path),
            clones=clones,
            repository_root=workspace,
        )
        _require_expected_result(result)
    except Exception as exc:
        print(f"TRUSTED COMPATIBILITY REFUSED: {exc}")
        return 1
    print(result.explain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
