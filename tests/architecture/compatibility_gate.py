"""The Kernel-successor compatibility gate — Slice 4.

Three product-specific evaluations (Academy, ERP, Sub), combined by one
**all-of** release gate: any single product refusing blocks the freeze.
Findings are complete and deterministic — same inputs, same findings, every
time, and nothing here samples or guesses.

**Compatibility only, never adoption.** Every evaluation below answers "is the
Kernel's published successor surface (``dotmac_kernel.session_runtime``,
``dotmac_kernel.db``) sufficient for this product's stated need". The
Kernel-side half is answered by reading KERNEL SOURCE in THIS repository via
``ast`` — never by importing, executing, or constructing a runtime and
asserting it works. Whether a product HAS adopted that surface is a fact
about the product's own tree, established by fetching, verifying and parsing
a fixed, typed readiness record from that product's own repository (see
"The runner" below). A verified record proves compatibility, never adoption
— ADR-0006 § 5, "reference proof is not adoption" — and adoption stays 0/3.

Corrected shape, second ruling (2026-09-09)
------------------------------------------------

An earlier shape asked a BINDER to supply a free-form claim (an ancestry
statement, an ``artefact`` path, caller-authored booleans) alongside the
bound revision. That shape made a wrong commit VISIBLE to a reviewer; it did
not make the evaluator CAPABLE of refusing it — nothing verified ancestry,
fetched the repository, or read the named artefact. `"a" * 40` plus a
fabricated claim evaluated as satisfied: the dominant defect class this
whole programme exists to close, sitting inside the gate built to enforce
it.

A further measurement sharpened the target past "verify the claim harder":
two different real ERP commits — the correct readiness commit and an
explicitly excluded, wrong one — hash to the IDENTICAL SHA-256 at the same
artefact path, because the file was unchanged between them. A digest alone
cannot separate the right commit from the wrong one either. In Michael's own
words, this is why commit identity stopped being the question at all:

    "commit narrative and 'where this first landed' are irrelevant. A
    protected-main revision is acceptable when it carries a valid
    product-owned record for the correct subject."

The fix is not a sharper claim shape; it is removing the claim and having
the gate VERIFY, and moving the pass/fail booleans to the only party that
can actually check them — the product's own CI, validating its own record
against its own tree, not Starter guessing about a tree it cannot see.

The fixed product specification
-----------------------------------

`PRODUCT_SPECS` is STARTER-OWNED and closed: for each product, the exact
repository, the exact repository-relative path of a TYPED (JSON) readiness
record (`docs/kernel-runtime-readiness.json`, uniform across all three), and
the exact `subject` string that record must self-declare. None of this is
selectable from `compatibility_gate_bindings.json` — a binding supplies a
`revision` and nothing else (`load_default_bindings` refuses any other
field). Existing product Markdown/prose documentation stays explanatory; it
is never parsed as a machine contract — only this fixed-path JSON record is.

The fixed record envelope (owned by Michael; raise a correction rather than
diverging)
-------------------------------------------------------------------------------

::

    {
      "schema": "kernel-runtime-readiness.v1",
      "product": "<dotmac_sub | dotmac_erp | dotmac_academy_app>",
      "subject": "<the fixed subject Starter specifies for that product>",
      "requirements": [
        {"id": "<stable slug>", "statement": "<what must hold>",
         "satisfied": true, "source_reference": "<path:line in this product's tree>"}
      ],
      "composition": [
        {"declaration": "<what this product composes>",
         "source_reference": "<path:line>"}
      ],
      "source_references": ["<path>", "..."]
    }

`satisfied` booleans are legitimate HERE, and only here: authored by the
product's own CI, in the tree they describe, validated against that tree by
that product's own pipeline — the inversion of the earlier defect, where the
boolean was authored by the party (Starter) that could not check it.

The runner
--------------

`fetch_readiness_record(spec, revision)` is the verifying half the earlier
shape was missing:

1. Locates a LOCAL clone of `spec.repository` via the fixed
   `spec.clone_env_var` — an infrastructure coordinate, never a per-binding
   one. **Absent** (the env var is simply unset) is the ordinary, expected
   "no CI runner wired up yet" state → refuses (`evidence_incomplete`).
   **Set but broken** (the directory does not exist, or `git` fails in a way
   that is not a legitimate "not an ancestor" answer) is an INFRASTRUCTURE
   FAILURE, not evidence about the revision, and `fetch_readiness_record`
   RAISES rather than returning a soft refusal — see "Infrastructure
   failure is not a status" below.
2. Verifies `revision` is an ancestor of that clone's `origin/main` via
   `git merge-base --is-ancestor` — an ACTUAL check. Refuses on a genuine
   negative answer (`not_on_protected_main`).
3. Reads the exact blob at `<revision>:<READINESS_RECORD_PATH>` via `git
   show`. Missing → refuses (`evidence_incomplete`).
4. Computes the blob's SHA-256 (see "The digest is output, never input"),
   parses it as JSON, and requires `schema`, `product` (== `spec.repository`)
   and `subject` (== `spec.subject`) to match exactly, and `requirements` /
   `composition` / `source_references` to be structurally well-formed. Any
   mismatch or malformed shape refuses (`evidence_incomplete`).
5. Product-specific facts come from the verified record's `requirements` —
   each evaluation looks up its own fixed requirement ids and reads their
   product-authored `satisfied` booleans. Never from caller-supplied input.

**No new public status vocabulary.** Every refusal above lands on one of the
two existing statuses, `not_on_protected_main` or `evidence_incomplete`.

Infrastructure failure is not a status
-------------------------------------------

The same lesson Control's `resolve_current_root` correction already drew: an
infrastructure failure reported as a deliberate state is a conflation, not a
convenience. A network or checkout failure must FAIL THE REQUIRED CI JOB,
never read as `evidence_incomplete` (which means "nobody has produced
evidence yet", a legitimate, expected, waiting state) or as `unbound` (which
means "no revision was named at all", reserved for an INTENTIONALLY absent
binding). `_clone_path_for` therefore returns `None` only when the env var is
genuinely unset; if it is set but the directory is missing, or if `git
merge-base` exits with anything other than the two codes that legitimately
mean "is" or "is not" an ancestor, this module raises `RuntimeError` instead
of returning a `FetchOutcome` — the caller (a required CI job) then fails
loudly, exactly as an infrastructure failure should.

The digest is output, never input
--------------------------------------

`EvaluationResult.artefact_digest` reports the SHA-256 this module itself
computed from the fetched blob's bytes. It is populated ONLY by
`fetch_readiness_record` from bytes THIS module read; nothing in
`ProductBinding`, `compatibility_gate_bindings.json`, or any other accepted
input can set or influence it — there is no `digest` field anywhere on the
input side. A caller cannot supply a digest and have it compared, trusted, or
otherwise treated as authoritative, which is exactly why a caller-supplied
digest would have been as weak as the claim it replaces (see the identical-
digest measurement above).

Which requirement ids Starter reads, and the verdict rule
-------------------------------------------------------------

A product's readiness record is free to carry any requirement ids it likes
— products keep their own granular, descriptive ids, and a record may carry
more than Starter consumes (guard 7). `PRODUCT_SPECS[product].requirements`
is the ONE place Starter names which of those product-owned ids it reads,
each as a `RequirementSpec(requirement_id, role)`. No evaluator function
spells a requirement-id string itself; every lookup goes through
`_evaluate_readiness_requirements`, which walks exactly this table (guard 4,
`test_no_evaluator_local_requirement_id_literals`).

`role` is closed to two values:

- `"compatibility"` — the record's `satisfied` boolean feeds the verdict
  directly. `False` (or the id being absent from the record) refuses the
  product.
- `"adoption_state"` — an OBSERVATION about whether the product has cut
  over yet. Always reported in `findings`, but its `satisfied` value never
  by itself refuses compatibility.

**The verdict rule.** Academy's five selected ids are ALL
`"adoption_state"`: its four eager reference imports and its absent runtime
binding are current adoption debt, not proof the Kernel successor's API is
inexpressible for Academy. Requiring them `true` before publication would
turn this compatibility gate into an adoption gate while adoption is 0/3 —
so Academy's evaluation reports every `false` value plainly and does not
refuse compatibility because of them. ERP and Sub's selected compatibility
ids still refuse the product the moment one is `false`; ERP additionally
carries one `"adoption_state"` id (`sync-runtime-not-yet-composed`) that is
informational only, for the identical reason: a pin in `pyproject.toml` is
installation, not adoption.

Three distinct refusal reasons
---------------------------------

A reviewer must be able to tell, from `EvaluationResult.status` alone (never
by parsing `findings`), which of these applies:

1. **`unbound`** — no revision named at all.
2. **`moving_ref`** / **`invalid_revision`** — the named revision is not an
   immutable 40-hex commit.
3. **`not_on_protected_main`** — a well-formed commit the runner verified is
   NOT an ancestor of the product's protected `main`.
4. **`evidence_incomplete`** — ancestry verified (or not yet checkable
   because no CI runner is wired up), but the readiness record could not be
   fetched, parsed, matched to the fixed spec, or is missing a required
   requirement id.
5. **`evaluation_refused`** — a complete, verified, matching record whose
   product-authored `satisfied` booleans themselves show the product does
   not meet the property.
6. **`satisfied`** — every check passed.

The absence-is-refusal rule
-----------------------------

**A missing product revision is a REFUSAL, never a skip and never a pass.**
`evaluate_gate` always evaluates exactly the three names in `PRODUCTS`, never
fewer. `GateResult.satisfied` additionally refuses an EMPTY `evaluations`
tuple outright (`all(())` is `True` in Python, and a gate that read that as
"nothing to refuse" would be the identical defect this whole programme has
been chasing).

**No product has a typed readiness record yet, so the gate still refuses on
all three today.** Sub (`288b68cf…`, #3015) and ERP (`b3b191cc…`, #510) are
SOURCE MATERIAL for the typed-record PRs now being built, not final
bindings — the record PRs will create their own replacement coordinates,
and this module does not hard-code today's SHAs as if they were the answer.
`compatibility_gate_bindings.json` therefore binds no revision for any
product today; all three refuse at `unbound`. Academy additionally has a
distinct, named reason it cannot yet be bound at all: it is mid-adoption of
the Foundation deployment contract, blocked on a Governance-pin
`schema_version` 9→10→11 migration prerequisite — an EVIDENCE-ELIGIBILITY
blocker, never a Kernel-incompatibility finding.

CI access this runner requires (reported, not provisioned)
------------------------------------------------------------

Starter, Sub, ERP and Academy are all PUBLIC repositories — **no credential,
GitHub App, or secret is required**. A required CI job checks out
`dotmac_erp`, `dotmac_sub`, and `dotmac_academy_app` alongside this
repository (`actions/checkout`, pinned by commit SHA, `fetch-depth: 0`,
`persist-credentials: false`) and exports `COMPAT_GATE_CLONE_DOTMAC_ERP`,
`COMPAT_GATE_CLONE_DOTMAC_SUB`, and `COMPAT_GATE_CLONE_DOTMAC_ACADEMY_APP`
pointing at those checkouts. A checkout or network failure in that job must
fail the job — see "Infrastructure failure is not a status" above. No
workflow file, secret, or credential is provisioned by this slice; this
paragraph is the report, not an implementation.

What this module does NOT establish (unmonitored, stated per ADR-0018)
--------------------------------------------------------------------------

- Whether a bound revision's fetched record still describes that product's
  CURRENT tree. Every revision is immutable; re-fetching at a later revision
  is the product's own next binding, not a re-derivation this module
  performs automatically.
- Whether ANY product has actually adopted anything (see "Compatibility
  only" above).
- Whether a `requirements[].satisfied` boolean is actually TRUE of the
  product's tree. That verification is EXPLICITLY the product's own CI's
  job, per the fixed envelope's design — this module checks that the claim
  is well-formed, present, and authored in the right record; it does not
  re-derive it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tests.architecture.adoption_evidence import IMMUTABLE_COMMIT, MOVING_REFS

_HERE: Final = Path(__file__).resolve().parent
_REPO_ROOT: Final = _HERE.parents[1]
_KERNEL_SRC: Final = _REPO_ROOT / "packages" / "dotmac-kernel" / "src" / "dotmac_kernel"
_DB_PATH: Final = _KERNEL_SRC / "db.py"
_SESSION_RUNTIME_PATH: Final = _KERNEL_SRC / "session_runtime.py"

#: The default location of the evidence-binding seam's one data file.
DEFAULT_BINDINGS_PATH: Final = _HERE / "compatibility_gate_bindings.json"
BINDINGS_SCHEMA_VALUE: Final = "compatibility_gate_bindings_v1"

#: A binding row supplies a revision and nothing else that could redefine
#: what is being checked — repository, path, schema and subject are fixed in
#: `PRODUCT_SPECS`. `_note` is a free human comment, read by nobody.
ALLOWED_BINDING_ROW_KEYS: Final = frozenset({"revision", "_note"})

#: Closed. Three products, three evaluations below, one procedure each — an
#: unknown product name is a claim no procedure below knows how to check, so
#: it is refused rather than silently accepted or silently skipped.
PRODUCTS: Final = ("academy", "erp", "sub")

#: The closed, structured verdict vocabulary for `EvaluationResult.status`.
#: Distinguishes "no evidence exists yet" from "evidence exists and was
#: refused" — the ABSENT-versus-REGISTRY_DISAGREEMENT split. Unchanged by
#: either ruling: no new public status was added for the runner's refusals.
#:
#: `unbound` — reason 1: no revision named at all.
#: `moving_ref` — reason 2: the named revision is a branch/HEAD/main, not a
#:   coordinate.
#: `invalid_revision` — a named revision that is neither `None` nor a
#:   recognised moving ref, but also not a well-formed 40-hex commit.
#: `not_on_protected_main` — reason 3: the runner verified the revision is
#:   NOT an ancestor of the product's protected `main`.
#: `evidence_incomplete` — ancestry could not be checked (no CI runner wired
#:   up) or was verified, but the record could not be fetched, parsed,
#:   matched to the fixed spec, or is missing a required requirement id.
#: `evaluation_refused` — a complete, verified record whose product-authored
#:   `satisfied` booleans show the product does not meet the property.
#: `satisfied` — every check passed.
EVALUATION_STATUSES: Final = frozenset(
    {
        "unbound",
        "moving_ref",
        "invalid_revision",
        "not_on_protected_main",
        "evidence_incomplete",
        "evaluation_refused",
        "satisfied",
    }
)

#: The one accepted `schema` value for a product's typed readiness record —
#: Michael's fixed envelope, reproduced verbatim. Raise a correction to him
#: rather than diverging from this literal string.
READINESS_SCHEMA_MARKER: Final = "kernel-runtime-readiness.v1"

#: The one canonical, repository-relative path of the typed readiness
#: record, uniform across all three products. No product-specific override
#: exists — a different path per product would itself be a selectable
#: knob, which the ruling forbids.
READINESS_RECORD_PATH: Final = "docs/kernel-runtime-readiness.json"


# ── The fixed product specification (Starter-owned, closed) ────────────────

#: `RequirementSpec.role` — the closed, two-value vocabulary a selected
#: requirement id is classified under. `"compatibility"`: the record's
#: `satisfied` boolean feeds the verdict directly — `False` refuses the
#: product (`evaluation_refused`). `"adoption_state"`: an OBSERVATION about
#: whether a product has cut over yet, always reported, but its `satisfied`
#: value never by itself refuses compatibility — see the module docstring,
#: "The verdict rule": adoption debt is not proof the successor API is
#: inexpressible, and must never masquerade as incompatibility.
REQUIREMENT_ROLES: Final = frozenset({"compatibility", "adoption_state"})


@dataclass(frozen=True)
class RequirementSpec:
    """ONE product-owned requirement id Starter has chosen to consume from a
    verified readiness record, plus Starter's classification of it —
    `requirement_id` names the product-authored id verbatim (products keep
    their own granular ids; Starter names, and only names, which of them it
    reads), `role` is `REQUIREMENT_ROLES`. This is the ONLY place a
    requirement-id string may be spelled: evaluator functions look an entry
    up through this table, never by writing the id literal themselves (see
    `test_no_evaluator_local_requirement_id_literals`)."""

    requirement_id: str
    role: str

    def __post_init__(self) -> None:
        if self.role not in REQUIREMENT_ROLES:
            raise ValueError(
                f"RequirementSpec({self.requirement_id!r}): unknown role "
                f"{self.role!r}; known roles are {sorted(REQUIREMENT_ROLES)!r}"
            )


@dataclass(frozen=True)
class ProductSpec:
    """WHAT is being checked for one product — fixed by the Kernel, never by
    a binding. `repository` doubles as the exact value the record's own
    `product` field must equal (`"dotmac_erp"`, not `"erp"`). `subject` is
    the exact string the record must self-declare, so a record copied from
    another product's repository or subject is refused. `requirements` is
    the closed, ordered set of that product's own requirement ids Starter
    consumes — a record may carry additional ids Starter does not select;
    those are simply not looked up."""

    product: str
    repository: str
    subject: str
    clone_env_var: str
    requirements: tuple[RequirementSpec, ...] = ()


#: Closed and Starter-owned. A product cannot redefine its own repository,
#: path, subject, clone coordinate, or SELECTED requirement ids from the
#: bindings file — see `ALLOWED_BINDING_ROW_KEYS`. Products keep their own
#: granular, descriptive ids; this table is the one place Starter names
#: which of them it consumes and how each affects the verdict.
PRODUCT_SPECS: Final[Mapping[str, ProductSpec]] = {
    "academy": ProductSpec(
        product="academy",
        repository="dotmac_academy_app",
        subject="academy-kernel-successor-readiness",
        clone_env_var="COMPAT_GATE_CLONE_DOTMAC_ACADEMY_APP",
        # All five are adoption-state observations, not compatibility
        # requirements — Academy's eager reference imports and absent
        # runtime binding are current adoption debt, not proof the
        # successor API is inexpressible (see "The verdict rule").
        requirements=(
            RequirementSpec(
                "no-module-scope-kernel-db-import-in-api-deps", "adoption_state"
            ),
            RequirementSpec(
                "no-module-scope-kernel-db-import-in-cli", "adoption_state"
            ),
            RequirementSpec(
                "no-module-scope-kernel-db-import-in-web-context", "adoption_state"
            ),
            RequirementSpec(
                "no-module-scope-kernel-db-import-in-web-labs", "adoption_state"
            ),
            RequirementSpec(
                "product-owned-runtime-bound-before-any-kernel-db-import",
                "adoption_state",
            ),
        ),
    ),
    "erp": ProductSpec(
        product="erp",
        repository="dotmac_erp",
        subject="erp-kernel-successor-readiness",
        clone_env_var="COMPAT_GATE_CLONE_DOTMAC_ERP",
        requirements=(
            RequirementSpec("single-sync-engine-construction-site", "compatibility"),
            RequirementSpec(
                "async-database-paths-isolated-from-kernel", "compatibility"
            ),
            RequirementSpec("fork-safety-hooks-owned-by-product", "compatibility"),
            RequirementSpec(
                "no-runtime-bypassrls-credential-or-per-call-flag", "compatibility"
            ),
            RequirementSpec(
                "statement-timeout-and-pool-tuning-set-at-construction",
                "compatibility",
            ),
            RequirementSpec("legacy-tenant-guc-is-a-single-named-value", "compatibility"),
            RequirementSpec(
                "no-runtime-bypass-rls-guc-writer-remains", "compatibility"
            ),
            # Informational adoption state, not a compatibility failure — ERP
            # has not yet composed the sync DatabaseRuntime successor; a
            # pin is installation, not adoption.
            RequirementSpec("sync-runtime-not-yet-composed", "adoption_state"),
        ),
    ),
    "sub": ProductSpec(
        product="sub",
        repository="dotmac_sub",
        subject="sub-kernel-successor-readiness",
        clone_env_var="COMPAT_GATE_CLONE_DOTMAC_SUB",
        # All eight are compatibility requirements, required true.
        requirements=(
            RequirementSpec(
                "tenant-guc-is-a-class-scoped-after-begin-listener", "compatibility"
            ),
            RequirementSpec(
                "tenant-guc-fires-only-on-root-transactions", "compatibility"
            ),
            RequirementSpec(
                "tenant-guc-value-is-transaction-local-set-config", "compatibility"
            ),
            RequirementSpec(
                "isolation-modes-apply-via-connection-execution-options-not-a-new-session-factory",
                "compatibility",
            ),
            RequirementSpec(
                "read-only-mode-is-repeatable-read-plus-postgresql-readonly-true",
                "compatibility",
            ),
            RequirementSpec(
                "serializable-write-mode-is-serializable-plus-postgresql-readonly-false",
                "compatibility",
            ),
            RequirementSpec(
                "no-set-transaction-sql-is-issued-for-isolation-mode", "compatibility"
            ),
            RequirementSpec(
                "tenant-guc-ordering-is-compatible-with-a-pre-begin-kernel-isolation-pin",
                "compatibility",
            ),
        ),
    ),
}
if set(PRODUCT_SPECS) != set(PRODUCTS):
    raise RuntimeError("PRODUCT_SPECS must declare exactly the names in PRODUCTS")

def _duplicate_requirement_ids(specs: Mapping[str, ProductSpec]) -> dict[str, list[str]]:
    """Guard 1: for each product, the requirement ids its `ProductSpec`
    selects, if any id is selected more than once — a duplicate would make
    one product-authored `satisfied` boolean silently shadow another,
    undetected. Returns an empty dict when every product's selection is
    duplicate-free."""
    problems: dict[str, list[str]] = {}
    for product_name, spec in specs.items():
        seen_ids = [requirement.requirement_id for requirement in spec.requirements]
        if len(seen_ids) != len(set(seen_ids)):
            problems[product_name] = sorted(
                {rid for rid in seen_ids if seen_ids.count(rid) > 1}
            )
    return problems


#: Guard 1, applied eagerly at import time to the fixed table itself, rather
#: than deferred to a per-evaluation check.
_duplicate_problems = _duplicate_requirement_ids(PRODUCT_SPECS)
if _duplicate_problems:
    raise RuntimeError(
        f"PRODUCT_SPECS selects duplicate requirement id(s): {_duplicate_problems!r}"
    )
del _duplicate_problems


# ── AST reads of the Kernel's own tree (pure, offline, deterministic) ──────


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _module_all(path: Path) -> tuple[str, ...]:
    """The module's declared `__all__`, as a sorted tuple. Read by parsing the
    literal list, never by importing — importing `dotmac_kernel.db` costs a
    parseable `DATABASE_URL` by design (ADR-0066 § 2), and this evaluator
    must not acquire that side effect just to answer a shape question."""
    tree = _parse(path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            return tuple(sorted(str(item) for item in value))
    raise LookupError(f"no module-level `__all__` assignment found in {path}")


def _module_level_bare_assignment_names(path: Path) -> frozenset[str]:
    """Names bound by a plain module-level `NAME = ...` assignment — the
    exact shape of `SessionLocal = runtime.session_factory` in `db.py`."""
    tree = _parse(path)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return frozenset(names)


def _class_public_method_names(path: Path, class_name: str) -> tuple[str, ...]:
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return tuple(
                sorted(
                    item.name
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    and not item.name.startswith("_")
                )
            )
    raise LookupError(f"class {class_name!r} not found in {path}")


def _class_async_method_names(path: Path, class_name: str) -> tuple[str, ...]:
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return tuple(
                sorted(
                    item.name
                    for item in node.body
                    if isinstance(item, ast.AsyncFunctionDef)
                )
            )
    raise LookupError(f"class {class_name!r} not found in {path}")


def _isolated_session_orders_execution_options_before_yield(path: Path) -> bool:
    """MEASURED, structurally: does `DatabaseRuntime._isolated_session` apply
    `execution_options` (the isolation-mode statement) strictly BEFORE its own
    `yield`? That ordering is the exact property Sub's evaluation depends on —
    the isolation mode must be set ahead of the transaction's BEGIN and ahead
    of any `after_begin` listener (a tenant-GUC hook among them), and nothing
    can run inside the block before the `yield` hands the session back."""
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DatabaseRuntime":
            for item in node.body:
                if (
                    isinstance(item, ast.FunctionDef)
                    and item.name == "_isolated_session"
                ):
                    return _connection_call_precedes_yield(item)
    raise LookupError(f"_isolated_session not found under DatabaseRuntime in {path}")


def _connection_call_precedes_yield(func: ast.FunctionDef) -> bool:
    connection_line: int | None = None
    yield_line: int | None = None
    for node in ast.walk(func):
        if connection_line is None and isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Attribute) and callee.attr == "connection":
                if any(kw.arg == "execution_options" for kw in node.keywords):
                    connection_line = node.lineno
        if (
            yield_line is None
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Yield)
        ):
            yield_line = node.lineno
    if connection_line is None or yield_line is None:
        return False
    return connection_line < yield_line


# ── Revision shape (reuses adoption_evidence.py's vocabulary) ──────────────


def _revision_problem(product: str, revision: object) -> str | None:
    """The exact refusal shape `adoption_evidence._revision_problem` uses for
    a `commit` field, reapplied to a product's bound readiness revision. A
    revision is a 40-hex commit or it is not a coordinate — a branch name, a
    tag, `HEAD`, or `main` is refused whether or not it looks precise."""
    if revision is None:
        return f"no revision is bound for {product!r}"
    if not isinstance(revision, str) or not revision.strip():
        return (
            f"{product}'s bound revision must be a non-empty string, "
            f"observed {revision!r}"
        )
    text = revision.strip()
    if IMMUTABLE_COMMIT.fullmatch(text):
        return None
    head, _, tail = text.partition("@")
    if tail and head.lower() in MOVING_REFS:
        return (
            f"{product}'s bound revision {revision!r} names the moving ref "
            f"{head.lower()!r}; a branch is whatever that repository merged "
            "last, so it is not an evaluatable coordinate"
        )
    if text.lower() in MOVING_REFS:
        return (
            f"{product}'s bound revision {revision!r} is a moving ref; a "
            "branch name, `HEAD`, or `main` is whatever that repository "
            "merged last, not a coordinate this gate can evaluate"
        )
    return (
        f"{product}'s bound revision {revision!r} must be an immutable "
        "40-character lowercase hex commit"
    )


def _revision_status(revision: object) -> str:
    """The `EVALUATION_STATUSES` bucket matching `_revision_problem`'s
    message for the same `revision` — companion function, single source of
    the classification. Only called once `_revision_problem` has already
    returned non-`None` for the same `revision`."""
    if revision is None:
        return "unbound"
    if not isinstance(revision, str) or not revision.strip():
        return "invalid_revision"
    text = revision.strip()
    if IMMUTABLE_COMMIT.fullmatch(text):
        raise ValueError(
            "_revision_status called on a well-formed commit; only call it "
            "after _revision_problem returned non-None for the same input"
        )
    head, _, tail = text.partition("@")
    if tail and head.lower() in MOVING_REFS:
        return "moving_ref"
    if text.lower() in MOVING_REFS:
        return "moving_ref"
    return "invalid_revision"


# ── The runner: verify ancestry, fetch the blob, parse the record ──────────


@dataclass(frozen=True)
class ReadinessRecord:
    """A verified, parsed, subject-and-product-matching readiness record —
    Michael's fixed envelope, structurally validated."""

    repository: str
    subject: str
    requirements: tuple[Mapping[str, object], ...]
    composition: tuple[Mapping[str, object], ...]
    source_references: tuple[str, ...]

    def requirement(self, requirement_id: str) -> Mapping[str, object] | None:
        for entry in self.requirements:
            if entry.get("id") == requirement_id:
                return entry
        return None


