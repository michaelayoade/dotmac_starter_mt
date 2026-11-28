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


_DOCKER_ARGV0 = r"(?:str\()?(?:\w+\.)*docker_path\)?"
#: R7-L1: container verbs no sanctioned call site uses; refused in any docker argv.
BANNED_ARGV_VERBS = {"stop", "start", "run", "create"}


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
                # R7-L1: and any first node that is NOT a string constant but whose
                # source mentions `docker_path` (`os.fspath(...)`, an f-string,
                # `.as_posix()`): a wrapped argv must not slip past the scan
                is_argv = re.fullmatch(_DOCKER_ARGV0, first) is not None or (
                    not isinstance(node.elts[0], ast.Constant)
                    and "docker_path" in first
                )
                if is_argv and isinstance(second, ast.Constant):
                    found.append((path.name, function.name, str(second.value)))
    return found


def test_the_only_mutating_container_command_is_rm_in_one_function() -> None:
    verbs = docker_verbs()
    assert verbs, "the scan found no docker argv at all: the guard is blind"
    for name, function, verb in verbs:
        assert verb not in BANNED_ARGV_VERBS, (name, function, verb)
        assert verb in ALLOWED.get(name, set()), (name, function, verb)
    mutating = [(n, f) for n, f, v in verbs if v == "rm"]
    assert mutating == [("launcher.py", "_remove_owned_containers")]


#: config.py only DEFINES `docker_path` and lists it among the paths it validates;
#: it never builds an argv, so it is the one file the use-site rule skips.
#: R9-L1: keyed on the EXACT path; a subpackage `config.py` is not exempt.
USE_SITE_EXEMPT = {SRC / "config.py"}


def stray_docker_path_uses(tree: ast.AST) -> list[int]:
    """R8-L3: line numbers of every `docker_path` use that is NOT inside the first
    element of an argv (a tuple or list) whose second element is a string constant.
    An alias (`d = str(cfg.docker_path)`) or a variable verb fails here."""
    covered: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Tuple | ast.List)
            and len(node.elts) >= 2
            and isinstance(node.elts[1], ast.Constant)
            and isinstance(node.elts[1].value, str)
        ):
            covered.update(id(n) for n in ast.walk(node.elts[0]))
    stray: list[int] = []
    for node in ast.walk(tree):
        named = (isinstance(node, ast.Attribute) and node.attr == "docker_path") or (
            isinstance(node, ast.Name) and node.id == "docker_path"
        )
        if named and id(node) not in covered:
            stray.append(node.lineno)  # type: ignore[attr-defined]
    return sorted(stray)


def test_every_docker_path_use_heads_an_argv_with_a_constant_verb() -> None:
    for path in sorted(SRC.rglob("*.py")):
        if path in USE_SITE_EXEMPT:
            continue
        assert stray_docker_path_uses(ast.parse(path.read_text())) == [], str(path)


def test_the_use_site_rule_is_sensitive_to_aliases_and_variable_verbs() -> None:
    def stray(source: str) -> list[int]:
        return stray_docker_path_uses(ast.parse(source))

    assert stray("a = (str(cfg.docker_path), 'ps', '-a')") == []
    assert stray("a = [os.fspath(cfg.docker_path), 'ps']") == []
    assert stray("d = str(cfg.docker_path)\na = (d, 'stop')") == [1]  # alias
    assert stray("a = (str(cfg.docker_path), verb)") == [1]  # variable verb
    assert stray("a = (str(cfg.docker_path),)") == [1]  # no verb at all
    assert stray("a = ('x', str(cfg.docker_path), 'ps')") == [1]  # not first
    assert stray("f(cfg.docker_path)") == [1]  # passed along
    assert stray("a = (docker_path, 'ps')") == []


def _is_os_exec(name: str) -> bool:
    return name in {"system", "popen"} or name.startswith(
        (
            "exec",
            "spawn",
            "posix_spawn",
        )
    )


#: C-1178-1: config.py's imports are a CLOSED set equal to its current imports.
#: A new import (even a harmless one) fails here until this test is edited.
CONFIG_PLAIN_IMPORTS = {"os", "re", "stat", "tomllib"}
CONFIG_FROM_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "pathlib",
    "typing",
    "urllib.parse",
    ".refusal",
    ".wire",
}
#: dynamic-execution and reflection names refused as ANY use (call or reference).
REFUSED_NAMES = {"getattr", "__import__", "exec", "eval", "compile", "importlib"}


