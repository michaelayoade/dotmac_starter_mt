"""Verify and execute one exact gate, propagating incompatibility as failure."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Final

ACTION_ROOT: Final = Path(__file__).resolve().parent
TRUSTED_CONTRACT_REVISION: Final = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
GATE_PATH: Final = "tools/composition_contract/compatibility_gate.py"
BINDINGS_PATH: Final = "tests/architecture/compatibility_gate_bindings.json"
GATE_SHA256: Final = "c37c85036e41f14b67786bcd5adc5e725b54e1944421425da7ac3ad8f958cfed"
BINDINGS_SHA256: Final = (
    "ab018998a626bed8540a68de50c42e2485a867a2fe1421a6225d123fede84121"
)
EXPECTED_REVISIONS: Final = {
    "academy": "ca1f9058a6483fe52207556fa2d17c56b1e237c7",
    "erp": "134abce8a74a205bd9636cafcf232178acb70874",
    "sub": "272a897b778899b110c5790fcaf43bbb54efe27c",
}
EXPECTED_STATUS: Final = {
    "academy": "satisfied",
    "erp": "satisfied",
    "sub": "satisfied",
}
EXPECTED_CATALOGUE_REVISION: Final = "8c12998b57a31b5cf4a3e296d092460f97e638ba"
EXPECTED_KERNEL_SUBJECT: Final = (
    "dotmac-kernel",
    "0.1.0a107",
    "dotmac-kernel-v0.1.0a107",
    EXPECTED_CATALOGUE_REVISION,
)
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


def _git_output(repository: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repository), *arguments],  # noqa: S607
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrustedRunnerAcquisitionError(
            f"could not acquire Git source: {exc}"
        ) from exc
    if result.returncode:
        raise TrustedRunnerAcquisitionError(
            f"git {arguments[0]} failed: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    if len(result.stdout) > 2 * 1024 * 1024:
        raise TrustedRunnerAcquisitionError("Git source exceeds the 2097152-byte bound")
    return result.stdout


def _materialize_verified_view(
    workspace: Path, view: Path, verifier: ModuleType, gate_source: bytes
) -> Mapping[str, bytes]:
    """Own a disposable Git-backed view of pinned semantics and captured gate."""

    raw_directory = _git_output(workspace, "rev-parse", "--absolute-git-dir")
    try:
        git_directory = raw_directory.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise TrustedRunnerAcquisitionError("Git directory is not UTF-8") from exc
    if not Path(git_directory).is_absolute() or any(
        character in git_directory for character in "\r\n"
    ):
        raise TrustedRunnerAcquisitionError("Git directory is not one absolute path")
    try:
        with (view / ".git").open("x", encoding="utf-8") as pointer:
            pointer.write(f"gitdir: {git_directory}\n")
        contract = view / "tools/composition_contract"
        contract.mkdir(parents=True)
        for _, filename in _HELPER_MODULES:
            relative = f"tools/composition_contract/{filename}"
            source = _git_output(
                workspace, "show", f"{TRUSTED_CONTRACT_REVISION}:{relative}"
            )
            with (view / relative).open("xb") as target:
                target.write(source)
        with (view / GATE_PATH).open("xb") as target:
            target.write(gate_source)
    except OSError as exc:
        raise TrustedRunnerAcquisitionError(
            f"could not materialize trusted view: {exc}"
        ) from exc
    _require_sha256(
        _read_regular_file(view / GATE_PATH, label="materialized compatibility gate"),
        GATE_SHA256,
        label="materialized compatibility gate",
    )
    return verifier.read_verified_sources(view)


def _require_exact_bindings(gate: ModuleType, content: bytes) -> Any:
    """Parse captured, SHA-verified bytes using the safely loaded pinned gate."""

    configuration = gate.parse_bindings(content)
    actual = {
        product: binding.revision for product, binding in configuration.bindings.items()
    }
    if actual != EXPECTED_REVISIONS:
        raise TrustedRunnerError(f"bindings changed: {actual!r}")
    if configuration.catalogue_revision != EXPECTED_CATALOGUE_REVISION:
        raise TrustedRunnerError("bindings changed the protected catalogue")
    subject = configuration.kernel_subject
    if (
        subject.distribution,
        subject.version,
        subject.tag,
        subject.peeled_commit,
    ) != EXPECTED_KERNEL_SUBJECT:
        raise TrustedRunnerError("bindings changed the prospective Kernel 107 subject")
    return configuration


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
    if len(evaluations) != len(EXPECTED_STATUS) or set(by_product) != set(
        EXPECTED_STATUS
    ):
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
    if any(item.deferred_debt for item in evaluations):
        raise TrustedRunnerError("a product retains deferred runtime debt")
    if any(
        item.runtime_exposure is None or item.runtime_exposure.unknown != 0
        for item in evaluations
    ):
        raise TrustedRunnerError("a product retains unknown runtime exposure")
    if not result.compatibility_satisfied:
        raise TrustedRunnerError(
            "gate did not establish compatibility for all products"
        )
    if result.adoption_status != "not_evaluated":
        raise TrustedRunnerError("compatibility evaluation must not claim adoption")


def _report_and_require_expected_result(result: Any) -> None:
    """Preserve the product findings before enforcing the closed expectation."""

    print(result.explain())
    _require_expected_result(result)


def _compatibility_exit_code(result: Any) -> int:
    """A proved incompatibility is a red gate, even when it is the expected fact."""

    return 0 if result.compatibility_satisfied else 1


def main() -> int:
    verifier: ModuleType | None = None
    gate: ModuleType | None = None
    try:
        workspace = _workspace()
        verifier = _load_verifier()
        gate_path = workspace / GATE_PATH
        bindings_path = workspace / BINDINGS_PATH
        gate_source = _read_regular_file(gate_path, label="compatibility gate")
        bindings_source = _read_regular_file(bindings_path, label="bindings")
        _require_sha256(gate_source, GATE_SHA256, label="compatibility gate")
        _require_sha256(bindings_source, BINDINGS_SHA256, label="bindings")
        with tempfile.TemporaryDirectory(prefix="trusted-composition-") as raw_view:
            view = Path(raw_view)
            helper_sources = _materialize_verified_view(
                workspace, view, verifier, gate_source
            )
            gate = _load_verified_gate(
                view,
                helper_sources=helper_sources,
                gate_source=gate_source,
            )
            clones = {
                "academy": workspace / ".compat-gate-clones/dotmac_academy_app",
                "erp": workspace / ".compat-gate-clones/dotmac_erp",
                "sub": workspace / ".compat-gate-clones/dotmac_sub",
            }
            bindings = _require_exact_bindings(gate, bindings_source)
            result = gate.evaluate_gate(
                bindings,
                clones=clones,
                repository_root=view,
            )
            _report_and_require_expected_result(result)
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
    return _compatibility_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