@dataclass(frozen=True)
class FetchOutcome:
    """Exactly one of `record` or `problem` is set. `digest` may be set even
    when `problem` is set (a blob can be read and hashed but fail to parse).
    `problem_status` is one of the two existing statuses reserved for
    runner-side refusals — never a new one."""

    record: ReadinessRecord | None
    digest: str | None
    problem: str | None
    problem_status: str | None


def _clone_path_for(spec: ProductSpec) -> Path | None:
    """The local clone coordinate is an INFRASTRUCTURE fact (a CI-provisioned
    checkout path), read from the fixed env var named in `spec` — never from
    a binding.

    Returns `None` only when the env var is genuinely unset (the ordinary,
    expected "no CI runner wired up yet" state — a soft refusal). Raises
    `RuntimeError` when the env var IS set but the path does not exist: that
    means the CI checkout step for this repository failed or was
    misconfigured, which is an infrastructure failure and must fail the job,
    never be reported as a gate refusal (see the module docstring,
    "Infrastructure failure is not a status")."""
    value = os.environ.get(spec.clone_env_var)
    if value is None or not value.strip():
        return None
    path = Path(value)
    if not path.is_dir():
        raise RuntimeError(
            f"{spec.clone_env_var}={value!r} is set but is not a directory "
            f"-- the CI checkout step for {spec.repository} must have "
            "failed or been misconfigured. This is an infrastructure "
            "failure and must fail the CI job, not be reported as a gate "
            "refusal."
        )
    return path


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    command = ["git", *args]
    return subprocess.run(command, cwd=cwd, capture_output=True, check=False)  # noqa: S603


