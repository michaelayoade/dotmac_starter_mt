"""No parameter of `Executor.__init__` or `RecoveryExecutor.__init__` lets a
caller state the artifact digest or the launcher digest, by ANY route.

## The defect this closes — twice, now

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
between.

Michael's ruling: BOTH parameters are also removed, from BOTH classes, with
nothing put in their place. There is no test-only seam left on either
constructor at all. This file's forbidden set now covers every name that
has, at some point, let a caller state a digest or its two ingredients
(receipt, metadata) directly.

`tests/architecture/test_deployment_foundation_host_source_coverage.py` (and
its `RecoveryExecutor` sibling) prove `_verify_host_source` is CALLED from
every mutating entry point. Neither ever inspected what that call was fed —
a gate that fires is not the same claim as a gate that cannot be handed its
own answer. This file is that second claim, proved structurally rather than
by exhausting every possible caller: it reads `Executor.__init__`'s and
`RecoveryExecutor.__init__`'s actual parameter lists via `ast` and asserts
the forbidden names are simply not there.
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


def test_executor_init_accepts_no_host_source_parameter_at_all() -> None:
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY))
    params = _init_param_names("Executor", tree, path=RUN_PY)

    present_forbidden = params & _FORBIDDEN_HOST_SOURCE_PARAMS
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
    params = _init_param_names("RecoveryExecutor", tree, path=RECOVERY_EXECUTION_PY)

    present_forbidden = params & _FORBIDDEN_HOST_SOURCE_PARAMS
    assert present_forbidden == set(), (
        f"RecoveryExecutor.__init__ still accepts {sorted(present_forbidden)} "
        "— the same bypass shape closed on Executor, reintroduced here"
    )


# ── sensitivity: the plant is NAMED ─────────────────────────────────────────


def test_the_guard_names_a_planted_regrowth_of_the_removed_seam() -> None:
    """PLANTED, in memory. A synthetic `Executor` whose `__init__` regrows
    the exact "narrowed but still forgeable" shape the independent review
    found (`host_source_receipt`/`host_source_metadata`, with the two
    round-1 parameters also present for good measure) must be caught, and
    caught BY NAME."""
    source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, *, "
        "host_source_receipt=None, host_source_installed=None, "
        "host_source_metadata=None, host_source_probe=None, "
        "host_source_receipts_dir=None):\n"
        "        pass\n"
    )
    tree = ast.parse(source, filename="<plant: regrown host-source seam>")
    params = _init_param_names("Executor", tree, path=Path("<plant>"))

    present_forbidden = params & _FORBIDDEN_HOST_SOURCE_PARAMS
    assert present_forbidden == _FORBIDDEN_HOST_SOURCE_PARAMS, (
        "the plant does not exhibit the shape being detected: expected all "
        f"forbidden params present, found {sorted(present_forbidden)}"
    )


# ── near-miss: the real, repaired shape must stay silent ────────────────────


def test_the_guard_stays_silent_on_the_repaired_shape() -> None:
    """NEAR-MISS, MUST BE SILENT, and EXERCISED rather than assumed: a
    synthetic `__init__` carrying only ORDINARY, unrelated parameters — the
    exact post-repair shape, where NOTHING host-source-related remains at
    all — must not be flagged."""
    source = (
        "class Executor:\n"
        "    def __init__(self, spec, effects, grant, *, "
        "sleep=None, evidence_policy=None):\n"
        "        pass\n"
    )
    tree = ast.parse(source, filename="<near-miss: repaired shape>")
    params = _init_param_names("Executor", tree, path=Path("<near-miss>"))

    assert params & _FORBIDDEN_HOST_SOURCE_PARAMS == set(), (
        "the near-miss plant was wrongly flagged: it carries no host-source "
        "parameter of any kind"
    )
    assert params == {
        "sleep",
        "evidence_policy",
    }, "the near-miss plant was not exercised as the shape it claims to be"


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
