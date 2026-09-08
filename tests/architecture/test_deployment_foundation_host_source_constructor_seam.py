"""No parameter of `Executor.__init__` or `RecoveryExecutor.__init__` lets a
caller state the artifact digest or the launcher digest, by ANY route — and
`_verify_host_source` on both classes calls EXACTLY
`require_host_source(receipt=None)`, never anything a caller could reach.

## The defect this closes — three times, now

Round 1: `Executor.__init__` accepted `host_source_installed` (a pre-built
`InstalledArtifact`, carrying the artifact digest itself) and
`host_source_probe` (a callable standing in for the launcher's source-tree
digest), forwarded straight into `require_host_source` as `installed=`/
`source_tree_digest=`.

Round 2, found by an independent review at exact head `541cee5d`: removing
those two and keeping `host_source_receipt`/`host_source_metadata` was the
SAME bypass respelled. `CandidateReceipt` is a plain frozen dataclass
carrying `artifact_digest` directly, constructible by anyone; `InstalledMetadata`
is a Protocol whose `read_text("direct_url.json")` returns a caller-chosen
string that becomes the offered digest. A caller supplying both, mutually
agreeing, stated the SAME digest on both sides of `require_host_source`'s one
comparison — byte-for-byte the round-1 admission, with a `json.dumps` in
between. Michael's ruling: both parameters are also removed, from both
classes, with nothing put in their place.

Round 3, an independent review of THIS file: "a two-string denylist wearing a
structural claim." A parameter-NAME denylist only catches a bypass that
reuses one of the names already on the list — it says nothing about:

* a **positional** parameter (no keyword name to deny at all — a caller
  passing a value in the third, fourth, ... position);
* `**kwargs` (accepts ANY keyword, defeating a name-based guard by
  construction — the guard would have to deny every string that has not been
  invented yet);
* `_verify_host_source` being CALLED (proved by the sibling coverage guards)
  without checking what it is CALLED WITH — a call to `require_host_source`
  fed `receipt=self._anything` would pass the coverage guards and reopen the
  exact defect this file exists to close.

This file now checks all three: the full ALLOWED positional parameter list
(nothing extra, no vararg), the absence of `**kwargs`, and the EXACT call
shape of `require_host_source` inside both `_verify_host_source` methods.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture.host_source_skip_inventory import (
    RETIRE_WHEN,
    SKIP_INVENTORY,
    SKIP_INVENTORY_SCOPE,
    tests_reaching,
)

REPO = Path(__file__).resolve().parents[2]
RUN_PY = (
    REPO
    / "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
    / "engine/run.py"
)
RECOVERY_EXECUTION_PY = (
    REPO
    / "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
    / "recovery_execution.py"
)

#: Parameters that let a caller state an artifact/launcher digest, or either
#: of the two ingredients (`CandidateReceipt`, `InstalledMetadata`) that
#: combine to state one. NONE of these may exist on either constructor,
#: ever, until trusted provenance (checked against distinct, non-caller-
#: controlled trust roots) is built as its own separate piece of work.
_FORBIDDEN_HOST_SOURCE_PARAMS = frozenset(
    {
        "host_source_installed",
        "host_source_probe",
        "host_source_receipt",
        "host_source_metadata",
        "host_source_receipts_dir",
    }
)

#: The ONLY positional parameters either constructor may declare — an
#: allowlist, not a denylist: anything not on this list, in the positional
#: slots, is refused by the "exact positional list" tests below, whether or
#: not its NAME happens to appear in `_FORBIDDEN_HOST_SOURCE_PARAMS`.
_ALLOWED_POSITIONAL = {
    "Executor": ("spec", "effects", "grant"),
    "RecoveryExecutor": ("spec", "manifest", "effects"),
}


def test_trusted_provenance_admission_ratchet_is_bidirectional() -> None:
    """The temporary non-admission seam and its skipped reach are one state.

    While the recorded reach remains, constructors and their host-source calls
    must be incapable of receiving non-``None`` evidence.  Conversely, when
    that reach is genuinely retired this refusal must be rewritten in the same
    change; leaving it behind would misdescribe a permanently non-admitting
    executor as a transition gate.
    """
    assert SKIP_INVENTORY_SCOPE == "Executor tests only"
    assert len(SKIP_INVENTORY) == 73
    _require_non_admission_while_inventory_remains(
        ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY)),
        class_name="Executor",
        path=RUN_PY,
    )
    plant = ast.parse(
        "class Executor:\n"
        "    def _verify_host_source(self):\n"
        "        return require_host_source(receipt=self._trusted_evidence)\n"
    )
    with pytest.raises(AssertionError, match="non-admitting"):
        _require_non_admission_while_inventory_remains(
            plant, class_name="Executor", path=Path("<plant>")
        )


def test_skip_inventory_is_machine_checked_as_executor_only_per_test_identity(
    tmp_path: Path,
) -> None:
    paths = {relative_path for relative_path, _ in SKIP_INVENTORY}
    executor_tests = {
        (relative_path, name)
        for relative_path in paths
        for name in tests_reaching(
            REPO / relative_path,
            target="Executor",
            target_module="dotmac_deployment_foundation.engine.run",
        )
    }
    recovery_tests = {
        (relative_path, name)
        for relative_path in paths
        for name in tests_reaching(
            REPO / relative_path,
            target="RecoveryExecutor",
            target_module="dotmac_deployment_foundation.recovery_execution",
        )
    }
    assert SKIP_INVENTORY <= executor_tests
    assert not SKIP_INVENTORY & recovery_tests

    split_subjects = tmp_path / "split_subjects.py"
    split_subjects.write_text(
        "from tests.unit.host_source_stance import valid_host_source_kwargs\n"
        "from dotmac_deployment_foundation.engine.run import Executor\n"
        "def test_fixture():\n"
        "    valid_host_source_kwargs()\n"
        "def test_executor_only():\n"
        "    Executor(None, None, None)\n",
        encoding="utf-8",
    )
    assert tests_reaching(split_subjects) == {"test_fixture"}
    assert tests_reaching(
        split_subjects,
        target="Executor",
        target_module="dotmac_deployment_foundation.engine.run",
    ) == {"test_executor_only"}

    same_subject = tmp_path / "same_subject.py"
    same_subject.write_text(
        "from tests.unit.host_source_stance import valid_host_source_kwargs\n"
        "from dotmac_deployment_foundation.engine.run import Executor\n"
        "def test_both():\n"
        "    Executor(None, None, None)\n"
        "    valid_host_source_kwargs()\n",
        encoding="utf-8",
    )
    assert tests_reaching(same_subject) == {"test_both"}
    assert tests_reaching(
        same_subject,
        target="Executor",
        target_module="dotmac_deployment_foundation.engine.run",
    ) == {"test_both"}

    recovery_subject = tmp_path / "recovery_subject.py"
    recovery_subject.write_text(
        "from tests.unit.host_source_stance import valid_host_source_kwargs\n"
        "from dotmac_deployment_foundation.recovery_execution import "
        "RecoveryExecutor\n"
        "def test_recovery():\n"
        "    RecoveryExecutor(None, None, None)\n"
        "    valid_host_source_kwargs()\n"
        "def test_prose_near_miss():\n"
        "    label = 'RecoveryExecutor'\n",
        encoding="utf-8",
    )
    assert tests_reaching(
        recovery_subject,
        target="RecoveryExecutor",
        target_module="dotmac_deployment_foundation.recovery_execution",
    ) == {"test_recovery"}


def _find_class(class_name: str, tree: ast.Module, *, path: Path) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"no class {class_name} found in {path}; this proves nothing")


def _find_method(class_node: ast.ClassDef, method_name: str) -> ast.FunctionDef:
    for member in class_node.body:
        if isinstance(member, ast.FunctionDef) and member.name == method_name:
            return member
    raise AssertionError(f"{class_node.name} has no {method_name}; this proves nothing")


def _init_args(class_name: str, tree: ast.Module, *, path: Path) -> ast.arguments:
    return _find_method(_find_class(class_name, tree, path=path), "__init__").args


def _kwonly_names(args: ast.arguments) -> set[str]:
    return {a.arg for a in args.kwonlyargs}


def _positional_names(args: ast.arguments) -> list[str]:
    """`self` excluded — every OTHER positional-or-keyword parameter, in
    declaration order. These are exactly the arguments a caller can supply
    WITHOUT naming them, which is why they get their own, stricter check
    (an exact ordered list, not a set) rather than being folded into the
    keyword-only denylist below."""
    return [a.arg for a in (*args.posonlyargs, *args.args) if a.arg != "self"]


# ── the named proof: no forbidden KEYWORD parameter, on the real files ─────


def test_executor_init_accepts_no_host_source_parameter_at_all() -> None:
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY))
    args = _init_args("Executor", tree, path=RUN_PY)
    names = set(_positional_names(args)) | _kwonly_names(args)

    present_forbidden = names & _FORBIDDEN_HOST_SOURCE_PARAMS
    assert present_forbidden == set(), (
        f"Executor.__init__ still accepts {sorted(present_forbidden)}, which "
        "lets a caller state an artifact/launcher digest, or the two "
        "ingredients (a CandidateReceipt, an InstalledMetadata) that combine "
        "to state one, directly instead of having it read"
    )


def test_recovery_executor_init_accepts_no_host_source_parameter_at_all() -> None:
    tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    args = _init_args("RecoveryExecutor", tree, path=RECOVERY_EXECUTION_PY)
    names = set(_positional_names(args)) | _kwonly_names(args)

    present_forbidden = names & _FORBIDDEN_HOST_SOURCE_PARAMS
    assert present_forbidden == set(), (
        f"RecoveryExecutor.__init__ still accepts {sorted(present_forbidden)} "
        "— the same bypass shape closed on Executor, reintroduced here"
    )


# ── the ALLOWLIST proof: exact positional parameters, no vararg, no kwarg ──
#
# A denylist over keyword NAMES cannot see a positional parameter (nothing
# to name) or `**kwargs` (accepts every name at once). These checks close
# both, structurally, on the real files.


def test_executor_init_has_exactly_the_allowed_positional_parameters() -> None:
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY))
    args = _init_args("Executor", tree, path=RUN_PY)

    assert _positional_names(args) == list(_ALLOWED_POSITIONAL["Executor"]), (
        f"Executor.__init__'s positional parameters are "
        f"{_positional_names(args)}, not exactly "
        f"{list(_ALLOWED_POSITIONAL['Executor'])} — an extra positional slot "
        "is a channel a parameter-NAME denylist cannot see at all"
    )
    assert args.vararg is None, (
        f"Executor.__init__ accepts *{args.vararg.arg if args.vararg else ''}, "
        "an unbounded positional channel no name-based guard can enumerate"
    )
    assert args.kwarg is None, (
        f"Executor.__init__ accepts **{args.kwarg.arg if args.kwarg else ''}, "
        "which admits ANY keyword — including every forbidden name above — "
        "and defeats a name-based guard by construction"
    )


def test_recovery_executor_init_has_exactly_the_allowed_positional_parameters() -> None:
    tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    args = _init_args("RecoveryExecutor", tree, path=RECOVERY_EXECUTION_PY)

    assert _positional_names(args) == list(_ALLOWED_POSITIONAL["RecoveryExecutor"]), (
        f"RecoveryExecutor.__init__'s positional parameters are "
        f"{_positional_names(args)}, not exactly "
        f"{list(_ALLOWED_POSITIONAL['RecoveryExecutor'])}"
    )
    assert args.vararg is None, (
        f"RecoveryExecutor.__init__ accepts "
        f"*{args.vararg.arg if args.vararg else ''}"
    )
    assert args.kwarg is None, (
        f"RecoveryExecutor.__init__ accepts "
        f"**{args.kwarg.arg if args.kwarg else ''}, which admits ANY keyword"
    )


# ── the CALL-SHAPE proof: `_verify_host_source` calls EXACTLY
# `require_host_source(receipt=None)` — not merely "is called" ─────────────
#
# The sibling coverage guards
# (`test_deployment_foundation_host_source_coverage.py` and its
# `RecoveryExecutor` sibling) prove `_verify_host_source` is CALLED from
# every mutating entry point. Neither ever inspected what `_verify_host_
# source` itself calls `require_host_source` WITH — a gate proven to fire is
# not the same claim as a gate proven to be unfeedable. This is that second
# claim.


def _require_host_source_calls(func_node: ast.FunctionDef) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(func_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require_host_source"
    ]


def _is_receipt_none_only(call: ast.Call) -> bool:
    """`True` iff `call` is EXACTLY `require_host_source(receipt=None)` — no
    positional arguments, exactly one keyword named `receipt`, and its value
    is the literal constant `None` (never a name, an attribute, or any other
    expression that could resolve to something a caller supplied)."""
    if call.args:
        return False
    if len(call.keywords) != 1:
        return False
    keyword = call.keywords[0]
    if keyword.arg != "receipt":
        return False
    return isinstance(keyword.value, ast.Constant) and keyword.value.value is None


def _require_non_admission_while_inventory_remains(
    tree: ast.Module, *, class_name: str, path: Path
) -> None:
    """The compound Executor transitional rule, usable against source/plants."""
    assert RETIRE_WHEN == "trusted-provenance-admission"
    assert len(SKIP_INVENTORY) == 73, "the retirement inventory changed"
    _require_non_admission_call_shape(tree, class_name=class_name, path=path)


def _require_non_admission_call_shape(
    tree: ast.Module, *, class_name: str, path: Path
) -> None:
    """Assert a class's host-source helper can only request ``None``."""
    method = _find_method(
        _find_class(class_name, tree, path=path), "_verify_host_source"
    )
    calls = _require_host_source_calls(method)
    assert len(calls) == 1 and _is_receipt_none_only(
        calls[0]
    ), "the executor is not non-admitting while its recorded gap remains"