#: `git merge-base --is-ancestor` documents exactly two outcome codes: 0
#: means "is an ancestor", 1 means "is not". Anything else (128 for "not a
#: git repository" or "unknown revision", or the `git` binary missing
#: entirely) is a tool/infrastructure failure, not an answer about the
#: revision — see `_is_ancestor_of_protected_main`.
_ANCESTOR_CHECK_EXIT_CODES: Final = frozenset({0, 1})


def _is_ancestor_of_protected_main(clone: Path, revision: str) -> bool:
    result = _run_git(["merge-base", "--is-ancestor", revision, "origin/main"], clone)
    if result.returncode not in _ANCESTOR_CHECK_EXIT_CODES:
        raise RuntimeError(
            "git merge-base --is-ancestor failed unexpectedly against "
            f"{clone} (exit {result.returncode}): "
            f"{result.stderr.decode('utf-8', 'replace')!r} -- this is an "
            "infrastructure failure (a broken or incomplete checkout, or "
            "git itself failing), not evidence about the revision, and "
            "must fail the CI job rather than being reported as a gate "
            "refusal."
        )
    return result.returncode == 0


def _read_blob(clone: Path, revision: str, path: str) -> bytes | None:
    """Called only once ancestry has already succeeded, so the clone is
    already known to be a working repository with `revision` a known object
    — a `git show` failure here is read as "no blob at that path", the
    evidence-shaped refusal, not as a fresh infrastructure failure."""
    result = _run_git(["show", f"{revision}:{path}"], clone)
    if result.returncode != 0:
        return None
    return result.stdout


