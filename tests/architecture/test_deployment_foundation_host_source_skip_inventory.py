"""The skip inventory is exact, in both directions — not a count.

See `host_source_skip_inventory.py` for the full account of why identity
matters here: a count-only ratchet stays green while one lost test is
silently swapped for another. This file recomputes, from the real test
files, exactly which `test_*` functions reach `valid_host_source_kwargs()`
(the shared fixture that now calls `pytest.skip(...)` because neither
mutating executor accepts a host-source parameter any more), and compares
that set against the recorded `SKIP_INVENTORY` — asserting BOTH that nothing
new is missing (an unrecorded loss of coverage) and that nothing recorded
has vanished (a loss that stopped being true and was never updated, or a
test quietly deleted).
"""

from __future__ import annotations

import pytest

from tests.architecture.host_source_skip_inventory import (
    REPO,
    SKIP_INVENTORY,
    TARGET_CALL,
    TARGET_MODULE,
    tests_reaching,
)


def _test_files(root=REPO) -> tuple[str, ...]:
    """The complete defined test corpus, independent of the inventory."""
    return tuple(
        sorted(
            path.relative_to(root).as_posix() for path in (root / "tests").rglob("*.py")
        )
    )


def _current_inventory() -> frozenset[tuple[str, str]]:
    current: set[tuple[str, str]] = set()
    for rel in _test_files():
        for name in tests_reaching(REPO / rel):
            current.add((rel, name))
    return frozenset(current)


# ── the two-directional proof, on the real files ────────────────────────────


def test_the_skip_inventory_has_no_unrecorded_new_losses() -> None:
    """A `test_*` function that now reaches `valid_host_source_kwargs()` but
    is not in `SKIP_INVENTORY` is a NEW, unrecorded loss of coverage — either
    a genuinely new casualty that needs recording and explaining, or a sign
    that a rewrite accidentally routed a previously-reachable test through
    the dead fixture."""
    current = _current_inventory()
    unrecorded = current - SKIP_INVENTORY
    assert unrecorded == set(), (
        f"{len(unrecorded)} test(s) reach valid_host_source_kwargs() but are "
        f"not recorded in SKIP_INVENTORY: {sorted(unrecorded)}. Add them, "
        "with the reason they are now unreachable, or fix whatever routed "
        "them through the dead fixture"
    )


def test_the_skip_inventory_has_no_vanished_entries() -> None:
    """A recorded entry whose test no longer reaches
    `valid_host_source_kwargs()` has VANISHED — the test was deleted, renamed,
    or rewritten to reach its subject a different way. Any of those is fine;
    silently leaving a stale entry in the inventory is not, because it hides
    which tests are ACTUALLY unreachable today."""
    current = _current_inventory()
    vanished = SKIP_INVENTORY - current
    assert vanished == set(), (
        f"{len(vanished)} recorded entr{'y is' if len(vanished) == 1 else 'ies are'} "
        f"no longer reachable: {sorted(vanished)}. Remove them from "
        "SKIP_INVENTORY, or investigate why a test that used to lose its "
        "subject no longer does"
    )


def test_the_inventory_is_not_accidentally_empty() -> None:
    """A check over an empty set passes for the wrong reason."""
    assert len(SKIP_INVENTORY) > 0
    assert len(_current_inventory()) == 73
    assert len(_test_files()) > 6


# ── sensitivity: both directions of the plant are NAMED ────────────────────


def test_tests_reaching_names_a_new_call_site(tmp_path) -> None:
    """PLANTED. A synthetic module where a test directly calls the target
    name must be reported by `tests_reaching`, proving the analysis this
    guard depends on actually detects a real addition."""
    module = tmp_path / "plant_new_call.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        "\n"
        "def test_something_that_now_reaches_it():\n"
        f"    {TARGET_CALL}()\n"
        "\n"
        "def test_something_unrelated():\n"
        "    pass\n",
        encoding="utf-8",
    )
    found = tests_reaching(module)
    assert found == {"test_something_that_now_reaches_it"}, found