def test_executor_verify_host_source_calls_exactly_require_host_source_of_none() -> (
    None
):
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY))
    method = _find_method(
        _find_class("Executor", tree, path=RUN_PY), "_verify_host_source"
    )
    calls = _require_host_source_calls(method)

    assert len(calls) == 1, (
        f"_verify_host_source calls require_host_source {len(calls)} time(s) "
        "in its own source, not exactly once"
    )
    assert _is_receipt_none_only(calls[0]), (
        "Executor._verify_host_source's call to require_host_source is not "
        "exactly `require_host_source(receipt=None)` — "
        f"args={[ast.dump(a) for a in calls[0].args]}, "
        f"keywords={[(k.arg, ast.dump(k.value)) for k in calls[0].keywords]}"
    )


def test_recovery_executor_verify_host_source_calls_require_host_source_of_none() -> (
    None
):
    tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    method = _find_method(
        _find_class("RecoveryExecutor", tree, path=RECOVERY_EXECUTION_PY),
        "_verify_host_source",
    )
    calls = _require_host_source_calls(method)

    assert len(calls) == 1, (
        f"_verify_host_source calls require_host_source {len(calls)} time(s) "
        "in its own source, not exactly once"
    )
    assert _is_receipt_none_only(calls[0]), (
        "RecoveryExecutor._verify_host_source's call to require_host_source "
        "is not exactly `require_host_source(receipt=None)` — "
        f"args={[ast.dump(a) for a in calls[0].args]}, "
        f"keywords={[(k.arg, ast.dump(k.value)) for k in calls[0].keywords]}"
    )


