#!/usr/bin/env python3
"""Resolve and conformance-gate a CONNECTOR PLUGIN release.

The fail-closed half of the connector lane, and the third sibling of
`scripts/release_module.py` and `scripts/release_adapter.py`. Every subcommand
refuses rather than warns: a release step that printed a warning and continued
would publish the thing it just objected to.

## The shape this lane governs

A connector is neither of the two shapes already covered, and the reasoning for
a third file is in `.github/release-connectors.json`'s own header. In one line:
a MODULE owns a schema and a lineage, an ADAPTER is a library a product CALLS
and is proved by its public surface, and a CONNECTOR is DISCOVERED and EXECUTED
by a control plane through the `dotmac_integration.connectors` entry-point
group. Discovery is fail-closed as a SET — one malformed connector refuses the
whole registry rather than silently offering the rest — so a connector that
conforms only in the presence of its neighbours is not independently
releasable, and the gate has to say so before publication rather than after.

## A release PROFILE, not a fourth classification

`connector-plugin` names this release profile and the architectural role. It is
NOT an `EXTRACTION.toml` classification: a connector's dossier declares
`stateless-protocol-adapter`, the same as any other distribution a product does
not install, because the four properties that classification governs (no
`ModuleManifest`, no lineage, no ledger allocation, no persistence import) are
exactly the four a connector has. Adding a synonym would mean amending ADR-0006
and the global validator to describe the same properties twice.

The consequence is the important part: **the classification does not separate
this lane from the adapter lane.** It is a floor they share. What separates them
is the strictness below, none of which the adapter lane asks for — exactly one
connector entry point, a PUBLISHED integration floor, connector-key
consistency, installed-wheel SPI conformance, and no persistence, secret
material or private retry/checkpoint engine. `dotmac-auth-oidc` carries the
identical classification and is refused here, which is what makes that claim
checkable rather than asserted.

## Where the floor lives, and why that is the interesting check

A module floors on a KERNEL release. A connector floors on a
`dotmac-integration` release, because the SPI it targets is that module's
contract. `release-modules.json` already states the rule that governs both:

    A floor naming an unpublished version cannot be resolved by an installer,
    so it is not a floor at all.

Here it is ENFORCED rather than only written down. `resolve` refuses an
`integration_floor` for which no release tag exists, using the same oracle as
`scripts/declared_publication_sweep.py` — the tag the release workflow writes
after `verify-registry` has installed the exact published version from the index.

This bites today, and that is the point rather than an inconvenience:
`dotmac-integration` declares `0.1.0a2` while only `0.1.0a1` is tagged. So the
first connector may floor at `a1` — inheriting a published module whose
`run_effect_once` raises `TypeError` on its first call — or wait for `a2` to be
released. It may NOT floor at `a2`, because nothing can install `a2`. A gate
that let it would produce a wheel whose dependency resolution fails for every
consumer, discovered at install time by someone who did not write it.

Stdlib only, deliberately: `resolve` and `conformance` run before anything is
installed. `verify-wheel` runs after, and is the only subcommand that imports
the connector or the kit.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tomllib
import zipfile
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from declared_publication_sweep import git_tags
from release_module import ReleaseRefused, secret_shaped

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[1]
ALLOWLIST: Final = REPO_ROOT / ".github" / "release-connectors.json"
WORKFLOW: Final = REPO_ROOT / ".github" / "workflows" / "release-connector.yml"
INTEGRATION_TAG_PREFIX: Final = "dotmac-integration-v"

#: Facts only a STATEFUL module has. An entry declaring one is in the wrong
#: lane, and accepting it here would publish a module while skipping every
#: namespace, lineage and dual-plane gate the module lane performs. A connector
#: owns no rows: its state lives in the control plane's `mod_intg`, which
#: `dotmac-integration` owns and a connector never writes directly.
STATEFUL_ONLY_FIELDS: Final = ("db_schema", "manifest_attr", "kernel_floor")

#: Module stems that mean a connector has built its OWN copy of machinery the
#: control plane owns. Matched on the module name because a private engine is a
#: module a connector declares, not a function it calls: importing
#: `dotmac_integration.retry` is the correct behaviour and must not trip this.
PRIVATE_ENGINE_MARKERS: Final = ("retry", "backoff", "checkpoint", "dead_letter")

#: What a connector entry must declare. Required rather than optional for the
#: reason the adapter lane records about the module lane: optionality is not
#: scoped to the package that needs it, so once `spi_range` may be absent a
#: connector whose registration was dropped in a bad merge stops being refused
#: and starts being treated as something else.
REQUIRED_FIELDS: Final = (
    "package_dir",
    "import_name",
    "plugin_attr",
    "connector_key",
    "spi_range",
    "integration_floor",
    "tag_prefix",
    "wheel_contents",
)


def load_policy() -> dict:
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    conformance = data.get("conformance")
    if not isinstance(conformance, dict):
        raise ReleaseRefused(f"{ALLOWLIST.name}: 'conformance' must be an object")
    connectors = data.get("connectors")
    if not isinstance(connectors, dict):
        raise ReleaseRefused(f"{ALLOWLIST.name}: 'connectors' must be an object")
    return data


def load_allowlist() -> dict[str, dict]:
    return load_policy()["connectors"]


def _refuse_unpublished_floor(distribution: str, floor: str, tags: set[str]) -> None:
    """A floor an installer cannot resolve is not a floor.

    Separated so its sensitivity proof can drive it with a tag set rather than a
    checkout — the failure it guards against is a version that exists in the
    repository and nowhere else, which is precisely what a checkout cannot show.
    """
    if f"{INTEGRATION_TAG_PREFIX}{floor}" in tags:
        return
    published = sorted(
        tag.removeprefix(INTEGRATION_TAG_PREFIX)
        for tag in tags
        if tag.startswith(INTEGRATION_TAG_PREFIX)
    )
    raise ReleaseRefused(
        f"{distribution}: integration_floor {floor!r} has no release tag, so no "
        "installer can resolve it — that is a declared version, not a floor. "
        f"Published dotmac-integration versions: {', '.join(published) or '(none)'}. "
        "Either floor at a published release or release the one you need first; "
        "do NOT lower the module's declared version to match (see "
        "docs/inventories/declared-publication-baseline.json)."
    )


def resolve(distribution: str, *, tags: set[str] | None = None) -> dict:
    """The gate. Every other subcommand takes its facts from this result."""
    policy = load_policy()
    connectors = policy["connectors"]
    entry = connectors.get(distribution)
    if entry is None:
        listed = (
            ", ".join(sorted(connectors)) if connectors else "(none — the lane is shut)"
        )
        raise ReleaseRefused(
            f"{distribution!r} is not an allowlisted connector plugin. "
            f"Publishable connectors are: {listed}. Adding one is a reviewed "
            f"change to .github/{ALLOWLIST.name}, not a dispatch input — and "
            "absence is the safety mechanism, not an oversight."
        )

    misplaced = [field for field in STATEFUL_ONLY_FIELDS if field in entry]
    if misplaced:
        raise ReleaseRefused(
            f"{distribution}: connector entry declares {', '.join(misplaced)} — "
            "those are STATEFUL facts. A package with a schema, a manifest "
            "attribute or a kernel floor is a module and belongs in "
            ".github/release-modules.json, where the namespace and lineage "
            "gates can actually check it. A connector's state lives in the "
            "control plane's mod_intg, which it never writes directly."
        )

    missing = [field for field in REQUIRED_FIELDS if field not in entry]
    if missing:
        raise ReleaseRefused(
            f"{distribution}: connector entry is missing {', '.join(missing)}. "
            "Every field is required; an absent one would be read downstream as "
            "'unknown' rather than 'refused'."
        )

    # First-party connectors live under Starter `packages/` with a name that
    # announces what they are. Enforced rather than conventional: a connector
    # released from an arbitrary directory would be governed by this lane while
    # looking, to anyone reading the tree, like something else. Later
    # third-party connectors may live in their own repositories under the same
    # governance profile; this lane governs only the ones Starter builds.
    prefix = policy["conformance"]["package_dir_prefix"]
    if not entry["package_dir"].startswith(prefix):
        raise ReleaseRefused(
            f"{distribution}: package_dir {entry['package_dir']!r} does not "
            f"start with {prefix!r}. First-party connectors are built, tested, "
            "versioned and published from Starter `packages/` as independent "
            "distributions — neither dotmac-integration nor the Integrator "
            "assembly may import one, so the path is how a reader tells a "
            "connector from a module without opening its dossier."
        )

    package_dir = REPO_ROOT / entry["package_dir"]
    if not (package_dir / "pyproject.toml").is_file():
        raise ReleaseRefused(
            f"{distribution}: allowlisted package_dir {entry['package_dir']!r} "
            "has no pyproject.toml"
        )

    # The GOVERNED classification — `stateless-protocol-adapter`, shared with
    # the adapter lane and NOT a fourth ADR-0006 classification. `connector-
    # plugin` is the name of this RELEASE PROFILE and of the architectural role;
    # promoting it to a dossier classification would need ADR-0006 and the
    # global validator amended to describe the same four properties twice.
    #
    # So this check is a FLOOR both lanes share, not the thing that separates
    # them. What separates them is everything else `resolve` and `conformance`
    # demand and the adapter lane does not: exactly one connector entry point, a
    # PUBLISHED integration floor, connector-key consistency, installed-wheel SPI
    # conformance, and no persistence, secret material or private
    # retry/checkpoint engine. `dotmac-auth-oidc` carries this exact
    # classification and is still refused here — proved in
    # `test_connector_release_policy.py`.
    dossier_path = package_dir / "EXTRACTION.toml"
    if not dossier_path.is_file():
        raise ReleaseRefused(
            f"{distribution}: no EXTRACTION.toml — the connector lane resolves "
            "its classification from the dossier and cannot proceed without one"
        )
    dossier = tomllib.loads(dossier_path.read_text(encoding="utf-8"))
    declared = dossier.get("classification")
    expected = policy["conformance"]["classification"]
    if declared != expected:
        raise ReleaseRefused(
            f"{distribution}: EXTRACTION.toml declares classification "
            f"{declared!r}, but this lane publishes only {expected!r}. Releasing "
            "it here would skip the checks its own classification requires."
        )

    _refuse_unpublished_floor(
        distribution,
        entry["integration_floor"],
        set(git_tags(REPO_ROOT)) if tags is None else tags,
    )

    return {**entry, "distribution": distribution, "package_path": package_dir}


def _declared(entry: dict) -> dict:
    return tomllib.loads(
        (entry["package_path"] / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]


def entry_point_registration(poetry: dict, group: str) -> dict[str, str]:
    """The connector's registrations in the discovery group.

    Poetry writes them under `[tool.poetry.plugins."<group>"]`. Read here rather
    than from installed metadata because `resolve` runs before installation —
    the installed form is checked again in `verify-wheel`, and the two must
    agree.
    """
    return dict(poetry.get("plugins", {}).get(group, {}))


def cmd_resolve(args: argparse.Namespace) -> None:
    entry = resolve(args.distribution)
    poetry = _declared(entry)
    policy = load_policy()["conformance"]

    if poetry["name"] != args.distribution:
        raise ReleaseRefused(
            f"pyproject declares {poetry['name']!r}, dispatched "
            f"{args.distribution!r}"
        )
    if args.version and poetry["version"] != args.version:
        raise ReleaseRefused(
            f"{args.distribution}: dispatched version {args.version!r} != package "
            f"version {poetry['version']!r}. The version is not inferred; fix "
            "one of them."
        )

    registrations = entry_point_registration(poetry, policy["entry_point_group"])
    if len(registrations) != 1:
        raise ReleaseRefused(
            f"{args.distribution}: registers {len(registrations)} entry points in "
            f"{policy['entry_point_group']!r}, expected exactly 1. Discovery is "
            "fail-closed as a set and keys must be unambiguous — a distribution "
            "shipping two connectors makes 'which one failed' unanswerable at "
            "boot, and a distribution shipping none is invisible to the control "
            "plane it was built for."
        )
    registered_key = next(iter(registrations))
    if registered_key != entry["connector_key"]:
        raise ReleaseRefused(
            f"{args.distribution}: entry point registers connector_key "
            f"{registered_key!r} but the allowlist says {entry['connector_key']!r}. "
            "A key mismatch is invisible until two connectors collide in a live "
            "registry, where the winner depends on install order."
        )
    registered_target = registrations[registered_key]
    expected_target = f"{entry['import_name']}:{entry['plugin_attr']}"
    if registered_target != expected_target:
        raise ReleaseRefused(
            f"{args.distribution}: entry point target {registered_target!r} != "
            f"allowlisted {expected_target!r}. The release proof must execute "
            "the same object package discovery will load."
        )

    for key in ("package_dir", "import_name", "tag_prefix", "connector_key"):
        print(f"{key}={entry[key]}")
    print(f"integration_floor={entry['integration_floor']}")
    print(f"spi_range={entry['spi_range']}")
    print(f"version={poetry['version']}")
    print(f"tag={entry['tag_prefix']}{poetry['version']}")


def cmd_conformance(args: argparse.Namespace) -> None:
    """Static conformance: the obligations checkable before installation.

    The EXECUTABLE half is `verify-wheel`, which runs the shipped kit against
    the installed bytes. Both halves exist because the source tree and the wheel
    are different objects and only one of them gets published — but a defect
    findable at PR time should not wait for a release run to surface.
    """
    entry = resolve(args.distribution)
    poetry = _declared(entry)
    policy = load_policy()["conformance"]
    problems: list[str] = []

    floor = f">={entry['integration_floor']}"
    declared_floor = poetry.get("dependencies", {}).get("dotmac-integration")
    if declared_floor != floor:
        problems.append(
            f"pyproject declares dotmac-integration {declared_floor!r}, allowlist "
            f"says {floor!r} — the floor a consumer resolves must be the floor "
            "this gate checked"
        )

    package_src = entry["package_path"] / "src" / entry["import_name"]
    if not package_src.is_dir():
        problems.append(f"no source package at src/{entry['import_name']}")
    elif (package_src / "migrations").exists():
        problems.append(
            "ships a migrations/ directory — a connector owns no schema and no "
            "lineage; its state lives in the control plane's mod_intg"
        )

    # ADR-0024 section 7: a connector holds a REFERENCE to credential material,
    # never the value. Reuses the module lane's name-shape list rather than a
    # second copy, for the reason that lane's docstring gives: two copies drift,
    # and the drift is silent in the worst direction.
    if package_src.is_dir():
        leaked = secret_shaped(
            str(path.relative_to(package_src))
            for path in sorted(package_src.rglob("*"))
            if path.is_file()
        )
        if leaked:
            problems.append(f"secret-shaped files ship in the wheel: {leaked}")

        # No PRIVATE retry/checkpoint engine. Delivery retry and feed
        # checkpoints are two of the six categories
        # `docs/inventories/external-connector-sources.md` ratchets OUT of
        # products and into the control plane; a connector that rebuilds them
        # locally moves the duplication instead of retiring it, and the fleet
        # count would not even see it because connectors are not in
        # `RUNTIME_ROOTS`. Declaring a MODULE is what makes it private — a
        # connector calling the control plane's `retry`/`execution` helpers is
        # doing exactly the right thing, so the check is on ownership, not on
        # the word.
        engines = sorted(
            str(path.relative_to(package_src))
            for path in package_src.rglob("*.py")
            if any(marker in path.stem for marker in PRIVATE_ENGINE_MARKERS)
        )
        if engines:
            problems.append(
                f"ships what looks like its own retry/checkpoint engine: {engines}. "
                "Delivery retry and feed checkpoints belong to the control plane "
                "(ADR-0024 section 6) — call dotmac_integration's, do not rebuild "
                "them behind a plugin boundary where the fleet ratchet cannot see "
                "them"
            )

    for name in policy["required_assertions"]:
        if not name.startswith("assert_"):
            problems.append(f"{name!r} is not an assertion the kit can enforce")

    if problems:
        raise ReleaseRefused(
            f"{args.distribution}: static conformance failed:\n  "
            + "\n  ".join(problems)
        )
    print(f"{args.distribution}: static conformance OK")


def cmd_inspect(args: argparse.Namespace) -> None:
    """Inspect the built wheel against the connector's reviewed closure."""
    entry = resolve(args.distribution)
    policy = entry["wheel_contents"]
    wheels = sorted(pathlib.Path(args.dist).glob("*.whl"))
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
        if "/migrations/" in name:
            problems.append(f"migration lineage in a stateless connector: {name}")

    requires = [
        line.split(":", 1)[1].strip()
        for line in meta_text.splitlines()
        if line.startswith("Requires-Dist:")
    ]
    allowed = {name.lower() for name in policy["allowed_requires"]}
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
        if name not in allowed:
            problems.append(f"dependency outside the allowed closure: {requirement!r}")
    problems.extend(secret_shaped(names))
    if problems:
        raise ReleaseRefused(
            f"{wheel.name} fails the wheel-content policy:\n  - "
            + "\n  - ".join(problems)
        )
    print(f"{wheel.name}: content policy OK ({len(names)} entries)")


