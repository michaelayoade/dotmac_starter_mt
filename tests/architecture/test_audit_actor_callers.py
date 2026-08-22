"""Every shipped tenant-audit caller supplies the canonical actor identity."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = (
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "packages",
)


#: Actor types whose `.actor_type` may stand in for a literal at a call site,
#: pinned to the EXACT vocabulary each one is allowed to carry.
#:
#: The literal rule exists so a reader can see who acted without running the
#: program. A frozen dataclass whose `actor_type` is a `Literal` and whose
#: constructor refuses everything outside it gives the same guarantee one level
#: up: the vocabulary is still closed and still auditable, it is simply proven
#: once at construction instead of restated at every call site. Fulfillment's
#: repair commands genuinely carry a runtime-selected operator, so the literal
#: form would mean branching three ways at three call sites to re-state a
#: vocabulary the type already enforces.
#:
#: The vocabulary is pinned HERE as well as declared in the package, which is
#: what keeps this a ratchet rather than a hole: widening the `Literal` alone
#: fails `test_the_validated_actor_registry_matches_the_shipped_types`, so a
#: larger actor set is a reviewed diff in this file.
ACCEPTED_ACTOR_TYPES: dict[str, frozenset[str]] = {
    "RepairActor": frozenset({"api_key", "service", "user"}),
}


def _walk_with_scope(
    tree: ast.AST,
) -> list[tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]]:
    """Every node paired with the function that lexically encloses it."""
    found: list[tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]] = []

    def visit(node: ast.AST, scope: ast.FunctionDef | ast.AsyncFunctionDef | None):
        found.append((node, scope))
        inner = (
            node if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else scope
        )
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, None)
    return found


def _writer_calls() -> (
    list[tuple[Path, ast.Call, ast.FunctionDef | ast.AsyncFunctionDef | None]]
):
    calls: list[
        tuple[Path, ast.Call, ast.FunctionDef | ast.AsyncFunctionDef | None]
    ] = []
    for root in PRODUCTION_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            calls.extend(
                (path, node, scope)
                for node, scope in _walk_with_scope(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "write_audit_event"
            )
    return calls


def _annotation_name(annotation: ast.expr | None) -> str | None:
    """The bare type name of a parameter annotation, if it is a bare name."""
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # `from __future__ import annotations` leaves some forms as strings.
        try:
            parsed = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
        return parsed.id if isinstance(parsed, ast.Name) else None
    return None


def _validated_actor_read(
    value: ast.expr,
    scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> str | None:
    """`actor.actor_type` where `actor` is statically an accepted actor type.

    Returns the type name when the read is provably closed-vocabulary, else
    None. A bare variable, an attribute on an UNANNOTATED parameter, and an
    attribute on any type outside the registry all return None — the call site
    then has to name a literal like every other one.
    """
    if not isinstance(value, ast.Attribute) or value.attr != "actor_type":
        return None
    if not isinstance(value.value, ast.Name) or scope is None:
        return None
    target = value.value.id
    args = scope.args
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        if arg.arg != target:
            continue
        name = _annotation_name(arg.annotation)
        return name if name in ACCEPTED_ACTOR_TYPES else None
    return None


def _actor_problem(
    call: ast.Call,
    scope: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
) -> str | None:
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    actor_type = keywords.get("actor_type")
    actor_id = keywords.get("actor_id")
    party_id = keywords.get("actor_party_id")
    if actor_type is None:
        return "actor_type must be explicit; actor_party_id is only enrichment"
    if not isinstance(actor_type, ast.Constant) or not isinstance(
        actor_type.value, str
    ):
        if _validated_actor_read(actor_type, scope) is None:
            return "actor_type must be an auditable literal at each shipped call site"
        # A closed-vocabulary actor still has to identify the principal: every
        # value it can carry is non-system, so `actor_id` is never optional.
        return (
            None
            if actor_id is not None
            else ("every non-system actor needs an explicit actor_id")
        )
    if actor_type.value != "system" and actor_id is None:
        return "every non-system actor needs an explicit actor_id"
    if actor_type.value == "user" and party_id is not None:
        if actor_id is None or ast.unparse(actor_id) != f"str({ast.unparse(party_id)})":
            return "an authenticated Party user must identify that same principal"
    return None


def test_every_shipped_audit_writer_names_the_canonical_actor() -> None:
    calls = _writer_calls()
    assert len(calls) == 21, "the ratchet must change when the caller set changes"

    problems = [
        f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}: {problem}"
        for path, call, scope in calls
        if (problem := _actor_problem(call, scope))
    ]
    assert not problems, f"non-canonical audit actor callers: {problems}"


def test_actor_guard_ignores_prose_that_only_names_the_writer() -> None:
    """A source-text grep would flag the explanation of the invariant itself."""
    tree = ast.parse("# write_audit_event must name the actor pair\nvalue = 1\n")
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "write_audit_event"
        for node in ast.walk(tree)
    )


def test_actor_guard_rejects_the_retired_party_only_shape() -> None:
    """Sensitivity: deleting the pair recreates the exact removed fallback."""
    tree = ast.parse("write_audit_event(db, actor_party_id=party.id)")
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert _actor_problem(call) == (
        "actor_type must be explicit; actor_party_id is only enrichment"
    )


def test_actor_guard_rejects_a_user_whose_identifier_is_still_derived() -> None:
    """Naming only the kind still relies on the other half of the old shim."""
    tree = ast.parse(
        'write_audit_event(db, actor_party_id=party.id, actor_type="user")'
    )
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert _actor_problem(call) == "every non-system actor needs an explicit actor_id"


# ── The premise behind ACCEPTED_ACTOR_TYPES, proven rather than asserted ─────
#
# A registry entry is a claim about a shipped type: frozen, `Literal`-typed,
# and refusing every value outside that Literal at construction. The claim is
# re-derived from source here, so the exemption cannot outlive the properties
# that justify it (ADR-0018 — an exemption states an ENFORCEABLE premise).


def _class_defs() -> dict[str, tuple[Path, ast.ClassDef]]:
    found: dict[str, tuple[Path, ast.ClassDef]] = {}
    for root in PRODUCTION_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in ACCEPTED_ACTOR_TYPES:
                    found[node.name] = (path, node)
    return found


def _is_frozen_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "dataclass":
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "frozen" and isinstance(keyword.value, ast.Constant):
                return keyword.value.value is True
    return False


def _literal_alias_for_actor_type(node: ast.ClassDef) -> str | None:
    """The alias annotating `actor_type`, e.g. `RepairActorType`."""
    for stmt in node.body:
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "actor_type"
        ):
            return _annotation_name(stmt.annotation)
    return None


def _literal_values(tree: ast.Module, alias: str) -> frozenset[str] | None:
    """The exact strings of `alias = Literal[...]` at module level."""
    for stmt in ast.walk(tree):
        target = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != alias:
            continue
        if not isinstance(value, ast.Subscript):
            continue
        head = value.value
        if getattr(head, "id", getattr(head, "attr", None)) != "Literal":
            continue
        elements = (
            value.slice.elts if isinstance(value.slice, ast.Tuple) else [value.slice]
        )
        if not all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elements
        ):
            return None
        return frozenset(e.value for e in elements)  # type: ignore[attr-defined]
    return None


def _refuses_outside_the_alias(node: ast.ClassDef, alias: str) -> bool:
    """`__post_init__` raises for anything not in a set DERIVED from `alias`.

    Derived, not restated: the membership test must read a name built from
    `get_args(alias)`, so widening the Literal cannot leave a narrower runtime
    check behind — or a wider one behind a narrower type.
    """
    post_init = next(
        (
            stmt
            for stmt in node.body
            if isinstance(stmt, ast.FunctionDef) and stmt.name == "__post_init__"
        ),
        None,
    )
    if post_init is None:
        return False
    for stmt in ast.walk(post_init):
        if not isinstance(stmt, ast.If):
            continue
        test = stmt.test
        if not (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.NotIn)
            and isinstance(test.comparators[0], ast.Name)
        ):
            continue
        if not any(isinstance(inner, ast.Raise) for inner in ast.walk(stmt)):
            continue
        return _derives_from_alias(node, test.comparators[0].id, alias)
    return False


def _derives_from_alias(node: ast.ClassDef, constant: str, alias: str) -> bool:
    """`constant = frozenset(get_args(alias))` somewhere in the same module."""
    module = _class_defs()[node.name][0]
    tree = ast.parse(module.read_text(), filename=str(module))
    for stmt in ast.walk(tree):
        target = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != constant:
            continue
        return any(
            isinstance(inner, ast.Call)
            and getattr(inner.func, "id", getattr(inner.func, "attr", None))
            == "get_args"
            and any(isinstance(a, ast.Name) and a.id == alias for a in inner.args)
            for inner in ast.walk(value)
        )
    return False


def test_the_validated_actor_registry_matches_the_shipped_types() -> None:
    """Every registry entry earns itself, and the pinned vocabulary is exact."""
    defs = _class_defs()
    missing = sorted(set(ACCEPTED_ACTOR_TYPES) - set(defs))
    assert not missing, (
        f"{missing} is exempted from the literal rule but ships nowhere — "
        "remove the entry rather than leaving an exemption with no subject"
    )

    for name, expected in sorted(ACCEPTED_ACTOR_TYPES.items()):
        path, node = defs[name]
        where = path.relative_to(PROJECT_ROOT)
        assert _is_frozen_dataclass(node), (
            f"{where}: {name} is exempted from the literal rule but is not a "
            "frozen dataclass — a mutable actor can be widened after validation"
        )
        alias = _literal_alias_for_actor_type(node)
        assert alias is not None, (
            f"{where}: {name}.actor_type must be annotated with a named Literal "
            "alias; a bare `str` proves nothing about the vocabulary"
        )
        values = _literal_values(ast.parse(path.read_text()), alias)
        assert (
            values is not None
        ), f"{where}: {alias} must be a module-level Literal of string constants"
        assert values == expected, (
            f"{where}: {name} now carries {sorted(values)}, but this guard pins "
            f"{sorted(expected)}. Widening the actor vocabulary is a reviewed "
            "diff in ACCEPTED_ACTOR_TYPES, never a silent one in the package."
        )
        assert _refuses_outside_the_alias(node, alias), (
            f"{where}: {name}.__post_init__ must refuse any value outside "
            f"frozenset(get_args({alias})) — a type annotation is not a runtime "
            "check, and the exemption rests on construction being closed"
        )


# ── Sensitivity: the shapes this exemption must still refuse ────────────────


def _one_call(source: str) -> tuple[ast.Call, ast.FunctionDef | None]:
    tree = ast.parse(source)
    call, scope = next(
        (node, scope)
        for node, scope in _walk_with_scope(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "write_audit_event"
    )
    return call, scope


LITERAL_REQUIRED = "actor_type must be an auditable literal at each shipped call site"


def test_an_arbitrary_variable_is_still_not_an_auditable_actor() -> None:
    """The plain dynamic case the rule exists for."""
    call, scope = _one_call(
        "def f(db, actor_type, actor_id):\n"
        "    write_audit_event(db, actor_type=actor_type, actor_id=actor_id)\n"
    )
    assert _actor_problem(call, scope) == LITERAL_REQUIRED


def test_an_untyped_actor_attribute_is_still_refused() -> None:
    """`.actor_type` reads like the accepted shape and proves nothing."""
    call, scope = _one_call(
        "def f(db, actor, actor_id):\n"
        "    write_audit_event(db, actor_type=actor.actor_type, actor_id=actor_id)\n"
    )
    assert _actor_problem(call, scope) == LITERAL_REQUIRED


def test_an_unregistered_actor_type_is_still_refused() -> None:
    """Annotation alone is not the premise; the type must be in the registry."""
    call, scope = _one_call(
        "def f(db, actor: SomeOtherActor, actor_id):\n"
        "    write_audit_event(db, actor_type=actor.actor_type, actor_id=actor_id)\n"
    )
    assert _actor_problem(call, scope) == LITERAL_REQUIRED


def test_a_cast_does_not_launder_an_unvalidated_actor() -> None:
    """`cast(RepairActor, x).actor_type` asserts the type rather than proving it."""
    call, scope = _one_call(
        "def f(db, payload, actor_id):\n"
        "    write_audit_event(\n"
        "        db,\n"
        "        actor_type=cast(RepairActor, payload).actor_type,\n"
        "        actor_id=actor_id,\n"
        "    )\n"
    )
    assert _actor_problem(call, scope) == LITERAL_REQUIRED


def test_a_registered_actor_still_needs_an_explicit_identifier() -> None:
    """Closing the vocabulary does not excuse identifying the principal."""
    call, scope = _one_call(
        "def f(db, actor: RepairActor):\n"
        "    write_audit_event(db, actor_type=actor.actor_type)\n"
    )
    assert _actor_problem(call, scope) == (
        "every non-system actor needs an explicit actor_id"
    )


def test_the_accepted_shape_passes() -> None:
    """The positive case, so the four refusals above are not vacuous."""
    call, scope = _one_call(
        "def f(db, actor: RepairActor, actor_id):\n"
        "    write_audit_event(db, actor_type=actor.actor_type, actor_id=actor_id)\n"
    )
    assert _actor_problem(call, scope) is None


def test_a_widened_vocabulary_fails_the_registry_not_the_call_site() -> None:
    """Sensitivity for the ratchet: the pin is what makes widening visible.

    A package that adds a fourth actor type keeps passing at every call site —
    the shape is unchanged — so the pinned set is the only thing that reports
    it. Proven against a copy, so the real registry is untouched.
    """
    widened = frozenset({"api_key", "service", "user", "impersonator"})
    assert widened != ACCEPTED_ACTOR_TYPES["RepairActor"]
    assert not widened <= ACCEPTED_ACTOR_TYPES["RepairActor"]
