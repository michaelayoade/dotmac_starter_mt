"""Resolve, inspect and verify a STATELESS PROTOCOL ADAPTER release.

The fail-closed half of `release-adapter.yml`, and the sibling of
`scripts/release_module.py`. Every subcommand refuses rather than warns — a
release step that printed a warning and continued would publish the thing it
just objected to.

## Why a second script and a second allowlist

ADR-0006's 2026-08-14 amendment added a fourth `EXTRACTION.toml` classification:
`stateless-protocol-adapter`, a distribution a product **calls** rather than
installs. It has no `ModuleManifest`, no migration lineage, no
`MIGRATION_OWNER_LEDGER` allocation and no persistence import.

The module lane asserts three facts an adapter simply does not have:

  * `db_schema`   — compared against the manifest's `short_code`, and asserted
                    again after installation;
  * `manifest_attr` — the attribute registered through `NamespaceRegistry`;
  * `kernel_floor`  — the release that allocated the schema, or the highest
                    kernel capability the manifest consumes.

Making those OPTIONAL in `release_module.py` was the cheaper change and the
worse one. Optionality is not scoped to the package that needs it: once
`db_schema` may be absent, a STATEFUL module whose `db_schema` was dropped in a
bad merge stops being refused and starts being treated as an adapter, with the
namespace assertion silently skipped rather than failed. The repository has
already settled this argument once, about the kernel — "one workflow pretending
to cover both would have to weaken whichever check the other cannot satisfy".

So the stateful lane keeps all three MANDATORY and this lane declares none of
them. `resolve` below REFUSES an entry carrying any of the three: a package with
those facts is a module, and belongs in the lane whose gates can interrogate it.

## What replaces the namespace proof

A module's verification is registration: constructing a `NamespaceRegistry` IS
the validation, because two modules that each register alone can still collide.
An adapter contests nothing, so the equivalent proof is its PUBLIC SURFACE and
its CLASSIFICATION, both checked against the INSTALLED bytes rather than the
checkout:

  1. it imports from `site-packages`, not from the source tree;
  2. `__version__` equals the version being released;
  3. every name in `__all__` resolves;
  4. no `ModuleManifest` attribute exists anywhere on the package;
  5. importing it pulls in no ORM or database driver.

(4) and (5) are the runtime halves of two of the four properties ADR-0006 gives
the classification. The static halves are checked at PR time, generically over
any package claiming the classification, by `stateless_adapter_violations` in
`tests/architecture/test_product_first_extraction.py`. Checking them again on
the artifact is not duplication: the source tree and the wheel are different
objects, and only one of them gets published.

## Shared with the module lane, deliberately

`secret_shaped` is IMPORTED from `release_module` rather than reimplemented, for
the reason that module's docstring already gives: two copies of a name-shape
list drift, and the drift is silent in the worst direction.

Stdlib only, deliberately: this runs before anything is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_module import ReleaseRefused, secret_shaped

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = REPO_ROOT / ".github" / "release-adapters.json"

CLASSIFICATION: Final = "stateless-protocol-adapter"

# Facts only a STATEFUL module has. An entry declaring one is in the wrong lane,
# and accepting it here would publish a module while skipping every namespace,
# lineage and dual-plane gate the module lane performs.
STATEFUL_ONLY_FIELDS: Final = ("db_schema", "manifest_attr", "kernel_floor")

# ADR-0006's fourth property ("no persistence import"), as it is checked at
# RUNTIME on the installed artifact. Deliberately the same four roots as
# `PERSISTENCE_ROOTS` in `tests/architecture/test_product_first_extraction.py`,
# which checks the same property statically over the source tree — a test there
# asserts the two lists agree, because two copies that drift would leave the
# artifact and the source held to different rules. Third-party ROOTS only: the
# stdlib `sqlite3` can arrive through an unrelated transitive import and would
# fail a release for a reason that is not this property.
PERSISTENCE_ROOTS: Final = ("alembic", "asyncpg", "psycopg", "sqlalchemy")


def load_allowlist() -> dict[str, dict]:
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    adapters = data.get("adapters")
    if not isinstance(adapters, dict):
        raise ReleaseRefused(f"{ALLOWLIST.name}: 'adapters' must be an object")
    return adapters


def resolve(distribution: str) -> dict:
    """The gate. Every other subcommand takes its facts from this result."""
    adapters = load_allowlist()
    entry = adapters.get(distribution)
    if entry is None:
        listed = (
            ", ".join(sorted(adapters)) if adapters else "(none — the lane is shut)"
        )
        raise ReleaseRefused(
            f"{distribution!r} is not an allowlisted stateless protocol adapter. "
            f"Publishable adapters are: {listed}. Adding one is a reviewed change "
            f"to .github/{ALLOWLIST.name}, not a dispatch input — and absence is "
            "the safety mechanism, not an oversight."
        )

    misplaced = [field for field in STATEFUL_ONLY_FIELDS if field in entry]
    if misplaced:
        raise ReleaseRefused(
            f"{distribution}: adapter entry declares {', '.join(misplaced)} — "
            "those are STATEFUL facts. A package with a schema, a manifest "
            "attribute or a kernel floor is a module and belongs in "
            ".github/release-modules.json, where the namespace and lineage gates "
            "can actually check it."
        )

    package_dir = REPO_ROOT / entry["package_dir"]
    if not (package_dir / "pyproject.toml").is_file():
        raise ReleaseRefused(
            f"{distribution}: allowlisted package_dir {entry['package_dir']!r} "
            "has no pyproject.toml"
        )

    # The lane is tied to the GOVERNED classification, not to a name. This is
    # what stops the adapter lane becoming a way to publish a stateful module
    # while skipping the module lane's gates.
    dossier_path = package_dir / "EXTRACTION.toml"
    if not dossier_path.is_file():
        raise ReleaseRefused(
            f"{distribution}: no EXTRACTION.toml — the adapter lane resolves its "
            "classification from the dossier and cannot proceed without one"
        )
    dossier = tomllib.loads(dossier_path.read_text(encoding="utf-8"))
    declared = dossier.get("classification")
    if declared != CLASSIFICATION:
        raise ReleaseRefused(
            f"{distribution}: EXTRACTION.toml declares classification "
            f"{declared!r}, but this lane publishes only {CLASSIFICATION!r}. "
            "Releasing it here would skip the namespace, lineage and dual-plane "
            "checks its own classification requires."
        )

    return {**entry, "distribution": distribution, "package_path": package_dir}


def _declared(entry: dict) -> dict:
    return tomllib.loads(
        (entry["package_path"] / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]


def cmd_resolve(args: argparse.Namespace) -> None:
    entry = resolve(args.distribution)
    manifest = _declared(entry)

    if manifest["name"] != args.distribution:
        raise ReleaseRefused(
            f"pyproject declares {manifest['name']!r}, dispatched "
            f"{args.distribution!r}"
        )
    if args.version and manifest["version"] != args.version:
        raise ReleaseRefused(
            f"{args.distribution}: dispatched version {args.version!r} != package "
            f"version {manifest['version']!r}. The version is not inferred; fix "
            "one of them."
        )

    # Consumed by the workflow via $GITHUB_OUTPUT. Deliberately no db_schema,
    # manifest_attr or kernel_floor — an adapter has none, and emitting an empty
    # value would let a later step read it as "unknown" rather than "absent".
    for key in ("package_dir", "import_name", "tag_prefix"):
        print(f"{key}={entry[key]}")
    print(f"version={manifest['version']}")
    print(f"tag={entry['tag_prefix']}{manifest['version']}")


def cmd_inspect(args: argparse.Namespace) -> None:
    """Wheel-content policy. What must ship, what must never, what may be required.

    Structurally the module lane's check, minus the migration lineage it has no
    business requiring. The dependency closure is per-entry rather than
    kernel-only: an adapter speaks a network protocol and verifies signatures,
    and neither can be faked, so its closure is reviewed in the allowlist diff.
    """
    entry = resolve(args.distribution)
    policy = entry["wheel_contents"]
    wheels = sorted(Path(args.dist).glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseRefused(
            f"expected exactly one wheel in {args.dist}, found {len(wheels)}"
        )
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        if metadata is None:
            raise ReleaseRefused(f"{wheel.name}: no METADATA in the wheel")
        meta_text = archive.read(metadata).decode("utf-8")

    problems: list[str] = []

    for required in policy["required"]:
        if required not in names:
            problems.append(f"missing from the wheel: {required}")

    for name in names:
        for prefix in policy["forbidden_prefixes"]:
            if name.startswith(prefix):
                problems.append(f"forbidden content: {name}")

    # A migration lineage in an ADAPTER wheel means the package became stateful
    # without changing its dossier — the exact drift ADR-0006's amendment names.
    # `forbidden_prefixes` cannot catch it: the lineage would live UNDER the
    # import package, not at a fixed top-level prefix.
    for name in names:
        if "/migrations/" in name:
            problems.append(
                f"migration lineage in a stateless adapter wheel: {name} — the "
                "package grew persistence without changing its classification"
            )

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


def _venv(path: Path) -> tuple[Path, Path]:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    bin_dir = path / ("Scripts" if sys.platform == "win32" else "bin")
    return bin_dir / "python", bin_dir / "pip"


# The adapter's answer to the module lane's `NamespaceRegistry` construction.
# Runs INSIDE the clean venv, against the installed distribution.
_SURFACE = """
import importlib, pathlib, sys

