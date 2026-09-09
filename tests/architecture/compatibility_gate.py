"""The Kernel-successor compatibility gate — Slice 4.

Three product-specific evaluations (Academy, ERP, Sub), combined by one
**all-of** release gate: any single product refusing blocks the freeze.
Findings are complete and deterministic — same inputs, same findings, every
time, and nothing here samples or guesses.

**Compatibility only, never adoption.** Every evaluation below answers "is the
Kernel's published successor surface (``dotmac_kernel.session_runtime``,
``dotmac_kernel.db``) sufficient for this product's stated need", by reading
KERNEL SOURCE in THIS repository via ``ast`` — never by importing, executing,
cloning or fetching anything, and never by constructing a runtime and
asserting it works. Whether a product HAS adopted that surface is a fact about
the product's own tree, established the same way
``tests/architecture/adoption_evidence.py``'s ``composed_at`` kind already
requires: a bound immutable revision plus product-side evidence captured
against that exact tree. Nobody has captured any yet (see "The evidence-
binding seam" below), so every product-side half of every evaluation below
refuses today, by construction, not by omission.

The evidence-binding seam
--------------------------

``load_default_bindings()`` reads ``compatibility_gate_bindings.json``, the
ONE file that changes when a real revision becomes available. Every
``ProductBinding`` in it is unbound (``revision: null``) today, and
``evaluate_gate(load_default_bindings())`` therefore refuses. When Governance
#87 is decided, Academy #130 is re-derived and merged, and exact immutable
ERP/Sub readiness revisions exist, binding them is editing that JSON file's
three ``revision``/``evidence`` fields — nothing in this module changes shape.

Vocabulary reused, verbatim, from ``adoption_evidence.py``
------------------------------------------------------------

``IMMUTABLE_COMMIT`` and ``MOVING_REFS``: a revision is an exact 40-character
lowercase hex commit or it is refused by construction, including a moving ref
embedded after ``@`` (``adoption_evidence._revision_problem`` handles that
identical case). This module does not invent a second revision-shape grammar.

The absence-is-refusal rule
-----------------------------

**A missing product revision is a REFUSAL, never a skip and never a pass.**
``evaluate_gate`` always evaluates exactly the three names in ``PRODUCTS``,
never fewer — a caller cannot make the gate "pass" by omitting a product from
the bindings mapping, because an omitted product is simply evaluated unbound,
which refuses. ``GateResult.satisfied`` additionally refuses an EMPTY
``evaluations`` tuple outright (``all(())`` is ``True`` in Python, and a gate
that read that as "nothing to refuse" would be the identical defect this
whole programme has been chasing — a check over no files, passing for having
nothing to check). See ``test_the_gate_cannot_pass_on_an_empty_evaluation_set``.

What this module does NOT establish (unmonitored, stated per ADR-0018)
--------------------------------------------------------------------------

- Whether a bound revision's captured ``evidence`` still describes that
  product's CURRENT tree. Every revision is immutable, so this is the same
  "assertion_resolution" gap ``adoption_evidence.py`` already names: a
  scheduled external re-derivation against the cited repository, not built
  here.
- Whether ANY product has actually adopted anything. This gate proves
  compatibility (the Kernel surface CAN satisfy the need), never adoption
  (the product DOES rely on it) — see ADR-0006 § 5, "reference proof is not
  adoption", which is exactly the shape this module refuses to blur.
"""

from __future__ import annotations

import ast
import json
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

#: Closed. Three products, three evaluations below, one procedure each — an
#: unknown product name is a claim no procedure below knows how to check, so
#: it is refused rather than silently accepted or silently skipped.
PRODUCTS: Final = ("academy", "erp", "sub")

#: The one accepted value for `evidence["async_status"]` short of a real
#: async DatabaseRuntime existing in the Kernel. Closed for the same reason
#: `HISTORICAL_ADOPTION_STATES` in `adoption_evidence.py` is closed: adding a
#: second accepted value is a schema decision with a reviewer, not something
#: a bound revision's evidence gets to assert into existence.
ASYNC_TRANSITIONAL: Final = "transitional"


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


