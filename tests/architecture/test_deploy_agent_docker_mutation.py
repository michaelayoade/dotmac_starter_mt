"""Architect r5 ruling (b): `docker rm -f` is the only mutating CONTAINER command the
deploy agent runs. It lives in ONE function, takes ids only from the label listing, and
never passes `-v`. (`docker pull` of a transferred image stays in `transfer.py`; `ps`,
`inspect` and `image inspect` are read-only.)"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-deploy-agent/src/dotmac_deploy_agent"
)
READ_ONLY = {"ps", "inspect", "image"}
ALLOWED = {
    "launcher.py": {"ps", "rm"},
    "reconcile.py": {"ps", "inspect", "image"},
    "job_delivery.py": {"image"},
    "transfer.py": {"pull", "image"},
}


def docker_verbs() -> list[tuple[str, str, str]]:
    """`(file, enclosing function, verb)` for every argv built with `docker_path`."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Tuple | ast.List) or len(node.elts) < 2:
                    continue
                first = ast.unparse(node.elts[0])
                second = node.elts[1]
                # an argv starts with `<config>.docker_path` (str() of it, or bare);
                # other tuples that merely mention a `*_path` name are not argv
                if (
                    re.fullmatch(r"(?:str\()?(?:\w+\.)*docker_path\)?", first)
                    and isinstance(second, ast.Constant)
                ):
                    found.append((path.name, function.name, str(second.value)))
    return found


def test_the_only_mutating_container_command_is_rm_in_one_function() -> None:
    verbs = docker_verbs()
    assert verbs, "the scan found no docker argv at all: the guard is blind"
    for name, function, verb in verbs:
        assert verb in ALLOWED.get(name, set()), (name, function, verb)
    mutating = [(n, f) for n, f, v in verbs if v == "rm"]
    assert mutating == [("launcher.py", "_remove_owned_containers")]


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def test_rm_takes_ids_only_from_the_label_listing_and_never_a_volume_flag() -> None:
    source = (SRC / "launcher.py").read_text()
    tree = ast.parse(source)
    function = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_remove_owned_containers"
    )  # fmt: skip
    text = ast.unparse(function)
    # the docstring is prose, not the argv: the `-v` checks run on code only
    body = [n for n in function.body if not _is_docstring(n)]
    code = ast.unparse(ast.Module(body=body, type_ignores=[]))
    assert "self._labelled(" in text  # the ids come from the listing ...
    assert "mine = [cid for cid, label in listing" in text  # ... and from it alone
    assert "self._is_mine(" in text  # only this agent's own dispatches
    assert "'-v'" not in code and "'--volumes'" not in code and "shell" not in code
    constants = {
        n.value
        for stmt in function.body
        if not _is_docstring(stmt)
        for n in ast.walk(stmt)
        if isinstance(n, ast.Constant)
    }
    assert {"rm", "-f"} <= constants and not {
        "-v",
        "--volumes",
        "-fv",
        "-vf",
    } & constants


def test_no_other_function_names_a_container_removal_verb() -> None:
    banned = {"rm", "kill", "rmi", "prune", "exec"}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            if (path.name, function.name) == (
                "launcher.py",
                "_remove_owned_containers",
            ):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Constant):
                    assert node.value not in banned, (
                        path.name,
                        function.name,
                        node.value,
                    )
