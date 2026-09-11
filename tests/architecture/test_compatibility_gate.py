"""Slice 4: verified product observations feed one refusing all-of gate."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tools.composition_contract import compatibility_gate as gate
from tools.composition_contract.composition_schema import (
    CompositionCoverageReport,
    RuntimeExposureReport,
)


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *arguments],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Gate Test")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _empty_coverage() -> CompositionCoverageReport:
    return CompositionCoverageReport(0, 0, 0, 0, 0, 0)


def _empty_exposure() -> RuntimeExposureReport:
    return RuntimeExposureReport(0, 0, 0)


def test_checked_in_bindings_are_closed_v3_coordinates() -> None:
    bindings = gate.load_bindings()

    assert set(bindings) == set(gate.PRODUCTS)
    assert all(re.fullmatch(r"[0-9a-f]{40}", row.revision) for row in bindings.values())
    assert {product: row.revision for product, row in bindings.items()} == {
        "academy": "a10a995163a4350c578abad7905e7da791d47891",
        "erp": "34041c718be3c555cfa1f77ba1dd29697da36b7b",
        "sub": "0553186b60f045ee350b5fe412a4b82231f71363",
    }


@pytest.mark.parametrize("revision", ["main", "A" * 40, "a" * 39, 7, None])
def test_binding_refuses_anything_except_an_immutable_lowercase_commit(
    revision: object,
) -> None:
    with pytest.raises(gate.GateConfigurationError, match="immutable lowercase"):
        gate.ProductBinding("erp", revision)  # type: ignore[arg-type]


def test_binding_loader_refuses_old_shape_extra_fields_and_duplicate_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bindings.json"
    path.write_text('{"schema":"old","bindings":{}}')
    with pytest.raises(gate.GateConfigurationError, match="schema must be"):
        gate.load_bindings(path)

    path.write_text(
        '{"schema":"kernel-composition-compatibility-bindings.v3",'
        '"bindings":{},"satisfied":true}'
    )
    with pytest.raises(gate.GateConfigurationError, match="exactly schema"):
        gate.load_bindings(path)

    path.write_text('{"schema":"a","schema":"b","bindings":{}}')
    with pytest.raises(gate.GateConfigurationError, match="duplicate field"):
        gate.load_bindings(path)


def test_all_of_gate_cannot_pass_an_empty_or_partly_refusing_set() -> None:
    assert not gate.GateResult(()).compatibility_satisfied

    satisfied = gate.ProductEvaluation(
        "academy", "a" * 40, "satisfied", (), _empty_coverage(), _empty_exposure()
    )
    refused = gate.ProductEvaluation(
        "erp",
        "b" * 40,
        "deferred_runtime_debt",
        ("reached debt",),
        _empty_coverage(),
        _empty_exposure(),
    )
    third = gate.ProductEvaluation(
        "sub", "c" * 40, "satisfied", (), _empty_coverage(), _empty_exposure()
    )
    result = gate.GateResult((satisfied, refused, third))

    assert result.evidence_verified
    assert not result.compatibility_satisfied
    assert result.refusing == (refused,)


def test_compatibility_and_adoption_are_distinct_structured_outputs() -> None:
    evaluation = gate.ProductEvaluation(
        "academy", "a" * 40, "satisfied", (), _empty_coverage(), _empty_exposure()
    )
    result = gate.GateResult((evaluation,))

    assert evaluation.compatibility_satisfied
    assert evaluation.adoption_status == "not_evaluated"
    assert result.adoption_status == "not_evaluated"
    assert "ADOPTION NOT EVALUATED" in result.explain()
    assert re.search(r"adoption[^\n]*\d+\s*/\s*\d+", result.explain(), re.I) is None


def test_python_index_reproduces_package_before_same_named_module(
    tmp_path: Path,
) -> None:
    repo = _repo(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/collision.py": "WINNER = 'module'\n",
            "app/collision/__init__.py": "WINNER = 'package'\n",
        },
    )

    index = gate._git_python_index(repo, "HEAD", "app")

    assert index["app.collision"].path == "app/collision/__init__.py"


def test_import_graph_tracks_consumed_static_and_dynamic_edges_not_mentions(
    tmp_path: Path,
) -> None:
    repo = _repo(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/main.py": """
from typing import TYPE_CHECKING
from app.pkg import child
import importlib

if TYPE_CHECKING:
    import dotmac_type_only.hidden

INVENTORY = "dotmac_inventory.only_a_mention"
importlib.import_module("dotmac_dynamic.service")
""",
            "app/pkg/__init__.py": "",
            "app/pkg/child.py": "from dotmac_static import service\n",
        },
    )
    index = gate._git_python_index(repo, "HEAD", "app")

    reached = gate._walk_import_graph(repository=repo, index=index, roots=("app.main",))

    assert "app.pkg.child" in reached.visited
    assert "dotmac_dynamic.service" in reached.external_modules
    assert "dotmac_static" in reached.external_modules
    assert "dotmac_type_only.hidden" not in reached.external_modules
    assert "dotmac_inventory.only_a_mention" not in reached.external_modules


def test_import_graph_refuses_an_unresolved_dynamic_target(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/main.py": """
from importlib import import_module

def load(value):
    return import_module(value)
""",
        },
    )
    index = gate._git_python_index(repo, "HEAD", "app")

    with pytest.raises(gate.GateDerivationError, match="not structurally resolvable"):
        gate._walk_import_graph(repository=repo, index=index, roots=("app.main",))


def test_lazy_import_helpers_follow_consumed_tables_not_unrelated_literals() -> None:
    table_tree = ast.parse(
        """
