"""Verify and execute one exact gate, propagating incompatibility as failure."""

from __future__ import annotations

import hashlib
import json
import os
import stat
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


class TrustedRunnerAcquisitionError(RuntimeError):
    """A required local artifact could not be captured safely."""


def _read_regular_file(
    path: Path, *, label: str, limit: int = 2 * 1024 * 1024
) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise TrustedRunnerAcquisitionError("this runner requires O_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
    except OSError as exc:
        raise TrustedRunnerAcquisitionError(f"{label} is unreadable: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TrustedRunnerAcquisitionError(f"{label} is not a regular file")
        if metadata.st_size > limit:
            raise TrustedRunnerAcquisitionError(
                f"{label} exceeds the {limit}-byte bound"
            )
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise TrustedRunnerAcquisitionError(
                    f"{label} exceeds the {limit}-byte bound"
                )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_source_module(name: str, source: bytes, *, filename: str) -> ModuleType:
    """Compile the captured source buffer; never consult a path or pyc cache."""

    module = ModuleType(name)
    module.__file__ = filename
    module.__package__ = name.rpartition(".")[0]
    sys.modules[name] = module
    try:
        code = compile(source, filename, "exec", dont_inherit=True)
        exec(code, module.__dict__)  # noqa: S102 - source buffer is SHA-pinned
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _load_verifier() -> ModuleType:
    """Load the sibling verifier by path, never via PYTHONPATH resolution."""

    path = ACTION_ROOT / "verify.py"
    return _load_source_module(
        "_trusted_composition_source_verifier",
        _read_regular_file(path, label="pinned verifier"),
        filename=str(path),
    )


def _workspace() -> Path:
    raw = os.environ.get("GITHUB_WORKSPACE")
    if not raw:
        raise TrustedRunnerAcquisitionError("GITHUB_WORKSPACE is unset")
    workspace = Path(raw).resolve()
    if not workspace.is_dir():
        raise TrustedRunnerAcquisitionError(
            f"GITHUB_WORKSPACE is not a directory: {workspace}"
        )
    return workspace


def _require_sha256(content: bytes, expected: str, *, label: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise TrustedRunnerError(
            f"{label} bytes are not the pinned candidate: expected {expected}, "
            f"got {actual}"
        )


def _strict_json(content: bytes) -> Mapping[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise TrustedRunnerError(f"bindings contain duplicate field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(content, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedRunnerError(f"bindings are not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise TrustedRunnerError("bindings root is not an object")
    return value


def _require_exact_bindings(content: bytes) -> None:
    document = _strict_json(content)
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


def _load_verified_gate(
    workspace: Path,
    *,
    helper_sources: Mapping[str, bytes],
    gate_source: bytes,
) -> ModuleType:
    """Load only pinned files; candidate package initializers never execute."""

    tools_root = workspace / "tools"
    contract_root = tools_root / "composition_contract"
    _safe_package("tools", tools_root)
    _safe_package("tools.composition_contract", contract_root)
    for name, filename in _HELPER_MODULES:
        relative = f"tools/composition_contract/{filename}"
        _load_source_module(
            name,
            helper_sources[relative],
            filename=str(contract_root / filename),
        )
    return _load_source_module(
        "tools.composition_contract.compatibility_gate",
        gate_source,
        filename=str(workspace / GATE_PATH),
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


def _compatibility_exit_code(result: Any) -> int:
    """A proved incompatibility is a red gate, even when it is the expected fact."""

    return 0 if result.compatibility_satisfied else 1


def main() -> int:
    verifier: ModuleType | None = None
    gate: ModuleType | None = None
    try:
        workspace = _workspace()
        verifier = _load_verifier()
        helper_sources = verifier.read_verified_sources(workspace)
        gate_path = workspace / GATE_PATH
        bindings_path = workspace / BINDINGS_PATH
        gate_source = _read_regular_file(gate_path, label="compatibility gate")
        bindings_source = _read_regular_file(bindings_path, label="bindings")
        _require_sha256(gate_source, GATE_SHA256, label="compatibility gate")
        _require_sha256(bindings_source, BINDINGS_SHA256, label="bindings")
        _require_exact_bindings(bindings_source)
        gate = _load_verified_gate(
            workspace,
            helper_sources=helper_sources,
            gate_source=gate_source,
        )
        clones = {
            "academy": workspace / ".compat-gate-clones/dotmac_academy_app",
            "erp": workspace / ".compat-gate-clones/dotmac_erp",
            "sub": workspace / ".compat-gate-clones/dotmac_sub",
        }
        bindings = {
            product: gate.ProductBinding(product, revision)
            for product, revision in EXPECTED_REVISIONS.items()
        }
        result = gate.evaluate_gate(
            bindings,
            clones=clones,
            repository_root=workspace,
        )
        _require_expected_result(result)
    except TrustedRunnerAcquisitionError as exc:
        print(f"TRUSTED COMPATIBILITY ACQUISITION FAILED: {exc}")
        return 2
    except Exception as exc:
        if verifier is not None and isinstance(
            exc, verifier.TrustedSourceAcquisitionError
        ):
            print(f"TRUSTED COMPATIBILITY ACQUISITION FAILED: {exc}")
            return 2
        if gate is not None and isinstance(exc, gate.GateAcquisitionError):
            print(f"TRUSTED COMPATIBILITY ACQUISITION FAILED: {exc}")
            return 2
        print(f"TRUSTED COMPATIBILITY REFUSED: {exc}")
        return 1
    print(result.explain())
    return _compatibility_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
