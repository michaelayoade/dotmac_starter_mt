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
import json
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
            f"{args.distribution}: dispatched version {args.version!r} != "
            f"package version {manifest['version']!r}. The version is not "
            "inferred; fix one of them."
        )

    # Consumed by the workflow via $GITHUB_OUTPUT. Deliberately no db_schema,
    # manifest_attr or kernel_floor — a facility has none, and emitting an
    # empty value would let a later step read it as "unknown" rather than
    # "absent".
    for key in ("package_dir", "entry_point", "tag_prefix"):
        print(f"{key}={entry[key]}")
    print(f"version={manifest['version']}")
    print(f"tag={entry['tag_prefix']}{manifest['version']}")


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


def _venv(path: Path) -> tuple[Path, Path]:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    bin_dir = path / ("Scripts" if sys.platform == "win32" else "bin")
    return bin_dir / "python", bin_dir / "pip"


def _bin(venv_python: Path, entry_point: str) -> Path:
    return venv_python.parent / (
        f"{entry_point}.exe" if sys.platform == "win32" else entry_point
    )


#: A concrete host, stated by this caller. `render_execution_plan` refuses an
#: empty target — "a plan with no target is a plan that authorizes every host" —
#: and a target derived from the descriptor would make the comparison below
#: compare the descriptor with itself and pass for every input.
SMOKE_TARGET: Final = "release-smoke-host"

#: The execution-plan proof, executed by the INSTALLED interpreter against the
#: INSTALLED bytes. It reads the document and the digest the console script
#: just printed and re-derives both through the library surface two other
#: repositories bind to.
#:
#: Written as a probe rather than as `import`-and-`hasattr` on purpose. A name
#: present in a wheel's source is not a contract that works: `execution_plan.py`
#: being an entry in the zip — which is all `inspect` can see — says nothing
#: about whether the module imports, and `hasattr(module, "...")` says nothing
#: about whether the thing it names produces the value Control freezes.
#:
#: `ExecutionPlanDigestV1` is deliberately NOT looked for as a name here, in any
#: form — not as an attribute, and not by comparing a constant's text against
#: the literal. It names a VALUE, not an importable object, so both of those
#: would be checking the spelling with extra steps, and a wheel can carry the
#: right spelling and produce nothing. What is checked instead is the value: the
#: console script must print a `sha256:` digest, and `execution_plan_digest` and
#: `FoundationExecutionPlanV1.digest()` must each INDEPENDENTLY re-derive that
#: exact digest over the exact document the console script printed.
_EXECUTION_PLAN_PROBE: Final = """\
import json
import re
import sys

from dotmac_deployment_foundation.execution_plan import (
    EXECUTION_PLAN_SCHEMA,
    FoundationExecutionPlanV1,
    execution_plan_digest,
)

document = json.loads(open(sys.argv[1], encoding="ascii").read())
printed = open(sys.argv[2], encoding="ascii").read().strip()

problems = []

if document.get("schema") != EXECUTION_PLAN_SCHEMA:
    problems.append(
        "the console script emitted schema %r, the installed module names %r"
        % (document.get("schema"), EXECUTION_PLAN_SCHEMA)
    )

# The document type, resolved as a TYPE: the class must round-trip the very
# document its own CLI printed. A class that merely exists under the right name
# would pass an import check and fail here.
rebuilt = FoundationExecutionPlanV1(
    product=document["product"],
    target=document["target"],
    operation=document["operation"],
    foundation_version=document["foundation_version"],
    image_reference=document["image_reference"],
    image_digest=document["image_digest"],
    source_revision=document["source_revision"],
    manifest_digest=document["manifest_digest"],
    descriptor_digest=document["descriptor_digest"],
    strategy=document["strategy"],
    environment_inventory=tuple(document["environment_inventory"]),
    steps=tuple(
        (
            step["kind"],
            step["target"],
            tuple(step["command"]),
            step["timeout_seconds"],
            step["retries"],
        )
        for step in document["steps"]
    ),
)
if rebuilt.as_document() != document:
    problems.append(
        "FoundationExecutionPlanV1 does not round-trip the document its own "
        "CLI printed"
    )

# The digest VALUE, re-derived twice and never asserted by name.
if not re.fullmatch(r"sha256:[0-9a-f]{64}", printed):
    problems.append("the console script printed %r, not a sha256 digest" % printed)
if execution_plan_digest(document) != printed:
    problems.append(
        "execution_plan_digest re-derived %r over the document the console "
        "script printed, which printed %r"
        % (execution_plan_digest(document), printed)
    )
if rebuilt.digest() != printed:
    problems.append(
        "FoundationExecutionPlanV1.digest() re-derived %r, the console script "
        "printed %r" % (rebuilt.digest(), printed)
    )

if problems:
    sys.stderr.write("\\n".join(problems) + "\\n")
    raise SystemExit(1)

sys.stdout.write(printed + "\\n")
"""


def _execution_plan_smoke(
    venv_python: Path, script: Path, descriptor: Path, workdir: Path
) -> str:
    """Prove the EXECUTION-PLAN contract on the installed artifact.

    Three other repositories are downstream of this one value. Platform CP
    submits an `ExecutionPlanDigestV1` and Control freezes and signs it, so a
    wheel that installs cleanly, answers `--help` and validates a descriptor —
    everything the smoke above checks — and then cannot render a plan leaves
    both of them unable to produce the value at all, while every gate in the
    release lane stays green.

    `inspect`'s `wheel_contents.required` list cannot close this: it asserts
    that `dotmac_deployment_foundation/execution_plan.py` is an entry in the
    zip. A file present in an archive is a spelling. This runs the path.
    """
    document_path = workdir / "execution-plan.json"
    digest_path = workdir / "execution-plan.digest"

    for fmt, destination in (("json", document_path), ("digest", digest_path)):
        rendered = subprocess.run(
            [
                str(script),
                "-f",
                str(descriptor),
                "execution-plan",
                "--target",
                SMOKE_TARGET,
                "--operation",
                "deploy",
                "--format",
                fmt,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if rendered.returncode != 0:
            raise ReleaseRefused(
                f"the installed `{script.name} execution-plan --format {fmt}` "
                f"path exited {rendered.returncode}. The wheel cannot produce "
                "the value Platform CP submits and Control freezes.\n"
                f"{rendered.stderr.strip()}"
            )
        destination.write_text(rendered.stdout, encoding="ascii")

    probe = workdir / "execution_plan_probe.py"
    probe.write_text(_EXECUTION_PLAN_PROBE, encoding="utf-8")
    checked = subprocess.run(
        [str(venv_python), str(probe), str(document_path), str(digest_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if checked.returncode != 0:
        raise ReleaseRefused(
            "the installed bytes do not carry a working execution-plan "
            "contract:\n  - " + "\n  - ".join(checked.stderr.strip().splitlines())
        )
    return checked.stdout.strip()


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

    That argument is unchanged, and it is one-directional: it says an import
    proof cannot stand in for a console-script proof. The converse is also
    true, and `execution-plan` is where it bites — `--help` and `validate`
    both pass on a wheel whose `execution_plan` module does not import, and
    the failure would surface in Platform CP and in Control rather than here.
    So the console script stays the surface, and the execution-plan path is
    RUN rather than assumed.
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

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        digest = _execution_plan_smoke(venv_python, script, descriptor, Path(tmp))
    print(f"{entry_point}: execution-plan contract OK ({digest})")


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