# ── Revision/evidence shape (reuses adoption_evidence.py's vocabulary) ─────


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


@dataclass(frozen=True)
class ProductBinding:
    """The evidence-binding seam's unit. Binding the real three revisions is
    a DATA change — construct one of these with a real `revision` (and, once
    product-side capture exists, `evidence`) — nothing below this dataclass
    changes shape when that happens."""

    product: str
    revision: str | None = None
    evidence: Mapping[str, object] | None = None


@dataclass(frozen=True)
class EvaluationResult:
    """One product's evaluation. `revision` is always the exact value that
    was evaluated (possibly `None`) — never a resolved or defaulted one, so a
    reader can see exactly what was and was not checked."""

    product: str
    revision: str | None
    satisfied: bool
    findings: tuple[str, ...]

    def explain(self) -> str:
        verdict = "SATISFIED" if self.satisfied else "REFUSED"
        revision_text = self.revision or "<unbound>"
        lines = [f"{verdict} ({self.product}@{revision_text})"]
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

    KERNEL-SIDE (measured here, from this repository, regardless of any
    bound revision): is `SessionLocal` — the attribute a non-request caller
    without the runtime seam would reach for — a member of the PUBLISHED
    surface? It is a bare module-level assignment in `db.py`
    (`SessionLocal = runtime.session_factory`), and COMPATIBILITY.md defines
    published names as members of a supported module's `__all__`.
    `db.py`'s own module docstring names the supported alternative:
    `resolve_database_runtime()` / `tenant_session` / `platform_session`.
    If the Kernel's `__all__` does not carry `SessionLocal`, that is reported
    as a finding about the SUCCESSOR's sufficiency, unconditionally — never
    worked around by this evaluator inferring what Academy "probably" does.

    PRODUCT-SIDE (needs a bound revision + captured evidence): whether
    Academy's own tree still reaches `dotmac_kernel.db.SessionLocal` by name,
    and whether a strict bind was observed to succeed against it. Refused
    when unbound, per the module's absence-is-refusal rule.
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
            f"MEASURED: {revision_problem}; no product-side evidence has "
            "been captured, so whether the product's own tree still reaches "
            "the attribute above cannot be established from this repository "
            "alone"
        )
        return EvaluationResult(
            product="academy",
            revision=binding.revision,
            satisfied=False,
            findings=tuple(findings),
        )

    evidence = binding.evidence
    if not isinstance(evidence, Mapping):
        findings.append(
            f"MEASURED: revision {binding.revision} is bound but no "
            "product-side evidence is attached; a bound revision with "
            "nothing captured against it is a coordinate with nothing to "
            "check, which this gate treats as a refusal rather than a pass"
        )
        return EvaluationResult(
            product="academy",
            revision=binding.revision,
            satisfied=False,
            findings=tuple(findings),
        )

    usage_sites = evidence.get("unpublished_session_local_usage_sites")
    strict_bind_verified = evidence.get("strict_bind_without_reference_import")
    shape_problems: list[str] = []
    if not isinstance(usage_sites, list | tuple):
        shape_problems.append(
            "product-side evidence must carry "
            "`unpublished_session_local_usage_sites` as a list (possibly "
            "empty) of file:line locations captured against the bound "
            "revision"
        )
    if not isinstance(strict_bind_verified, bool):
        shape_problems.append(
            "product-side evidence must carry a boolean "
            "`strict_bind_without_reference_import`, observed at the bound "
            "revision"
        )
    if shape_problems:
        findings.extend(f"MEASURED: {problem}" for problem in shape_problems)
        return EvaluationResult(
            product="academy",
            revision=binding.revision,
            satisfied=False,
            findings=tuple(findings),
        )

    usage_sites = tuple(usage_sites)  # type: ignore[arg-type]
    if usage_sites:
        findings.append(
            f"OBSERVED at academy@{binding.revision}: {len(usage_sites)} "
            f"site(s) still reach the unpublished `dotmac_kernel.db"
            f".SessionLocal` attribute directly: {usage_sites!r}"
        )
    if strict_bind_verified is False:
        findings.append(
            f"OBSERVED at academy@{binding.revision}: a strict "
            "`bind_database_runtime(runtime, required=True)` did not "
            "succeed while `dotmac_kernel.db` stayed unimported"
        )

    satisfied = not usage_sites and strict_bind_verified is True
    return EvaluationResult(
        product="academy",
        revision=binding.revision,
        satisfied=satisfied,
        findings=tuple(findings),
    )


