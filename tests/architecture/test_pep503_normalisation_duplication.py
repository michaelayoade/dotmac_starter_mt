"""Starter `scripts/` AST-pattern inventory for PEP 503 normalisation.

`scripts/bundle_envelope.py::_normalise_pep503_name` is this repository's
port of the validating PEP 503 semantics. The inventory derives from the
specific top-level AST pattern this port uses, not from a self-declared list.
Alternative implementations, nested/unreachable bodies, and production
source outside `scripts/` are unmonitored. ERP is also unmonitored from this
checkout because candidate CI does not hold immutable ERP source. See the
inventory document for the boundary and retirement condition.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_DOC = (
    PROJECT_ROOT / "docs" / "inventories" / "pep503-normalisation-duplication.md"
)

# Candidate CI scans only this production source root. Other Starter
# production roots, tests, generated files, and ERP are outside this guard.
PEP503_SCAN_ROOTS = (PROJECT_ROOT / "scripts",)

# The observed AST-pattern inventory. ERP is deliberately absent.
KNOWN_PEP503_NORMALISATION_COPIES = (
    "dotmac_starter_mt:scripts/bundle_envelope.py:_normalise_pep503_name",
)


def _starter_observed_pep503_pattern(
    source_roots: tuple[Path, ...], *, relative_to: Path
) -> tuple[str, ...]:
    """Find the port's exact top-level AST pattern in bounded source roots.

    It recognises top-level compiled literal regexes and direct function-body
    expressions only. Equivalent alternate syntax, aliases, nested functions,
    and calls reachable only through nested statement bodies are deliberately
    outside this narrow syntactic guard.
    """

    found: list[str] = []
    for source_root in source_roots:
        for source_path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(
                source_path.read_text(encoding="utf-8"), filename=str(source_path)
            )
            separator_patterns: set[str] = set()
            alphabet_patterns: set[str] = set()
            for statement in tree.body:
                if not isinstance(statement, ast.Assign | ast.AnnAssign):
                    continue
                value = statement.value
                if not isinstance(value, ast.Call) or not value.args:
                    continue
                if not (
                    isinstance(value.func, ast.Attribute)
                    and value.func.attr == "compile"
                    and isinstance(value.args[0], ast.Constant)
                    and isinstance(value.args[0].value, str)
                ):
                    continue
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if value.args[0].value == r"[-_.]+":
                        separator_patterns.add(target.id)
                    if value.args[0].value == r"\A[A-Za-z0-9._-]+\Z":
                        alphabet_patterns.add(target.id)
            for function in (
                node for node in tree.body if isinstance(node, ast.FunctionDef)
            ):
                direct_expression_roots: list[ast.expr] = []
                for statement in function.body:
                    if isinstance(statement, ast.If):
                        direct_expression_roots.append(statement.test)
                    elif isinstance(statement, ast.Assign | ast.AnnAssign | ast.Return):
                        if statement.value is not None:
                            direct_expression_roots.append(statement.value)
                    elif isinstance(statement, ast.Expr):
                        direct_expression_roots.append(statement.value)
                calls = [
                    node
                    for root in direct_expression_roots
                    for node in ast.walk(root)
                    if isinstance(node, ast.Call)
                ]
                has_collapse = any(
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "sub"
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in separator_patterns
                    for call in calls
                )
                has_alphabet = any(
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "match"
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in alphabet_patterns
                    for call in calls
                )
                has_lower = any(
                    isinstance(call.func, ast.Attribute) and call.func.attr == "lower"
                    for call in calls
                )
                attributes = [
                    node
                    for root in direct_expression_roots
                    for node in ast.walk(root)
                    if isinstance(node, ast.Attribute)
                ]
                has_edge_refusal = {attribute.attr for attribute in attributes} >= {
                    "startswith",
                    "endswith",
                }
                if has_collapse and has_alphabet and has_lower and has_edge_refusal:
                    found.append(
                        "dotmac_starter_mt:"
                        f"{source_path.relative_to(relative_to)}:{function.name}"
                    )
    return tuple(found)


def _load_bundle_envelope():
    module_name = "bundle_envelope_pep503_dup_check"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, PROJECT_ROOT / "scripts" / "bundle_envelope.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_the_inventory_doc_exists_and_names_the_recorded_symbols() -> None:
    """The inventory doc and this list must reference the same local symbol
    — a doc that drifts from the enforced list would make the record and
    the gate disagree about what is actually tracked."""

    assert INVENTORY_DOC.is_file()
    text = INVENTORY_DOC.read_text(encoding="utf-8")
    assert "unmonitored" in text
    assert "_normalise_pep503_name" in text


def test_the_source_derived_local_inventory_is_exact_today() -> None:
    """A new `scripts/` copy of the observed AST pattern fails."""

    assert (
        _starter_observed_pep503_pattern(PEP503_SCAN_ROOTS, relative_to=PROJECT_ROOT)
        == KNOWN_PEP503_NORMALISATION_COPIES
    )
    assert len(KNOWN_PEP503_NORMALISATION_COPIES) == 1


def test_local_enumerator_detects_a_second_copy_of_the_observed_pattern(
    tmp_path: Path,
) -> None:
    """Sensitivity plant; the adjacent whitespace normaliser is excluded."""

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "third_copy.py").write_text(
        "import re\n"
        "_RUNS = re.compile(r'[-_.]+')\n"
        "_VALID = re.compile(r'\\A[A-Za-z0-9._-]+\\Z')\n"
        "def third_copy(name):\n"
        "    if not _VALID.match(name): raise ValueError(name)\n"
        "    result = _RUNS.sub('-', name).lower()\n"
        "    if result.startswith('-') or result.endswith('-'):\n"
        "        raise ValueError(name)\n"
        "    return result\n",
        encoding="utf-8",
    )
    (scripts / "whitespace.py").write_text(
        "import re\n"
        "def normalise_whitespace(value): return re.sub(r'\\s+', ' ', value).lower()\n",
        encoding="utf-8",
    )

    assert _starter_observed_pep503_pattern((scripts,), relative_to=tmp_path) == (
        "dotmac_starter_mt:scripts/third_copy.py:third_copy",
    )


def test_the_inventory_names_starters_own_copy_by_its_exact_symbol() -> None:
    """This repository's own entry is pinned to the exact function
    `build_local_index` calls — a silent rename here would desynchronise the
    inventory from the code it describes."""

    assert (
        "dotmac_starter_mt:scripts/bundle_envelope.py:_normalise_pep503_name"
        in KNOWN_PEP503_NORMALISATION_COPIES
    )


def test_starters_copy_still_exists_at_its_recorded_location() -> None:
    """Confirms the symbol the inventory names still exists, rather than
    having silently moved or been renamed out from under the inventory."""

    module = _load_bundle_envelope()
    assert hasattr(module, "_normalise_pep503_name")


def test_starters_copy_matches_the_known_pep503_boundary_cases() -> None:
    """Parity note made executable. `dependency_normalisation.py`'s own
    docstring documents ERP having already suffered a silent divergence
    between two of its own scripts on exactly this boundary (edge-separator
    stripping) before it consolidated onto one owner. This repository
    cannot import ERP's copy directly (separate checkouts/deployments), so
    this pins THIS repository's port against the same boundary cases
    instead: run-collapsing, case-folding, input-charset refusal, and
    edge-separator refusal on the normalised output.

    Breaks if `_normalise_pep503_name`'s collapsing, case-folding, charset
    check, or edge-separator check silently diverges from ERP's owner.
    """

    module = _load_bundle_envelope()
    normalise = module._normalise_pep503_name

    assert normalise("Dotmac_Kernel") == "dotmac-kernel"
    assert normalise("dotmac--kernel") == "dotmac-kernel"
    assert normalise("dotmac.kernel") == "dotmac-kernel"

    for invalid in ("-dotmac-kernel-", "dotmac/kernel", "dotmac kernel"):
        try:
            normalise(invalid)
        except ValueError:
            continue
        raise AssertionError(f"{invalid!r} should have been refused")
