"""A caught exception's TEXT never reaches a persisted or returned field.

## What this defends

`dotmac-integration` runs code it did not write. Connector plugins are
independently released distributions discovered from package metadata
(ADR-0024), and this module calls them at three points: `spi.verify_plugin_modes`
during discovery, `ingress` on the inbound request path, and `dispatch.invoke`
on the outbound one. All three run AFTER configuration has been resolved, so a
plugin that interpolates a materialized credential into its own exception — an
ordinary connector bug, not a contrived one — hands that credential to this
module.

`0.1.0a3` (#201) closed the discovery path. `ingress` never opened. `dispatch`
was the third, and the worst of the three: it wrote
`f"{type(exc).__name__}: {exc}"` into `Outcome.error_detail`, which `settle`
persists into `mod_intg.delivery_attempts.error_detail`. A leak into a log line
can be rotated away; a leak into a column outlives the process, the request and
the credential's own rotation, and is read back by every operator surface over
that table. `0.1.0a4` closed it.

Three point fixes are three chances for the fourth site to be written the old
way, which is what this scan is for. The behavioural proofs with their
sensitivity halves live beside the code they cover
(`tests/unit/test_integration_dispatch_safety.py`,
`tests/unit/test_integration_spi_modes.py`); this file is the ratchet that keeps
a NEW handler from reopening it.

## The rule, and the enforceable premise for its one allowance (ADR-0018)

Inside an `except ... as name:` handler, `name` may appear in a value assigned
to `error_detail` ONLY as `type(name).__name__`.

The allowance is not a path list, which would be unenforceable the moment a
function moved. It is a property of the CAUGHT TYPE: a handler that catches an
exception class **this package defines** is reading a message some caller
deliberately authored for this module's own contract, in this module's own
vocabulary — that is the class #201 explicitly left alone ("errors raised by
this module's own registry"). `receipt_delivery.TransportFailure` is the live
example: the protocol documents it as the one exception a gateway converts into
an outcome, and its message is written to be read back. A handler catching
`Exception`, or any type this package did not define, has no such author and
gets no such allowance.

Both halves are machine-checked here: the class names are read from the package
source, not from a list, so deleting a class narrows the allowance
automatically rather than silently widening it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "packages/dotmac-integration/src/dotmac_integration"

#: The field this scan protects. Two real columns carry it —
#: `mod_intg.delivery_attempts.error_detail` and
#: `mod_intg.inbox_receipts.error_detail`, both `Text` — written by
#: `execution.settle_delivery` and `execution.settle_receipt` respectively.
#: Checked against `models.PLATFORM_TABLES` rather than recalled: an earlier
#: draft of this comment also named `receipt_delivery_attempts`, which does not
#: exist.
PROTECTED_FIELD = "error_detail"


#: Functions that reduce a caught exception to its type name. A call to one is
#: a SAFE spelling — but only because the second test below proves each one
#: does what its name claims, by the same erasure this scan uses on handlers.
#: An allowlisted name with an unproven body would be exactly the unenforceable
#: premise ADR-0018 refuses.
SANITISERS = frozenset({"_connector_error_detail"})


def _sources() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def _classes_defined(trees: dict[Path, ast.Module]) -> frozenset[str]:
    """Every class name this package defines.

    Not filtered to exception-shaped classes, and the imprecision is harmless
    in the direction that matters: only an exception type can appear in an
    `except` clause, so a non-exception name in this set can never widen the
    allowance. Filtering on a base-class heuristic would be the riskier choice,
    since it would silently NARROW on any exception defined through an alias or
    a base this scan cannot resolve without importing.

    Read from source rather than imported: the scan must describe the files as
    a reviewer sees them, and an import would resolve re-exports the diff does
    not show.
    """
    names: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names.add(node.name)
    return frozenset(names)


def _caught_names(handler: ast.ExceptHandler) -> list[str]:
    """The type names this handler catches, flattened over a tuple."""
    node = handler.type
    if node is None:
        return ["<bare except>"]
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    out: list[str] = []
    for part in parts:
        if isinstance(part, ast.Name):
            out.append(part.id)
        elif isinstance(part, ast.Attribute):
            out.append(part.attr)
        else:  # pragma: no cover - defensive
            out.append(ast.unparse(part))
    return out


def _protected_values(handler: ast.ExceptHandler) -> list[ast.expr]:
    """Every expression inside this handler that ends up in `error_detail`.

    Both spellings are collected, because both occur in this package: the
    keyword form (`Outcome(error_detail=...)`, `ProductOutcome(...)`) and the
    attribute assignment (`receipt.error_detail = ...`).
    """
    values: list[ast.expr] = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == PROTECTED_FIELD:
                    values.append(keyword.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == PROTECTED_FIELD:
                    values.append(node.value)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and target.attr == PROTECTED_FIELD
                and node.value is not None
            ):
                values.append(node.value)
    return values


def _mentions_beyond_the_type_name(value: ast.expr, bound: str) -> bool:
    """Does `bound` appear anywhere in `value` other than as `type(bound).__name__`?

    Implemented by ERASING the safe form and then looking for what is left, so
    a new safe spelling has to be added here deliberately rather than being
    matched by an over-broad pattern.
    """

    class _Eraser(ast.NodeTransformer):
        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
            self.generic_visit(node)
            inner = node.value
            if (
                node.attr == "__name__"
                and isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "type"
                and len(inner.args) == 1
                and isinstance(inner.args[0], ast.Name)
                and inner.args[0].id == bound
            ):
                return ast.Constant(value="<type name>")
            return node

        def visit_Call(self, node: ast.Call) -> ast.AST:
            self.generic_visit(node)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in SANITISERS
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == bound
            ):
                return ast.Constant(value="<sanitised>")
            return node

    remaining = _Eraser().visit(ast.parse(ast.unparse(value)))
    return any(
        isinstance(node, ast.Name) and node.id == bound for node in ast.walk(remaining)
    )


def _violations(trees: dict[Path, ast.Module], defined: frozenset[str]) -> list[str]:
    problems: list[str] = []
    for path, tree in sorted(trees.items()):
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.name is None:
                continue
            caught = _caught_names(node)
            # The enforceable allowance: every caught type is one this package
            # defines, so the message was authored for this module's contract.
            if caught and all(name in defined for name in caught):
                continue
            for value in _protected_values(node):
                if _mentions_beyond_the_type_name(value, node.name):
                    try:
                        where = str(path.relative_to(REPO_ROOT))
                    except ValueError:
                        # The sensitivity proofs point this at a tmp_path copy.
                        where = path.name
                    problems.append(
                        f"{where}:{value.lineno}: "
                        f"`except {'/'.join(caught)} as {node.name}` puts "
                        f"{node.name!r} into {PROTECTED_FIELD} as "
                        f"{ast.unparse(value)!r}. That field is PERSISTED, and "
                        "a connector's exception message may carry a "
                        "materialized secret. Only `type("
                        f"{node.name}).__name__` may travel"
                    )
    return problems


def _parse(paths: list[Path]) -> dict[Path, ast.Module]:
    return {path: ast.parse(path.read_text(encoding="utf-8")) for path in paths}


def test_no_caught_foreign_exception_reaches_a_persisted_error_detail() -> None:
    trees = _parse(_sources())
    assert trees, "the package source moved; this scan would pass over nothing"
    assert not _violations(trees, _classes_defined(trees))


def test_the_scan_bites_on_the_shape_that_shipped(tmp_path: Path) -> None:
    """Sensitivity proof, using the exact line `0.1.0a1`..`0.1.0a3` shipped.

    A scan asserting an empty list passes for the wrong reason the day its
    detector breaks, so the detector is shown RED against the real defect
    rather than against an invented one.
    """
    source = tmp_path / "dispatch.py"
    source.write_text(
        "def invoke(handler, request):\n"
        "    try:\n"
        "        return handler(request)\n"
        "    except Exception as exc:\n"
        "        return Outcome(\n"
        "            status=RECONCILIATION_REQUIRED,\n"
        '            error_code="connector_raised",\n'
        '            error_detail=f"{type(exc).__name__}: {exc}",\n'
        "        )\n",
        encoding="utf-8",
    )
    trees = _parse([source])
    problems = _violations(trees, _classes_defined(trees))
    assert len(problems) == 1, problems
    assert "error_detail" in problems[0]
    assert "except Exception as exc" in problems[0]


def test_the_scan_accepts_the_repaired_shape(tmp_path: Path) -> None:
    """Specificity, in both directions.

    The repaired dispatch shape must stay silent — a guard that refused every
    mention of the bound name would fail here and force the type name out too,
    leaving `error_detail` unable to locate the connector bug at all. And the
    module-owned allowance must stay silent for the same value it exists for.
    """
    source = tmp_path / "mixed.py"
    source.write_text(
        "class TransportFailure(Exception):\n"
        "    pass\n"
        "\n"
        "\n"
        "def outbound(handler, request):\n"
        "    try:\n"
        "        return handler(request)\n"
        "    except Exception as exc:\n"
        '        return Outcome(error_detail=f"raised {type(exc).__name__}")\n'
        "\n"
        "\n"
        "def inbound(gateway, request):\n"
        "    try:\n"
        "        return gateway.deliver(request)\n"
        "    except TransportFailure as failure:\n"
        "        return ProductOutcome(error_detail=str(failure))\n",
        encoding="utf-8",
    )
    trees = _parse([source])
    assert not _violations(trees, _classes_defined(trees))


def test_the_module_owned_allowance_is_not_a_blanket_one(tmp_path: Path) -> None:
    """The allowance is the CAUGHT TYPE, not the file it sits in.

    Written because the tempting simplification — "`receipt_delivery.py` is
    fine" — would let a future `except Exception` in that same file through,
    which is precisely the handler that has no author for its message.
    """
    source = tmp_path / "same_file.py"
    source.write_text(
        "class TransportFailure(Exception):\n"
        "    pass\n"
        "\n"
        "\n"
        "def deliver(gateway, request):\n"
        "    try:\n"
        "        return gateway.deliver(request)\n"
        "    except TransportFailure as failure:\n"
        "        return ProductOutcome(error_detail=str(failure))\n"
        "    except Exception as exc:\n"
        "        return ProductOutcome(error_detail=str(exc))\n",
        encoding="utf-8",
    )
    trees = _parse([source])
    problems = _violations(trees, _classes_defined(trees))
    assert len(problems) == 1, problems
    assert "except Exception as exc" in problems[0]


def test_the_allowance_is_read_from_the_package_not_a_list() -> None:
    """`TransportFailure` is allowed because the package DEFINES it.

    Asserted rather than assumed: if the class were ever moved out of this
    distribution, the allowance must narrow by itself and the handler in
    `receipt_delivery.py` must start failing — which is the difference between
    a premise and a name in a list.
    """
    defined = _classes_defined(_parse(_sources()))
    assert "TransportFailure" in defined
    assert "Exception" not in defined
    assert "KeyError" not in defined


def test_every_sanitiser_reduces_its_argument_to_a_type_name() -> None:
    """The allowance's premise, machine-checked rather than asserted.

    `SANITISERS` widens the scan: a call to one erases its argument the way
    `type(exc).__name__` does. That is only sound while each named function
    really does reduce its parameter to a type name — otherwise the allowlist
    is a hole with a reassuring name on it, and the scan would pass a handler
    that laundered the message through a helper.

    So each one is checked by the SAME erasure the handlers get: inside its
    body, its own parameter may not appear except as `type(param).__name__`.
    """
    trees = _parse(_sources())
    found: set[str] = set()
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in SANITISERS:
                continue
            found.add(node.name)
            args = node.args.args
            assert len(args) == 1, f"{node.name} takes {len(args)} args, expected 1"
            param = args[0].arg
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and inner.value is not None:
                    assert not _mentions_beyond_the_type_name(inner.value, param), (
                        f"{path.name}:{inner.lineno}: {node.name} returns "
                        f"{ast.unparse(inner.value)!r}, which mentions {param!r} "
                        "beyond its type name — so it does not sanitise, and "
                        "allowing calls to it would launder the message"
                    )
    assert found == set(SANITISERS), (
        f"declared sanitisers {sorted(set(SANITISERS) - found)} are not defined "
        "in this package; an allowlist naming a function that does not exist "
        "silently allows nothing and hides that it is stale"
    )