def exec_capability_in(source: str) -> list[str]:
    """R9-L1 / C-1178-1: everything in `source` that is outside the closed shape of a
    module that cannot run a process: imports outside the allow-list (relative ones
    included), aliased imports, `os` used other than as `os.<attr>` (rebinding),
    `os.system/popen/exec*/spawn*/posix_spawn*`, and any use of getattr, __import__,
    exec, eval, compile or importlib."""
    tree = ast.parse(source)
    found: set[str] = set()
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in CONFIG_PLAIN_IMPORTS or alias.asname:
                    found.add(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            if module not in CONFIG_FROM_IMPORTS:
                found.add(f"from {module} import")
            for alias in node.names:
                if alias.name == "*":
                    found.add(f"from {module} import *")
                elif alias.asname and module != "__future__":
                    found.add(f"from {module} import {alias.name} as")
        elif isinstance(node, ast.Name):
            if node.id in REFUSED_NAMES:
                found.add(f"name {node.id}")
            if node.id == "os":
                parent = parents[id(node)]
                if not (isinstance(parent, ast.Attribute) and parent.value is node):
                    found.add("os rebound or passed as a value")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                if _is_os_exec(node.attr):
                    found.add(f"os.{node.attr}")
    return sorted(found)


def test_config_py_is_exempt_by_exact_path_and_cannot_run_a_process() -> None:
    assert (SRC / "config.py").is_file()
    assert exec_capability_in((SRC / "config.py").read_text()) == []


def test_the_exec_capability_check_is_a_closed_allow_list() -> None:
    def hits(source: str) -> list[str]:
        return exec_capability_in(source)

    # the current shape is clean
    assert (
        hits("import os\nfrom .wire import x\nos.path.isabs('/')\nos.geteuid()") == []
    )
    # a rebound os, and getattr on os
    assert hits("import os\no = os\no.system('x')\n") == [
        "os rebound or passed as a value"
    ]
    assert hits("import os\ngetattr(os, 'system')('x')\n") == [
        "name getattr",
        "os rebound or passed as a value",
    ]
    assert hits("import os\nf(os)\n") == ["os rebound or passed as a value"]
    # dynamic import and execution
    assert hits("__import__('subprocess')\n") == ["name __import__"]
    assert hits("import importlib\n") == ["import importlib"]
    assert hits("import importlib\nimportlib.import_module('x')\n") == [
        "import importlib",
        "name importlib",
    ]
    assert hits("exec('x')\n") == ["name exec"]
    assert hits("eval('x')\n") == ["name eval"]
    assert hits("compile('x', 'f', 'exec')\n") == ["name compile"]
    # imports outside the allow-list
    assert hits("import pty\n") == ["import pty"]
    assert hits("import asyncio\n") == ["import asyncio"]
    assert hits("import ctypes\n") == ["import ctypes"]
    assert hits("import multiprocessing\n") == ["import multiprocessing"]
    assert hits("from .runner import run\n") == ["from .runner import"]
    assert hits("from . import runner\n") == ["from . import"]
    assert hits("from .commands import run\n") == ["from .commands import"]
    assert hits("import subprocess\n") == ["import subprocess"]
    assert hits("from subprocess import run\n") == ["from subprocess import"]
    assert hits("import pkg.commands\n") == ["import pkg.commands"]
    # aliases and the exec family
    assert hits("import os as o\no.execv('/bin/sh', [])\n") == ["import os"]
    assert hits("from os import execv as e\ne('x', [])\n") == [
        "from os import",
        "from os import execv as",
    ]
    assert hits("import os\nos.execv('x', [])\n") == ["os.execv"]
    assert hits("import os\nos.system('x')\n") == ["os.system"]
    assert hits("import os\nos.popen('x')\n") == ["os.popen"]
    assert hits("import os\nos.posix_spawnp('x', [], {})\n") == ["os.posix_spawnp"]


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


def test_the_scan_sees_wrapped_docker_path_argv(monkeypatch, tmp_path: Path) -> None:
    """R7-L1: wrapped first elements are still docker argv; new verbs banned."""
    source = (
        "import os\n"
        "def f(cfg):\n"
        "    a = (os.fspath(cfg.docker_path), 'stop', 'x')\n"
        "    b = (f'{cfg.docker_path}', 'start', 'x')\n"
        "    c = (cfg.docker_path.as_posix(), 'run', 'x')\n"
        "    d = ('docker_path', 'create')\n"
    )
    (tmp_path / "wrapped.py").write_text(source)
    monkeypatch.setitem(globals(), "SRC", tmp_path)
    assert [v for _, _, v in docker_verbs()] == ["stop", "start", "run"]
    assert {"stop", "start", "run", "create"} <= BANNED_ARGV_VERBS