def cmd_verify_wheel(args: argparse.Namespace) -> None:
    """Executable conformance against the INSTALLED bytes.

    `assert_plugin_conforms` subsumes `assert_connector_conforms`, so metadata
    and executability are proved together: a distribution that declares a
    capability it cannot hand back a handler for passes every metadata check and
    fails at the first dispatch instead.

    Run in a subprocess against the venv the wheel was installed into, so the
    thing being certified is the artifact rather than the checkout.
    """
    entry = resolve(args.distribution)
    policy = load_policy()["conformance"]
    program = CONFORMANCE_PROGRAM.format(
        kit=policy["kit_module"],
        import_name=entry["import_name"],
        plugin_attr=entry["plugin_attr"],
        connector_key=entry["connector_key"],
        spi_range=entry["spi_range"],
        version=_declared(entry)["version"],
    )
    result = subprocess.run(  # nosec B603 — fixed argv, no shell
        [args.python, "-c", program], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ReleaseRefused(
            f"{args.distribution}: conformance failed against the installed "
            f"wheel:\n{result.stdout}{result.stderr}"
        )
    print(result.stdout.strip())


#: Kept as a module constant rather than inline so a test can assert what the
#: verification actually does, without installing a connector that does not yet
#: exist.
CONFORMANCE_PROGRAM: Final = """
import sys
from {kit} import assert_plugin_conforms
import {import_name} as package

if "src" in getattr(package, "__file__", ""):
    raise SystemExit("imported from the source tree, not the installed wheel")

plugin = getattr(package, "{plugin_attr}")
plugin = plugin() if isinstance(plugin, type) else plugin
assert_plugin_conforms(plugin)

key = plugin.manifest.connector_key
if key != "{connector_key}":
    raise SystemExit(f"manifest connector_key {{key!r}} != allowlisted "
                     "{connector_key!r}")
if str(plugin.manifest.spi_range) != "{spi_range}":
    raise SystemExit(f"manifest SPI range {{plugin.manifest.spi_range!s}} != "
                     "allowlisted {spi_range!r}")
if package.__version__ != "{version}" or plugin.manifest.version != "{version}":
    raise SystemExit("package, manifest and allowlisted versions disagree")
print("{import_name}: conformance OK ({connector_key})")
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("resolve", help="gate on the allowlist and emit its facts")
    p.add_argument("distribution")
    p.add_argument("--version", default="")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("conformance", help="static conformance, before install")
    p.add_argument("distribution")
    p.set_defaults(func=cmd_conformance)

    p = sub.add_parser("inspect", help="inspect the built wheel contents")
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("verify-wheel", help="run the SPI kit on the installed bytes")
    p.add_argument("distribution")
    p.add_argument("--python", default=sys.executable)
    p.set_defaults(func=cmd_verify_wheel)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
