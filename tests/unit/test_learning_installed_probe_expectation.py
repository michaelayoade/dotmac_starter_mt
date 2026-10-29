"""The Learning installed-pair probe must name absolute root owners.

`scripts/verify_learning_installed.py` derives its source expectation from the
AST of `dotmac_learning/__init__.py` and later imports each recorded owner in
an isolated site-packages. Relative imports in that root therefore have to be
recorded as `dotmac_learning.*` owners: a bare owner (`bank_validation`) is
not importable outside the package and failed the hosted installed-pair proof
at PR #907 head 9d92e5c before any wheel check ran.
"""

from __future__ import annotations

import ast
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
_DUMMIES = [f"n{i}" for i in range(600 - 1 - len(_RELATIVE))]


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_learning_installed", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkout(tmp_path: Path, *, bank_line: str) -> Path:
    """A minimal checkout whose Learning root binds 600 exports."""
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


def _probe_string_constants() -> set[str]:
    tree = ast.parse(SCRIPT.read_text())
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _source_head(versions: Path) -> str:
    revisions: dict[str, object] = {}
    for path in sorted(versions.glob("*.py")):
        values: dict[str, object] = {}
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in (
                        "revision",
                        "down_revision",
                    ):
                        values[target.id] = ast.literal_eval(node.value)
        if "revision" in values:
            revisions[str(values["revision"])] = values.get("down_revision")
    referenced: set[str] = set()
    for down in revisions.values():
        if isinstance(down, tuple | list):
            referenced.update(str(item) for item in down if item)
        elif down:
            referenced.add(str(down))
    heads = set(revisions) - referenced
    assert len(heads) == 1, heads
    return heads.pop()


def test_probe_names_the_current_source_migration_heads():
    """The hosted probe hardcodes the pair heads; they must track source.

    PR #923 run 40332006063 failed `Installed pair graph head differs`:
    the probe still named lr_0007 after A3 added lr_0008-lr_0010.
    """
    constants = _probe_string_constants()
    kernel_head = _source_head(
        PROJECT_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
    )
    learning_head = _source_head(
        PROJECT_ROOT
        / "packages/dotmac-learning/src/dotmac_learning/migrations/versions"
    )
    assert kernel_head in constants
    assert learning_head in constants


def _probe_migrate_expectations(source: str) -> tuple[set[str], list[str], set[str]]:
    """Extract (live alembic_version set, upgrade targets, binding targets).

    All three come from the probe's `migrate()` body alone, so a stale or
    widened literal anywhere else in the file cannot satisfy this check.
    """
    tree = ast.parse(source)
    migrate = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "migrate"
        ),
        None,
    )
    assert migrate is not None, "probe has no migrate()"
    calls = sorted(
        (node for node in ast.walk(migrate) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    upgrades: list[str] = []
    bindings: set[str] = set()
    live_sets: list[set[str]] = []
    for call in calls:
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "upgrade"
            and isinstance(func.value, ast.Name)
            and func.value.id == "command"
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
        ):
            upgrades.append(str(call.args[1].value))
        if (
            isinstance(func, ast.Name)
            and func.id == "PrerequisiteBinding"
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
        ):
            bindings.add(str(call.args[1].value))
        if isinstance(func, ast.Name) and func.id == "require" and call.args:
            condition = call.args[0]
            names_live_rows = any(
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "SELECT version_num FROM public.alembic_version" in node.value
                for node in ast.walk(condition)
            )
            if not names_live_rows:
                continue
            assert isinstance(condition, ast.Compare), ast.dump(condition)
            assert len(condition.ops) == 1 and isinstance(condition.ops[0], ast.Eq)
            assert len(condition.comparators) == 1
            expected = condition.comparators[0]
            assert isinstance(expected, ast.Set), ast.dump(expected)
            live_sets.append(
                {
                    str(element.value)
                    for element in expected.elts
                    if isinstance(element, ast.Constant)
                }
            )
            assert len(live_sets[-1]) == len(expected.elts)
    assert len(live_sets) == 1, live_sets
    return live_sets[0], upgrades, bindings


def test_probe_migrate_live_head_and_upgrade_targets_match_bindings():
    """migrate() must expect the singleton live head its bindings produce.

    The probe binds every Learning prerequisite to the kernel head, so the
    learning branch's depends_on resolves to that head and Alembic consumes
    the kernel version row as the branch advances: after upgrade(kernel)
    then upgrade(learning), alembic_version holds {learning_head} alone.
    The pair {kernel_head, learning_head} is the graph-heads set, not the
    live set (PR #923 repair cycle 3 conflated the two).
    """
    kernel_head = _source_head(
        PROJECT_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
    )
    learning_head = _source_head(
        PROJECT_ROOT
        / "packages/dotmac-learning/src/dotmac_learning/migrations/versions"
    )
    live, upgrades, bindings = _probe_migrate_expectations(SCRIPT.read_text())
    assert bindings == {kernel_head}
    assert upgrades == [kernel_head, learning_head]
    assert live == {learning_head}


def test_probe_rls_table_count_matches_source_migrations():
    """`count(*)=N` in the probe must equal the migrations' table tally."""
    versions = (
        PROJECT_ROOT
        / "packages/dotmac-learning/src/dotmac_learning/migrations/versions"
    )
    tables: set[str] = set()
    for path in sorted(versions.glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not (isinstance(node, ast.FunctionDef) and node.name == "upgrade"):
                continue
            for call in ast.walk(node):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("create_table", "drop_table")
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                ):
                    continue
                name = str(call.args[0].value)
                if call.func.attr == "create_table":
                    tables.add(name)
                else:
                    tables.discard(name)
    assert any(
        f"count(*)={len(tables)}" in constant for constant in _probe_string_constants()
    )


def test_relative_root_import_escaping_the_package_refuses(probe, tmp_path):
    checkout = _checkout(
        tmp_path,
        bank_line="from ..bank_validation import (\n"
        "    BankDoc,\n    balance_targets,\n    propose_balance,\n)\n",
    )
    with pytest.raises(RuntimeError, match="escapes package"):
        probe.expectation(checkout, tmp_path / "expectation.json")
