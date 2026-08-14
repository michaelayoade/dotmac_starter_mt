"""There is exactly ONE place that turns a permission code into a role check.

`dotmac_kernel.deps.authorize_party` resolves a declared code to its
`default_roles` and asks the membership query. Every surface — the bearer API,
a cookie-rendered portal, a separate assembly's own plane — reaches the decision
through it.

The invariant this file guards is narrow and mechanical: `active_permissions()`
is called in exactly one function in `deps.py`. That call is the code→roles
binding, and a SECOND one would be a second authorization path — the shape that
lets one surface drift after the other is fixed. Workspace blocker B1 is the
concrete case: the assembly needed a cookie-authenticated permission check, and
the tempting fix was to hand-roll the role query in the assembly. This test is
what makes the shared seam the only reachable answer inside the kernel, and
`tests/unit/test_permission_seam.py` is what proves the two surfaces agree.

AST-based, never string matching: a mention of `active_permissions` in a
docstring, a comment or `__all__` never trips it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import dotmac_kernel.deps

DEPS_PATH = Path(dotmac_kernel.deps.__file__)

# The one function permitted to bind a permission code to its roles.
SEAM = "authorize_party"

# The catalogue lookup that IS the binding. `_holds_any_role` is deliberately
# not guarded here: it is the raw membership query and `require_role` calls it
# directly and legitimately, naming a role rather than a decision.
BINDING_CALL = "active_permissions"


def functions_calling(source: str, callee: str) -> list[str]:
    """Every top-level function in `source` whose body calls `callee`.

    A pure checker over text so the sensitivity proof below can feed it
    synthetic source without touching the filesystem (ADR-0018: a guard carries
    a proof that its detector still fires).
    """
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == callee
            ):
                found.append(node.name)
                break
    return found


def test_only_one_function_binds_a_permission_code_to_roles() -> None:
    callers = functions_calling(DEPS_PATH.read_text(), BINDING_CALL)
    assert callers == [SEAM], (
        f"{BINDING_CALL}() is called by {sorted(callers)!r}, expected only "
        f"[{SEAM!r}]. A second caller is a second authorization path: the two "
        "will drift, and the surface nobody is looking at is the one that keeps "
        "the old behaviour after a security fix. Route the new caller through "
        f"{SEAM} (or `permission_guard`, which wraps it) instead."
    )


def test_the_bearer_guard_holds_no_decision_of_its_own() -> None:
    """`require_permission` must delegate, not re-derive. If it regains its own
    catalogue lookup, the test above already fails — this one names the specific
    regression so the failure reads as what it is."""
    callers = functions_calling(DEPS_PATH.read_text(), BINDING_CALL)
    assert "require_permission" not in callers
    assert "permission_guard" not in callers


def test_the_detector_fires_on_a_planted_second_binding() -> None:
    """Sensitivity proof (ADR-0018). A check that passes over source it cannot
    actually read would pass for the wrong reason forever."""
    planted = (
        "def authorize_party(db, *, tenant, party, code):\n"
        "    spec = active_permissions().require(code)\n"
        "    return _holds_any_role(db, tenant=tenant, party=party,"
        " role_slugs=spec.default_roles)\n"
        "\n"
        "def sneaky_second_path(db, *, tenant, party, code):\n"
        "    spec = active_permissions().require(code)\n"
        "    return True\n"
    )
    assert functions_calling(planted, BINDING_CALL) == [
        "authorize_party",
        "sneaky_second_path",
    ]


def test_the_detector_is_not_fooled_by_a_mention_in_prose() -> None:
    """The complement of the proof above: it must also NOT fire on text."""
    prose = (
        "def authorize_party(db, *, tenant, party, code):\n"
        '    """Calls active_permissions() — this docstring must not count."""\n'
        "    spec = active_permissions().require(code)\n"
        "    return True\n"
        "\n"
        "def innocent():\n"
        "    # active_permissions() is mentioned here in a comment only\n"
        '    return "active_permissions()"\n'
    )
    assert functions_calling(prose, BINDING_CALL) == ["authorize_party"]