def _readiness_shape_problems(payload: Mapping[str, object]) -> list[str]:
    """Structural validation of Michael's fixed envelope: `requirements` and
    `composition` are lists of well-formed objects, `source_references` is a
    list of strings. Content (whether a `satisfied` boolean is actually true
    of the product's tree) is explicitly not this module's job — see the
    module docstring."""
    problems: list[str] = []

    requirements = payload.get("requirements")
    if not isinstance(requirements, list):
        problems.append("`requirements` must be a list")
    else:
        for index, entry in enumerate(requirements):
            where = f"requirements[{index}]"
            if not isinstance(entry, Mapping):
                problems.append(f"{where} must be an object")
                continue
            if not isinstance(entry.get("id"), str) or not entry["id"].strip():
                problems.append(f"{where}.id must be a non-empty string")
            if (
                not isinstance(entry.get("statement"), str)
                or not entry["statement"].strip()
            ):
                problems.append(f"{where}.statement must be a non-empty string")
            if not isinstance(entry.get("satisfied"), bool):
                problems.append(f"{where}.satisfied must be a boolean")
            if (
                not isinstance(entry.get("source_reference"), str)
                or not entry["source_reference"].strip()
            ):
                problems.append(f"{where}.source_reference must be a non-empty string")

    composition = payload.get("composition")
    if not isinstance(composition, list):
        problems.append("`composition` must be a list")
    else:
        for index, entry in enumerate(composition):
            where = f"composition[{index}]"
            if not isinstance(entry, Mapping):
                problems.append(f"{where} must be an object")
                continue
            if (
                not isinstance(entry.get("declaration"), str)
                or not entry["declaration"].strip()
            ):
                problems.append(f"{where}.declaration must be a non-empty string")
            if (
                not isinstance(entry.get("source_reference"), str)
                or not entry["source_reference"].strip()
            ):
                problems.append(f"{where}.source_reference must be a non-empty string")

    source_references = payload.get("source_references")
    if not isinstance(source_references, list) or not all(
        isinstance(item, str) and item.strip() for item in source_references
    ):
        problems.append("`source_references` must be a list of non-empty strings")

    return problems