# ── sensitivity: every plant above is NAMED, every near-miss is SILENT ──────


def test_the_guard_names_a_planted_regrowth_of_the_removed_seam() -> None:
    """PLANTED, in memory. A synthetic `Executor` whose `__init__` regrows
    the exact "narrowed but still forgeable" shape the independent review
    found must be caught, and caught BY NAME."""
    source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, *, "
        "host_source_receipt=None, host_source_installed=None, "
        "host_source_metadata=None, host_source_probe=None, "
        "host_source_receipts_dir=None):\n"
        "        pass\n"
    )
    tree = ast.parse(source, filename="<plant: regrown host-source seam>")
    args = _init_args("Executor", tree, path=Path("<plant>"))
    names = set(_positional_names(args)) | _kwonly_names(args)

    present_forbidden = names & _FORBIDDEN_HOST_SOURCE_PARAMS
    assert present_forbidden == _FORBIDDEN_HOST_SOURCE_PARAMS, (
        "the plant does not exhibit the shape being detected: expected all "
        f"forbidden params present, found {sorted(present_forbidden)}"
    )


def test_the_guard_names_a_planted_positional_smuggle() -> None:
    """PLANTED. A parameter-NAME denylist cannot see this: the digest is
    smuggled in as an unnamed FOURTH positional argument. The allowlist
    check must name the exact positional list mismatch."""
    source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, sneaky):\n"
        "        pass\n"
    )
    tree = ast.parse(source, filename="<plant: positional smuggle>")
    args = _init_args("Executor", tree, path=Path("<plant>"))

    assert _positional_names(args) != list(
        _ALLOWED_POSITIONAL["Executor"]
    ), "the plant does not exhibit the shape being detected"
    assert _positional_names(args) == ["spec", "effects", "grant", "sneaky"]


