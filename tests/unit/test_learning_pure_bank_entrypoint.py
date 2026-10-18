"""Hosted canaries for the same-wheel standard-library bank-rules entry point.

The explicit source path subprocess is a source boundary check. Installed wheel,
sdist, outside-checkout provenance and typing qualification are separate gates.
"""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
import textwrap
import tomllib
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "dotmac-learning"
SRC = PACKAGE / "src"
PURE = SRC / "dotmac_learning_bank_rules" / "__init__.py"
PUBLIC = ["BankDoc", "lint_bank", "balance_targets", "propose_balance"]
BANNED = {
    "app",
    "dotmac_learning",
    "dotmac_kernel",
    "sqlalchemy",
    "pydantic",
    "alembic",
    "fastapi",
}


def _assert_stdlib_imports(source: str) -> None:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import bypasses standalone boundary"
            roots = {(node.module or "").split(".")[0]}
        else:
            continue
        assert not roots & BANNED, f"banned import: {sorted(roots & BANNED)}"
        assert roots <= sys.stdlib_module_names, f"non-stdlib import: {roots}"


def test_pure_owner_retains_all_source_rules_and_constants():
    source = PURE.read_text()
    _assert_stdlib_imports(source)
    tree = ast.parse(source)
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.Assign)
        and not (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
        )
    ]
    # Frozen from the accepted full-513 bank_validation owner, before relocation.
    retained = ast.dump(
        ast.Module(body=definitions, type_ignores=[]), include_attributes=False
    )
    assert (
        hashlib.sha256(retained.encode()).hexdigest()
        == "1687cd663e39de43eb26cdd3f026a2557a220ac2bb6370a747648c02e1bde4b9"
    )
    assert sum(isinstance(node, ast.FunctionDef) for node in definitions) == 7
    alias_tree = ast.parse((SRC / "dotmac_learning" / "bank_validation.py").read_text())
    imports = [node for node in alias_tree.body if isinstance(node, ast.ImportFrom)]
    assert imports and all(
        node.level == 0 and node.module == "dotmac_learning_bank_rules"
        for node in imports
    )
    aliases = [alias for node in imports for alias in node.names]
    expected_names = {
        node.name
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
        else node.targets[0].id
        for node in definitions
    }
    assert len(aliases) == len(expected_names)
    assert all(alias.name == alias.asname for alias in aliases)
    assert {alias.name for alias in aliases} == expected_names
    assert not any(
        isinstance(node, ast.FunctionDef | ast.ClassDef) for node in alias_tree.body
    )


@pytest.mark.parametrize("root", sorted(BANNED | {"yaml"}))
@pytest.mark.parametrize("template", ("import {root}", "from {root} import sentinel"))
def test_import_guard_rejects_planted_dependency(root, template):
    with pytest.raises(AssertionError):
        _assert_stdlib_imports(PURE.read_text() + "\n" + template.format(root=root))


def test_pure_package_runs_in_fresh_stdlib_only_interpreter():
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(SRC)!r})
        import dotmac_learning_bank_rules as pure
        assert pure.__all__ == {PUBLIC!r}
        assert pure.lint_bank(
            pure.BankDoc("x", None, "chapter", 1, [])
        ) == ["bank has no questions"]
        assert pure.balance_targets(pure.BankDoc("x", None, "chapter", 1, [])) == []
        assert pure.propose_balance(pure.BankDoc("x", None, "chapter", 1, [])) == []
        banned = {sorted(BANNED)!r}
        assert not any(name.split(".")[0] in banned for name in sys.modules)
        print("pure-only-ok")
        """
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-S", "-c", code],
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "pure-only-ok"


def test_same_owner_identity_and_raw_command_values():
    import dotmac_learning as learning
    import dotmac_learning.bank_validation as compatibility
    import dotmac_learning_bank_rules as pure
    from dotmac_learning.bank_activity_contracts import ImportBankWithActivityCommand

    assert learning.BankDoc is compatibility.BankDoc is pure.BankDoc
    assert (
        learning.balance_targets
        is compatibility.balance_targets
        is pure.balance_targets
    )
    assert (
        learning.propose_balance
        is compatibility.propose_balance
        is pure.propose_balance
    )
    assert compatibility.lint_bank is pure.lint_bank
    assert pure.BankDoc.__module__ == "dotmac_learning_bank_rules"
    assert pure.__all__ == PUBLIC
    marker = date(2026, 10, 17)
    questions = [{"id": "q", "options": [marker], "weight": 1.9}]
    policy = {"pool": marker}
    doc = pure.BankDoc("x", None, "chapter", 1, questions, policy)
    command = ImportBankWithActivityCommand(tenant_id=uuid4(), doc=doc)
    assert command.doc is doc
    assert command.doc.questions is questions and command.doc.policy is policy
    assert command.doc.questions[0]["options"][0] is marker
    assert command.doc.questions[0]["weight"] == 1.9
    doc.questions.clear()
    assert learning.lint_bank(doc) == pure.lint_bank(doc) == ["bank has no questions"]


def test_same_distribution_package_and_pep561_source_declarations():
    poetry = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["tool"]["poetry"]
    assert poetry["name"] == "dotmac-learning" and poetry["version"] == "0.1.0a1"
    assert poetry["packages"] == [
        {"include": "dotmac_learning", "from": "src"},
        {"include": "dotmac_learning_bank_rules", "from": "src"},
    ]
    marker = "src/dotmac_learning_bank_rules/py.typed"
    assert (PACKAGE / marker).read_bytes() == b""
    assert {"path": marker, "format": ["sdist", "wheel"]} in poetry["include"]
    # No optional-dependency or alternate-distribution promise is introduced.
    assert poetry["dependencies"] == {
        "python": ">=3.11,<3.14",
        "dotmac-kernel": ">=0.1.0a106",
        "sqlalchemy": "^2.0",
        "pydantic": "^2.9",
    }
    assert "extras" not in poetry


def test_competency_is_read_as_the_question_domain():
    """The technical manuals tag `competency:`; only `category:` was ever read."""
    from dotmac_learning_bank_rules import _competency_of

    assert _competency_of({"competency": "net-fundamentals"}) == "net-fundamentals"
    assert _competency_of({"category": "safety"}) == "safety"
    # `category` wins where a bank somehow carries both.
    assert _competency_of({"category": "safety", "competency": "other"}) == "safety"
    assert _competency_of({}) is None
