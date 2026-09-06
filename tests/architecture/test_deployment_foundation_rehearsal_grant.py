"""Control issues the rehearsal grant. This facility consumes one and mints none.

## The defect

`rehearsal.py` owns Lane 3's evidence and owns it correctly. What sat beside it
was an authorization gap with a name: item 8 is `provoked_rollback`, *"Rollback,
provoked rather than simulated"*, and provoking it means seeding rules that
belong to nobody into `DOCKER-USER` and `INPUT` and arming a condition a live
deployment cannot reconcile. A named destructive act, an executor for it in
`scripts/lane3_provocation.py`, and **no grant type anywhere**.

`authorization.py` states the shape this repeats: *"the party being restrained
is the party answering the question."* A provocation harness with no grant is
that sentence with the question removed.

## What this file proves, and the distinction that is the whole test

`rehearsal_grant.py` verifies a grant and cannot issue one. The static half of
that claim is here: **no function in this facility returns a
`RehearsalGrantV1`.**

The near-miss the detector must stay SILENT on is `rehearsal.build_receipt`,
and it is measured against the real tree rather than a fixture. Minting
EVIDENCE about a run that happened is this facility's job; minting PERMISSION is
not. A naive "no rehearsal-related factories" rule catches both, reads as
stricter, and would forbid the thing Lane 3 is built out of.

## Why the plants are synthetic, and the one exemption is premise-checked

A plant left on disk becomes part of the tree the guard approves — the trap
`test_deployment_foundation_single_executor` names. Every plant below is parsed
from a string this file writes.

One function is allowed to construct a `RehearsalGrantV1`: the verifier, which
is the only route from attested material to typed terms. That exemption states
an ENFORCEABLE premise rather than a name — it holds only while that function
takes a keyword-only `verifier` and returns `VerifiedRehearsalGrant` — and
`test_the_exemption_is_not_a_hole` plants a same-named function that has lost
the premise and shows it is reported like any other issuer.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "packages/dotmac-deployment-foundation"
SRC = PACKAGE / "src/dotmac_deployment_foundation"

#: EVERY entry-point family that can hold a factory, not one directory. The
#: same extent argument `test_deployment_foundation_single_executor` makes and
#: paid for: a guard scoped to one directory reads as covering a property when
#: it covers a location, and `scripts/` is where the provocation harness lives.
SCANNED_ROOTS = (SRC, REPO / "scripts")

#: The type Control issues and this facility may never produce.
GRANT = "RehearsalGrantV1"

#: The one construction site, by file and function. Allowed only while the
#: premise below holds; see `_premise_holds`.
SOLE_VERIFIER = (
    "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
    "/rehearsal_grant.py::verify_rehearsal_grant"
)


def _scanned_files() -> list[Path]:
    found = [path for root in SCANNED_ROOTS for path in sorted(root.rglob("*.py"))]
    assert found, "the scan found no files; every assertion below would be vacuous"
    return found


def _premise_holds(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """The verifier exemption's premise, checked rather than asserted.

    A function may construct a grant only if it is the seam that turns ATTESTED
    material into typed terms: it must be handed a verifier it did not choose,
    and it must hand back the verified wrapper rather than the bare document.
    A function of the right NAME that lost either property is not that seam.
    """
    takes_verifier = any(arg.arg == "verifier" for arg in node.args.kwonlyargs)
    returns_verified = (
        node.returns is not None
        and "VerifiedRehearsalGrant" in ast.unparse(node.returns)
        and GRANT not in ast.unparse(node.returns)
    )
    return takes_verifier and returns_verified


def _issuers(tree: ast.AST, *, label: str) -> dict[str, str]:
    """Every function that hands a caller a `RehearsalGrantV1`, and why.

    Two rules, because either alone is evadable:

    * a declared return annotation naming the type — the honest factory;
    * constructing one and returning ANYTHING — which is how a factory written
      without an annotation, or one that returns the grant inside a tuple or a
      local variable, still hands a caller a grant it minted.

    A function that merely CONSUMES one (a parameter annotation) is not an
    issuer and is deliberately not matched: refusing an unauthorized grant is
    the entire point of this module, and a rule that flagged consumers would
    forbid the repair.
    """
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        key = f"{label}::{node.name}"
        if node.returns is not None and GRANT in ast.unparse(node.returns):
            found[key] = f"declares -> {ast.unparse(node.returns)}"
            continue
        constructs = any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == GRANT
            for inner in ast.walk(node)
        )
        gives_back = any(
            isinstance(inner, ast.Return) and inner.value is not None
            for inner in ast.walk(node)
        )
        if constructs and gives_back:
            if _premise_holds(node):
                found[key] = "sole verifier (premise holds)"
            else:
                found[key] = f"constructs {GRANT} and returns"
    return found


def _tree_issuers() -> dict[str, str]:
    issuers: dict[str, str] = {}
    for path in _scanned_files():
        label = str(path.relative_to(REPO))
        issuers |= _issuers(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
            label=label,
        )
    return issuers


def _functions_scanned() -> set[str]:
    names: set[str] = set()
    for path in _scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        label = str(path.relative_to(REPO))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(f"{label}::{node.name}")
    return names


# ── the population is real ──────────────────────────────────────────────────


def test_the_scan_actually_reaches_both_halves_of_the_distinction() -> None:
    """Non-vacuity, first. Both subjects must be IN the scanned population.

    A sweep that silently missed `rehearsal.py` would report "no issuers" and
    would also have proved nothing about the near-miss it exists to spare. This
    asserts the two functions the whole file is about were actually read.
    """
    scanned = _functions_scanned()
    receipt = (
        "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
        "/rehearsal.py::build_receipt"
    )
    assert receipt in scanned, "the evidence factory was not scanned"
    assert SOLE_VERIFIER in scanned, "the verifier was not scanned"


# ── the named proof ─────────────────────────────────────────────────────────


def test_this_facility_exports_no_function_that_returns_a_rehearsal_grant() -> None:
    """Foundation consumes a grant and cannot issue one.

    The exemption is the verifier and nothing else. Two-directional: a NEW
    issuer is the boundary violation this guard exists for, and the exemption
    DISAPPEARING is news that must be recorded here too, because an allowance
    that evaporates silently stops distinguishing "the seam moved" from "the
    seam was deleted".
    """
    issuers = _tree_issuers()
    assert set(issuers) == {SOLE_VERIFIER}, (
        "the set of functions producing a RehearsalGrantV1 moved.\n"
        f"  now:      {sorted(issuers)}\n"
        f"  expected: [{SOLE_VERIFIER!r}]\n"
        "A NEW one is an issuer on the consuming side: this facility would be "
        "minting its own permission to break a target, which is the entire act "
        "the rehearsal/provocation boundary forbids. A MISSING one means the "
        "verifier stopped being the single route from attested material to "
        "typed terms."
    )
    assert issuers[SOLE_VERIFIER] == "sole verifier (premise holds)", (
        "the verifier still constructs a grant but no longer looks like the "
        f"attestation seam: {issuers[SOLE_VERIFIER]}"
    )


# ── sensitivity: the plants ─────────────────────────────────────────────────


def _synthetic(source: str) -> dict[str, str]:
    return _issuers(ast.parse(source), label="plant.py")


def test_a_planted_annotated_issuer_is_named() -> None:
    """The obvious factory — the one a well-meaning successor writes."""
    planted = _synthetic(
        "def build_rehearsal_grant(**terms) -> RehearsalGrantV1:\n"
        "    return RehearsalGrantV1(**terms)\n"
    )
    assert set(planted) == {"plant.py::build_rehearsal_grant"}


def test_a_planted_unannotated_issuer_is_named() -> None:
    """The evasion: no annotation, and the grant leaves inside a local.

    An annotation-only detector passes this, which is why the second rule
    exists. This is not a hypothetical evasion — it is what an untyped helper
    looks like by default.
    """
    planted = _synthetic(
        "def _mint(statement, signature):\n"
        "    grant = RehearsalGrantV1(statement, signature)\n"
        "    return grant, 'issued'\n"
    )
    assert set(planted) == {"plant.py::_mint"}


def test_the_exemption_is_not_a_hole() -> None:
    """A function with the RIGHT NAME and the wrong shape is still reported.

    The exemption is a premise, not a name. If it were a name, adding a
    `verify_rehearsal_grant` that took no verifier and returned the bare
    document would be an issuer wearing the one word the guard trusts — which
    is exactly how an allowlist entry becomes a bypass.
    """
    without_verifier = _synthetic(
        "def verify_rehearsal_grant(material) -> VerifiedRehearsalGrant:\n"
        "    return RehearsalGrantV1(**material)\n"
    )
    assert without_verifier == {
        "plant.py::verify_rehearsal_grant": f"constructs {GRANT} and returns"
    }

    returning_the_document = _synthetic(
        "def verify_rehearsal_grant(material, *, verifier) -> RehearsalGrantV1:\n"
        "    return RehearsalGrantV1(**verifier.attest_rehearsal(material))\n"
    )
    assert returning_the_document == {
        "plant.py::verify_rehearsal_grant": "declares -> RehearsalGrantV1"
    }


# ── the near-miss that must stay silent ─────────────────────────────────────


def test_the_evidence_factory_is_not_flagged() -> None:
    """`build_receipt` mints EVIDENCE, and that is this facility's job.

    Measured on the REAL `rehearsal.py`, not a fixture, because the claim is
    about the tree: the guard runs over a file containing a large,
    rehearsal-named, digest-bearing factory and says nothing about it. A rule
    written as "no rehearsal factories" would catch it, read as stricter, and
    forbid the mechanism Lane 3's whole anti-drift design rests on.
    """
    path = SRC / "rehearsal.py"
    issuers = _issuers(
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        label=str(path.relative_to(REPO)),
    )
    assert issuers == {}, (
        "the detector flagged something in the evidence module. `build_receipt` "
        "returns a RehearsalReceiptV1 — a record of what happened — and it must "
        f"remain distinguishable from a {GRANT}, which is permission."
    )

    # And prove the near-miss was actually EXERCISED rather than absent: the
    # file really does contain a factory the sloppier rule would have caught.
    source = path.read_text(encoding="utf-8")
    assert "def build_receipt(" in source
    assert "-> RehearsalReceiptV1:" in source


def test_a_consumer_of_a_grant_is_not_flagged() -> None:
    """Refusing a grant is the repair; a rule that forbade it would be inverted."""
    consumer = _synthetic(
        "def refuse_unless_permitted(grant: RehearsalGrantV1) -> None:\n"
        "    raise PreconditionFailed('no')\n"
    )
    assert consumer == {}

    inspector = _synthetic(
        "def describe(grant: RehearsalGrantV1) -> str:\n" "    return grant.grant_id\n"
    )
    assert inspector == {}


# ── the facility ships no verifier of its own ───────────────────────────────


def test_no_default_rehearsal_verifier_is_shipped() -> None:
    """A verifier with a built-in fallback is an issuer wearing another word.

    `ExecutionBindings.rehearsal_grant_verifier` must default to None. A
    facility that could attest its own grant would be self-authorizing through
    the one seam that exists to stop it, and the refusal would still read as a
    verification.
    """
    path = SRC / "execution_bindings.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    defaults: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "rehearsal_grant_verifier"
        ):
            defaults[node.target.id] = (
                "" if node.value is None else ast.unparse(node.value)
            )
    assert defaults == {"rehearsal_grant_verifier": "None"}, (
        "the rehearsal verifier binding is missing or no longer defaults to "
        f"None: {defaults}"
    )
