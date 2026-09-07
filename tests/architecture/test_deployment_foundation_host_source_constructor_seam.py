"""No parameter of `Executor.__init__` or `RecoveryExecutor.__init__` lets a
caller state the artifact digest or the launcher digest directly.

## The defect this closes

`Executor.__init__` used to accept `host_source_installed` (a pre-built
`InstalledArtifact`, carrying the artifact digest itself) and
`host_source_probe` (a callable standing in for the launcher's source-tree
digest), and `_verify_host_source` forwarded both straight into
`require_host_source` as `installed=`/`source_tree_digest=`. A caller passing
`installed=InstalledArtifact(artifact_digest=X, ...)` alongside a receipt
naming `sha256=X` was admitted without either value ever being read from the
running interpreter or launcher — the CHANGELOG called both "test-only
override hooks", but nothing enforced that: any real caller could supply them.

`tests/architecture/test_deployment_foundation_host_source_coverage.py` proves
`_verify_host_source` is CALLED from every mutating entry point. It never
inspected what that call was fed — a gate that fires is not the same claim as
a gate that cannot be handed its own answer. This file is that second claim,
proved structurally rather than by exhausting every possible caller: it reads
`Executor.__init__`'s and `RecoveryExecutor.__init__`'s actual parameter
lists via `ast` and asserts the forbidden names are simply not there.

`RecoveryExecutor` never had `host_source_installed`/`host_source_probe` to
begin with — it is a NEW seam (Boundary 4's ruling extended to this class,
see `recovery_execution.py::RecoveryExecutor._verify_host_source`) — so its
half of this guard is a permanent invariant rather than a regression proof:
nothing here may ever add a parameter that hands over a digest directly, and
`RecoveryExecutor` additionally takes no `host_source_receipt` constructor
argument at all — the receipt is resolved internally, never accepted.
"""

from __future__ import annotations

import ast
from pathlib import Path

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

#: Parameters that hand a caller the ability to STATE a digest directly,
#: rather than have it read. Neither may exist on either constructor, ever.
_FORBIDDEN_DIGEST_PARAMS = frozenset({"host_source_installed", "host_source_probe"})

#: The one seam that must remain: an `InstalledMetadata` reader, which stands
#: in for WHERE a digest is read from, never for the digest itself.
_REQUIRED_TEST_SEAM = "host_source_metadata"


def _init_param_names(class_name: str, tree: ast.Module, *, path: Path) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == "__init__":
                    args = member.args
                    names = {a.arg for a in args.args if a.arg != "self"}
                    names |= {a.arg for a in args.kwonlyargs}
                    if args.vararg:
                        names.add(args.vararg.arg)
                    if args.kwarg:
                        names.add(args.kwarg.arg)
                    return names
            raise AssertionError(
                f"{class_name} has no __init__ in {path}; this proves nothing"
            )
    raise AssertionError(f"no class {class_name} found in {path}; this proves nothing")


# ── the named proof, on the real files ──────────────────────────────────────


def test_executor_init_has_no_parameter_that_states_a_digest_directly() -> None:
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY))
    params = _init_param_names("Executor", tree, path=RUN_PY)

    present_forbidden = params & _FORBIDDEN_DIGEST_PARAMS
    assert present_forbidden == set(), (
        f"Executor.__init__ still accepts {sorted(present_forbidden)}, which "
        "hands a caller the ability to state an artifact or launcher digest "
        "directly instead of having it read — this is the live bypass "
        "Boundary 4's follow-up ruling closes"
    )
    assert _REQUIRED_TEST_SEAM in params, (
        f"Executor.__init__ no longer accepts {_REQUIRED_TEST_SEAM!r} at all; "
        "the one legitimate test seam (an InstalledMetadata reader) must "
        "remain even though the two digest-stating hooks must not"
    )


def test_recovery_executor_init_has_no_parameter_that_states_a_digest_directly() -> (
    None
):
    tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    params = _init_param_names("RecoveryExecutor", tree, path=RECOVERY_EXECUTION_PY)

    present_forbidden = params & _FORBIDDEN_DIGEST_PARAMS
    assert present_forbidden == set(), (
        f"RecoveryExecutor.__init__ accepts {sorted(present_forbidden)} — "
        "the same bypass shape closed on Executor, reintroduced here"
    )
    assert "host_source_receipt" not in params, (
        "RecoveryExecutor.__init__ accepts host_source_receipt: the ruling "
        "for this class specifically is that the receipt is RESOLVED "
        "internally, from the installed version, never handed in by a "
        "caller — a receipt parameter here is the same class of bypass by a "
        "different name"
    )
    assert _REQUIRED_TEST_SEAM in params, (
        f"RecoveryExecutor.__init__ does not accept {_REQUIRED_TEST_SEAM!r}; "
        "without it there is no way to admit a genuine install in a test"
    )


# ── sensitivity: the plant is NAMED ─────────────────────────────────────────


def test_the_guard_names_a_planted_digest_stating_parameter() -> None:
    """PLANTED, in memory. A synthetic `Executor` whose `__init__` regrows
    exactly the removed shape must be caught, and caught BY NAME rather than
    merely triggering a generic failure."""
    source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, *, "
        "host_source_receipt=None, host_source_installed=None, "
        "host_source_metadata=None, host_source_probe=None):\n"
        "        pass\n"
    )
    tree = ast.parse(source, filename="<plant: regrown digest-stating params>")
    params = _init_param_names("Executor", tree, path=Path("<plant>"))

    present_forbidden = params & _FORBIDDEN_DIGEST_PARAMS
    assert present_forbidden == _FORBIDDEN_DIGEST_PARAMS, (
        "the plant does not exhibit the shape being detected: expected both "
        f"forbidden params present, found {sorted(present_forbidden)}"
    )


# ── near-miss: the real, repaired shape must stay silent ────────────────────


def test_the_guard_stays_silent_on_the_repaired_shape() -> None:
    """NEAR-MISS, MUST BE SILENT, and EXERCISED rather than assumed: a
    synthetic `__init__` carrying ONLY the legitimate receipt and metadata
    parameters — the exact post-repair shape — must not be flagged."""
    source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, *, "
        "host_source_receipt=None, host_source_metadata=None):\n"
        "        pass\n"
    )
    tree = ast.parse(source, filename="<near-miss: repaired shape>")
    params = _init_param_names("Executor", tree, path=Path("<near-miss>"))

    assert params & _FORBIDDEN_DIGEST_PARAMS == set(), (
        "the near-miss plant was wrongly flagged: it carries neither "
        "forbidden parameter"
    )
    assert _REQUIRED_TEST_SEAM in params


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