def fetch_readiness_record(spec: ProductSpec, revision: str) -> FetchOutcome:
    """The verifying runner. Every refusal states what was actually
    observed, never a presumed reason, and every step is REAL: a local `git`
    invocation against a CI-provisioned clone, not a claim this module takes
    on faith."""
    clone = _clone_path_for(spec)
    if clone is None:
        return FetchOutcome(
            None,
            None,
            f"no local clone is configured for {spec.repository} "
            f"({spec.clone_env_var} is unset) — the runner cannot verify "
            "ancestry or read the readiness record without one",
            "evidence_incomplete",
        )
    if not _is_ancestor_of_protected_main(clone, revision):
        return FetchOutcome(
            None,
            None,
            f"{revision} was verified NOT to be an ancestor of "
            f"{spec.repository}'s protected `main` (`git merge-base "
            "--is-ancestor` returned 1)",
            "not_on_protected_main",
        )
    blob = _read_blob(clone, revision, READINESS_RECORD_PATH)
    if blob is None:
        return FetchOutcome(
            None,
            None,
            f"no blob found at {revision}:{READINESS_RECORD_PATH} in "
            f"{spec.repository}",
            "evidence_incomplete",
        )
    digest = hashlib.sha256(blob).hexdigest()
    try:
        payload = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return FetchOutcome(
            None,
            digest,
            f"{READINESS_RECORD_PATH} at {revision} in {spec.repository} is "
            f"not valid JSON: {exc}",
            "evidence_incomplete",
        )
    if not isinstance(payload, Mapping):
        return FetchOutcome(
            None,
            digest,
            f"{READINESS_RECORD_PATH} at {revision} in {spec.repository} "
            "must be a JSON object",
            "evidence_incomplete",
        )
    schema = payload.get("schema")
    if schema != READINESS_SCHEMA_MARKER:
        return FetchOutcome(
            None,
            digest,
            f"{READINESS_RECORD_PATH} at {revision} in {spec.repository}: "
            f"`schema` is {schema!r}, expected {READINESS_SCHEMA_MARKER!r}",
            "evidence_incomplete",
        )
    product = payload.get("product")
    if product != spec.repository:
        return FetchOutcome(
            None,
            digest,
            f"{READINESS_RECORD_PATH} at {revision}: `product` is "
            f"{product!r}, expected {spec.repository!r}",
            "evidence_incomplete",
        )
    subject = payload.get("subject")
    if subject != spec.subject:
        return FetchOutcome(
            None,
            digest,
            f"{READINESS_RECORD_PATH} at {revision} in {spec.repository}: "
            f"`subject` is {subject!r}, expected {spec.subject!r} — a "
            "record for a different subject is not evidence for this one",
            "evidence_incomplete",
        )
    shape_problems = _readiness_shape_problems(payload)
    if shape_problems:
        return FetchOutcome(
            None,
            digest,
            f"{READINESS_RECORD_PATH} at {revision} in {spec.repository} "
            "failed schema validation: " + "; ".join(shape_problems),
            "evidence_incomplete",
        )
    return FetchOutcome(
        ReadinessRecord(
            repository=product,
            subject=subject,
            requirements=tuple(payload["requirements"]),
            composition=tuple(payload["composition"]),
            source_references=tuple(payload["source_references"]),
        ),
        digest,
        None,
        None,
    )


