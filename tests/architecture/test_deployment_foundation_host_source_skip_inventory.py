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

from tests.architecture.host_source_skip_inventory import (
    REPO,
    SKIP_INVENTORY,
    tests_reaching,
)

#: Every file this analysis covers — derived from the inventory itself, so
#: a file with zero recorded entries can never silently drop out of scope.
_FILES = sorted({path for path, _ in SKIP_INVENTORY})


def _current_inventory() -> frozenset[tuple[str, str]]:
    current: set[tuple[str, str]] = set()
    for rel in _FILES:
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
    assert len(_FILES) == 6, sorted(_FILES)


# ── sensitivity: both directions of the plant are NAMED ────────────────────


def test_tests_reaching_names_a_new_call_site(tmp_path) -> None:
    """PLANTED. A synthetic module where a test directly calls the target
    name must be reported by `tests_reaching`, proving the analysis this
    guard depends on actually detects a real addition."""
    module = tmp_path / "plant_new_call.py"
    module.write_text(
        "def valid_host_source_kwargs():\n"
        "    return {}\n"
        "\n"
        "def test_something_that_now_reaches_it():\n"
        "    valid_host_source_kwargs()\n"
        "\n"
        "def test_something_unrelated():\n"
        "    pass\n",
        encoding="utf-8",
    )
    found = tests_reaching(module)
    assert found == {"test_something_that_now_reaches_it"}, found


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
        "def valid_host_source_kwargs():\n"
        "    return {}\n"
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