def test_tests_reaching_scans_a_new_seventh_file(tmp_path) -> None:
    """PLANTED. A newly added test file is in scope even with no inventory
    entry yet; coverage cannot be narrowed by editing the recorded set."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    module = tests_dir / "test_seventh_file.py"
    module.write_text(
        f"from {TARGET_MODULE} import valid_host_source_kwargs as stance\n"
        "def test_new_file_reaches_the_fixture():\n"
        "    stance()\n",
        encoding="utf-8",
    )
    assert _test_files(tmp_path) == ("tests/test_seventh_file.py",)
    assert tests_reaching(tmp_path / "tests/test_seventh_file.py") == {
        "test_new_file_reaches_the_fixture"
    }


def test_tests_reaching_follows_an_alias_import(tmp_path) -> None:
    """PLANTED. A direct alias of the imported fixture remains in scope."""
    module = tmp_path / "plant_alias.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL} as stance\n"
        "def test_calls_alias():\n"
        "    stance()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == {"test_calls_alias"}


def test_tests_reaching_follows_a_module_alias_import(tmp_path) -> None:
    """PLANTED. A module-qualified alias is the other import spelling that
    must resolve to the real fixture rather than an attribute near miss."""
    module = tmp_path / "plant_module_alias.py"
    module.write_text(
        f"import {TARGET_MODULE} as stance_module\n"
        "def test_calls_module_alias():\n"
        "    stance_module.valid_host_source_kwargs()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == {"test_calls_module_alias"}


def test_tests_reaching_follows_an_unaliased_full_module_import(tmp_path) -> None:
    """PLANTED. The unaliased import binds ``tests``; the complete dotted
    attribute chain must still resolve to the shared fixture."""
    module = tmp_path / "plant_unaliased_module.py"
    module.write_text(
        f"import {TARGET_MODULE}\n"
        "def test_calls_full_module_path():\n"
        "    tests.unit.host_source_stance.valid_host_source_kwargs()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == {"test_calls_full_module_path"}


def test_tests_reaching_follows_from_parent_package_alias(tmp_path) -> None:
    """PLANTED. A package-level import alias must resolve the target module."""
    module = tmp_path / "plant_parent_package_alias.py"
    module.write_text(
        "from tests.unit import host_source_stance as stance\n"
        "def test_calls_parent_alias():\n"
        "    stance.valid_host_source_kwargs()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == {"test_calls_parent_alias"}


def test_tests_reaching_follows_imported_parent_module_prefixes(tmp_path) -> None:
    """PLANTED. Normal parent-module imports retain the path needed to
    resolve the target instead of disappearing behind a shorter alias."""
    module = tmp_path / "plant_parent_module_prefixes.py"
    module.write_text(
        "from tests import unit\n"
        "import tests.unit as unit_alias\n"
        "def test_calls_from_parent_import():\n"
        "    unit.host_source_stance.valid_host_source_kwargs()\n"
        "def test_calls_from_module_alias():\n"
        "    unit_alias.host_source_stance.valid_host_source_kwargs()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == {
        "test_calls_from_module_alias",
        "test_calls_from_parent_import",
    }


def test_tests_reaching_does_not_leak_a_function_local_import(tmp_path) -> None:
    """PLANTED. A target import in one test must not contaminate siblings."""
    module = tmp_path / "plant_local_import.py"
    module.write_text(
        "def test_local_import():\n"
        f"    from {TARGET_MODULE} import {TARGET_CALL}\n"
        f"    {TARGET_CALL}()\n"
        "\n"
        "def test_sibling_without_import():\n"
        f"    {TARGET_CALL}()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == {"test_local_import"}


def test_tests_reaching_stays_silent_on_a_local_same_named_function(tmp_path) -> None:
    """NEAR-MISS. A local function with the same name is not the imported
    fixture and must not be counted as lost coverage."""
    module = tmp_path / "plant_local_shadow.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        f"def {TARGET_CALL}():\n"
        "    return {}\n"
        "def test_calls_local_shadow():\n"
        f"    {TARGET_CALL}()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == set()


def test_tests_reaching_stays_silent_on_a_local_assignment_shadow(tmp_path) -> None:
    """NEAR-MISS. A local assignment shadows an imported target for the
    whole function and must not be treated as the shared fixture."""
    module = tmp_path / "plant_local_assignment.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        "def test_calls_assignment_shadow():\n"
        f"    {TARGET_CALL} = lambda: None\n"
        f"    {TARGET_CALL}()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == set()


def test_tests_reaching_stays_silent_on_a_parameter_shadow(tmp_path) -> None:
    """NEAR-MISS. A parameter is a local binding for the whole function and
    must shadow the module-level fixture import."""
    module = tmp_path / "plant_parameter_shadow.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        f"def test_calls_parameter_shadow({TARGET_CALL}):\n"
        f"    {TARGET_CALL}()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == set()


def test_tests_reaching_refuses_an_import_rebound_in_one_function(tmp_path) -> None:
    """PLANTED. Statement ordering inside control flow is not guessed: a
    scope that both imports and otherwise binds the target name is refused
    instead of being misreported as either reached or silent."""
    module = tmp_path / "plant_ambiguous_rebinding.py"
    module.write_text(
        "def test_rebinds_the_import():\n"
        "    stance = lambda: None\n"
        f"    from {TARGET_MODULE} import {TARGET_CALL} as stance\n"
        "    stance()\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="rebound in the same function scope"):
        tests_reaching(module)


def test_tests_reaching_stays_silent_on_a_nested_definition_shadow(tmp_path) -> None:
    """NEAR-MISS. A nested definition binds the same local name and shadows
    the imported target in its containing test function."""
    module = tmp_path / "plant_nested_definition.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        "def test_calls_nested_shadow():\n"
        f"    def {TARGET_CALL}():\n"
        "        return None\n"
        f"    {TARGET_CALL}()\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == set()


def test_tests_reaching_does_not_execute_an_uncalled_nested_function(tmp_path) -> None:
    """NEAR-MISS. Merely defining a nested function that calls the fixture
    does not make the containing test reach it; the nested body has its own
    lexical scope and is never executed here."""
    module = tmp_path / "plant_uncalled_nested.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        "def test_defines_but_does_not_call_nested():\n"
        "    def nested():\n"
        f"        {TARGET_CALL}()\n"
        "    assert callable(nested)\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == set()


def test_tests_reaching_includes_collected_class_methods_and_helpers(tmp_path) -> None:
    """PLANTED. Pytest collects Test* methods as tests; a direct call and a
    call through a same-class helper are both inventory identities."""
    module = tmp_path / "plant_class_tests.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        "class TestHostSource:\n"
        "    def _helper(self):\n"
        f"        {TARGET_CALL}()\n"
        "    def test_calls_directly(self):\n"
        f"        {TARGET_CALL}()\n"
        "    def test_calls_helper(self):\n"
        "        self._helper()\n"
        "    def test_unrelated(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    assert tests_reaching(module) == {
        "TestHostSource.test_calls_directly",
        "TestHostSource.test_calls_helper",
    }


def test_vanished_entry_is_named_by_identity(tmp_path) -> None:
    """PLANTED. A renamed/deleted test produces a vanished identity rather
    than silently shrinking a count-only baseline."""
    module = tmp_path / "plant_vanished.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        "def test_new_name():\n"
        f"    {TARGET_CALL}()\n",
        encoding="utf-8",
    )
    current = {("tests/plant_vanished.py", name) for name in tests_reaching(module)}
    recorded = frozenset({("tests/plant_vanished.py", "test_old_name")})
    assert recorded - current == {("tests/plant_vanished.py", "test_old_name")}


def test_tests_reaching_stays_silent_on_a_near_miss(tmp_path) -> None:
    """NEAR-MISS, MUST BE SILENT, and EXERCISED. A test that calls a
    same-named METHOD on an object (`obj.valid_host_source_kwargs()`,
    an attribute access) is a different call entirely and must not be
    confused with the module-level function — this is the exact false
    positive an earlier draft of this analysis produced by matching on bare
    attribute names."""
    module = tmp_path / "plant_near_miss.py"
    module.write_text(
        "class Obj:\n"
        "    def valid_host_source_kwargs(self):\n"
        "        return {}\n"
        "\n"
        "def test_calls_a_same_named_method_not_the_function():\n"
        "    Obj().valid_host_source_kwargs()\n",
        encoding="utf-8",
    )
    found = tests_reaching(module)
    assert found == set(), (
        f"a same-named METHOD call was wrongly treated as reaching the "
        f"module-level function: {found}"
    )


def test_tests_reaching_follows_a_transitive_helper_chain(tmp_path) -> None:
    """The real files' shape: a test calls a local helper, which calls
    another local helper, which finally calls the target. Two hops, not
    one, must still be found."""
    module = tmp_path / "plant_transitive.py"
    module.write_text(
        f"from {TARGET_MODULE} import {TARGET_CALL}\n"
        "\n"
        "def _inner():\n"
        "    return valid_host_source_kwargs()\n"
        "\n"
        "def _outer():\n"
        "    return _inner()\n"
        "\n"
        "def test_two_hops_away():\n"
        "    _outer()\n",
        encoding="utf-8",
    )
    found = tests_reaching(module)
    assert found == {"test_two_hops_away"}, found