def _requirement_problem(
    record: ReadinessRecord, requirement_id: str
) -> tuple[Mapping[str, object] | None, str | None]:
    """Looks up one fixed requirement id in a verified record. Returns
    `(entry, None)` on success or `(None, problem)` when the id is simply
    absent — a shape gap in the record, not a failed requirement (that is a
    different thing: an entry that IS present with `satisfied: false`)."""
    entry = record.requirement(requirement_id)
    if entry is None:
        return None, (
            f"the readiness record carries no requirement with id "
            f"{requirement_id!r}"
        )
    return entry, None


@dataclass(frozen=True)
class RequirementsEvaluation:
    """The outcome of walking one product's `PRODUCT_SPECS[...].requirements`
    against a verified record. `shape_status` is set (and `findings` holds
    only the shape problem(s)) when any SELECTED id is absent from the
    record — guard 2's `evidence_incomplete`. Otherwise `shape_status` is
    `None` and `compatibility_satisfied` is the verdict-bearing half: `True`
    unless some `role="compatibility"` entry's `satisfied` is not `True`. A
    `role="adoption_state"` entry's `satisfied` value is reported in
    `findings` but never changes `compatibility_satisfied` — see
    `RequirementSpec.role`."""

    findings: tuple[str, ...]
    compatibility_satisfied: bool
    shape_status: str | None


def _evaluate_readiness_requirements(
    product: str, revision: str, record: ReadinessRecord
) -> RequirementsEvaluation:
    """The ONE place a verified record's requirements are read against
    `PRODUCT_SPECS` — every evaluator below calls this instead of looking up
    a requirement id itself, so no evaluator function's own source ever
    spells a requirement-id literal (see
    `test_no_evaluator_local_requirement_id_literals`)."""
    specs = PRODUCT_SPECS[product].requirements
    entries: dict[str, Mapping[str, object]] = {}
    shape_problems: list[str] = []
    for spec in specs:
        entry, problem = _requirement_problem(record, spec.requirement_id)
        if problem is not None:
            shape_problems.append(problem)
        else:
            assert entry is not None
            entries[spec.requirement_id] = entry
    if shape_problems:
        return RequirementsEvaluation(
            findings=tuple(f"MEASURED: {problem}" for problem in shape_problems),
            compatibility_satisfied=False,
            shape_status="evidence_incomplete",
        )

    findings: list[str] = []
    compatibility_satisfied = True
    for spec in specs:
        entry = entries[spec.requirement_id]
        satisfied_value = entry.get("satisfied")
        if spec.role == "compatibility":
            if satisfied_value is not True:
                compatibility_satisfied = False
                findings.append(
                    f"OBSERVED at {product}@{revision}: requirement "
                    f"{spec.requirement_id!r} is satisfied={satisfied_value!r} "
                    f"({entry.get('source_reference')!r})"
                )
        else:  # "adoption_state" — always reported, never gates compatibility
            findings.append(
                f"ADOPTION STATE at {product}@{revision} (adoption debt, "
                "not a compatibility failure): requirement "
                f"{spec.requirement_id!r} is satisfied={satisfied_value!r} "
                f"({entry.get('source_reference')!r})"
            )
    return RequirementsEvaluation(
        findings=tuple(findings),
        compatibility_satisfied=compatibility_satisfied,
        shape_status=None,
    )


@dataclass(frozen=True)
class ProductBinding:
    """The evidence-binding seam's unit. `revision` is the ONLY thing a
    binding supplies — repository, path, schema and subject are fixed in
    `PRODUCT_SPECS`, never selectable here (see `ALLOWED_BINDING_ROW_KEYS`
    and `load_default_bindings`'s refusal of any other field)."""

    product: str
    revision: str | None = None


@dataclass(frozen=True)
class EvaluationResult:
    """One product's evaluation. `revision` is always the exact value that
    was evaluated (possibly `None`).

    `status` is the STRUCTURED verdict, not just prose in `findings` — a
    reader must be able to tell "this product has not yet produced evidence"
    apart from "this product produced evidence and the evaluation refused
    it" WITHOUT parsing free text. `satisfied` is DERIVED from `status`,
    never stored.

    `artefact_digest` is OUTPUT ONLY: the SHA-256 this module itself computed
    from the fetched readiness-record blob's bytes, or `None` when no blob
    was ever successfully read. No accepted input can set this field — see
    the module docstring, "The digest is output, never input"."""

    product: str
    revision: str | None
    status: str
    findings: tuple[str, ...]
    artefact_digest: str | None = None

    def __post_init__(self) -> None:
        if self.status not in EVALUATION_STATUSES:
            raise ValueError(
                f"{self.product}: unknown EvaluationResult.status {self.status!r}; "
                f"known statuses are {sorted(EVALUATION_STATUSES)!r}"
            )

    @property
    def satisfied(self) -> bool:
        return self.status == "satisfied"

    def explain(self) -> str:
        verdict = "SATISFIED" if self.satisfied else f"REFUSED ({self.status})"
        revision_text = self.revision or "<unbound>"
        lines = [f"{verdict} ({self.product}@{revision_text})"]
        if self.artefact_digest is not None:
            lines.append(f"  digest: sha256:{self.artefact_digest}")
        lines.extend(f"  - {finding}" for finding in self.findings)
        return "\n".join(lines)


