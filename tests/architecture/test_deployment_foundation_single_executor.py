"""One executor, and one way to reach a host: through an authorization.

## The defect

`dotmac-deploy exposure-apply --execute` recreated a product's containers and
rewrote its rules in `DOCKER-USER` and `INPUT` — chains SHARED with everything
else on the host — against a live target. Its parser offered no
`--authorization` flag at all. Not "the check was somewhere else": there was no
way for an operator to supply a Control receipt to that path, and nothing in it
looked for one.

`authorization.py` opens by saying the distance between printing a plan and
mutating production must not be "one boolean flag that the caller supplied to
itself", and that the seam is closed by construction because `Executor` cannot
be built without an `ExecutionGrant`. Both sentences were true and neither
covered `exposure.ExposureTransaction`, which was a second executor: it took a
lock, ordered effects, verified, and compensated, with no grant, no frozen plan
and no receipt anywhere in the file.

## Why the plant is synthetic

The obvious sensitivity proof — add a violating subcommand, watch it fail,
remove it — is the trap: the commit that lands the fixture changes the state
the guard reads, so a plant left in the tree becomes part of the tree the guard
approves. Both plants here are built IN MEMORY, against a parser and an AST
this test constructs, so the guard is exercised on a violating input on every
run without a violating input ever existing on disk.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

import pytest
from dotmac_deployment_foundation.cli import build_parser

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"

#: EVERY entry-point family that can reach a host, not one directory.
#:
#: The first version of this file scanned `SRC` alone and shipped green while
#: `scripts/exposure_rehearsal_runner.py` still imported and constructed the
#: deleted class — CI found it at collection, and this guard, whose entire
#: subject is that class, did not. That is `AGENTS.md` rule 25's extent shape
#: exactly: a guard scoped to one directory reads as covering a property when
#: it covers a location. `scripts/` is where Lane 3, the release facility and
#: the provocation harness live, and all three drive real hosts.
#:
#: DERIVED per root rather than declared per file, so a new script joins the
#: population by existing.
SCANNED_ROOTS = (SRC, REPO / "scripts")


def _scanned_files() -> list[Path]:
    found = [path for root in SCANNED_ROOTS for path in sorted(root.rglob("*.py"))]
    assert found, "the scan found no files; every assertion below would be vacuous"
    return found


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO))


#: `--execute` subcommands that reach an effect WITHOUT offering
#: `--authorization`, with the boundary that owns each. This is a
#: TWO-DIRECTIONAL ratchet: it fails when an entry appears AND when one
#: disappears without being removed here, because a silently-shrinking
#: exemption list is how "reviewed and correct" gets confused with
#: "grandfathered".
UNAUTHORIZED_EXECUTE_BACKLOG: dict[str, str] = {
    # Restores into a target `backup.RestoreRehearsal` requires to be
    # DISPOSABLE and never the product's — a different risk class from a
    # deployment, but still an `--execute` that no Control decision gates.
    #
    # THE PREVIOUS RATIONALE HERE WAS WRONG, AND MEASURING IT IS THE REPAIR.
    # It read: owned by the rehearsal/provocation grant boundary, unrepairable
    # only because the issuing counterparty did not exist yet. The consumer now
    # exists — `rehearsal_grant.py`, which verifies a Control-issued
    # `RehearsalGrant.v1` and cannot mint one — and this subcommand STILL
    # cannot use it.
    #
    # Read against `dotmac_deployment_control` PR #45 at
    # `3a06488cd34c42caa93b9d9bac89fd203b738246`: the grant that PR issues is a
    # PROVOCATION grant. `ProvocableRefusal` is closed at one member,
    # `plan_verification_refusal`, bound to Lane 3 item 8, and the terminal is
    # DERIVED from it as `rolled_back`. A restore rehearsal is not that act: it
    # creates a fresh isolated cluster, restores into it, and destroys it on the
    # adjudicator's verdict. It is supposed to SUCCEED. There is no member of
    # Control's vocabulary that names it and no signer purpose issued for it.
    #
    # So gating it would mean inventing a refusal member on this side — which is
    # `authorization.OPERATIONS`'s recorded `recover` mistake exactly, arrived at
    # from the other direction: naming an act because a chain exists to wrap
    # around it rather than because a counterparty authorizes it. The entry
    # therefore STAYS, with a measured premise instead of a waiting one, and the
    # act that WAS repairable — the item 8 provocation in
    # `scripts/lane3_provocation.py` — now requires a `ProvocationPermit`.
    #
    # What would close this: a Control-side grant naming the restore-rehearsal
    # act, or a decision that `_execute_restore_rehearsal`'s existing structural
    # premise (it acts only on a target it created itself, and `RecoveryEffects`
    # has no method that reaches a running deployment) is sufficient and the
    # entry should be retired rather than gated. That decision is not this
    # boundary's to take.
    "restore-rehearsal": (
        "restore rehearsal is not a provocation; Control PR #45's closed "
        "vocabulary cannot name it"
    ),
}


def _unauthorized_execute_subcommands(parser: argparse.ArgumentParser) -> set[str]:
    """Every subcommand offering `--execute` and not `--authorization`."""
    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert subparsers, "the parser exposes no subcommands; this proves nothing"
    found = set()
    for name, sub in subparsers[0].choices.items():
        options = {opt for action in sub._actions for opt in action.option_strings}
        if "--execute" in options and "--authorization" not in options:
            found.add(name)
    return found


def test_every_execute_subcommand_offers_an_authorization() -> None:
    """The named proof. This failed on `exposure-apply` before the repair.

    The assertion is about the SURFACE rather than about a call: a path an
    operator cannot hand a receipt to cannot be checking one, whatever its body
    does. That makes this cheap to run and impossible to satisfy by accident.
    """
    unauthorized = _unauthorized_execute_subcommands(build_parser())
    assert unauthorized == set(UNAUTHORIZED_EXECUTE_BACKLOG), (
        "the set of `--execute` subcommands with no `--authorization` moved.\n"
        f"  now:      {sorted(unauthorized)}\n"
        f"  recorded: {sorted(UNAUTHORIZED_EXECUTE_BACKLOG)}\n"
        "A NEW entry is a second path from a flag to a mutation, which is the "
        "whole defect this guard exists for. A REMOVED entry is good news that "
        "must be recorded here too — an exemption list that shrinks silently "
        "stops distinguishing 'repaired' from 'never re-measured'."
    )


def test_the_execute_surface_guard_still_bites() -> None:
    """Sensitivity, on a parser this test builds rather than on the real one.

    A check over a clean tree passes for the wrong reason. This plants exactly
    the shape the guard is for — and plants it in memory, so committing this
    file cannot turn the fixture into part of what the guard approves.
    """
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    offender = sub.add_parser("plant-deploy")
    offender.add_argument("--execute", action="store_true")

    assert _unauthorized_execute_subcommands(parser) == {"plant-deploy"}


def test_the_execute_surface_guard_does_not_bite_a_near_miss() -> None:
    """It must stay silent on the two shapes that are correct.

    An `--execute` that DOES offer `--authorization` is the normal deploy path,
    and a subcommand with neither is a read-only one. A guard that flagged
    either would be flagging the behaviour it exists to protect.
    """
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    authorized = sub.add_parser("plant-authorized")
    authorized.add_argument("--execute", action="store_true")
    authorized.add_argument("--authorization")
    sub.add_parser("plant-readonly").add_argument("--descriptor")

    assert _unauthorized_execute_subcommands(parser) == set()


# ── the second executor, structurally ───────────────────────────────────────

#: The four calls that, performed in sequence by one type, ARE an executor:
#: take a lock, mutate, re-observe, compensate. `engine/run.py` is the one
#: place allowed to hold that shape.
_MUTATORS = frozenset({"apply_compose", "replace_rules", "restore_chains"})


def _modules_ordering_host_mutation() -> dict[str, set[str]]:
    """Every module whose own code calls more than one exposure mutator."""
    offenders: dict[str, set[str]] = {}
    for path in _scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _MUTATORS
        }
        if len(called) > 1:
            offenders[_rel(path)] = called
    return offenders


def test_only_the_engine_orders_exposure_mutations() -> None:
    """`exposure.py` describes the seam; it must no longer drive it.

    One module calling one mutator is a provider implementing its own method.
    More than one, in sequence, is an ORDER of effects — and an order of effects
    against a host is the thing `Executor` exists to be the only holder of.
    """
    assert _modules_ordering_host_mutation() == {
        "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
        "/engine/run.py": {"replace_rules", "restore_chains"},
    }, (
        "a module other than the engine is sequencing host mutations again. "
        "That is how `ExposureTransaction` came to be a second executor: it "
        "was never declared as one, it simply accumulated the calls."
    )


def test_the_second_executor_guard_still_bites(tmp_path: Path) -> None:
    """Sensitivity, again on synthetic source rather than on the tree."""
    planted = tmp_path / "plant.py"
    planted.write_text(
        "def go(effects):\n"
        "    effects.apply_compose(['up'], timeout_seconds=1)\n"
        "    effects.replace_rules('ipv4', 'DOCKER-USER', ())\n",
        encoding="utf-8",
    )
    tree = ast.parse(planted.read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _MUTATORS
    }
    assert len(called) > 1, "the plant does not exhibit the shape being detected"


def _identifiers(path: Path) -> set[str]:
    """Every name this module DEFINES or REFERS TO, excluding prose.

    Deliberately not a substring scan of the file. The docstrings in
    `exposure.py`, `cli.py` and `providers/exposure_host.py` explain at length
    what `ExposureTransaction` was and why it is gone, and that history is the
    most valuable thing in those files — a guard that forbade the WORD would
    force the explanation out, leaving a codebase that had merely forgotten.
    What must not come back is the identifier.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                names.add(node.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            continue
    return names


def test_no_module_still_names_the_deleted_transaction() -> None:
    """`ExposureTransaction` is gone, and must not come back as an identifier.

    Named separately from the mutator check because the two fail differently:
    this one catches a re-introduction by NAME — a shim, a re-export, a
    compatibility alias — which the mutator count would not see until the shim
    grew a body.
    """
    survivors = [
        _rel(path)
        for path in _scanned_files()
        if {"ExposureTransaction", "apply_exposure"} & _identifiers(path)
    ]
    assert survivors == [], (
        f"{survivors} still define or reference ExposureTransaction. A "
        "compatibility alias is a fork with a friendly name: it keeps the "
        "second ordering reachable while reading in a diff as a courtesy to "
        "existing callers."
    )


def test_the_deleted_name_guard_still_bites(tmp_path: Path) -> None:
    """Sensitivity, on synthetic source — and it must ignore prose.

    Both directions matter. A module that re-imports the class must be caught;
    a module that merely EXPLAINS why the class is gone must not be, or the
    guard's first casualty is the record of what it is guarding against.
    """
    reintroduced = tmp_path / "shim.py"
    reintroduced.write_text(
        "from dotmac_deployment_foundation.exposure import ExposureTransaction\n",
        encoding="utf-8",
    )
    assert "ExposureTransaction" in _identifiers(reintroduced)

    prose_only = tmp_path / "history.py"
    prose_only.write_text(
        '"""ExposureTransaction was deleted; this explains why."""\n',
        encoding="utf-8",
    )
    assert "ExposureTransaction" not in _identifiers(prose_only)


@pytest.mark.parametrize("symbol", ["ExposureTransaction", "apply_exposure"])
def test_the_retired_surface_is_UNAVAILABLE_not_merely_unmentioned(
    symbol: str,
) -> None:
    """Assert the import FAILS. A test that stops naming the class proves nothing.

    This is the difference between evidence and absence. Every other test in
    this file was rewritten to drive `Executor`, and a reader could reasonably
    ask whether the old surface went with it or is simply no longer exercised —
    a shim, a lazy re-export or a `__getattr__` fallback would leave every one
    of them green while the second executor stayed reachable. So the retirement
    is asserted directly, at both surfaces a consumer can reach:

    * the module the name lived in, where `from ... import X` must raise; and
    * the package root, where a re-export would be the tempting courtesy.

    The import is executed as the STATEMENT a consumer would actually write.
    `__import__(..., fromlist=[symbol])` was the first attempt and is wrong: it
    returns the module regardless and the missing name surfaces later as an
    `AttributeError`, so the test would have asserted the wrong exception and
    failed against a correctly retired surface. `from X import Y` is what
    breaks in a consumer's file, so `from X import Y` is what is asserted.

    A real import rather than a `hasattr` check, too: `hasattr` is satisfied by
    a module-level `__getattr__` returning something, which is precisely the
    shape a compatibility shim takes.
    """
    import dotmac_deployment_foundation as facility

    with pytest.raises(ImportError):
        exec(  # noqa: S102 - the statement under test IS an import statement
            f"from dotmac_deployment_foundation.exposure import {symbol}",
            {},
        )

    assert not hasattr(facility, symbol), (
        f"{symbol} is reachable from the package root. A re-export keeps the "
        "removed ordering callable while reading in a diff as a kindness to "
        "existing consumers"
    )
    assert symbol not in facility.__all__