import {import_name} as package

here = pathlib.Path(package.__file__).resolve()
# The installed distribution, never the checkout it was built from.
assert "site-packages" in str(here), f"{import_name} resolved to {{here}}"

expected = {version!r}
assert package.__version__ == expected, (package.__version__, expected)

# Every advertised name resolves. A wheel that ships a truncated package still
# imports; this is what notices.
missing = [name for name in package.__all__ if not hasattr(package, name)]
assert not missing, f"__all__ names absent from the installed package: {{missing}}"

# ADR-0006's classification, proved on the ARTIFACT rather than the source tree.
assert not hasattr(package, "ModuleManifest"), (
    "a stateless protocol adapter is CALLED, not installed"
)
for name in package.SUPPORTED_MODULES:
    submodule = importlib.import_module(name)
    assert not hasattr(submodule, "ModuleManifest"), name

persistence = sorted(set({persistence_roots!r}) & set(sys.modules))
assert not persistence, (
    f"importing the adapter pulled in persistence: {{persistence}} — it has "
    "become a module without changing its dossier"
)

print("verified", package.__name__, package.__version__,
      f"({{len(package.__all__)}} public names)")
"""


def _verify_installed(python: Path, import_name: str, version: str) -> None:
    script = _SURFACE.format(
        import_name=import_name,
        version=version,
        persistence_roots=PERSISTENCE_ROOTS,
    )
    subprocess.run([str(python), "-c", script], check=True)


def cmd_verify_wheel(args: argparse.Namespace) -> None:
    """Pre-publish smoke: the built bytes, installed clean, and the surface they
    expose.

    Needs no kernel artifact — unlike a module, an adapter does not floor on a
    kernel and registers nothing. Its third-party dependencies still come from
    the public index, so `--find-links` supplies the wheel and the resolver
    fetches the rest.
    """
    entry = resolve(args.distribution)
    manifest = _declared(entry)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(Path(tmp) / "venv")
        subprocess.run(
            [
                str(pip),
                "install",
                "--quiet",
                "--find-links",
                args.dist,
                entry["distribution"],
            ],
            check=True,
        )
        _verify_installed(python, entry["import_name"], manifest["version"])
    print(f"{entry['distribution']}: wheel smoke OK")


def cmd_verify_registry(args: argparse.Namespace) -> None:
    """Post-publish: install the PUBLISHED release from the private index.

    `--index` carries the authenticated simple-index URL. An exact pin only: a
    range would let this pass against a version nobody published in this run.

    The private index is `--index-url` and the PUBLIC one is `--extra-index-url`,
    for the reason `release_module.py` records at length: `--index-url` REPLACES
    the default index, so with it alone pip can see the Dotmac distribution and
    nothing it depends on. That matters more here, not less — an adapter's whole
    dependency set is public (`pyjwt`, `httpx`).

    `PUBLIC_INDEX_URL` overrides the public index so an air-gapped or mirrored
    runner can point at its own, per the everything-by-config rule.
    """
    import tempfile

    distribution, _, version = args.pin.partition("==")
    if not version:
        raise ReleaseRefused(f"{args.pin!r} is not an exact pin (name==version)")
    entry = resolve(distribution)

    public_index = os.environ.get("PUBLIC_INDEX_URL", "https://pypi.org/simple")
    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(Path(tmp) / "venv")
        subprocess.run(
            [
                str(pip),
                "install",
                "--quiet",
                "--index-url",
                args.index,
                "--extra-index-url",
                public_index,
                args.pin,
            ],
            check=True,
        )
        _verify_installed(python, entry["import_name"], version)
    print(f"registry verification OK for {args.pin}")


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

    p = sub.add_parser("verify-wheel", help="install the built wheel and smoke it")
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.set_defaults(func=cmd_verify_wheel)

    p = sub.add_parser(
        "verify-registry", help="install an exact pin from the index and smoke it"
    )
    p.add_argument("--index", required=True)
    p.add_argument("--pin", required=True)
    p.set_defaults(func=cmd_verify_registry)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