@dataclass(frozen=True)
class GateResult:
    """The all-of combination. `satisfied` is a hard-refusal on an empty
    `evaluations` tuple — see module docstring, "the absence-is-refusal
    rule" — so this gate cannot pass on mechanism alone: it needs at least
    one evaluation, and every one of them must be satisfied."""

    evaluations: tuple[EvaluationResult, ...]

    @property
    def satisfied(self) -> bool:
        if not self.evaluations:
            return False
        return all(evaluation.satisfied for evaluation in self.evaluations)

    @property
    def refusing(self) -> tuple[EvaluationResult, ...]:
        return tuple(
            evaluation for evaluation in self.evaluations if not evaluation.satisfied
        )

    def explain(self) -> str:
        if not self.evaluations:
            return "REFUSED: no product was evaluated (empty evaluation set)"
        if self.satisfied:
            names = ", ".join(evaluation.product for evaluation in self.evaluations)
            return f"SATISFIED: all-of gate passed for {names}"
        lines = [
            f"REFUSED: {len(self.refusing)} of {len(self.evaluations)} product "
            "evaluation(s) refused (all-of: any single refusal blocks freeze)"
        ]
        for evaluation in self.refusing:
            lines.append(evaluation.explain())
        return "\n".join(lines)


# ── Per-product evaluations ─────────────────────────────────────────────────


def evaluate_academy(binding: ProductBinding) -> EvaluationResult:
    """Academy — a strict product runtime binds
    (`bind_database_runtime(runtime, required=True)` succeeds) while the
    reference runtime (`dotmac_kernel.db`) remains genuinely unavailable —
    the same property `test_kernel_runtime_composition_seam.py`'s probe
    proves in the general, product-agnostic case.

    KERNEL-SIDE (measured here, regardless of any bound revision): is
    `SessionLocal` a member of the PUBLISHED surface? Reported unconditionally
    as a finding about the SUCCESSOR's sufficiency.

    PRODUCT-SIDE: fetches and verifies Academy's typed readiness record (see
    module docstring, "The runner") and reads every requirement id
    `PRODUCT_SPECS["academy"].requirements` selects. Every one of those ids
    is classified `role="adoption_state"` — Academy's eager reference
    imports and absent runtime binding are current adoption debt, never
    proof the successor API is inexpressible (see the module docstring,
    "The verdict rule") — so their `satisfied` values are reported but
    never refuse compatibility on their own.
    """
    findings: list[str] = []

    all_names = _module_all(_DB_PATH)
    bare_names = _module_level_bare_assignment_names(_DB_PATH)
    session_local_published = "SessionLocal" in all_names
    session_local_exists = "SessionLocal" in bare_names
    if session_local_exists and not session_local_published:
        findings.append(
            "MEASURED (kernel-side, dotmac_kernel/db.py, this repository): "
            "`SessionLocal` is a bare module-level attribute but is not a "
            f"member of `dotmac_kernel.db.__all__` (observed __all__: "
            f"{list(all_names)!r}). COMPATIBILITY.md defines the published "
            "surface as membership in a supported module's `__all__`, so a "
            "consumer that needs this attribute by name has no published "
            "spelling for it; the supported equivalent for non-request work "
            "is `resolve_database_runtime()` / `tenant_session` / "
            "`platform_session`. This is a finding about the successor's "
            "sufficiency, reported unconditionally."
        )

    revision_problem = _revision_problem("academy", binding.revision)
    if revision_problem is not None:
        findings.append(
            f"MEASURED: {revision_problem}; no readiness record can be "
            "fetched without a revision, so whether the product's own tree "
            "still reaches the attribute above cannot be established from "
            "this repository alone"
        )
        return EvaluationResult(
            product="academy",
            revision=binding.revision,
            status=_revision_status(binding.revision),
            findings=tuple(findings),
        )

    outcome = fetch_readiness_record(PRODUCT_SPECS["academy"], binding.revision)
    if outcome.problem is not None:
        findings.append(f"MEASURED: {outcome.problem}")
        assert outcome.problem_status is not None
        return EvaluationResult(
            product="academy",
            revision=binding.revision,
            status=outcome.problem_status,
            findings=tuple(findings),
            artefact_digest=outcome.digest,
        )

    assert outcome.record is not None
    requirements = _evaluate_readiness_requirements(
        "academy", binding.revision, outcome.record
    )
    findings.extend(requirements.findings)
    if requirements.shape_status is not None:
        return EvaluationResult(
            product="academy",
            revision=binding.revision,
            status=requirements.shape_status,
            findings=tuple(findings),
            artefact_digest=outcome.digest,
        )

    return EvaluationResult(
        product="academy",
        revision=binding.revision,
        status="satisfied" if requirements.compatibility_satisfied else "evaluation_refused",
        findings=tuple(findings),
        artefact_digest=outcome.digest,
    )


def evaluate_erp(binding: ProductBinding) -> EvaluationResult:
    """ERP — synchronous session requirements are expressible against
    `dotmac_kernel.session_runtime.DatabaseRuntime`.

    KERNEL-SIDE (measured here): the successor's public boundary methods,
    and confirmation that NONE is `async def`.

    PRODUCT-SIDE: fetches and verifies ERP's typed readiness record and reads
    every requirement id `PRODUCT_SPECS["erp"].requirements` selects — seven
    `role="compatibility"` requirements (required `true`) plus one
    informational `role="adoption_state"` id about sync-runtime composition
    that is reported but never refuses compatibility on its own: a pin in
    `pyproject.toml` is installation, not adoption.
    """
    findings: list[str] = []

    sync_methods = _class_public_method_names(_SESSION_RUNTIME_PATH, "DatabaseRuntime")
    async_methods = _class_async_method_names(_SESSION_RUNTIME_PATH, "DatabaseRuntime")
    findings.append(
        "MEASURED (kernel-side, dotmac_kernel/session_runtime.py, this "
        f"repository): DatabaseRuntime publishes {len(sync_methods)} public "
        f"boundary method(s) ({list(sync_methods)!r}) and "
        f"{len(async_methods)} `async def` method(s) ({list(async_methods)!r}). "
        "The Kernel is sync-only today, as ADR-0066 states; async support "
        "for ERP is explicitly transitional, not satisfied by this surface."
    )

    revision_problem = _revision_problem("erp", binding.revision)
    if revision_problem is not None:
        findings.append(
            f"MEASURED: {revision_problem}; whether ERP's own sync session "
            "requirements are actually met by the surface above cannot be "
            "established from this repository alone"
        )
        return EvaluationResult(
            product="erp",
            revision=binding.revision,
            status=_revision_status(binding.revision),
            findings=tuple(findings),
        )

    outcome = fetch_readiness_record(PRODUCT_SPECS["erp"], binding.revision)
    if outcome.problem is not None:
        findings.append(f"MEASURED: {outcome.problem}")
        assert outcome.problem_status is not None
        return EvaluationResult(
            product="erp",
            revision=binding.revision,
            status=outcome.problem_status,
            findings=tuple(findings),
            artefact_digest=outcome.digest,
        )

    assert outcome.record is not None
    requirements = _evaluate_readiness_requirements(
        "erp", binding.revision, outcome.record
    )
    findings.extend(requirements.findings)
    if requirements.shape_status is not None:
        return EvaluationResult(
            product="erp",
            revision=binding.revision,
            status=requirements.shape_status,
            findings=tuple(findings),
            artefact_digest=outcome.digest,
        )

    return EvaluationResult(
        product="erp",
        revision=binding.revision,
        status="satisfied" if requirements.compatibility_satisfied else "evaluation_refused",
        findings=tuple(findings),
        artefact_digest=outcome.digest,
    )


