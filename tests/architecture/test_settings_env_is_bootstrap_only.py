"""ADR-0011 (static half): nothing on the settings surface reads the environment.

The runtime half (`tests/unit/test_settings_resolution_ignores_env.py`) catches
an environment read however it is spelled, but only on paths a test drives.
This covers every path, including ones no test exercises — and names the
functions that ARE allowed to read the environment, so each permission is a
listed exception rather than an accident.

**This sweep replaces a substring scan of one file.** The previous detector
read each function's source text looking for `"environ"` / `"getenv"`, visited
`ast.FunctionDef` only, and scanned `settings_resolver.py` alone. It had
failures in both directions, and the sensitivity proofs below plant one case
for each:

- a helper in ANOTHER module was invisible, and delegation is the obvious shape
  a reintroduced read would take;
- `from os import environ as _e` at module scope, then `_e["X"]` in the body,
  matched no substring at all;
- `ast.AsyncFunctionDef` is a separate node type and was never visited, so an
  `async def` was unscanned;
- module-level code was never visited either, so `_D = os.environ.get(...)`
  feeding a default evaded it;
- and prose was indistinguishable from code — three `settings_crypto`
  functions were flagged purely for saying "the environment" in a docstring.

So this resolves aliases and attribute chains through the AST rather than
matching text, visits sync and async definitions and module scope, and cannot
see a docstring at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotmac_kernel import settings_resolver as sr

KERNEL = Path(sr.__file__).parent
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The settings surface, not one file. Every module a resolved value passes
#: through on its way to a caller, plus the assembly's own settings feature.
#: A module added to the settings path and not added here is unmonitored rather
#: than exempt, which `test_the_swept_surface_is_the_real_one` refuses.
SWEPT_FILES: tuple[Path, ...] = (
    KERNEL / "settings_resolver.py",
    KERNEL / "settings_cache.py",
    KERNEL / "settings_crypto.py",
    KERNEL / "settings_models.py",
    KERNEL / "settings_admin.py",
    KERNEL / "settings_shadow.py",
    KERNEL / "setting_scopes.py",
    KERNEL / "setting_domains.py",
    KERNEL / "setting_value_types.py",
    *sorted((REPO_ROOT / "app" / "features" / "settings").glob("*.py")),
)

#: Reading the environment is legitimate in exactly two places, for two
#: different reasons. Keyed by file stem -> qualified names within it.
#:
#: `seed_settings_from_env` is ADR-0011's bootstrap: it turns `env_var` into a
#: real row at startup, and everything downstream reads the row.
#:
#: `_keyring_from_env` is ADR-0009's key material, which is NOT a setting value
#: — a key that protects data at rest must not live in the database it
#: protects, so the environment (or an installed `KeyProvider`) is where it
#: comes from. `settings_crypto` is swept rather than excluded so that
#: allowance is a named line here instead of an unwatched module.
ENV_READERS_ALLOWED: dict[str, frozenset[str]] = {
    "settings_resolver": frozenset({"seed_settings_from_env"}),
    "settings_crypto": frozenset({"_keyring_from_env"}),
}

#: The two `os` members that read process environment.
ENV_MEMBERS = frozenset({"environ", "getenv"})


class _EnvironmentReads(ast.NodeVisitor):
    """Report every qualified name whose body reaches `os.environ`/`os.getenv`.

    Resolves the ways the access can be spelled instead of matching text:
    ``import os`` / ``import os as o`` (attribute access off the module alias),
    ``from os import environ`` / ``as _e`` (a bare name), and
    ``getattr(os, "environ")``. Attribution is by enclosing definition, with
    module-level code reported as ``<module>``.
    """

    def __init__(self) -> None:
        self.module_aliases: set[str] = set()
        self.member_aliases: set[str] = set()
        self.scope: list[str] = []
        self.found: set[str] = set()

    # -- binding forms -------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            # `import os.path` binds the name `os` unless aliased.
            root = alias.name.split(".")[0]
            if root == "os":
                self.module_aliases.add(alias.asname or root)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "os":
            for alias in node.names:
                if alias.name in ENV_MEMBERS:
                    self.member_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    # -- scopes --------------------------------------------------------
    def _visit_scope(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node, node.name)

    # -- access forms --------------------------------------------------
    def _record(self) -> None:
        self.found.add(".".join(self.scope) if self.scope else "<module>")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in self.module_aliases
            and node.attr in ENV_MEMBERS
        ):
            self._record()
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.member_aliases:
            self._record()
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # getattr(os, "environ") — the string never appears as an attribute.
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in self.module_aliases
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in ENV_MEMBERS
        ):
            self._record()
        self.generic_visit(node)


def _readers(path: Path) -> set[str]:
    visitor = _EnvironmentReads()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return visitor.found


def test_only_the_allowlisted_bootstraps_read_the_environment() -> None:
    offenders: dict[str, list[str]] = {}
    for path in SWEPT_FILES:
        allowed = ENV_READERS_ALLOWED.get(path.stem, frozenset())
        unexpected = sorted(_readers(path) - allowed)
        if unexpected:
            offenders[path.name] = unexpected

    assert not offenders, (
        f"{offenders} read the environment. Settings resolution answers from "
        "stored rows, the assembly profile default, and the spec fallback; "
        "`env_var` is a bootstrap input consumed once by "
        "`seed_settings_from_env` (ADR-0011). An env read on the resolution "
        "path makes the environment a second authority over a value the "
        "settings screen claims to own."
    )


@pytest.mark.parametrize(
    ("stem", "name"),
    sorted(
        (stem, name) for stem, names in ENV_READERS_ALLOWED.items() for name in names
    ),
)
def test_each_allowlisted_reader_still_reads_it(stem: str, name: str) -> None:
    """The allowlist must describe reality.

    If `seed_settings_from_env` stops reading the environment, `env_var` has
    quietly become inert and every spec declaring one is lying. If
    `_keyring_from_env` stops, the env-configured keyring is silently dead and
    every secret degrades to its spec default.
    """
    path = next(p for p in SWEPT_FILES if p.stem == stem)
    assert name in _readers(path), (
        f"{stem}.{name} no longer reads the environment — either it moved, in "
        "which case the sweep is now blind to it, or the mechanism is dead and "
        "the allowlist entry is a lie. Neither is a passing state."
    )


def test_the_swept_surface_is_the_real_one() -> None:
    """Every kernel settings module is swept, so none is unmonitored.

    A new `setting*.py` on the resolution path that nobody adds to
    `SWEPT_FILES` would otherwise inherit an exemption by omission — the exact
    shape ADR-0018 rejects.
    """
    on_disk = {p.name for p in KERNEL.glob("setting*.py")}
    swept = {p.name for p in SWEPT_FILES}
    assert on_disk <= swept, (
        f"unswept kernel settings modules: {sorted(on_disk - swept)}. Add them "
        "to SWEPT_FILES (with an allowlist entry and a reason if one really "
        "must read the environment) rather than leaving them unmonitored."
    )


# --------------------------------------------------------------------------
# Sensitivity proofs: one per way the previous detector could be fooled.
# A passing suite must mean the rule holds, not that the detector never looked.
# --------------------------------------------------------------------------


def _readers_of(source: str, tmp_path: Path, name: str = "planted.py") -> set[str]:
    planted = tmp_path / name
    planted.write_text(source, encoding="utf-8")
    return _readers(planted)


def test_it_catches_a_plain_attribute_read(tmp_path: Path) -> None:
    assert _readers_of(
        "import os\n\n\ndef _finish():\n    return os.environ.get('X')\n", tmp_path
    ) == {"_finish"}


def test_it_catches_an_aliased_member_import_and_mapping_access(
    tmp_path: Path,
) -> None:
    """The spelling that matched no substring: `environ` never appears in the body."""
    assert _readers_of(
        "from os import environ as _e\n\n\ndef _finish():\n    return _e['X']\n",
        tmp_path,
    ) == {"_finish"}


def test_it_catches_an_aliased_module_import(tmp_path: Path) -> None:
    assert _readers_of(
        "import os as _o\n\n\ndef _finish():\n    return _o.getenv('X')\n", tmp_path
    ) == {"_finish"}


def test_it_catches_an_async_read(tmp_path: Path) -> None:
    """`ast.AsyncFunctionDef` is a separate node the old scan never visited."""
    assert _readers_of(
        "import os\n\n\nasync def _resolve():\n    return os.environ.get('X')\n",
        tmp_path,
    ) == {"_resolve"}


def test_it_catches_a_module_level_read(tmp_path: Path) -> None:
    """A default computed at import time is still the environment answering."""
    assert _readers_of("import os\n\n_D = os.environ.get('X')\n", tmp_path) == {
        "<module>"
    }


def test_it_catches_a_getattr_read(tmp_path: Path) -> None:
    assert _readers_of(
        "import os\n\n\ndef _finish():\n    return getattr(os, 'environ')['X']\n",
        tmp_path,
    ) == {"_finish"}


def test_it_catches_a_read_inside_a_method(tmp_path: Path) -> None:
    assert _readers_of(
        "import os\n\n\nclass R:\n    def resolve(self):\n"
        "        return os.environ.get('X')\n",
        tmp_path,
    ) == {"R.resolve"}


def test_a_delegated_helper_is_caught_because_its_module_is_swept(
    tmp_path: Path,
) -> None:
    """Delegation only hides a read if the helper's module goes unswept.

    The detector is per-file by construction, so the defence against
    delegation is `SWEPT_FILES` covering the surface — proven by
    `test_the_swept_surface_is_the_real_one`, and shown here to be the thing
    that matters: the caller looks clean, the helper does not.
    """
    caller = "from helper import read\n\n\ndef _finish():\n    return read('X')\n"
    helper = "import os\n\n\ndef read(key):\n    return os.environ.get(key)\n"
    assert _readers_of(caller, tmp_path, "caller.py") == set()
    assert _readers_of(helper, tmp_path, "helper.py") == {"read"}


def test_prose_about_the_environment_is_not_a_read(tmp_path: Path) -> None:
    """The false-positive direction, which the substring scan also got wrong.

    Three `settings_crypto` functions were flagged for saying "the environment"
    in a docstring. A detector that cries wolf gets its allowlist padded until
    it stops meaning anything.
    """
    assert (
        _readers_of(
            'def keyring():\n    """Read from the environment, os.environ, '
            'getenv."""\n    return None\n',
            tmp_path,
        )
        == set()
    )
