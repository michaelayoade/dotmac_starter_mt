"""Resolve, inspect and verify an installable module release.

The publish path's fail-closed half. `release-module.yml` calls every subcommand
here, and each one refuses rather than warns — a release step that printed a
warning and continued would publish the thing it just objected to.

## Why an allowlist and not a package path

A dispatch input is a string a human types under time pressure. `.github/
release-modules.json` is the CLOSED set of publishable modules, reviewed as a
diff, and `resolve` fails on anything absent from it. That is what stops a
release run from building an arbitrary directory — including one that is not a
module at all, or one whose wheel policy nobody has agreed.

The workflow's `choice` input constrains the same set at the UI layer, and
`tests/architecture/test_module_release_allowlist.py` asserts the two agree, so
the convenience list cannot drift away from the enforced one.

## Stateful and stateless modules without an optional gate

Both shapes have a ``ModuleManifest`` and a real kernel floor. Every allowlist
row also has a mandatory ``db_schema`` key: a ``mod_*`` string for a stateful
module or explicit null for a stateless one. ``resolve`` reads the manifest's
literal ``short_code`` and ``migration_prefix`` and requires both or neither,
then compares the result to that key. Dropping a stateful row's schema therefore
still refuses; changing it to null while the manifest remains stateful also
refuses. The clean-wheel smoke repeats the proof through ``ModuleRegistry``, and
a stateless wheel containing a migration lineage is refused.

This is distinct from ``stateless-protocol-adapter``: an adapter has no
``ModuleManifest`` and no kernel floor, and remains in its own stricter lane.

## Logical dependency closure is separate from Python imports

``module_dependencies`` maps actual manifest dependency codes to reviewed exact
distribution pins. It does not widen ``wheel_contents.allowed_requires``:
Fulfillment can require the Timers manifest through an assembly adapter while
remaining forbidden from importing its sibling's Python package. Both smoke
paths use one resolver, reject unreviewed edges, and independently compare each
installed manifest to the source plan before the unchanged ``ModuleRegistry``.
Registry first-party artifacts come from the private index alone; their actual
Python requirements drive resolution, and explicit-path provenance is checked
before registration. Dependency publication is never part of the target run.

## Why the kernel floor is checked here

`kernel_floor` is the EARLIEST kernel that can actually load and register the
module: the highest of everything it needs, not one fixed rule.

Usually that is the release which allocated its schema in
`MIGRATION_OWNER_LEDGER` — an earlier kernel raises `UnallocatedNamespaceError`
and the module cannot register at all. But a module whose manifest consumes a
newer kernel CAPABILITY floors at that release instead: ADR-0023's dual-plane
`platform_tables` landed in `0.1.0a53`, and a kernel without the field raises
`TypeError` at import, before the allocation check is ever reached. Where both
apply, the higher wins.

Either way `verify-registry` can only pass once that kernel is published.
Checking the declared floor against the allowlist catches the case where a
module's pyproject is edited without the allowlist following — and
`tests/architecture/test_kernel_version_sync.py` keeps the allocation and
capability cases in separate, reasoned maps so a module cannot sit in neither.

Resolve and inspect are stdlib-only. Smoke staging lazily loads the separately
hash-locked packaging parser, then installs only an inspected wheelhouse offline.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import Iterable
from contextlib import contextmanager
from email.parser import Parser
from itertools import product
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = REPO_ROOT / ".github" / "release-modules.json"
PACKAGES_ROOT = REPO_ROOT / "packages"
CLASSIFICATION: Final = "optional-module"


class ReleaseRefused(SystemExit):
    """Any refusal. Exits non-zero so a workflow step cannot continue past it."""

    def __init__(self, message: str) -> None:
        super().__init__(f"::error::{message}")


def load_allowlist() -> dict[str, dict]:
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    return data["modules"]


def _manifest_keywords(entry: dict) -> dict[str, ast.expr]:
    """Read the assigned declaration without importing application code."""
    path = entry["package_path"] / "src" / entry["import_name"] / "manifest.py"
    if not path.is_file():
        raise ReleaseRefused(
            f"{entry['distribution']}: no manifest.py at {path.relative_to(REPO_ROOT)}"
        )

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    declarations: list[ast.Call] = []
    for statement in tree.body:
        value: ast.expr | None = None
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            value = statement.value
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            value = statement.value
            targets = [statement.target]
        if not isinstance(value, ast.Call):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == entry["manifest_attr"]
            for target in targets
        ):
            declarations.append(value)
    if len(declarations) != 1:
        raise ReleaseRefused(
            f"{entry['distribution']}: manifest.py must assign "
            f"{entry['manifest_attr']!r} exactly once"
        )

    declaration = declarations[0]
    if any(keyword.arg is None for keyword in declaration.keywords):
        raise ReleaseRefused(
            f"{entry['distribution']}: manifest unpacking is not literal"
        )
    return {
        keyword.arg: keyword.value
        for keyword in declaration.keywords
        if keyword.arg is not None
    }


def _manifest_db_schema(entry: dict) -> str | None:
    """Both persistence identity fields must be literal, or both absent."""
    keywords = _manifest_keywords(entry)

    def optional_literal(name: str) -> str | None:
        node = keywords.get(name)
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        raise ReleaseRefused(
            f"{entry['distribution']}: manifest {name} must be a literal string "
            "or absent so the pre-install release gate can prove its shape"
        )

    short_code = optional_literal("short_code")
    migration_prefix = optional_literal("migration_prefix")
    if (short_code is None) != (migration_prefix is None):
        raise ReleaseRefused(
            f"{entry['distribution']}: manifest must declare short_code and "
            "migration_prefix together, or neither for a stateless module"
        )
    return None if short_code is None else f"mod_{short_code}"


def resolve(distribution: str) -> dict:
    """The gate. Every other subcommand takes its facts from this result."""
    modules = load_allowlist()
    entry = modules.get(distribution)
    if entry is None:
        raise ReleaseRefused(
            f"{distribution!r} is not an allowlisted module. Publishable modules "
            f"are: {', '.join(sorted(modules))}. Adding one is a reviewed change "
            f"to {ALLOWLIST.relative_to(REPO_ROOT)}, not a dispatch input."
        )
    required = {
        "package_dir",
        "import_name",
        "manifest_attr",
        "kernel_floor",
        "db_schema",
        "tag_prefix",
        "wheel_contents",
    }
    missing = sorted(required - set(entry))
    if missing:
        raise ReleaseRefused(
            f"{distribution}: release entry is missing mandatory field(s): "
            + ", ".join(missing)
        )

    package_dir = REPO_ROOT / entry["package_dir"]
    if not (package_dir / "pyproject.toml").is_file():
        raise ReleaseRefused(
            f"{distribution}: allowlisted package_dir {entry['package_dir']!r} "
            "has no pyproject.toml"
        )
    resolved = {**entry, "distribution": distribution, "package_path": package_dir}

    dossier_path = package_dir / "EXTRACTION.toml"
    if not dossier_path.is_file():
        raise ReleaseRefused(
            f"{distribution}: no EXTRACTION.toml — the module lane cannot prove "
            "the governed classification"
        )
    dossier = tomllib.loads(dossier_path.read_text(encoding="utf-8"))
    declared = dossier.get("classification")
    if declared != CLASSIFICATION:
        raise ReleaseRefused(
            f"{distribution}: EXTRACTION.toml declares classification "
            f"{declared!r}, but this lane publishes only {CLASSIFICATION!r}"
        )

    recorded_schema = entry["db_schema"]
    if recorded_schema is not None and not (
        isinstance(recorded_schema, str) and recorded_schema.startswith("mod_")
    ):
        raise ReleaseRefused(
            f"{distribution}: db_schema must be a mod_* string for a stateful "
            "module or null for a stateless module"
        )
    manifest_schema = _manifest_db_schema(resolved)
    if manifest_schema != recorded_schema:
        raise ReleaseRefused(
            f"{distribution}: manifest schema {manifest_schema!r} disagrees "
            f"with release allowlist {recorded_schema!r}. null is mandatory for "
            "a stateless module; a stateful module may not drop its schema."
        )

    return resolved


def _declared(entry: dict) -> dict:
    manifest = tomllib.loads(
        (entry["package_path"] / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]
    return manifest


def _native_package(entry: dict) -> str | None:
    """Enrollment belongs to the owning package, never a second module list."""
    data = tomllib.loads(
        (entry["package_path"] / "pyproject.toml").read_text(encoding="utf-8")
    )
    native = data.get("tool", {}).get("dotmac", {}).get("native")
    if native is None:
        return None
    if (
        not isinstance(native, dict)
        or native.get("python-package") != entry["import_name"]
        or not isinstance(native.get("decimal-core", False), bool)
    ):
        raise ReleaseRefused(
            f"{entry['distribution']}: invalid native enrollment; python-package "
            "must equal the reviewed import_name and decimal-core must be boolean"
        )
    return native["python-package"]


def _exact_pin(pin: str) -> tuple[str, str]:
    # No extras, markers, URLs or resolver ranges in reviewed logical edges.
    match = re.fullmatch(r"([a-z0-9]+(?:-[a-z0-9]+)*)==([0-9][A-Za-z0-9.!+_-]*)", pin)
    if match is None:
        raise ReleaseRefused(f"{pin!r} is not an exact pin (name==version)")
    return match[1], match[2]


def _module_identity(entry: dict) -> dict:
    keywords = _manifest_keywords(entry)
    try:
        code = ast.literal_eval(keywords["code"])
        dependencies = ast.literal_eval(
            keywords.get("dependencies", ast.Tuple(elts=[]))
        )
    except (KeyError, ValueError, TypeError) as error:
        raise ReleaseRefused(
            f"{entry['distribution']}: code and dependencies must be literal"
        ) from error
    if not isinstance(code, str) or not code:
        raise ReleaseRefused(f"{entry['distribution']}: invalid module code")
    if not isinstance(dependencies, tuple) or any(
        not isinstance(value, str) or not value for value in dependencies
    ):
        raise ReleaseRefused(
            f"{entry['distribution']}: dependencies must be a string tuple"
        )
    if len(set(dependencies)) != len(dependencies):
        raise ReleaseRefused(f"{entry['distribution']}: duplicate logical dependency")
    return {**entry, "code": code, "dependencies": dependencies}


def _module_closure(pins: Iterable[str]) -> list[dict]:
    """Resolve only reviewed logical edges; Python requirements stay separate.

    The source AST supplies an expected plan, never proof of installed bytes.
    Both smoke paths independently compare the installed distribution and
    actual manifest against each descriptor before constructing ModuleRegistry.
    """
    modules = load_allowlist()
    codes: dict[str, str] = {}
    for distribution, row in sorted(modules.items()):
        identity = _module_identity(
            {
                **row,
                "distribution": distribution,
                "package_path": REPO_ROOT / row["package_dir"],
            }
        )
        code = identity["code"]
        if code in codes:
            raise ReleaseRefused(
                f"duplicate module code {code!r}: {codes[code]} and {distribution}"
            )
        codes[code] = distribution

    versions: dict[str, str] = {}
    complete: dict[str, dict] = {}
    visiting: set[str] = set()

    def visit(pin: str, expected_code: str | None = None) -> None:
        distribution, version = _exact_pin(pin)
        if distribution in versions and versions[distribution] != version:
            raise ReleaseRefused(
                f"conflicting pins for {distribution}: "
                f"{versions[distribution]} and {version}"
            )
        versions[distribution] = version
        entry = _module_identity(resolve(distribution))
        if _declared(entry)["name"] != distribution:
            raise ReleaseRefused(
                f"{distribution}: source pyproject distribution disagrees with policy"
            )
        if expected_code is not None and entry["code"] != expected_code:
            raise ReleaseRefused(
                f"logical dependency {expected_code!r} pins {distribution}, "
                f"which declares code {entry['code']!r}"
            )
        if distribution in visiting:
            raise ReleaseRefused(f"logical module dependency cycle at {distribution}")
        if distribution in complete:
            return
        policy = entry.get("module_dependencies", {})
        if not isinstance(policy, dict) or set(policy) != set(entry["dependencies"]):
            raise ReleaseRefused(
                f"{distribution}: logical dependency policy disagrees "
                "with manifest dependencies"
            )
        visiting.add(distribution)
        for code, dependency in sorted(policy.items()):
            if not isinstance(dependency, str):
                raise ReleaseRefused(
                    f"{distribution}: logical dependency needs an exact pin"
                )
            visit(dependency, code)
        visiting.remove(distribution)
        complete[distribution] = {
            **entry,
            "version": version,
            "native_package": _native_package(entry),
        }

    for pin in sorted(pins):
        visit(pin)
    # Registry construction validates the graph; it does not require input
    # topological order. Sorting makes repeated/shared roots byte-stable.
    return [complete[name] for name in sorted(complete)]


def _installed_targets(closure: list[dict]) -> list[dict]:
    return [
        {
            key: entry[key]
            for key in (
                "distribution",
                "version",
                "code",
                "dependencies",
                "import_name",
                "manifest_attr",
                "db_schema",
            )
        }
        for entry in closure
    ]


def _smoke_dependencies(closure: list[dict], target: str) -> list[Path]:
    """Logical closure plus existing reviewed Python inputs, built once each."""
    found = {entry["package_path"] for entry in closure}
    for entry in closure:
        found.update(local_first_party_dependencies(entry))
    found.difference_update(
        entry["package_path"] for entry in closure if entry["distribution"] == target
    )
    return sorted(found)


def _inspect_native(entry: dict, wheel: Path) -> None:
    module = _native_package(entry)
    if module is None:
        return
    # This sibling is part of the release source closure. Its stdlib-only
    # verifier owns Decimal's license, static-link and private-symbol policy.
    from native_qualify import DECIMAL_MODULES, verify_wheel

    data = tomllib.loads(
        (entry["package_path"] / "pyproject.toml").read_text(encoding="utf-8")
    )
    if (
        data["tool"]["dotmac"]["native"].get("decimal-core", False)
        and module not in DECIMAL_MODULES
    ):
        raise ReleaseRefused(f"{module}: Decimal qualification is not enrolled")
    try:
        verify_wheel(wheel, module)
        with zipfile.ZipFile(wheel) as archive:
            extensions = [
                name for name in archive.namelist() if name.endswith((".so", ".pyd"))
            ]
            if len(extensions) != 1:
                raise ValueError("native wheel needs exactly one Python extension")
            (metadata,) = (
                name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")
            )
            headers = Parser().parsestr(archive.read(metadata).decode("utf-8"))
        if headers.get_all("Root-Is-Purelib") != ["false"]:
            raise ValueError("native wheel must declare Root-Is-Purelib: false once")

        def tags(raw: str) -> set[tuple[str, str, str]]:
            interpreter, abi, platform = raw.split("-")
            expanded = set(
                product(interpreter.split("."), abi.split("."), platform.split("."))
            )
            if any(
                not re.fullmatch(r"cp[0-9]+[a-z]*", py)
                or not re.fullmatch(r"cp[0-9]+[a-z]*", binary)
                or not target
                or target == "any"
                for py, binary, target in expanded
            ):
                raise ValueError("native wheel needs CPython ABI and platform tags")
            return expanded

        declared = headers.get_all("Tag", [])
        if not declared:
            raise ValueError("native wheel has no Tag header")
        wheel_tags = tags("-".join(wheel.stem.rsplit("-", 3)[-3:]))
        metadata_tags = set().union(*(tags(tag) for tag in declared))
        if wheel_tags != metadata_tags:
            raise ValueError("native wheel filename and WHEEL tags disagree")
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        raise ReleaseRefused(
            f"{wheel.name}: native qualification failed: {error}"
        ) from error


def local_first_party_dependencies(entry: dict) -> list[Path]:
    """Return reviewed first-party dependencies that need local smoke wheels.

    ``wheel_contents.allowed_requires`` is the authority: merely existing under
    ``packages/`` never opts a distribution into a release. The intersection is
    useful because the pre-publish smoke has no registry credential and must be
    able to install every permitted first-party dependency from this exact
    checkout. Third-party dependencies keep resolving from the public index.

    The kernel is excluded because it has a stronger, dedicated artifact path
    tied to ``kernel_floor``. The target itself is already supplied by ``--dist``.
    """
    permitted = {
        name.casefold() for name in entry["wheel_contents"]["allowed_requires"]
    }
    excluded = {entry["distribution"].casefold(), "dotmac-kernel"}
    found: dict[str, Path] = {}

    for pyproject in sorted(PACKAGES_ROOT.glob("*/pyproject.toml")):
        manifest = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        poetry = manifest.get("tool", {}).get("poetry", {})
        name = poetry.get("name")
        if not isinstance(name, str):
            continue
        normalized = name.casefold()
        if normalized not in permitted or normalized in excluded:
            continue
        if normalized in found:
            raise ReleaseRefused(
                f"{entry['distribution']}: first-party dependency {name!r} is "
                f"declared by both {found[normalized]} and {pyproject.parent}"
            )
        found[normalized] = pyproject.parent

    return [found[name] for name in sorted(found)]


def cmd_resolve(args: argparse.Namespace) -> None:
    entry = resolve(args.distribution)
    manifest = _declared(entry)

    if manifest["name"] != args.distribution:
        raise ReleaseRefused(
            f"pyproject declares {manifest['name']!r}, dispatched {args.distribution!r}"
        )
    if args.version and manifest["version"] != args.version:
        raise ReleaseRefused(
            f"{args.distribution}: dispatched version {args.version!r} != package "
            f"version {manifest['version']!r}. The version is not inferred; fix "
            "one of them."
        )

    declared_version = manifest["version"]
    if "+" in declared_version:
        published_form = declared_version.split("+", 1)[0]
        raise ReleaseRefused(
            f"{args.distribution}: declared version {declared_version!r} carries a "
            "local version segment ('+'), which no package index can publish. "
            f"Allocate a real successor version; the publishable form would be "
            f"{published_form!r}, but editing the declared version to drop the "
            "local segment is a reviewed pyproject change, not something this "
            "resolver may infer."
        )

    floor = manifest["dependencies"].get("dotmac-kernel")
    expected = f">={entry['kernel_floor']}"
    if floor != expected:
        floor_reason = (
            f"the release that allocated {entry['db_schema']!r}"
            if entry["db_schema"] is not None
            else "the earliest published kernel capability the stateless manifest uses"
        )
        raise ReleaseRefused(
            f"{args.distribution}: kernel floor is {floor!r} but the allowlist "
            f"records {expected!r} as {floor_reason}. An earlier kernel cannot "
            "register this module at all."
        )

    # Consumed by the workflow via $GITHUB_OUTPUT.
    for key in (
        "package_dir",
        "import_name",
        "manifest_attr",
        "kernel_floor",
        "tag_prefix",
    ):
        print(f"{key}={entry[key]}")
    if entry["db_schema"] is None:
        print("release_shape=stateless")
    else:
        print("release_shape=stateful")
        print(f"db_schema={entry['db_schema']}")
    print(f"version={manifest['version']}")
    print(f"tag={entry['tag_prefix']}{manifest['version']}")
    print(f"native_package={_native_package(entry) or ''}")


def migration_extent_problems(entry: dict) -> list[str]:
    """DERIVE the migration-lineage extent from the source tree; do not trust
    the hand-maintained `required` list alone to have kept up with it
    (AGENTS.md's "DERIVE the extent, never declare it"). Enumerating one
    migration proves that FILE ships; it proves nothing about a LATER
    file added to `migrations/versions/` and never added to
    `wheel_contents.required` — that omission would ship a wheel missing
    the migration silently, with every enumerated-path check green, which
    is the exact defect this closes.

    A standalone, independently testable function so ONE sensitivity
    proof (a planted unlisted migration is named; the conforming
    repository state is silent) backs BOTH consumers that would otherwise
    duplicate this calculation: `cmd_inspect` below (the release-time
    gate) and `tests/architecture/test_module_release_allowlist.py`'s
    PR-time architecture test.
    """
    if entry["db_schema"] is None:
        return []
    versions_dir = (
        entry["package_path"] / "src" / entry["import_name"] / "migrations" / "versions"
    )
    on_disk = {
        f"{entry['import_name']}/migrations/versions/{path.name}"
        for path in versions_dir.glob("*.py")
        if path.name != "__init__.py"
    }
    undeclared = sorted(on_disk - set(entry["wheel_contents"]["required"]))
    if not undeclared:
        return []
    return [
        "migration file(s) exist on disk but are not enumerated in "
        "wheel_contents.required — a future migration omitted from "
        f"the wheel would pass this check undetected: {undeclared}"
    ]


def cmd_inspect(args: argparse.Namespace) -> None:
    """Wheel-content policy. What must ship, what must never, what may be required."""
    entry = resolve(args.distribution)
    policy = entry["wheel_contents"]
    wheels = sorted(Path(args.dist).glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseRefused(
            f"expected exactly one wheel in {args.dist}, found {len(wheels)}"
        )
    wheel = wheels[0]
    _inspect_native(entry, wheel)

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        if metadata is None:
            raise ReleaseRefused(f"{wheel.name}: no METADATA in the wheel")
        meta_text = archive.read(metadata).decode("utf-8")

    problems: list[str] = []

    # Required package data. `py.typed` absent means a consumer's type-checker
    # silently treats every contract as Any; a missing migration means the
    # lineage does not ship and the consuming assembly finds nothing to compose.
    for required in policy["required"]:
        if required not in names:
            problems.append(f"missing from the wheel: {required}")

    # The loop above only proves every ENUMERATED path shipped — it says
    # nothing about a migration file that exists on disk and was never
    # added to `required` at all. `migration_extent_problems` derives
    # that extent from the source tree instead of trusting the
    # hand-maintained list alone; see its own docstring.
    problems.extend(migration_extent_problems(entry))

    # Nothing from the assembly, the test suite, or the repo's tooling.
    for name in names:
        for prefix in policy["forbidden_prefixes"]:
            if name.startswith(prefix):
                problems.append(f"forbidden content: {name}")

    if entry["db_schema"] is None:
        for name in names:
            if "/migrations/" in name:
                problems.append(
                    f"migration lineage in a stateless module wheel: {name} — "
                    "the package gained persistence without changing its "
                    "release profile"
                )

    # Dependency closure is reviewed per module. Most modules depend on the
    # kernel and third-party persistence libraries only; ADR-0006 also permits
    # the module -> dotmac-ui direction used by brand profiles. A sibling module
    # or the assembly appearing here would still make the module unreleasable
    # independently (ADR-0006 § 2).
    requires = [
        line.split(":", 1)[1].strip()
        for line in meta_text.splitlines()
        if line.startswith("Requires-Dist:")
    ]
    for requirement in requires:
        name = (
            requirement.split(";")[0]
            .split("(")[0]
            .split("[")[0]
            .split("<")[0]
            .split(">")[0]
            .split("=")[0]
            .split("!")[0]
            .strip()
            .lower()
        )
        if name not in {a.lower() for a in policy["allowed_requires"]}:
            problems.append(f"dependency outside the allowed closure: {requirement!r}")

    # Secret-shaped material. A wheel is world-readable to anyone with index
    # access; a key that ships once is a key that is rotated, not recalled.
    problems.extend(secret_shaped(names))

    if problems:
        raise ReleaseRefused(
            f"{wheel.name} fails the wheel-content policy:\n  - "
            + "\n  - ".join(problems)
        )
    print(f"{wheel.name}: content policy OK ({len(names)} entries)")


# Name shapes that must never ship in a wheel. A NAME check, deliberately: it
# cannot tell a module ABOUT secret handling from a file CONTAINING secret
# material, and it assumes the worse of the two. That is the right bias — the
# cost of a false positive is a rename, and the cost of a false negative is a
# credential in an immutable published artefact.
#
# `dotmac-integration` hit exactly that false positive: its `secrets.py` was the
# refusal enforcing "references, never values". It was renamed to
# `secret_refs.py` rather than exempted, because widening a security check to
# accommodate a filename trades a real protection for a cosmetic one.
SECRET_SHAPED_MARKERS: Final = (
    ".env",
    "id_rsa",
    ".pem",
    ".key",
    "secrets.",
    "credentials.",
)


def secret_shaped(names: Iterable[str]) -> list[str]:
    """Names matching a shape that must never ship. Shared with the PR-time gate
    in `tests/architecture/test_publishable_packages_ship_no_secret_shape.py` so
    the release check and the local check cannot drift apart."""
    return [
        f"secret-shaped file in the wheel: {name}"
        for name in names
        if any(marker in name.lower() for marker in SECRET_SHAPED_MARKERS)
    ]


def _venv(path: Path) -> tuple[Path, Path]:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    bin_dir = path / ("Scripts" if sys.platform == "win32" else "bin")
    return bin_dir / "python", bin_dir / "pip"


_REGISTER = """
import importlib, pathlib, site
from importlib import metadata
import dotmac_kernel
from dotmac_kernel.modules import ModuleRegistry

manifests, names = [], {names!r}
installed_roots = [pathlib.Path(root).resolve() for root in site.getsitepackages()]
for target in names:
    import_name, attr, schema = (
        target["import_name"], target["manifest_attr"], target["db_schema"]
    )
    dist = metadata.distribution(target["distribution"])
    assert dist.version == target["version"], (target, dist.version)
    mod = importlib.import_module(import_name)
    manifest = getattr(mod, attr)
    here = pathlib.Path(mod.__file__).resolve()
    # The installed distribution, never the checkout it was built from.
    assert any(here.is_relative_to(root) for root in installed_roots), here
    owned = {{pathlib.Path(dist.locate_file(f)).resolve() for f in dist.files or []}}
    assert here in owned, (import_name, here, "not owned by expected distribution")
    # The export may come from a submodule; verify that declaration's origin
    # independently so an installed facade cannot import a checkout manifest.
    declaration = importlib.import_module(import_name + ".manifest")
    assert getattr(declaration, attr) is manifest, import_name
    assert pathlib.Path(declaration.__file__).resolve() in owned, import_name
    assert manifest.code == target["code"], (target, manifest.code)
    assert manifest.version == target["version"], (target, manifest.version)
    assert tuple(manifest.dependencies) == target["dependencies"], (
        target, manifest.dependencies
    )
    assert manifest.db_schema == schema, (import_name, manifest.db_schema, schema)
    assert manifest.is_stateful is (schema is not None), (
        import_name, manifest.is_stateful, schema
    )
    if schema is None:
        assert manifest.migration_owner() is None, import_name
    manifests.append(manifest)

# Construction IS validation: module identities, dependencies and contracts,
# plus the namespace checks for every stateful member. A stateless manifest is
# registered as a module while contributing no schema or migration owner.
ModuleRegistry(manifests)
print("kernel", dotmac_kernel.__version__, "registered:",
      ", ".join(m.db_schema or f"{{m.code}} (stateless)" for m in manifests))
"""


_NATIVE_REGISTER = """
import importlib, pathlib, site
from importlib.machinery import ExtensionFileLoader, EXTENSION_SUFFIXES

checkout = pathlib.Path({checkout!r}).resolve()
installed_roots = [pathlib.Path(root).resolve() for root in site.getsitepackages()]
for name in {names!r}:
    native = importlib.import_module(name + "._native")
    spec = native.__spec__
    assert spec is not None and isinstance(spec.loader, ExtensionFileLoader), name
    assert spec.name == name + "._native", (name, spec.name)
    origin = pathlib.Path(spec.origin).resolve()
    assert origin == pathlib.Path(native.__file__).resolve(), (name, origin)
    assert any(str(origin).endswith(suffix) for suffix in EXTENSION_SUFFIXES), origin
    assert any(origin.is_relative_to(root) for root in installed_roots), origin
    assert not origin.is_relative_to(checkout), origin
    assert origin.parent.name == name and origin.name.startswith("_native."), origin
    print("native extension loaded:", name, origin)
"""


def _verify_installed(
    python: Path,
    targets: list[dict],
    native_targets: list[str] | None = None,
) -> None:
    # `-I`: isolated mode — no user site, no PYTHON* environment, and the
    # current directory never on sys.path, so nothing can resolve from a
    # checkout by accident.
    script = _REGISTER.format(names=targets) + _NATIVE_REGISTER.format(
        names=native_targets or [], checkout=str(REPO_ROOT)
    )
    subprocess.run([str(python), "-I", "-c", script], check=True)


#: Proves each first-party distribution in the smoke venv was installed from
#: EXACTLY the wheel file this run hashed (pip records the file URL in
#: `direct_url.json` for an explicit-path install) and that none of its files
#: live under the checkout.
_PROVENANCE = """
import json, pathlib
from importlib import metadata

expected = {expected!r}
checkout = pathlib.Path({checkout!r}).resolve()
def normal(name):
    return name.lower().replace("-", "_").replace(".", "_")
installed = {{
    normal(d.metadata["Name"]) for d in metadata.distributions()
    if normal(d.metadata["Name"]).startswith("dotmac")
}}
unexpected = installed - {{normal(n) for n in expected}}
assert not unexpected, f"first-party distributions not from this run: {{unexpected}}"
for name, wheel in sorted(expected.items()):
    dist = metadata.distribution(name)
    raw = dist.read_text("direct_url.json")
    assert raw, f"{{name}} was not installed from an explicit wheel file"
    url = json.loads(raw).get("url", "")
    want = pathlib.Path(wheel).resolve().as_uri()
    assert url == want, f"{{name}} installed from {{url}}, not {{want}}"
    for record in dist.files or []:
        located = pathlib.Path(dist.locate_file(record)).resolve()
        assert not located.is_relative_to(checkout), f"{{name}}: {{located}}"
print("smoke provenance OK:", ", ".join(sorted(expected)))
"""


def _wheels(directory: str) -> list[Path]:
    return sorted(Path(directory).resolve().glob("*.whl"))


def _wheel_distribution(wheel: Path) -> str:
    """The distribution name a wheel filename declares (PEP 427 first field)."""
    return wheel.name.split("-", 1)[0]


def _normal_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _wheel_metadata(wheel: Path):
    try:
        with zipfile.ZipFile(wheel) as archive:
            entries = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(entries) != 1:
                raise ValueError("expected exactly one METADATA")
            return Parser().parsestr(archive.read(entries[0]).decode("utf-8"))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise ReleaseRefused(f"{wheel.name}: invalid wheel metadata") from error


def _wheel_identity(wheel: Path) -> tuple[str, str]:
    headers = _wheel_metadata(wheel)
    names, versions = headers.get_all("Name", []), headers.get_all("Version", [])
    if len(names) != 1 or len(versions) != 1:
        raise ReleaseRefused(f"{wheel.name}: expected one Name and Version")
    name = _normal_distribution(names[0])
    if name != _normal_distribution(_wheel_distribution(wheel)):
        raise ReleaseRefused(
            f"{wheel.name}: filename and METADATA distribution disagree"
        )
    if wheel.name.split("-")[1] != versions[0]:
        raise ReleaseRefused(f"{wheel.name}: filename and METADATA version disagree")
    return name, versions[0]


def _pip_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PIP_")
    }
    environment["PIP_CONFIG_FILE"] = os.devnull
    return environment


def _packaging_api():
    # Resolve/inspect remain stdlib-only. Every smoke caller bootstraps this
    # exact parser from the reviewed hash lock before entering the walker.
    from importlib import metadata

    from packaging.markers import default_environment
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version

    if metadata.version("packaging") != "26.2":
        raise ReleaseRefused("release verifier requires hash-locked packaging 26.2")
    return (
        Requirement,
        InvalidRequirement,
        SpecifierSet,
        canonicalize_name,
        Version,
        InvalidVersion,
        default_environment(),
    )


def _download_one(
    pip: Path, name: str, specifier: str, index: str, output: Path
) -> Path:
    """Only canonical name + parsed version constraints reach pip; no edges."""
    output.mkdir(parents=True)
    result = subprocess.run(
        [
            str(pip),
            "download",
            "--quiet",
            "--no-deps",
            "--only-binary=:all:",
            "--index-url",
            index,
            "--dest",
            str(output),
            name + specifier,
        ],
        env=_pip_environment(),
        cwd=output.parent,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # Both authenticated indexes and metadata URLs stay out of exceptions.
        raise ReleaseRefused(f"binary-only wheel resolution failed for {name}")
    wheels = _wheels(str(output))
    if len(wheels) != 1 or any(path.suffix != ".whl" for path in output.iterdir()):
        raise ReleaseRefused(f"expected exactly one binary wheel for {name}")
    if _wheel_identity(wheels[0])[0] != name:
        raise ReleaseRefused(f"wheel resolution substituted distribution {name}")
    return wheels[0]


def _stage_wheels(
    pip: Path,
    directory: Path,
    *,
    pins: list[str],
    local: list[Path],
    permitted: set[str],
    owner_policy: dict[str, set[str]],
    private_index: str | None,
) -> list[Path]:
    """Expand inspected binary wheels without letting pip traverse metadata.

    This is a bounded fixed-point verifier, not a backtracking resolver. Standard
    packaging owns PEP 508 parsing, marker environments, extras and specifiers.
    Direct references are Unsupported even when dormant. First-party extras and
    markers are also explicitly Unsupported; the qualified module path must use
    unconditional first-party edges. Third-party extras/markers are evaluated
    with the actual target interpreter environment and requesting extra context.
    """
    (
        Requirement,
        InvalidRequirement,
        SpecifierSet,
        canonicalize_name,
        Version,
        InvalidVersion,
        environment,
    ) = _packaging_api()

    def parse(raw: str):
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            raise ReleaseRefused("malformed wheel dependency requirement") from None
        name = canonicalize_name(requirement.name)
        if requirement.url is not None:
            raise ReleaseRefused("Unsupported: direct-reference wheel requirement")
        if name.startswith("dotmac"):
            if requirement.extras or requirement.marker is not None:
                raise ReleaseRefused(
                    "Unsupported: first-party extras or environment marker"
                )
            if name not in permitted:
                raise ReleaseRefused(f"unapproved first-party wheel dependency: {name}")
        return name, requirement

    fixed: dict[str, Path] = {}
    root_requirements = list(pins)
    for wheel in local:
        name, version = _wheel_identity(wheel)
        if name in fixed or not name.startswith("dotmac") or name not in permitted:
            raise ReleaseRefused(
                f"duplicate or unapproved local first-party wheel: {name}"
            )
        fixed[name] = wheel
        root_requirements.append(f"{name}=={version}")

    def add(
        raw: str, constraints: dict[str, set[str]], extras: dict[str, set[str]]
    ) -> str:
        name, requirement = parse(raw)
        constraints.setdefault(name, set()).add(str(requirement.specifier))
        extras.setdefault(name, set()).update(
            canonicalize_name(extra) for extra in requirement.extras
        )
        return name

    def roots():
        constraints: dict[str, set[str]] = {}
        extras: dict[str, set[str]] = {}
        for raw in root_requirements:
            add(raw, constraints, extras)
        return constraints, extras

    def state(constraints, extras):
        return tuple(
            (
                name,
                str(SpecifierSet(",".join(sorted(constraints[name])))),
                tuple(sorted(extras[name])),
            )
            for name in sorted(constraints)
        )

    constraints, extras = roots()
    visited = set()
    downloaded: dict[tuple[str, str], Path] = {}
    # Resource bounds refuse an indeterminate graph; they never relax an edge.
    for number in range(128):
        current = state(constraints, extras)
        if current in visited:
            raise ReleaseRefused("wheel dependency resolution did not converge")
        visited.add(current)
        if len(current) > 1024:
            raise ReleaseRefused("wheel dependency graph exceeds verifier bound")
        candidates: dict[str, Path] = {}
        for name, specifier, _requested_extras in current:
            wheel = fixed.get(name)
            if wheel is None:
                if name.startswith("dotmac") and private_index is None:
                    raise ReleaseRefused(
                        f"missing approved first-party smoke wheel: {name}"
                    )
                index = (
                    private_index
                    if name.startswith("dotmac")
                    else os.environ.get("PUBLIC_INDEX_URL", "https://pypi.org/simple")
                )
                key = (name, specifier)
                wheel = downloaded.get(key)
                if wheel is None:
                    wheel = _download_one(
                        pip, name, specifier, index, directory / str(number) / name
                    )
                    downloaded[key] = wheel
            actual_name, version = _wheel_identity(wheel)
            try:
                matches = SpecifierSet(specifier).contains(
                    Version(version), prereleases=True
                )
            except InvalidVersion as error:
                raise ReleaseRefused(
                    f"invalid resolved wheel version for {name}"
                ) from error
            if actual_name != name or not matches:
                raise ReleaseRefused(
                    f"resolved wheel does not satisfy constraints for {name}"
                )
            candidates[name] = wheel

        # Rewalk from roots each round: dependencies of a superseded artifact
        # must not remain as stale, executable inputs in the final wheelhouse.
        next_constraints, next_extras = roots()
        queue = sorted(next_constraints)
        processed: dict[str, frozenset[str]] = {}
        while queue:
            name = queue.pop(0)
            contexts = frozenset({"", *next_extras[name]})
            if processed.get(name) == contexts or name not in candidates:
                continue
            processed[name] = contexts
            wheel = candidates[name]
            headers = _wheel_metadata(wheel)
            provided = {
                canonicalize_name(extra)
                for extra in headers.get_all("Provides-Extra", [])
            }
            if not next_extras[name].issubset(provided):
                raise ReleaseRefused(f"wheel lacks a requested extra for {name}")
            # Parse every edge before any request in the NEXT round. Unsupported
            # dormant URL/first-party conditional edges are refused as declared.
            for raw in headers.get_all("Requires-Dist", []):
                dependency, requirement = parse(raw)
                if (
                    dependency.startswith("dotmac")
                    and name in owner_policy
                    and dependency not in owner_policy[name]
                ):
                    raise ReleaseRefused(
                        f"{name}: unapproved first-party Python requirement"
                    )
                try:
                    active = requirement.marker is None or any(
                        requirement.marker.evaluate(
                            environment={**environment, "extra": extra},
                            context="metadata",
                        )
                        for extra in contexts
                    )
                except (KeyError, ValueError) as error:
                    raise ReleaseRefused(
                        f"Unsupported: wheel marker environment for {name}"
                    ) from error
                if not active:
                    continue
                previous = frozenset(next_extras.get(dependency, set()))
                is_new = dependency not in next_constraints
                add(raw, next_constraints, next_extras)
                if is_new or previous != frozenset(next_extras[dependency]):
                    queue.append(dependency)
        if state(next_constraints, next_extras) == current:
            return [candidates[name] for name in sorted(candidates)]
        constraints, extras = next_constraints, next_extras
    raise ReleaseRefused("wheel dependency resolution exceeded verifier round bound")


def _module_wheel_policy(closure: list[dict]) -> tuple[set[str], dict[str, set[str]]]:
    owner_policy = {
        member["distribution"]: {
            _normal_distribution(name)
            for name in member["wheel_contents"]["allowed_requires"]
        }
        for member in closure
    }
    permitted = {"dotmac-kernel", *(member["distribution"] for member in closure)}
    for names in owner_policy.values():
        permitted.update(name for name in names if name.startswith("dotmac"))
    return permitted, owner_policy


def _registry_wheels(
    args: argparse.Namespace, closure: list[dict], pip: Path, directory: Path
) -> list[Path]:
    pins = [f"{member['distribution']}=={member['version']}" for member in closure]
    if args.kernel:
        _exact_pin(f"dotmac-kernel=={args.kernel}")
        pins.append(f"dotmac-kernel=={args.kernel}")
    permitted, owner_policy = _module_wheel_policy(closure)
    wheels = _stage_wheels(
        pip,
        directory,
        pins=pins,
        local=[],
        permitted=permitted,
        owner_policy=owner_policy,
        private_index=args.index,
    )
    if "dotmac-kernel" not in {_wheel_identity(wheel)[0] for wheel in wheels}:
        raise ReleaseRefused("private registry omitted the required kernel wheel")
    return wheels


def _offline_install(pip: Path, wheels: list[Path], directory: str | Path) -> None:
    result = subprocess.run(
        [str(pip), "install", "--quiet", "--no-index", "--no-deps", *map(str, wheels)],
        env=_pip_environment(),
        cwd=directory,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ReleaseRefused("offline wheel installation failed")


def _verify_smoke_inputs(
    entry: dict, closure: list[dict], wheels: list[Path], *, kernel: bool
) -> None:
    """Refuse substituted, extra or missing artifacts before pip runs."""
    paths = [
        entry["package_path"],
        *_smoke_dependencies(closure, entry["distribution"]),
    ]
    if kernel:
        paths.append(PACKAGES_ROOT / "dotmac-kernel")
    expected: dict[str, str] = {}
    for path in paths:
        declared = tomllib.loads((path / "pyproject.toml").read_text())["tool"][
            "poetry"
        ]
        name, version = _normal_distribution(declared["name"]), declared["version"]
        if name in expected:
            raise ReleaseRefused(f"duplicate first-party source distribution: {name}")
        expected[name] = version
    for member in closure:
        name = _normal_distribution(member["distribution"])
        if expected[name] != member["version"]:
            raise ReleaseRefused(
                f"{name}: local source version differs from exact logical pin "
                f"{member['version']}"
            )
    actual: dict[str, Path] = {}
    for wheel in wheels:
        name, version = _wheel_identity(wheel)
        if name in actual:
            raise ReleaseRefused(
                f"duplicate first-party distributions in smoke inputs: {name}"
            )
        if name not in expected or version != expected[name]:
            raise ReleaseRefused(
                f"unapproved smoke wheel or wrong version: {wheel.name}"
            )
        actual[name] = wheel
    if set(actual) != set(expected):
        raise ReleaseRefused(
            f"missing approved smoke wheels: {sorted(set(expected) - set(actual))}"
        )
    for member in closure:
        _inspect_native(member, actual[_normal_distribution(member["distribution"])])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cmd_build_local_dependencies(args: argparse.Namespace) -> None:
    """Build every policy-permitted first-party dependency for wheel smoke.

    This is intentionally separate from the target and kernel builds: those two
    have release-specific inspection and floor rules. These artifacts are only
    resolver inputs for the pre-publish clean install; ``verify-registry`` later
    proves the release against independently published dependency artifacts.
    """
    entry = resolve(args.distribution)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    closure = _module_closure(
        [f"{entry['distribution']}=={_declared(entry)['version']}"]
    )
    dependencies = _smoke_dependencies(closure, entry["distribution"])
    for member in closure:
        if _declared(member)["version"] != member["version"]:
            raise ReleaseRefused(
                f"{member['distribution']}: local source version differs "
                f"from exact logical pin {member['version']}"
            )

    for dependency in dependencies:
        subprocess.run(
            ["poetry", "build", "--output", str(output)],
            cwd=dependency,
            check=True,
        )

    if dependencies:
        names = ", ".join(path.name for path in dependencies)
        print(f"{entry['distribution']}: built local smoke dependencies: {names}")
    else:
        print(f"{entry['distribution']}: no extra first-party smoke dependencies")


def cmd_verify_wheel(args: argparse.Namespace) -> None:
    """Pre-publish smoke: the built bytes, installed clean, registering.

    Needs a kernel ARTIFACT, not just a kernel: the module floors at the release
    that allocated its schema, and that kernel may not be published yet — this
    smoke runs BEFORE any publication, including the kernel's own. So the caller
    supplies a locally built kernel wheel through `--kernel-dist`, and any
    additional first-party distributions permitted by the module's reviewed
    wheel policy through ``--dependency-dist`` (built by
    ``build-local-dependencies``). Every first-party wheel is installed by its
    explicit path — never resolved by name — and an isolated interpreter proves
    each installed first-party distribution came from exactly that file.

    That is a weaker claim than the registry verification deliberately: it
    proves these bytes install and register against the kernel THIS CHECKOUT
    produces. Proving them against the PUBLISHED kernel is `verify-registry`'s
    job, and it necessarily runs after both are on the index.
    """
    entry = resolve(args.distribution)
    import tempfile

    closure = _module_closure(
        [f"{entry['distribution']}=={_declared(entry)['version']}"]
    )

    # Every first-party wheel is installed by explicit PATH, never resolved by
    # name, so no index can substitute a different artifact for the target,
    # the kernel or a first-party dependency.
    targets = _wheels(args.dist)
    if len(targets) != 1:
        raise SystemExit(f"expected exactly one target wheel in {args.dist}")
    if _normal_distribution(_wheel_distribution(targets[0])) != entry["distribution"]:
        raise ReleaseRefused(
            "target artifact directory does not contain the release target"
        )
    dependencies: list[Path] = []
    if args.kernel_dist:
        kernel_wheels = _wheels(args.kernel_dist)
        if (
            len(kernel_wheels) != 1
            or _normal_distribution(_wheel_distribution(kernel_wheels[0]))
            != "dotmac-kernel"
        ):
            raise ReleaseRefused(
                "kernel artifact directory must contain exactly the kernel wheel"
            )
        dependencies += kernel_wheels
    for dependency_dist in args.dependency_dist:
        dependencies += _wheels(dependency_dist)
    first_party = [*targets, *dependencies]
    _verify_smoke_inputs(entry, closure, first_party, kernel=bool(args.kernel_dist))

    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(Path(tmp) / "venv")
        permitted, owner_policy = _module_wheel_policy(closure)
        staged = _stage_wheels(
            pip,
            Path(tmp) / "wheelhouse",
            pins=[],
            local=first_party,
            permitted=permitted,
            owner_policy=owner_policy,
            private_index=None,
        )
        _offline_install(pip, staged, tmp)
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                _PROVENANCE.format(
                    expected={
                        _wheel_distribution(wheel): str(wheel) for wheel in first_party
                    },
                    checkout=str(REPO_ROOT),
                ),
            ],
            check=True,
            cwd=tmp,
        )
        _verify_installed(
            python,
            _installed_targets(closure),
            [
                member["native_package"]
                for member in closure
                if member["native_package"]
            ],
        )

    if args.emit_dependency_manifest:
        # The exact tested inputs, recorded as immutable coordinates. Their
        # source stays outside ReleaseAuthority.v1: it is tested input, not
        # release-control code.
        manifest = {
            "schema": "SmokeDependencyWheels.v1",
            "wheels": [
                {"filename": wheel.name, "sha256": _sha256(wheel)}
                for wheel in sorted(dependencies, key=lambda path: path.name)
            ],
        }
        out = Path(args.emit_dependency_manifest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(f"{entry['distribution']}: wheel smoke OK")


def cmd_verify_registry(args: argparse.Namespace) -> None:
    """Post-publish: install the PUBLISHED release from the private index.

    `--index` carries the authenticated simple-index URL. Exact pins only: a
    range would let this pass against a version nobody published in this run.

    First-party wheels are downloaded from the private index alone. Actual
    wheel metadata supplies permitted first-party Python dependency ranges;
    reviewed logical dependencies remain exact pins. All first-party bytes are
    then installed by explicit path and their provenance is independently
    verified. Public fallback supplies only third-party requirements.

    `PUBLIC_INDEX_URL` overrides the public index so an air-gapped or mirrored
    runner can point at its own, per the everything-by-config rule.
    """
    import tempfile

    closure = _module_closure(args.pin)
    targets = _installed_targets(closure)
    native_targets = [
        member["native_package"] for member in closure if member["native_package"]
    ]
    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(Path(tmp) / "venv")
        wheels = _registry_wheels(args, closure, pip, Path(tmp) / "registry-wheels")
        _offline_install(pip, wheels, tmp)
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                _PROVENANCE.format(
                    expected={
                        _wheel_distribution(wheel): str(wheel)
                        for wheel in wheels
                        if _wheel_identity(wheel)[0].startswith("dotmac")
                    },
                    checkout=str(REPO_ROOT),
                ),
            ],
            check=True,
            cwd=tmp,
        )
        _verify_installed(python, targets, native_targets)
    print(
        "registry verification OK for "
        + ", ".join(
            f"{member['distribution']}=={member['version']}" for member in closure
        )
    )


@contextmanager
def _candidate_environment():
    """Keep registry credentials out of installer and installed-code children."""
    original = dict(os.environ)
    clean = {
        key: value
        for key, value in original.items()
        if key != "INDEX"
        and not key.startswith(("PIP_", "TWINE_", "FORGEJO_"))
        and not (key.endswith("INDEX_URL") and key != "PUBLIC_INDEX_URL")
    }
    try:
        os.environ.clear()
        os.environ.update(clean)
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def _candidate_source_binding(closure: list[dict]) -> dict[str, str]:
    """Hash the reviewed plan and implementation, never caller attestations."""
    paths = {ALLOWLIST, Path(__file__)}
    for member in closure:
        package = member["package_path"]
        paths.update(
            {
                package / "pyproject.toml",
                package / "EXTRACTION.toml",
                package / "src" / member["import_name"] / "manifest.py",
            }
        )
    return {
        path.relative_to(REPO_ROOT).as_posix(): _sha256(path) for path in sorted(paths)
    }


def _candidate_installed_check(
    python: Path, closure: list[dict], expected: dict[str, str], directory: Path
) -> None:
    # Capture failures: imports and native loaders must never echo credentials.
    script = (
        _PROVENANCE.format(expected=expected, checkout=str(REPO_ROOT))
        + _REGISTER.format(names=_installed_targets(closure))
        + _NATIVE_REGISTER.format(
            names=[
                member["native_package"]
                for member in closure
                if member["native_package"]
            ],
            checkout=str(REPO_ROOT),
        )
    )
    result = subprocess.run(
        [str(python), "-I", "-c", script],
        cwd=directory,
        env=_pip_environment(),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ReleaseRefused("installed candidate verification failed")


def cmd_qualify_candidate(args: argparse.Namespace) -> None:
    # Wheel inspection can invoke native policy subprocesses too, so the clean
    # environment must cover the entire command, including early failures.
    with _candidate_environment():
        _qualify_candidate_impl(args)


def _qualify_candidate_impl(args: argparse.Namespace) -> None:
    """Qualify this local candidate against published dependencies before upload.

    This receipt proves an installation check, not release/admission authority.
    The output is written only after all checks, with its receipt written last.
    """
    import shutil
    import tempfile

    entry = resolve(args.distribution)
    version = _declared(entry)["version"]
    closure = _module_closure([f"{entry['distribution']}=={version}"])
    target_dir = Path(args.dist)
    if target_dir.is_symlink() or not target_dir.is_dir():
        raise ReleaseRefused("target directory must contain exactly one regular wheel")
    contents = list(target_dir.resolve().iterdir())
    wheels = _wheels(args.dist)
    sdists = {
        f"{name}-{version}.tar.gz"
        for name in (entry["distribution"], entry["distribution"].replace("-", "_"))
    }
    if (
        len(wheels) != 1
        or any(path.is_symlink() or not path.is_file() for path in contents)
        or any(path != wheels[0] and path.name not in sdists for path in contents)
        or len(contents) > 2
    ):
        raise ReleaseRefused(
            "target directory must contain exactly one regular wheel and "
            "at most one corresponding source distribution"
        )
    candidate = wheels[0]
    if _wheel_identity(candidate) != (entry["distribution"], version):
        raise ReleaseRefused("candidate target identity differs from reviewed source")
    output = Path(args.output).absolute()
    if output.exists() or output.is_symlink():
        raise ReleaseRefused("candidate binding output must not exist")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ReleaseRefused(
            "candidate binding output parent must be a regular directory"
        )
    # Existing content/native policy applies to the exact candidate before any
    # downloads; this does not build or infer sibling Python versions from HEAD.
    try:
        cmd_inspect(argparse.Namespace(distribution=args.distribution, dist=args.dist))
    except ReleaseRefused:
        raise ReleaseRefused("candidate wheel-content policy failed") from None
    source = _candidate_source_binding(closure)
    candidate_hash = _sha256(candidate)
    with tempfile.TemporaryDirectory() as tmp:
        temporary = Path(tmp)
        snapshot = temporary / candidate.name
        shutil.copyfile(candidate, snapshot)
        if _sha256(snapshot) != candidate_hash or _sha256(candidate) != candidate_hash:
            raise ReleaseRefused("candidate changed during qualification")
        *_, Version, InvalidVersion, _environment = _packaging_api()
        try:
            floor = max(Version(member["kernel_floor"]) for member in closure)
            kernel = args.kernel or str(floor)
            _exact_pin(f"dotmac-kernel=={kernel}")
            if Version(kernel) < floor:
                raise ReleaseRefused(
                    "candidate kernel pin is below the reviewed closure floor"
                )
        except InvalidVersion:
            raise ReleaseRefused("invalid candidate kernel version") from None
        pins = [
            f"{member['distribution']}=={member['version']}"
            for member in closure
            if member["distribution"] != entry["distribution"]
        ]
        pins.append(f"dotmac-kernel=={kernel}")
        python, pip = _venv(temporary / "venv")
        permitted, owner_policy = _module_wheel_policy(closure)
        wheels = _stage_wheels(
            pip,
            temporary / "wheelhouse",
            pins=pins,
            local=[snapshot],
            permitted=permitted,
            owner_policy=owner_policy,
            private_index=args.index,
        )
        first_party: dict[str, Path] = {}
        for wheel in wheels:
            name, actual_version = _wheel_identity(wheel)
            if not name.startswith("dotmac"):
                continue
            if name in first_party or name not in permitted:
                raise ReleaseRefused(
                    "duplicate or unapproved qualified first-party wheel"
                )
            first_party[name] = wheel
            if name == entry["distribution"] and (
                wheel != snapshot
                or actual_version != version
                or _sha256(wheel) != candidate_hash
            ):
                raise ReleaseRefused("candidate substitution during qualification")
        if any(member["distribution"] not in first_party for member in closure):
            raise ReleaseRefused("missing logical module wheel during qualification")
        for member in closure:
            name = member["distribution"]
            if _wheel_identity(first_party[name])[1] != member["version"]:
                raise ReleaseRefused(
                    "qualified logical module version differs from reviewed pin"
                )
            _inspect_native(member, first_party[name])
        if (
            "dotmac-kernel" not in first_party
            or _wheel_identity(first_party["dotmac-kernel"])[1] != kernel
        ):
            raise ReleaseRefused("qualified published kernel differs from exact pin")
        hashes = {name: _sha256(wheel) for name, wheel in first_party.items()}
        _offline_install(pip, wheels, tmp)
        _candidate_installed_check(
            python,
            closure,
            {name: str(wheel) for name, wheel in first_party.items()},
            temporary,
        )
        if (
            _sha256(candidate) != candidate_hash
            or _candidate_source_binding(closure) != source
            or any(
                _sha256(wheel) != hashes[name] for name, wheel in first_party.items()
            )
        ):
            raise ReleaseRefused(
                "candidate, source or qualified wheel changed during checks"
            )
        records = [
            {
                "name": name,
                "version": _wheel_identity(wheel)[1],
                "filename": wheel.name,
                "sha256": hashes[name],
            }
            for name, wheel in sorted(first_party.items())
        ]
        if len({record["filename"] for record in records}) != len(records):
            raise ReleaseRefused("qualified first-party wheel filenames collide")
        receipt = {
            "schema": "CandidateQualification.v1",
            "claim": "candidate-installation-only",
            "candidate": next(
                record for record in records if record["name"] == entry["distribution"]
            ),
            "kernel": f"dotmac-kernel=={kernel}",
            "source": source,
            "logical_modules": _installed_targets(closure),
            "wheels": records,
        }
        # Exclusive creation refuses an output-directory race. No existing
        # directory is replaced, and no usable receipt survives a failed copy.
        try:
            output.mkdir()
        except FileExistsError:
            raise ReleaseRefused("candidate binding output must not exist") from None
        try:
            for record in records:
                destination = output / record["filename"]
                shutil.copyfile(first_party[record["name"]], destination)
                if _sha256(destination) != record["sha256"]:
                    raise ReleaseRefused(
                        "qualified binding copy differs from checked wheel"
                    )
            (output / "CandidateQualification.v1.json").write_text(
                json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        except BaseException:
            shutil.rmtree(output)
            raise
    print(f"{entry['distribution']}: candidate installation qualified")


def cmd_compare_published(args: argparse.Namespace) -> None:
    """Prove the PUBLISHED artifact is byte-identical to what a run built.

    The recovery case: a release whose `publish` succeeded and whose `verify`
    then failed leaves an artifact on the index with no tag. Deleting and
    re-uploading is the wrong repair — it would make one version identify two
    different sets of bytes, and every consumer that already resolved it would
    be silently holding something else. So the artifact is RECOVERED instead,
    and recovery has to start by proving the published bytes are the ones the
    original run inspected and smoked.

    Downloads both distribution forms from the index and compares SHA-256
    against the retained build artifacts. Any difference, or any file present on
    one side only, refuses — at that point the version is burned rather than
    recovered.
    """
    import hashlib
    import tempfile

    entry = resolve(args.distribution)
    built = Path(args.dist)
    if not built.is_dir():
        raise ReleaseRefused(f"--dist is not a directory: {built}")

    def digests(directory: Path) -> dict[str, str]:
        found: dict[str, str] = {}
        for path in sorted(directory.iterdir()):
            if path.suffix not in {".whl", ".gz"} and not path.name.endswith(".tar.gz"):
                continue
            found[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return found

    pin = f"{entry['distribution']}=={args.version}"
    public_index = os.environ.get("PUBLIC_INDEX_URL", "https://pypi.org/simple")
    with tempfile.TemporaryDirectory() as tmp:
        downloaded = Path(tmp) / "published"
        downloaded.mkdir()
        # `--no-deps` because only THIS artifact is under comparison. The public
        # index is still required: downloading an sdist makes pip prepare its
        # metadata in an isolated environment, which resolves the declared build
        # backend (Poetry Core) before the file reaches this comparison.
        for extra in (["--no-binary", ":all:"], []):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "--quiet",
                    "--no-deps",
                    "--index-url",
                    args.index,
                    "--extra-index-url",
                    public_index,
                    "--dest",
                    str(downloaded),
                    *extra,
                    pin,
                ],
                check=True,
            )
        published = digests(downloaded)

    local = digests(built)
    if not published:
        raise ReleaseRefused(f"nothing downloaded from the index for {pin}")

    problems: list[str] = []
    for name, digest in sorted(published.items()):
        if name not in local:
            problems.append(f"{name}: on the index, absent from the build artifacts")
        elif local[name] != digest:
            problems.append(
                f"{name}: index {digest[:16]}… != built {local[name][:16]}…"
            )
    for name in sorted(set(local) - set(published)):
        problems.append(f"{name}: built but never published")

    if problems:
        raise ReleaseRefused(
            "published bytes do not match the original build:\n  "
            + "\n  ".join(problems)
            + "\nDo NOT delete and re-upload. Burn this version and release the "
            "next one, leaving the published artifact documented as unverified."
        )
    for name, digest in sorted(published.items()):
        print(f"{name}: sha256 {digest} — identical to the original build")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("resolve", help="gate on the allowlist and emit its facts")
    p.add_argument("distribution")
    p.add_argument("--version", default="")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("inspect", help="wheel-content policy")
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser(
        "build-local-dependencies",
        help="build reviewed first-party dependency wheels for the local smoke",
    )
    p.add_argument("distribution")
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_build_local_dependencies)

    p = sub.add_parser("verify-wheel", help="install the built wheel and register")
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.add_argument(
        "--kernel-dist",
        default="",
        help="directory holding a locally built dotmac-kernel wheel",
    )
    p.add_argument(
        "--dependency-dist",
        action="append",
        default=[],
        help="directory holding locally built, policy-permitted dependency wheels",
    )
    p.add_argument(
        "--emit-dependency-manifest",
        default="",
        help="write the exact kernel/dependency wheel filenames and SHA-256 "
        "the smoke installed (SmokeDependencyWheels.v1)",
    )
    p.set_defaults(func=cmd_verify_wheel)

    p = sub.add_parser(
        "qualify-candidate",
        help="qualify local candidate against published dependencies",
    )
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.add_argument("--index", required=True)
    p.add_argument(
        "--kernel", default="", help="exact published kernel; defaults to closure floor"
    )
    p.add_argument(
        "--output", required=True, help="new qualified first-party binding directory"
    )
    p.set_defaults(func=cmd_qualify_candidate)

    p = sub.add_parser(
        "compare-published",
        help="prove the published artifact matches a run's build artifacts",
    )
    p.add_argument("distribution")
    p.add_argument("--version", required=True)
    p.add_argument("--index", required=True)
    p.add_argument("--dist", required=True)
    p.set_defaults(func=cmd_compare_published)

    p = sub.add_parser(
        "verify-registry", help="install exact pins from the index and register"
    )
    p.add_argument("--index", required=True)
    p.add_argument("--kernel", default="", help="exact published kernel version")
    p.add_argument("--pin", action="append", required=True)
    p.set_defaults(func=cmd_verify_registry)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
