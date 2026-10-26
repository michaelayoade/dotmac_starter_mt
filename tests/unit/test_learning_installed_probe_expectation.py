"""The Learning installed-pair probe must name absolute root owners.

`scripts/verify_learning_installed.py` derives its source expectation from the
AST of `dotmac_learning/__init__.py` and later imports each recorded owner in
an isolated site-packages. Relative imports in that root therefore have to be
recorded as `dotmac_learning.*` owners: a bare owner (`bank_validation`) is
not importable outside the package and failed the hosted installed-pair proof
at PR #907 head 9d92e5c before any wheel check ran.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "verify_learning_installed.py"

_RELATIVE = {
    "BankDoc": ["dotmac_learning.bank_validation", "BankDoc"],
    "balance_targets": ["dotmac_learning.bank_validation", "balance_targets"],
    "propose_balance": ["dotmac_learning.bank_validation", "propose_balance"],
    "decoded_doc_from_db": ["dotmac_learning.curriculum", "decoded_doc_from_db"],
    "BankDocumentHeader": [
        "dotmac_learning.curriculum_contracts",
        "BankDocumentHeader",
    ],
}
_DUMMIES = [f"n{i}" for i in range(539 - 1 - len(_RELATIVE))]


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_learning_installed", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkout(tmp_path: Path, *, bank_line: str) -> Path:
    """A minimal checkout whose Learning root binds 539 exports."""
    root_init = tmp_path / "packages/dotmac-learning/src/dotmac_learning/__init__.py"
    root_init.parent.mkdir(parents=True)
    exports = ["__version__", *_RELATIVE, *_DUMMIES]
    root_init.write_text(
        "from dotmac_learning.owner import (\n"
        + "".join(f"    {name},\n" for name in _DUMMIES)
        + ")\n"
        + bank_line
        + "from .curriculum import decoded_doc_from_db\n"
        + "from .curriculum_contracts import BankDocumentHeader\n"
        + '__version__ = "0.0-test"\n'
        + f"__all__ = {exports!r}\n"
    )
    pyproject = tmp_path / "packages/dotmac-learning/pyproject.toml"
    pyproject.write_text('[tool.poetry.dependencies]\ndotmac-kernel = ">=0.1.0a106"\n')
    kernel_init = tmp_path / "packages/dotmac-kernel/src/dotmac_kernel/__init__.py"
    kernel_init.parent.mkdir(parents=True)
    kernel_init.write_text('__version__ = "0.1.0a999"\n')
    modules = tmp_path / ".github/release-modules.json"
    modules.parent.mkdir(parents=True)
    modules.write_text(
        json.dumps(
            {"modules": {"dotmac-learning": {"wheel_contents": {"required": []}}}}
        )
    )
    return tmp_path


def _expectation(probe, checkout: Path, tmp_path: Path) -> dict:
    destination = tmp_path / "expectation.json"
    probe.expectation(checkout, destination)
    return json.loads(destination.read_text())


@pytest.fixture()
def probe(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "0" * 40)
    monkeypatch.setenv("LEARNING_BASE_SHA", "1" * 40)
    return _load_module()


def test_relative_root_imports_record_absolute_owners(probe, tmp_path):
    checkout = _checkout(
        tmp_path,
        bank_line="from .bank_validation import (\n"
        "    BankDoc,\n    balance_targets,\n    propose_balance,\n)\n",
    )
    document = _expectation(probe, checkout, tmp_path)
    assert document["kernel_version"] == "0.1.0a999"
    for name, binding in _RELATIVE.items():
        assert document["bindings"][name] == binding
    owners = {owner for owner, _exported in document["bindings"].values()}
    assert all(owner.startswith("dotmac_learning") for owner in owners)


def test_bare_relative_root_import_records_the_package_itself(probe, tmp_path):
    checkout = _checkout(
        tmp_path,
        bank_line="from . import BankDoc\n"
        "from .bank_validation import balance_targets, propose_balance\n",
    )
    document = _expectation(probe, checkout, tmp_path)
    assert document["bindings"]["BankDoc"] == ["dotmac_learning", "BankDoc"]


def test_relative_root_import_escaping_the_package_refuses(probe, tmp_path):
    checkout = _checkout(
        tmp_path,
        bank_line="from ..bank_validation import (\n"
        "    BankDoc,\n    balance_targets,\n    propose_balance,\n)\n",
    )
    with pytest.raises(RuntimeError, match="escapes package"):
        probe.expectation(checkout, tmp_path / "expectation.json")
