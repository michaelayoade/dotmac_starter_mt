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

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = REPO_ROOT / ".github" / "release-modules.json"


class ReleaseRefused(SystemExit):
    """Any refusal. Exits non-zero so a workflow step cannot continue past it."""

    def __init__(self, message: str) -> None:
        super().__init__(f"::error::{message}")


def load_allowlist() -> dict[str, dict]:
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    return data["modules"]


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
    package_dir = REPO_ROOT / entry["package_dir"]
    if not (package_dir / "pyproject.toml").is_file():
        raise ReleaseRefused(
            f"{distribution}: allowlisted package_dir {entry['package_dir']!r} "
            "has no pyproject.toml"
        )
    return {**entry, "distribution": distribution, "package_path": package_dir}


def _declared(entry: dict) -> dict:
    manifest = tomllib.loads(
        (entry["package_path"] / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]
    return manifest


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

    floor = manifest["dependencies"].get("dotmac-kernel")
    expected = f">={entry['kernel_floor']}"
    if floor != expected:
        raise ReleaseRefused(
            f"{args.distribution}: kernel floor is {floor!r} but the allowlist "
            f"records {expected!r} as the release that allocated "
            f"{entry['db_schema']!r}. An earlier kernel cannot register this "
            "module at all."
        )

    # Consumed by the workflow via $GITHUB_OUTPUT.
    for key in (
        "package_dir",
        "import_name",
        "manifest_attr",
        "kernel_floor",
        "db_schema",
        "tag_prefix",
    ):
        print(f"{key}={entry[key]}")
    print(f"version={manifest['version']}")
    print(f"tag={entry['tag_prefix']}{manifest['version']}")


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

    # Nothing from the assembly, the test suite, or the repo's tooling.
    for name in names:
        for prefix in policy["forbidden_prefixes"]:
            if name.startswith(prefix):
                problems.append(f"forbidden content: {name}")

    # Dependency closure: a module depends on the kernel and nothing else in the
    # fleet. A sibling module or the assembly appearing here would make the
    # module un-releasable independently (ADR-0006 § 2).
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
    for name in names:
        lowered = name.lower()
        if any(
            marker in lowered
            for marker in (".env", "id_rsa", ".pem", ".key", "secrets.", "credentials.")
        ):
            problems.append(f"secret-shaped file in the wheel: {name}")

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


_REGISTER = """
import pathlib, sys
import dotmac_kernel
from dotmac_kernel.namespaces import NamespaceRegistry

manifests, names = [], {names!r}
for import_name, attr, schema in names:
    mod = __import__(import_name, fromlist=[attr])
    manifest = getattr(mod, attr)
    here = pathlib.Path(mod.__file__).resolve()
    # The installed distribution, never the checkout it was built from.
    assert "site-packages" in str(here), f"{{import_name}} resolved to {{here}}"
    assert manifest.db_schema == schema, (import_name, manifest.db_schema, schema)
    manifests.append(manifest)

# Construction IS validation: distinct schemas, prefixes, branch labels, and no
# contested table. With more than one manifest this is the composition proof.
NamespaceRegistry.from_manifests(manifests)
print("kernel", dotmac_kernel.__version__, "registered:",
      ", ".join(m.db_schema for m in manifests))
"""


def _verify_installed(python: Path, targets: list[tuple[str, str, str]]) -> None:
    subprocess.run([str(python), "-c", _REGISTER.format(names=targets)], check=True)


def cmd_verify_wheel(args: argparse.Namespace) -> None:
    """Pre-publish smoke: the built bytes, installed clean, registering.

    Needs a kernel ARTIFACT, not just a kernel: the module floors at the release
    that allocated its schema, and that kernel may not be published yet — this
    smoke runs BEFORE any publication, including the kernel's own. So the caller
    supplies a locally built kernel wheel through `--kernel-dist`, and the
    module resolves against it via `--find-links`.

    That is a weaker claim than the registry verification deliberately: it
    proves these bytes install and register against the kernel THIS CHECKOUT
    produces. Proving them against the PUBLISHED kernel is `verify-registry`'s
    job, and it necessarily runs after both are on the index.
    """
    entry = resolve(args.distribution)
    import tempfile

    links = ["--find-links", args.dist]
    if args.kernel_dist:
        links += ["--find-links", args.kernel_dist]

    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(Path(tmp) / "venv")
        subprocess.run(
            [str(pip), "install", "--quiet", *links, entry["distribution"]],
            check=True,
        )
        _verify_installed(
            python,
            [(entry["import_name"], entry["manifest_attr"], entry["db_schema"])],
        )
    print(f"{entry['distribution']}: wheel smoke OK")


def cmd_verify_registry(args: argparse.Namespace) -> None:
    """Post-publish: install the PUBLISHED release from the private index.

    `--index` carries the authenticated simple-index URL. Exact pins only: a
    range would let this pass against a version nobody published in this run.

    The private index is `--index-url` and the PUBLIC one is `--extra-index-url`,
    which is not a convenience: `--index-url` REPLACES the default index, so with
    it alone pip can see the Dotmac distributions and nothing they depend on.
    Every module declares `sqlalchemy`, so verification failed for every module
    that could ever be released — and it failed AFTER publishing, leaving the
    artifact on the index with no tag. `scripts/consumer_boot_check.sh` pairs the
    two flags for exactly this reason; the module path had only copied half of it.

    `PUBLIC_INDEX_URL` overrides the public index so an air-gapped or mirrored
    runner can point at its own, per the everything-by-config rule.
    """
    import tempfile

    targets: list[tuple[str, str, str]] = []
    specs: list[str] = []
    if args.kernel:
        # The kernel is NOT an allowlisted module — it has its own release
        # workflow and its own inspection rules. It is pinned here rather than
        # resolved, so a composition check proves the set against a SPECIFIC
        # published kernel instead of whatever the resolver happens to pick.
        specs.append(f"dotmac-kernel=={args.kernel}")
    for pair in args.pin:
        distribution, _, version = pair.partition("==")
        if not version:
            raise ReleaseRefused(f"{pair!r} is not an exact pin (name==version)")
        entry = resolve(distribution)
        targets.append(
            (entry["import_name"], entry["manifest_attr"], entry["db_schema"])
        )
        specs.append(pair)

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
                *specs,
            ],
            check=True,
        )
        _verify_installed(python, targets)
    print(f"registry verification OK for {', '.join(specs)}")


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

    p = sub.add_parser("verify-wheel", help="install the built wheel and register")
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.add_argument(
        "--kernel-dist",
        default="",
        help="directory holding a locally built dotmac-kernel wheel",
    )
    p.set_defaults(func=cmd_verify_wheel)

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