def evaluate_sub(binding: ProductBinding) -> EvaluationResult:
    """Sub — tenant GUC behaviour plus the typed `readonly_session()` /
    `serializable_session()` transaction modes (slice 3, `#681`) are
    expressible. The part that matters is the GUC HOOK ORDERING relative to
    those modes.

    KERNEL-SIDE (measured here): `readonly_session`/`serializable_session`/
    `tenant_scope` all exist on `DatabaseRuntime`, and — structurally — the
    `execution_options` call appears strictly BEFORE `_isolated_session`'s
    `yield`.

    PRODUCT-SIDE: fetches and verifies Sub's typed readiness record and reads
    every requirement id `PRODUCT_SPECS["sub"].requirements` selects — all
    eight `role="compatibility"`, required `true`.
    """
    findings: list[str] = []

    sync_methods = _class_public_method_names(_SESSION_RUNTIME_PATH, "DatabaseRuntime")
    required = ("readonly_session", "serializable_session", "tenant_scope")
    missing = tuple(name for name in required if name not in sync_methods)
    if missing:
        findings.append(
            "MEASURED (kernel-side, dotmac_kernel/session_runtime.py, this "
            f"repository): DatabaseRuntime is missing required boundary "
            f"method(s) {missing!r} (observed public methods: "
            f"{list(sync_methods)!r})"
        )

    ordering_holds = _isolated_session_orders_execution_options_before_yield(
        _SESSION_RUNTIME_PATH
    )
    findings.append(
        "MEASURED (kernel-side, dotmac_kernel/session_runtime.py, this "
        "repository): `_isolated_session` applies `execution_options` "
        f"before its own `yield`: {ordering_holds}. This is the ordering "
        "guarantee any tenant-GUC `after_begin` hook composed inside the "
        "block depends on."
    )

    revision_problem = _revision_problem("sub", binding.revision)
    if revision_problem is not None:
        findings.append(
            f"MEASURED: {revision_problem}; whether Sub's own tenant-GUC "
            "hook is actually composed in the order the Kernel guarantees "
            "cannot be established from this repository alone"
        )
        return EvaluationResult(
            product="sub",
            revision=binding.revision,
            status=_revision_status(binding.revision),
            findings=tuple(findings),
        )

    outcome = fetch_readiness_record(PRODUCT_SPECS["sub"], binding.revision)
    if outcome.problem is not None:
        findings.append(f"MEASURED: {outcome.problem}")
        assert outcome.problem_status is not None
        return EvaluationResult(
            product="sub",
            revision=binding.revision,
            status=outcome.problem_status,
            findings=tuple(findings),
            artefact_digest=outcome.digest,
        )

    assert outcome.record is not None
    requirements = _evaluate_readiness_requirements(
        "sub", binding.revision, outcome.record
    )
    findings.extend(requirements.findings)
    if requirements.shape_status is not None:
        return EvaluationResult(
            product="sub",
            revision=binding.revision,
            status=requirements.shape_status,
            findings=tuple(findings),
            artefact_digest=outcome.digest,
        )

    satisfied = (
        not missing and ordering_holds and requirements.compatibility_satisfied
    )
    return EvaluationResult(
        product="sub",
        revision=binding.revision,
        status="satisfied" if satisfied else "evaluation_refused",
        findings=tuple(findings),
        artefact_digest=outcome.digest,
    )


_EVALUATORS: Final = {
    "academy": evaluate_academy,
    "erp": evaluate_erp,
    "sub": evaluate_sub,
}


def evaluate_gate(bindings: Mapping[str, ProductBinding]) -> GateResult:
    """The all-of combination. Always evaluates exactly `PRODUCTS`, in that
    fixed order, regardless of what `bindings` contains — a product absent
    from `bindings` is evaluated as unbound (refused), never skipped.

    Raises `ValueError` for a key in `bindings` outside `PRODUCTS`.
    """
    unknown = sorted(set(bindings) - set(PRODUCTS))
    if unknown:
        raise ValueError(
            f"evaluate_gate received binding(s) for unknown product(s) "
            f"{unknown!r}; known products are {list(PRODUCTS)!r}"
        )

    evaluations = tuple(
        _EVALUATORS[product](bindings.get(product, ProductBinding(product=product)))
        for product in PRODUCTS
    )
    return GateResult(evaluations=evaluations)


def load_default_bindings(
    path: Path = DEFAULT_BINDINGS_PATH,
) -> Mapping[str, ProductBinding]:
    """Read the evidence-binding seam's one data file. A binding row supplies
    ONLY `revision` (and an optional, unread `_note`) — any other field is
    refused, because repository/path/schema/subject are fixed in
    `PRODUCT_SPECS` and are not selectable per binding."""
    payload = json.loads(path.read_text())
    schema = payload.get("schema")
    if schema != BINDINGS_SCHEMA_VALUE:
        raise ValueError(
            f"{path}: `schema` must be {BINDINGS_SCHEMA_VALUE!r}, observed "
            f"{schema!r}"
        )
    raw_bindings = payload.get("bindings")
    if not isinstance(raw_bindings, Mapping):
        raise ValueError(f"{path}: `bindings` must be an object")
    unknown = sorted(set(raw_bindings) - set(PRODUCTS))
    if unknown:
        raise ValueError(
            f"{path}: `bindings` names unknown product(s) {unknown!r}; known "
            f"products are {list(PRODUCTS)!r}"
        )
    result: dict[str, ProductBinding] = {}
    for product in PRODUCTS:
        row = raw_bindings.get(product, {})
        if not isinstance(row, Mapping):
            raise ValueError(f"{path}: bindings[{product!r}] must be an object")
        unknown_row_keys = sorted(set(row) - ALLOWED_BINDING_ROW_KEYS)
        if unknown_row_keys:
            raise ValueError(
                f"{path}: bindings[{product!r}] carries unrecognised "
                f"field(s) {unknown_row_keys!r}; a binding supplies a "
                "`revision` only — repository, path, schema and subject are "
                "fixed in PRODUCT_SPECS and are not selectable per binding"
            )
        result[product] = ProductBinding(product=product, revision=row.get("revision"))
    return result
