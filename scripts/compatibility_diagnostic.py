"""Stdlib-only hosted measurement; candidate execution is never acceptance.

The temporary Git-backed view materializes the exact three 8b4 helper blobs.
Candidate package initializers and mutable helper files are never imported.
No dependency installation, product execution, or credential access is needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any

SEMANTIC_REVISION = "8b4b6d4b42e650c47fe4c04a679a5ccb51c4b2cd"
HELPERS = ("composition_schema", "observations", "specs")
GATE_PATH = "tools/composition_contract/compatibility_gate.py"
BINDINGS_PATH = "tests/architecture/compatibility_gate_bindings.json"
PRODUCT_DIRECTORIES = {
    "academy": "dotmac_academy_app",
    "erp": "dotmac_erp",
    "sub": "dotmac_sub",
}


def _git(repository: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"git {arguments[0]} failed: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return result.stdout


def _read_regular(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2 * 1024 * 1024:
            raise ValueError(f"bounded regular source required: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            source = handle.read(2 * 1024 * 1024 + 1)
        if len(source) > 2 * 1024 * 1024:
            raise ValueError(f"source exceeds bound: {path}")
        return source
    finally:
        os.close(descriptor)


def _source_module(name: str, source: bytes, path: Path) -> ModuleType:
    module = ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition(".")[0]
    sys.modules[name] = module
    exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)  # noqa: S102 - explicitly untrusted diagnostic candidate, not acceptance
    return module


def _load_evaluator(
    view: Path, helper_sources: dict[str, bytes], gate_source: bytes
) -> ModuleType:
    for name, directory in (
        ("tools", view / "tools"),
        ("tools.composition_contract", view / "tools/composition_contract"),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        package = ModuleType(name)
        package.__package__ = name
        package.__path__ = [str(directory)]
        sys.modules[name] = package
    # Materialize every helper before any of them or the candidate is imported.
    for name, source in helper_sources.items():
        (view / f"tools/composition_contract/{name}.py").write_bytes(source)
    # Foreign-parser children reopen the gate through its __file__ path.
    (view / GATE_PATH).write_bytes(gate_source)
    for name in HELPERS:
        _source_module(
            f"tools.composition_contract.{name}",
            helper_sources[name],
            view / f"tools/composition_contract/{name}.py",
        )
    return _source_module(
        "tools.composition_contract.compatibility_gate", gate_source, view / GATE_PATH
    )


def _require_clone_revision(clone: Path, revision: str) -> None:
    actual = _git(clone, "rev-parse", "HEAD").strip().decode("ascii")
    if actual != revision:
        raise ValueError(
            f"{clone.name}: checkout {actual} differs from binding {revision}"
        )


def _evaluate(workspace: Path) -> tuple[Any, dict[str, object]]:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("diagnostic host must be Python 3.11")
    gate_source = _read_regular(workspace / GATE_PATH)
    bindings_source = _read_regular(workspace / BINDINGS_PATH)
    helper_sources = {
        name: _git(
            workspace,
            "show",
            f"{SEMANTIC_REVISION}:tools/composition_contract/{name}.py",
        )
        for name in HELPERS
    }
    facts: dict[str, object] = {
        "candidate_revision": _git(workspace, "rev-parse", "HEAD").strip().decode(),
        "gate_sha256": hashlib.sha256(gate_source).hexdigest(),
        "bindings_sha256": hashlib.sha256(bindings_source).hexdigest(),
        "semantic_revision": SEMANTIC_REVISION,
        "host_python": sys.version,
        "semantic_sha256": {
            name: hashlib.sha256(source).hexdigest()
            for name, source in helper_sources.items()
        },
    }
    with tempfile.TemporaryDirectory(prefix="compatibility-diagnostic-") as raw:
        view = Path(raw)
        git_directory = (
            _git(workspace, "rev-parse", "--absolute-git-dir").decode().strip()
        )
        (view / ".git").write_text(f"gitdir: {git_directory}\n", encoding="utf-8")
        gate = _load_evaluator(view, helper_sources, gate_source)
        if gate.TRUSTED_CONTRACT_REVISION != SEMANTIC_REVISION:
            raise ValueError("candidate gate changed its semantic observation contract")
        configuration = gate.parse_bindings(
            bindings_source, source="captured diagnostic"
        )
        facts["catalogue_revision"] = configuration.catalogue_revision
        facts["product_revisions"] = {
            product: binding.revision for product, binding in configuration.items()
        }
        clones = {
            product: workspace / ".compat-gate-clones" / directory
            for product, directory in PRODUCT_DIRECTORIES.items()
        }
        for product, clone in clones.items():
            _require_clone_revision(clone, configuration[product].revision)
        result = gate.evaluate_gate(configuration, clones=clones, repository_root=view)
    return result, facts


def _result_document(result: Any, facts: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "kernel-compatibility-diagnostic.v1",
        "diagnostic_only": True,
        "trusted_acceptance": False,
        "execution_status": "evaluated",
        "inputs": facts,
        "compatibility_satisfied": result.compatibility_satisfied,
        "evidence_verified": result.evidence_verified,
        "adoption_status": result.adoption_status,
        "kernel_subject": asdict(result.kernel_subject),
        "evaluations": [
            {
                **asdict(evaluation),
                "evidence_verified": evaluation.evidence_verified,
                "compatibility_satisfied": evaluation.compatibility_satisfied,
                "adoption_status": evaluation.adoption_status,
            }
            for evaluation in result.evaluations
        ],
        "explanation": result.explain(),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args(arguments)
    try:
        result, facts = _evaluate(options.workspace.resolve())
        document = _result_document(result, facts)
        status = 0 if result.compatibility_satisfied else 1
    except Exception as exc:
        document = {
            "schema": "kernel-compatibility-diagnostic.v1",
            "diagnostic_only": True,
            "trusted_acceptance": False,
            "execution_status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        status = 2
    serialized = json.dumps(document, sort_keys=True, indent=2) + "\n"
    options.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