def test_the_guard_names_a_planted_positional_only_smuggle() -> None:
    """PLANTED. Positional-only parameters are another channel a keyword
    denylist cannot see; the exact ordered positional allowlist must include
    them when it decides whether a constructor shape is admissible."""
    source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, sneaky, /):\n"
        "        pass\n"
    )
    tree = ast.parse(source, filename="<plant: positional-only smuggle>")
    args = _init_args("Executor", tree, path=Path("<plant>"))

    assert [a.arg for a in args.posonlyargs if a.arg != "self"] == [
        "spec",
        "effects",
        "grant",
        "sneaky",
    ]
    assert _positional_names(args) != list(_ALLOWED_POSITIONAL["Executor"])


def test_the_guard_names_a_planted_kwargs_catch_all() -> None:
    """PLANTED. `**kwargs` admits every forbidden name at once; a
    name-based denylist alone would see nothing wrong here."""
    source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, **kwargs):\n"
        "        pass\n"
    )
    tree = ast.parse(source, filename="<plant: kwargs catch-all>")
    args = _init_args("Executor", tree, path=Path("<plant>"))

    assert args.kwarg is not None, "the plant does not exhibit the shape being detected"
    assert args.kwarg.arg == "kwargs"


def test_the_guard_names_a_planted_fed_receipt() -> None:
    """PLANTED. `_verify_host_source` still calls `require_host_source`
    exactly once, but feeds it a value that COULD have come from a caller
    (`self._host_source_receipt`) instead of the literal `None`. The
    coverage guards would pass this — the call happened — which is exactly
    why the call SHAPE, not merely its presence, must be checked."""
    source = (
        "class Executor:\n"
        "    def _verify_host_source(self):\n"
        "        self._host_source = require_host_source(\n"
        "            receipt=self._host_source_receipt\n"
        "        )\n"
    )
    tree = ast.parse(source, filename="<plant: fed receipt>")
    method = _find_method(
        _find_class("Executor", tree, path=Path("<plant>")), "_verify_host_source"
    )
    calls = _require_host_source_calls(method)

    assert len(calls) == 1, "the plant does not exhibit the shape being detected"
    assert not _is_receipt_none_only(
        calls[0]
    ), "the plant's fed-receipt call was wrongly accepted as receipt=None"


