"""Resolve, inspect and verify a UNIVERSAL FACILITY release.

The fail-closed half of `release-facility.yml`, and the third sibling of
`scripts/release_module.py` and `scripts/release_adapter.py`. Every subcommand
refuses rather than warns — a release step that printed a warning and continued
would publish the thing it just objected to.

## Why a third script and a third allowlist

ADR-0006's `universal-facility` classification (see
`packages/dotmac-deployment-foundation/EXTRACTION.toml`) is a distribution a
product's BUILD RUNNER calls through a CLI entry point, not something a product
process imports at request time. It has, like a stateless protocol adapter, no
`ModuleManifest`, no migration lineage and no `MIGRATION_OWNER_LEDGER`
allocation — so `.github/release-modules.json`'s `db_schema`, `manifest_attr`
and `kernel_floor` describe facts it does not have, for the same reason
`release_adapter.py`'s docstring already gives.

A facility is not simply an adapter with a different name, and that is exactly
why it gets its OWN allowlist rather than a row in `.github/
release-adapters.json`:

  * an adapter's release proof is an IMPORT-SURFACE proof — every `__all__`
    name resolves on the installed bytes, because a product imports the
    adapter into its own process. A facility declares zero runtime
    dependencies and is never imported by a product process; its release
    proof is that its CONSOLE SCRIPT (`dotmac-deploy`) runs, because that is
    the only surface a product actually calls;
  * a reader sent to `.github/release-adapters.json` to add a facility would
    find fields — `import_name`, an `__all__`-shaped surface — that assume the
    wrong verification story, and would have to weaken `verify-wheel`'s
    import-and-assert-`__all__` check to accommodate a package that has no
    business being imported at all. That is the same argument this repository
    has recorded twice already, about the kernel and about the adapter lane:
    "one workflow pretending to cover both would have to weaken whichever
    check the other cannot satisfy".

So this script does not import the released package at all. It shells out to
the installed CONSOLE SCRIPT, in a clean venv, exactly as an operator or a
product's CI would.

## The independently delivered controller

The wheel is published once through the private PyPI registry. The controller
launcher is not part of that wheel, so `create-controller-receipt` binds the
exact wheel and `scripts/run_deployment_controller.py` to one strict
`DeploymentControllerReleaseReceipt.v1`. The workflow preserves all three as
run provenance, publishes only the launcher and receipt as durable Forgejo
generic-package files, and verifies their downloaded bytes before tagging. It
never creates a second generic copy of the wheel.

## The classification is checked, not trusted

`resolve` reads the package's `EXTRACTION.toml` and refuses anything whose
`classification` is not `universal-facility`. That is what stops this lane
from becoming a way to publish a module or an adapter while skipping the
checks their own classifications require.

## Shared with the other release scripts, deliberately

`ReleaseRefused` and `secret_shaped` are IMPORTED from `release_module` rather
than reimplemented — `release_adapter.py` already made this argument: two
copies of a name-shape list drift, and the drift is silent in the worst
direction.

Stdlib only, deliberately: this runs before anything is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_module import ReleaseRefused, secret_shaped

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = REPO_ROOT / ".github" / "release-facilities.json"

CLASSIFICATION: Final = "universal-facility"
CONTROLLER_RECEIPT_SCHEMA: Final = "DeploymentControllerReleaseReceipt.v1"
CONTROLLER_RECEIPT_FILENAME: Final = f"{CONTROLLER_RECEIPT_SCHEMA}.json"
CONTROLLER_GENERIC_PACKAGE: Final = "dotmac-deployment-controller"

_DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION: Final = re.compile(r"^[0-9a-f]{40}$")
_VERSION: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+)?$")

_RECEIPT_FIELDS: Final = frozenset(
    {
        "schema",
        "distribution",
        "exact_version",
        "artifact_sha256",
        "launcher_sha256",
        "source_revision",
        "release_run_id",
        "tag",
    }
)

# Facts only a STATEFUL module has. An entry declaring one is in the wrong
# lane, and accepting it here would publish a module while skipping every
# namespace, lineage and dual-plane gate the module lane performs. Kept
# identical to `release_adapter.STATEFUL_ONLY_FIELDS` — a facility has exactly
# as little business carrying these as an adapter does.
STATEFUL_ONLY_FIELDS: Final = ("db_schema", "manifest_attr", "kernel_floor")


def load_allowlist() -> dict[str, dict]:
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    facilities = data.get("facilities")
    if not isinstance(facilities, dict):
        raise ReleaseRefused(f"{ALLOWLIST.name}: 'facilities' must be an object")
    return facilities


def resolve(distribution: str) -> dict:
    """The gate. Every other subcommand takes its facts from this result."""
    facilities = load_allowlist()
    entry = facilities.get(distribution)
    if entry is None:
        listed = (
            ", ".join(sorted(facilities)) if facilities else "(none — the lane is shut)"
        )
        raise ReleaseRefused(
            f"{distribution!r} is not an allowlisted universal facility. "
            f"Publishable facilities are: {listed}. Adding one is a reviewed "
            f"change to .github/{ALLOWLIST.name}, not a dispatch input."
        )

    misplaced = [field for field in STATEFUL_ONLY_FIELDS if field in entry]
    if misplaced:
        raise ReleaseRefused(
            f"{distribution}: facility entry declares {', '.join(misplaced)} — "
            "those are STATEFUL facts. A package with a schema, a manifest "
            "attribute or a kernel floor is a module and belongs in "
            ".github/release-modules.json, where the namespace and lineage "
            "gates can actually check it."
        )

    package_dir = REPO_ROOT / entry["package_dir"]
    if not (package_dir / "pyproject.toml").is_file():
        raise ReleaseRefused(
            f"{distribution}: allowlisted package_dir {entry['package_dir']!r} "
            "has no pyproject.toml"
        )

    # The lane is tied to the GOVERNED classification, not to a name. This is
    # what stops the facility lane becoming a way to publish a module or an
    # adapter while skipping the checks their own classification requires.
    dossier_path = package_dir / "EXTRACTION.toml"
    if not dossier_path.is_file():
        raise ReleaseRefused(
            f"{distribution}: no EXTRACTION.toml — the facility lane resolves "
            "its classification from the dossier and cannot proceed without one"
        )
    dossier = tomllib.loads(dossier_path.read_text(encoding="utf-8"))
    declared = dossier.get("classification")
    if declared != CLASSIFICATION:
        raise ReleaseRefused(
            f"{distribution}: EXTRACTION.toml declares classification "
            f"{declared!r}, but this lane publishes only {CLASSIFICATION!r}. "
            "Releasing it here would skip the checks its own classification "
            "requires."
        )

    receipt_schema = entry.get("controller_receipt_schema")
    if receipt_schema != CONTROLLER_RECEIPT_SCHEMA:
        raise ReleaseRefused(
            f"{distribution}: controller_receipt_schema must be "
            f"{CONTROLLER_RECEIPT_SCHEMA!r}, got {receipt_schema!r}"
        )
    generic_package = entry.get("controller_generic_package")
    if generic_package != CONTROLLER_GENERIC_PACKAGE:
        raise ReleaseRefused(
            f"{distribution}: controller_generic_package must be "
            f"{CONTROLLER_GENERIC_PACKAGE!r}, got {generic_package!r}"
        )
    launcher_value = entry.get("controller_launcher")
    if not isinstance(launcher_value, str) or not launcher_value:
        raise ReleaseRefused(
            f"{distribution}: controller_launcher must be a repository-relative path"
        )
    launcher_path = (REPO_ROOT / launcher_value).resolve()
    if not launcher_path.is_relative_to(REPO_ROOT) or not launcher_path.is_file():
        raise ReleaseRefused(
            f"{distribution}: controller_launcher {launcher_value!r} must resolve "
            "to a file inside this repository"
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
            f"pyproject declares {manifest['name']!r}, dispatched {args.distribution!r}"
        )
    if args.version and manifest["version"] != args.version:
        raise ReleaseRefused(
            f"{args.distribution}: dispatched version {args.version!r} != "
            f"package version {manifest['version']!r}. The version is not "
            "inferred; fix one of them."
        )

    # Consumed by the workflow via $GITHUB_OUTPUT. Deliberately no db_schema,
    # manifest_attr or kernel_floor — a facility has none, and emitting an
    # empty value would let a later step read it as "unknown" rather than
    # "absent".
    for key in (
        "package_dir",
        "entry_point",
        "tag_prefix",
        "controller_launcher",
        "controller_receipt_schema",
        "controller_generic_package",
    ):
        print(f"{key}={entry[key]}")
    print(f"version={manifest['version']}")
    print(f"tag={entry['tag_prefix']}{manifest['version']}")
    print(
        f"controller_artifact_name={args.distribution}-controller-{manifest['version']}"
    )


def cmd_inspect(args: argparse.Namespace) -> None:
    """Wheel-content policy. What must ship, what must never, what may be
    required.

    Structurally the adapter lane's check. `allowed_requires` is expected to
    be empty for every entry in this file today: a universal facility that
    declares zero runtime dependencies (ADR-0070 § "composition_boundary") has
    nothing legitimate to require, so any `Requires-Dist` line at all is a
    defect worth failing the release over rather than reviewing away silently.
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

    # A migration lineage in a FACILITY wheel means the package became
    # stateful without changing its dossier — the exact drift ADR-0006 names.
    # `forbidden_prefixes` cannot catch it: the lineage would live UNDER the
    # import package, not at a fixed top-level prefix.
    for name in names:
        if "/migrations/" in name:
            problems.append(
                f"migration lineage in a universal-facility wheel: {name} — "
                "the package grew persistence without changing its "
                "classification"
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


def _strict_object(
    value: object, *, name: str, fields: frozenset[str]
) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReleaseRefused(f"{name} must be an object")
    document = dict(value)
    missing = sorted(fields - set(document))
    unknown = sorted(set(document) - fields)
    if missing or unknown:
        raise ReleaseRefused(
            f"{name} fields differ: missing={missing}, unknown={unknown}"
        )
    return document


def _strict_text(
    value: object,
    *,
    name: str,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReleaseRefused(f"{name} must be a non-empty, trimmed string")
    if pattern is not None and not pattern.fullmatch(value):
        raise ReleaseRefused(f"{name} has an invalid shape")
    return value


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReleaseRefused(f"{name} must be a positive integer")
    return value


def _sha256(path: Path, *, name: str) -> str:
    if not path.is_file():
        raise ReleaseRefused(f"{name} does not exist: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ReleaseRefused(f"cannot hash {name} {path}: {exc}") from exc
    return f"sha256:{digest.hexdigest()}"


def _controller_receipt(
    *,
    entry: dict,
    version: str,
    source_revision: str,
    release_run_id: int,
    tag: str,
    wheel: Path,
    launcher: Path,
) -> dict[str, object]:
    version = _strict_text(version, name="exact_version", pattern=_VERSION)
    source_revision = _strict_text(
        source_revision, name="source_revision", pattern=_REVISION
    )
    release_run_id = _positive_int(release_run_id, name="release_run_id")
    expected_tag = f"{entry['tag_prefix']}{version}"
    if tag != expected_tag:
        raise ReleaseRefused(f"controller tag is {tag!r}, expected {expected_tag!r}")

    normalized_distribution = entry["distribution"].replace("-", "_")
    expected_wheel_prefix = f"{normalized_distribution}-{version}-"
    if not wheel.name.startswith(expected_wheel_prefix) or wheel.suffix != ".whl":
        raise ReleaseRefused(
            f"controller wheel {wheel.name!r} must start with "
            f"{expected_wheel_prefix!r} and end in '.whl'"
        )

    launcher_source = entry["controller_launcher"]
    expected_launcher = (REPO_ROOT / launcher_source).resolve()
    if launcher.resolve() != expected_launcher:
        raise ReleaseRefused(
            f"controller launcher must be the allowlisted {launcher_source!r}, "
            f"got {launcher}"
        )

    return {
        "schema": CONTROLLER_RECEIPT_SCHEMA,
        "distribution": entry["distribution"],
        "exact_version": version,
        "artifact_sha256": _sha256(wheel, name="controller wheel"),
        "launcher_sha256": _sha256(launcher, name="controller launcher"),
        "source_revision": source_revision,
        "release_run_id": release_run_id,
        "tag": tag,
    }


def _load_controller_receipt(path: Path) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise ReleaseRefused(f"duplicate controller receipt field {key!r}")
            document[key] = value
        return document

    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseRefused(f"cannot read controller receipt {path}: {exc}") from exc

    receipt = _strict_object(
        raw, name="controller release receipt", fields=_RECEIPT_FIELDS
    )
    if receipt["schema"] != CONTROLLER_RECEIPT_SCHEMA:
        raise ReleaseRefused(
            f"controller receipt schema is {receipt['schema']!r}, expected "
            f"{CONTROLLER_RECEIPT_SCHEMA!r}"
        )
    _strict_text(receipt["distribution"], name="distribution")
    _strict_text(receipt["exact_version"], name="exact_version", pattern=_VERSION)
    _strict_text(receipt["artifact_sha256"], name="artifact_sha256", pattern=_DIGEST)
    _strict_text(receipt["launcher_sha256"], name="launcher_sha256", pattern=_DIGEST)
    _strict_text(receipt["source_revision"], name="source_revision", pattern=_REVISION)
    _positive_int(receipt["release_run_id"], name="release_run_id")
    _strict_text(receipt["tag"], name="tag")
    return receipt


def verify_controller_receipt(
    *,
    receipt_path: Path,
    wheel: Path,
    launcher: Path,
    distribution: str,
    version: str,
    source_revision: str,
    release_run_id: int,
    tag: str,
) -> dict[str, object]:
    """Strictly rebind a receipt to expected coordinates and observed bytes."""
    entry = resolve(distribution)
    manifest = _declared(entry)
    if manifest["version"] != version:
        raise ReleaseRefused(
            f"{distribution}: expected version {version!r} != package version "
            f"{manifest['version']!r}"
        )

    receipt = _load_controller_receipt(receipt_path)
    expected = {
        "distribution": distribution,
        "exact_version": version,
        "source_revision": source_revision,
        "release_run_id": release_run_id,
        "tag": tag,
    }
    for field, value in expected.items():
        if receipt[field] != value:
            raise ReleaseRefused(
                f"controller receipt {field} is {receipt[field]!r}, expected {value!r}"
            )
    if receipt["tag"] != f"{entry['tag_prefix']}{receipt['exact_version']}":
        raise ReleaseRefused("controller receipt tag does not match its exact version")
    observed_wheel = _sha256(wheel, name="controller wheel")
    observed_launcher = _sha256(launcher, name="controller launcher")
    if observed_wheel != receipt["artifact_sha256"]:
        raise ReleaseRefused(
            f"controller wheel hashes to {observed_wheel}, receipt requires "
            f"{receipt['artifact_sha256']}"
        )
    if observed_launcher != receipt["launcher_sha256"]:
        raise ReleaseRefused(
            f"controller launcher hashes to {observed_launcher}, receipt requires "
            f"{receipt['launcher_sha256']}"
        )
    return receipt


def cmd_create_controller_receipt(args: argparse.Namespace) -> None:
    entry = resolve(args.distribution)
    manifest = _declared(entry)
    if manifest["version"] != args.version:
        raise ReleaseRefused(
            f"{args.distribution}: requested version {args.version!r} != package "
            f"version {manifest['version']!r}"
        )

    wheel = Path(args.wheel).resolve()
    launcher = (REPO_ROOT / entry["controller_launcher"]).resolve()
    receipt = _controller_receipt(
        entry=entry,
        version=args.version,
        source_revision=args.source_revision,
        release_run_id=args.release_run_id,
        tag=args.tag,
        wheel=wheel,
        launcher=launcher,
    )

    bundle = Path(args.output_dir).resolve()
    if bundle.exists() and any(bundle.iterdir()):
        raise ReleaseRefused(f"controller release bundle is not empty: {bundle}")
    bundled_wheel = bundle / "wheel" / wheel.name
    bundled_launcher = bundle / "launcher" / launcher.name
    bundled_wheel.parent.mkdir(parents=True, exist_ok=True)
    bundled_launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wheel, bundled_wheel)
    shutil.copyfile(launcher, bundled_launcher)
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / CONTROLLER_RECEIPT_FILENAME).write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verify_controller_receipt(
        receipt_path=bundle / CONTROLLER_RECEIPT_FILENAME,
        wheel=bundled_wheel,
        launcher=bundled_launcher,
        distribution=args.distribution,
        version=args.version,
        source_revision=args.source_revision,
        release_run_id=args.release_run_id,
        tag=args.tag,
    )
    print(f"controller release receipt written to {bundle}")


def cmd_verify_controller_receipt(args: argparse.Namespace) -> None:
    verify_controller_receipt(
        receipt_path=Path(args.receipt).resolve(),
        wheel=Path(args.wheel).resolve(),
        launcher=Path(args.launcher).resolve(),
        distribution=args.distribution,
        version=args.version,
        source_revision=args.source_revision,
        release_run_id=args.release_run_id,
        tag=args.tag,
    )
    print("controller release receipt OK")


def _venv(path: Path) -> tuple[Path, Path]:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    bin_dir = path / ("Scripts" if sys.platform == "win32" else "bin")
    return bin_dir / "python", bin_dir / "pip"


def _bin(venv_python: Path, entry_point: str) -> Path:
    return venv_python.parent / (
        f"{entry_point}.exe" if sys.platform == "win32" else entry_point
    )


def _cli_smoke(venv_python: Path, entry_point: str, descriptor: Path) -> None:
    """The facility's answer to the adapter lane's `__all__` proof.

    A universal facility is CALLED through its console script, never
    imported, so the proof that matters is that the script runs — `--help`,
    which needs nothing but a working entry point, and `validate` against a
    real descriptor, which needs the whole parse/refuse path to be intact.
    An importable module proves nothing about a console script: `pip install`
    can produce a wheel whose `[project.scripts]` entry point is broken (a
    missing `console_scripts` line, a typo'd target) while `import
    dotmac_deployment_foundation` still succeeds cleanly.
    """
    script = _bin(venv_python, entry_point)
    if not script.is_file():
        raise ReleaseRefused(
            f"{entry_point}: no console script at {script} after install — "
            "the wheel's entry point is broken"
        )
    subprocess.run([str(script), "--help"], check=True, capture_output=True)
    subprocess.run(
        [str(script), "-f", str(descriptor), "validate"],
        check=True,
        capture_output=True,
    )


def cmd_verify_wheel(args: argparse.Namespace) -> None:
    """Pre-publish smoke: the built bytes, installed clean, and the CLI they
    expose, run against a real descriptor.

    `--descriptor` is the Starter's own `deploy/product.toml`: this package's
    own tree (`packages/dotmac-deployment-foundation/`) carries no fixture
    descriptor of its own — it is a library and a CLI, not a product — and
    `deploy/product.toml` is deliberately the smallest COMPLETE descriptor in
    this repository (its own header says so). It needs no digest realism to
    prove the CLI parses and validates a real descriptor end to end; digest
    realism is `require-real-digests`'s job in `deployment-conformance.yml`,
    a separate concern from "does the entry point work".
    """
    entry = resolve(args.distribution)
    _declared(entry)

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
        _cli_smoke(python, entry["entry_point"], Path(args.descriptor))
    print(f"{entry['distribution']}: wheel CLI smoke OK")


def cmd_verify_registry(args: argparse.Namespace) -> None:
    """Post-publish: install the PUBLISHED release from the private index and
    re-run the same CLI smoke.

    `--index` carries the authenticated simple-index URL. An exact pin only: a
    range would let this pass against a version nobody published in this run.

    Unlike `release_adapter.py verify-registry`, there is deliberately NO
    `--extra-index-url` here. `dotmac-deployment-foundation` declares zero
    runtime dependencies (its own `pyproject.toml` names nothing beyond
    `python`), so there is nothing for pip to need from the public index —
    pairing one in would only add a public name to the resolution the private
    package never asked for.
    """
    distribution, _, version = args.pin.partition("==")
    if not version:
        raise ReleaseRefused(f"{args.pin!r} is not an exact pin (name==version)")
    entry = resolve(distribution)

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(Path(tmp) / "venv")
        subprocess.run(
            [
                str(pip),
                "install",
                "--quiet",
                "--index-url",
                args.index,
                args.pin,
            ],
            check=True,
        )
        _cli_smoke(python, entry["entry_point"], Path(args.descriptor))
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

    p = sub.add_parser("verify-wheel", help="install the built wheel and smoke its CLI")
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.add_argument("--descriptor", required=True)
    p.set_defaults(func=cmd_verify_wheel)

    p = sub.add_parser(
        "create-controller-receipt",
        help="copy the exact controller wheel and launcher and bind their receipt",
    )
    p.add_argument("distribution")
    p.add_argument("--version", required=True)
    p.add_argument("--source-revision", required=True)
    p.add_argument("--release-run-id", required=True, type=int)
    p.add_argument("--tag", required=True)
    p.add_argument("--wheel", required=True)
    p.add_argument("--output-dir", required=True)
    p.set_defaults(func=cmd_create_controller_receipt)

    p = sub.add_parser(
        "verify-controller-receipt",
        help="verify strict release coordinates and exact wheel/launcher bytes",
    )
    p.add_argument("distribution")
    p.add_argument("--version", required=True)
    p.add_argument("--source-revision", required=True)
    p.add_argument("--release-run-id", required=True, type=int)
    p.add_argument("--tag", required=True)
    p.add_argument("--receipt", required=True)
    p.add_argument("--wheel", required=True)
    p.add_argument("--launcher", required=True)
    p.set_defaults(func=cmd_verify_controller_receipt)

    p = sub.add_parser(
        "verify-registry",
        help="install an exact pin from the index and smoke its CLI",
    )
    p.add_argument("--index", required=True)
    p.add_argument("--pin", required=True)
    p.add_argument("--descriptor", required=True)
    p.set_defaults(func=cmd_verify_registry)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
