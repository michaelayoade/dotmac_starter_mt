"""Enabling an installation is a lifecycle TRANSITION, not a binding writer.

The report this file answers was "enabling a connector installation resets that
installation's bindings". `dotmac_integration.lifecycle.enable` does not do
that today — it writes `connector_installations` and the one
`connector_config_revisions` row it validated, and nothing else. The reason to
assert it anyway is that nothing stopped it becoming true: `enable` already
calls three private helpers, two of the other lifecycle operations legitimately
reach `_invalidate_activation` (which disables every binding on the
installation), and adding one call to it from `enable` would be a two-word edit
that no existing test notices.

## The enforceable premise (ADR-0018)

A type-directed "does this write a `CapabilityBinding` column?" check is not
available to a static scan — the receiver's type is not in the source. What IS
in the source, and what this scan therefore enforces, is a strict allowlist of
RECEIVER NAMES: every attribute assignment reachable from `enable` must be to
`installation.…` or `revision.…`. A future edit that writes `binding.state`
fails on the name; one that writes `b.state` fails too, because the allowlist is
closed rather than a `binding`-prefix denylist. Evading it requires naming a
capability binding `installation`, which is a deliberate lie rather than an
oversight — and that is the honest boundary of what this guard covers.

Two facts back it up, and neither depends on names:

* `lifecycle` does not import `CapabilityDestinationRevision` at all, so
  nothing on this path can append or rewrite a destination revision — where a
  binding's traffic lands is `destination_binding`'s table and
  `establish_destination`'s decision;
* the binding writers — `_invalidate_activation`, `add_binding`,
  `set_binding_enabled`, `set_binding_scope`,
  `set_binding_selection_policy` — are absent from `enable`'s transitive
  callee closure.

Every check here carries a sensitivity half that plants the violation and
proves the detector fires, because a scan whose subject is currently clean
passes for the wrong reason otherwise.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = (
    REPO_ROOT / "packages/dotmac-integration/src/dotmac_integration/lifecycle.py"
)

#: The only receivers `enable` and its helpers may write attributes on. The
#: installation is the row whose state is transitioning; the revision is the
#: configuration that transition validated. A capability binding is neither.
ALLOWED_RECEIVERS = frozenset({"installation", "revision"})

#: Functions that write `CapabilityBinding` columns. None may be reachable from
#: `enable`: enabling is a decision about the INSTALLATION, and a binding's
#: state is its own operator decision with its own owner.
BINDING_WRITERS = frozenset(
    {
        "_invalidate_activation",
        "add_binding",
        "set_binding_enabled",
        "set_binding_scope",
        "set_binding_selection_policy",
    }
)


def _module_functions(source: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(source)
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _called_names(node: ast.AST) -> set[str]:
    """Every bare `name(...)` call in a function body.

    Deliberately ignores `obj.method(...)`: those are the session and the
    registry, neither of which this module can reach a binding through without
    naming `CapabilityBinding`, which the import check covers.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            names.add(child.func.id)
    return names


def reachable_from(source: str, *, entry: str) -> set[str]:
    """The transitive closure of module-level functions `entry` can call."""
    functions = _module_functions(source)
    if entry not in functions:  # pragma: no cover - a rename must fail loudly
        raise AssertionError(f"{entry!r} is not a module-level function")
    seen: set[str] = set()
    pending = [entry]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        node = functions.get(current)
        if node is None:
            continue
        pending.extend(_called_names(node) & functions.keys())
    return seen


def attribute_writes(source: str, *, entry: str) -> set[tuple[str, str]]:
    """`(receiver, attribute)` for every attribute assignment reachable from
    `entry`, including augmented and annotated assignments."""
    functions = _module_functions(source)
    writes: set[tuple[str, str]] = set()
    for name in reachable_from(source, entry=entry):
        node = functions.get(name)
        if node is None:  # pragma: no cover - closure only holds real functions
            continue
        for child in ast.walk(node):
            targets: list[ast.expr] = []
            if isinstance(child, ast.Assign):
                targets = list(child.targets)
            elif isinstance(child, ast.AugAssign | ast.AnnAssign):
                targets = [child.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(
                    target.value, ast.Name
                ):
                    writes.add((target.value.id, target.attr))
    return writes


def _source() -> str:
    return LIFECYCLE.read_text(encoding="utf-8")


# ── 1. Enabling writes the installation and its revision, and nothing else ──


def test_enable_writes_only_the_installation_and_its_revision() -> None:
    writes = attribute_writes(_source(), entry="enable")
    receivers = {receiver for receiver, _ in writes}
    assert receivers <= ALLOWED_RECEIVERS, sorted(receivers - ALLOWED_RECEIVERS)


def test_the_receiver_allowlist_bites() -> None:
    """Sensitivity proof: plant exactly the reported defect."""
    planted = (
        "def enable(db, installation, *, registry):\n"
        "    binding = None\n"
        "    binding.state = 'disabled'\n"
        "    installation.state = 'enabled'\n"
    )
    receivers = {receiver for receiver, _ in attribute_writes(planted, entry="enable")}
    assert not receivers <= ALLOWED_RECEIVERS
    assert ("binding", "state") in attribute_writes(planted, entry="enable")


def test_the_allowlist_reaches_through_a_helper() -> None:
    """A defect one call deep must fail too — that is the whole point of the
    closure, since `enable` already delegates to three private helpers."""
    planted = (
        "def _reseed(db, installation):\n"
        "    binding = None\n"
        "    binding.enabled_at = None\n"
        "\n"
        "def enable(db, installation, *, registry):\n"
        "    _reseed(db, installation)\n"
        "    installation.state = 'enabled'\n"
    )
    assert ("binding", "enabled_at") in attribute_writes(planted, entry="enable")


# ── 2. No binding writer is reachable from enable ───────────────────────────


def test_no_binding_writer_is_reachable_from_enable() -> None:
    reached = reachable_from(_source(), entry="enable")
    assert not reached & BINDING_WRITERS, sorted(reached & BINDING_WRITERS)


def test_every_named_binding_writer_still_exists() -> None:
    """The denylist is only meaningful while its names are real.

    A rename that emptied `BINDING_WRITERS` would leave the check above
    trivially green — the failure mode ADR-0018 calls an implied guard.
    """
    functions = _module_functions(_source())
    missing = sorted(BINDING_WRITERS - functions.keys())
    assert not missing, missing


def test_the_writer_reachability_check_bites() -> None:
    planted = (
        "def _invalidate_activation(db, installation, *, reason, actor):\n"
        "    installation.state = 'draft'\n"
        "\n"
        "def enable(db, installation, *, registry):\n"
        "    _invalidate_activation(db, installation, reason='x', actor=None)\n"
    )
    assert "_invalidate_activation" in reachable_from(planted, entry="enable")


# ── 3. A destination revision is not even nameable here ─────────────────────


def test_the_lifecycle_owner_cannot_reach_a_destination_revision() -> None:
    """Where traffic LANDS is `destination_binding`'s decision and its own
    append-only table. The lifecycle owner never imports the model, so no
    lifecycle transition — enabling included — can append, rewrite or delete a
    destination revision."""
    tree = ast.parse(_source())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "CapabilityDestinationRevision" not in imported
    assert "dotmac_integration.destination_binding" not in imported


def test_the_destination_import_check_bites() -> None:
    planted = "from dotmac_integration.models import CapabilityDestinationRevision\n"
    tree = ast.parse(planted)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "CapabilityDestinationRevision" in imported