def test_the_guard_names_a_planted_extra_keyword() -> None:
    """PLANTED. `receipt=None` alone is correct; adding a second keyword
    (even one that looks innocuous, like a metadata reader) widens the call
    beyond the one shape that can never be fed by a caller."""
    source = (
        "class Executor:\n"
        "    def _verify_host_source(self):\n"
        "        self._host_source = require_host_source(\n"
        "            receipt=None, metadata=self._host_source_metadata\n"
        "        )\n"
    )
    tree = ast.parse(source, filename="<plant: extra keyword>")
    method = _find_method(
        _find_class("Executor", tree, path=Path("<plant>")), "_verify_host_source"
    )
    calls = _require_host_source_calls(method)

    assert len(calls) == 1, "the plant does not exhibit the shape being detected"
    assert not _is_receipt_none_only(
        calls[0]
    ), "the plant's extra-keyword call was wrongly accepted as receipt=None"


def test_the_guard_stays_silent_on_the_repaired_shape() -> None:
    """NEAR-MISS, MUST BE SILENT, and EXERCISED rather than assumed: a
    synthetic `__init__` carrying only ORDINARY, unrelated parameters, and a
    `_verify_host_source` calling exactly `require_host_source(receipt=None)`
    — the exact post-repair shape — must not be flagged by anything above."""
    init_source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, *, "
        "sleep=None, evidence_policy=None):\n"
        "        pass\n"
    )
    init_tree = ast.parse(init_source, filename="<near-miss: repaired __init__>")
    args = _init_args("Executor", init_tree, path=Path("<near-miss>"))

    names = set(_positional_names(args)) | _kwonly_names(args)
    assert names & _FORBIDDEN_HOST_SOURCE_PARAMS == set()
    assert _positional_names(args) == list(_ALLOWED_POSITIONAL["Executor"])
    assert args.vararg is None
    assert args.kwarg is None

    call_source = (
        "class Executor:\n"
        "    def _verify_host_source(self):\n"
        "        self._host_source = require_host_source(receipt=None)\n"
    )
    call_tree = ast.parse(call_source, filename="<near-miss: repaired call>")
    method = _find_method(
        _find_class("Executor", call_tree, path=Path("<near-miss>")),
        "_verify_host_source",
    )
    calls = _require_host_source_calls(method)
    assert len(calls) == 1
    assert _is_receipt_none_only(calls[0])


def test_the_real_classes_are_actually_found() -> None:
    """A guard that silently found zero classes would look identical to a
    fully-passing one. This is the "found something real" control."""
    run_tree = ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY))
    assert any(
        isinstance(node, ast.ClassDef) and node.name == "Executor"
        for node in ast.walk(run_tree)
    )
    recovery_tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    assert any(
        isinstance(node, ast.ClassDef) and node.name == "RecoveryExecutor"
        for node in ast.walk(recovery_tree)
    )