def evaluate_erp(binding: ProductBinding) -> EvaluationResult:
    """ERP — synchronous session requirements are expressible against
    `dotmac_kernel.session_runtime.DatabaseRuntime`. Asynchronous support
    stays EXPLICITLY TRANSITIONAL (ADR-0066's "async is out of scope",
    restated by the ERP adoption plan's boundary 6 and named follow-up) —
    recorded as such, never silently omitted and never silently satisfied by
    a claim the Kernel side cannot back.

    KERNEL-SIDE (measured here): the successor's public, non-underscore
    boundary methods on `DatabaseRuntime`, and confirmation that NONE of
    them is `async def` — the Kernel publishes a sync-only runtime today,
    exactly as ADR-0066 states, and this evaluator checks that claim rather
    than repeating it.

    PRODUCT-SIDE (needs a bound revision + captured evidence): whether ERP's
    sync session requirements are actually satisfied by that surface, and
    an explicit `async_status`. `async_status == "satisfied"` is refused
    outright: the Kernel side has no async runtime to satisfy it against, so
    a claim of "satisfied" would be exactly the silent, quiet satisfaction
    this evaluation exists to forbid.
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
            satisfied=False,
            findings=tuple(findings),
        )

    evidence = binding.evidence
    if not isinstance(evidence, Mapping):
        findings.append(
            f"MEASURED: revision {binding.revision} is bound but no "
            "product-side evidence is attached; a bound revision with "
            "nothing captured against it is a coordinate with nothing to "
            "check, which this gate treats as a refusal rather than a pass"
        )
        return EvaluationResult(
            product="erp",
            revision=binding.revision,
            satisfied=False,
            findings=tuple(findings),
        )

    sync_satisfied = evidence.get("sync_requirements_satisfied")
    async_status = evidence.get("async_status")
    shape_problems: list[str] = []
    if not isinstance(sync_satisfied, bool):
        shape_problems.append(
            "product-side evidence must carry a boolean "
            "`sync_requirements_satisfied`, observed at the bound revision"
        )
    if not isinstance(async_status, str) or not async_status.strip():
        shape_problems.append(
            "product-side evidence must carry a non-empty `async_status` "
            f"string; the only accepted value today is {ASYNC_TRANSITIONAL!r} "
            "since the Kernel publishes no async runtime"
        )
    if shape_problems:
        findings.extend(f"MEASURED: {problem}" for problem in shape_problems)
        return EvaluationResult(
            product="erp",
            revision=binding.revision,
            satisfied=False,
            findings=tuple(findings),
        )

    assert isinstance(async_status, str)
    if async_status != ASYNC_TRANSITIONAL:
        findings.append(
            f"OBSERVED at erp@{binding.revision}: `async_status` is "
            f"{async_status!r}, not {ASYNC_TRANSITIONAL!r}; the Kernel "
            "publishes no async DatabaseRuntime boundary, so any status "
            "other than an explicit transitional acknowledgement is a claim "
            "this evaluation refuses to accept silently"
        )
    if sync_satisfied is False:
        findings.append(
            f"OBSERVED at erp@{binding.revision}: ERP's synchronous session "
            "requirements were captured as NOT satisfied by the successor "
            "surface"
        )

    satisfied = sync_satisfied is True and async_status == ASYNC_TRANSITIONAL
    return EvaluationResult(
        product="erp",
        revision=binding.revision,
        satisfied=satisfied,
        findings=tuple(findings),
    )


def evaluate_sub(binding: ProductBinding) -> EvaluationResult:
    """Sub — tenant GUC behaviour plus the typed `readonly_session()` /
    `serializable_session()` transaction modes (slice 3, `#681`) are
    expressible. The part that matters is the GUC HOOK ORDERING relative to
    those modes: the isolation mode must be applied before any `after_begin`
    listener — a tenant-GUC hook among them — can fire, or the mode is
    silently discarded (see `_isolated_session`'s own docstring in
    `session_runtime.py`).

    KERNEL-SIDE (measured here): `readonly_session`/`serializable_session`/
    `tenant_scope` all exist on `DatabaseRuntime`, and — structurally, by
    parsing `_isolated_session`'s own body — the `execution_options` call
    that sets the isolation mode appears strictly BEFORE that method's
    `yield`, which is what guarantees the mode is set ahead of any
    `after_begin` GUC hook a caller composes inside the block.

    PRODUCT-SIDE (needs a bound revision + captured evidence): whether Sub's
    own tenant-GUC `after_begin` hook is actually composed inside
    `tenant_scope`, ordered after the isolation mode, at the bound revision.
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
            satisfied=False,
            findings=tuple(findings),
        )

    evidence = binding.evidence
    if not isinstance(evidence, Mapping):
        findings.append(
            f"MEASURED: revision {binding.revision} is bound but no "
            "product-side evidence is attached; a bound revision with "
            "nothing captured against it is a coordinate with nothing to "
            "check, which this gate treats as a refusal rather than a pass"
        )
        return EvaluationResult(
            product="sub",
            revision=binding.revision,
            satisfied=False,
            findings=tuple(findings),
        )

    guc_hook_ordered_after_mode = evidence.get("guc_hook_ordered_after_isolation_mode")
    tenant_scope_composed = evidence.get(
        "tenant_scope_composed_with_readonly_or_serializable"
    )
    shape_problems: list[str] = []
    if not isinstance(guc_hook_ordered_after_mode, bool):
        shape_problems.append(
            "product-side evidence must carry a boolean "
            "`guc_hook_ordered_after_isolation_mode`, observed at the bound "
            "revision"
        )
    if not isinstance(tenant_scope_composed, bool):
        shape_problems.append(
            "product-side evidence must carry a boolean "
            "`tenant_scope_composed_with_readonly_or_serializable`, observed "
            "at the bound revision"
        )
    if shape_problems:
        findings.extend(f"MEASURED: {problem}" for problem in shape_problems)
        return EvaluationResult(
            product="sub",
            revision=binding.revision,
            satisfied=False,
            findings=tuple(findings),
        )

    if guc_hook_ordered_after_mode is False:
        findings.append(
            f"OBSERVED at sub@{binding.revision}: the tenant-GUC hook was "
            "captured as NOT ordered after the isolation mode"
        )
    if tenant_scope_composed is False:
        findings.append(
            f"OBSERVED at sub@{binding.revision}: `tenant_scope` was "
            "captured as NOT composed with `readonly_session`/"
            "`serializable_session`"
        )

    satisfied = (
        not missing
        and ordering_holds
        and guc_hook_ordered_after_mode is True
        and tenant_scope_composed is True
    )
    return EvaluationResult(
        product="sub",
        revision=binding.revision,
        satisfied=satisfied,
        findings=tuple(findings),
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

    Raises `ValueError` for a key in `bindings` outside `PRODUCTS`: an
    unknown product name is a claim no procedure here knows how to check,
    and silently ignoring it would let a caller "pass" a product that was
    never really evaluated.
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
    """Read the evidence-binding seam's one data file. Today every entry is
    unbound (`revision: null`); binding a real revision is editing this file
    alone."""
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
        result[product] = ProductBinding(
            product=product,
            revision=row.get("revision"),
            evidence=row.get("evidence"),
        )
    return result