ROUTES = [("app.api.one", "router"), ("app.api.two", "router")]
UNRELATED = "app.api.not_a_route"
def load(module_name):
    return import_module(module_name)
def mount(spec):
    module_name, attr = spec
    return load(module_name)
def all_routes():
    for spec in ROUTES:
        mount(spec)
"""
    )
    membership_tree = ast.parse(
        """
LAZY = {"one", "two"}
def load(name):
    if name in LAZY:
        return import_module(f"app.services.{name}")
"""
    )

    table_values = gate._table_driven_import_strings(table_tree, "module_name")
    assert table_values is not None
    assert table_values == frozenset({"app.api.one", "app.api.two"})
    assert "app.api.not_a_route" not in table_values
    assert gate._table_driven_import_strings(membership_tree, "name") == frozenset(
        {"one", "two"}
    )


def test_mapping_and_forwarder_helpers_retain_all_possible_imports() -> None:
    mapping_tree = ast.parse(
        """
REGISTRY = {"one": ("app.models.one", "One"), "two": ("app.models.two", "Two")}
def load(key):
    entry = REGISTRY.get(key)
    module_path, class_name = entry
    return import_module(module_path)
"""
    )
    split_tree = ast.parse(
        """
def run(class_path):
    module_path, class_name = class_path.rsplit(".", 1)
    return import_module(module_path)
def one():
    return run("app.computers.one.Computer")
def two():
    return run("app.computers.two.Computer")
"""
    )

    assert gate._mapping_tuple_import_strings(mapping_tree, "module_path") == frozenset(
        {"app.models.one", "app.models.two"}
    )
    assert gate._split_forwarded_import_strings(split_tree, "module_path") == frozenset(
        {"app.computers.one", "app.computers.two"}
    )


def test_guarded_namespace_expands_only_the_enforced_internal_prefix() -> None:
    tree = ast.parse(
        """
def call(target):
    if not target.startswith("app.services."):
        raise ValueError
    module_path, name = target.split(":", 1)
    return import_module(module_path)
"""
    )
    index = {
        "app.services.one": gate._GitPythonFile("app/services/one.py", "a" * 40),
        "app.models.one": gate._GitPythonFile("app/models/one.py", "b" * 40),
    }

    assert gate._guarded_namespace_import_strings(
        tree, "module_path", index
    ) == frozenset({"app.services.one"})


def test_registration_and_lineage_are_derived_from_their_own_structures() -> None:
    assembly = """
from dotmac_files.manifest import module as files_module
from product import FeatureManifest
local_feature = FeatureManifest(name="local")
modules = (files_module, local_feature)
"""
    assert gate._module_registrations(
        assembly, "modules", {"dotmac-files": "dotmac_files"}
    ) == {"dotmac-files"}

    lineage = """
[alembic]
version_locations = dotmac_files.migrations:versions product.migrations:versions
"""
    assert gate._migration_lineages(lineage, {"dotmac-files": "dotmac_files"}) == {
        "dotmac-files"
    }


def test_deferred_debt_loader_is_two_directional_and_nonempty(tmp_path: Path) -> None:
    debt = tmp_path / "debt.json"
    debt.write_text(
        json.dumps(
            {
                "total": 2,
                "files": {"packages/dotmac-x/src/dotmac_x/service.py": 1},
            }
        )
    )
    with pytest.raises(gate.GateConfigurationError, match="total disagrees"):
        gate._load_debt(debt)

    debt.write_text(json.dumps({"total": 0, "files": {}}))
    with pytest.raises(gate.GateConfigurationError, match="must be non-empty"):
        gate._load_debt(debt)


def test_gate_source_has_no_v1_adapter_or_product_authored_verdict_input() -> None:
    source = Path(gate.__file__).read_text()

    assert "kernel-runtime-readiness.v1" not in source
    assert "compatibility_gate_bindings_v1" not in source
    assert "record.satisfied" not in source
    assert 'document["satisfied"]' not in source
    assert "ADOPTION SATISFIED" not in source


def test_real_coordinates_verify_evidence_and_preserve_runtime_debt() -> None:
    missing = [
        spec.clone_env_var
        for spec in gate._DERIVATION_SPECS.values()
        if not os.environ.get(spec.clone_env_var)
    ]
    if missing:
        pytest.skip(
            "the dedicated compatibility-gate job supplies all public product "
            f"checkouts; absent here: {missing!r}"
        )

    result = gate.evaluate_gate(
        gate.load_bindings(), clones=gate.clone_paths_from_environment()
    )
    by_product = {item.product: item for item in result.evaluations}

    assert result.evidence_verified
    assert not result.compatibility_satisfied
    assert by_product["academy"].compatibility_status == "satisfied"
    assert by_product["sub"].compatibility_status == "satisfied"
    assert by_product["erp"].compatibility_status == "deferred_runtime_debt"
    assert {
        (item.source_path, item.sites) for item in by_product["erp"].deferred_debt
    } == {
        ("packages/dotmac-files/src/dotmac_files/service.py", 1),
        ("packages/dotmac-tax/src/dotmac_tax/service.py", 5),
    }
    assert all(
        item.runtime_exposure is not None and item.runtime_exposure.unknown == 0
        for item in result.evaluations
    )
    assert result.adoption_status == "not_evaluated"
