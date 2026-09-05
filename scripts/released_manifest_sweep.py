#!/usr/bin/env python3
"""A published version's manifest is its contract, and a contract does not move.

## The hole this closes

An installation adopts a connector by MANIFEST DIGEST. `mod_intg` stores the
digest a binding was enabled against, and `dotmac_integration.spi
.accepts_manifest_digest` answers "is this installation still adoptable?" by
asking whether the plugin still carries a manifest with that digest — the
current one, or one of `historical_manifests`.

Nothing stopped a change from editing the manifest of an ALREADY-PUBLISHED
version. Do that and one version name means two different contracts: the wheel
on the registry publishes digest A, the repository publishes digest B under the
same version string, and an installation adopted against A is unidentifiable —
`accepts_manifest_digest` returns False for a digest the connector really did
publish, and the operator is told to re-approve a binding whose contract never
actually changed.

It was not theoretical. On this branch's base FIVE connectors declared their
published version while carrying a different manifest, and two of them —
`dotmac-connector-flutterwave` and `dotmac-connector-remita` — carried TWO
manifests both claiming the same version string, the published one preserved in
`historical_manifests` and a superseding one as `manifest`. Every existing gate
was green: the version-identity guard compares three version SURFACES, the
publication sweep compares a version to a TAG, and neither reads a manifest.

## Why a checked-in ledger rather than deriving from tags

Deriving is possible — `--verify-tags` does exactly that, and every recorded
digest here was produced by it. It is not admissible as THE gate, for the
reason `docs/inventories/declared-publication-baseline.json` already gives
about querying the index: a gate that cannot run is not a gate.

  * Deriving needs the TAGS. `actions/checkout` fetches none by default, so on
    the `quality` matrix — the cheap static job every pull request runs — the
    derivation would refuse on every run. Refusing-as-passing is how
    `test_declared_publication.py` spent its life green while checking nothing.
  * Deriving needs to IMPORT code from an arbitrary older revision, against
    today's `dotmac_integration.spi`. That works today for all eleven connector
    tags, and it is exactly the kind of thing that stops working silently.
  * A digest in a file is REVIEWABLE. An edit to a published manifest shows up
    in the diff as a digest change beside the capability list that produced it,
    at the moment somebody can still object. A derivation shows nothing until
    CI runs, and shows a hash rather than a contract.

So the ledger is the primary and it is offline-runnable with no git at all:
`--check` reads the ledger and the working tree, and nothing else. The
derivation is the CROSS-CHECK (`--verify-tags`), which is what stops the ledger
being brought into line with a bad edit in one commit — doctoring it then
requires moving a tag on `origin`. Neither half is redundant; this is the same
two-half shape `tests/architecture/test_released_migrations.py` uses for
released migration bytes, applied to the contract rather than the DDL.

## Two directions, both failures (ADR-0018)

  * a recorded version whose digest the tree no longer produces — the manifest
    was EDITED, or the historical manifest was DROPPED. Rising.
  * a version the tree names that is neither recorded-published nor the single
    currently-declared version — a historical manifest invented for a version
    with no verified publication. Falling.
  * a published tag with no ledger row (`--verify-tags`) — a release recorded
    nowhere.
  * a ledger row whose tag does not exist or peels elsewhere
    (`--verify-tags`) — coordinates that are not coordinates (AGENTS.md rule
    30).

## Scope, and what is UNMONITORED rather than exempt

CONNECTOR distributions listed in `.github/release-connectors.json`. That file
already owns the coordinates — import name, plugin attribute, tag prefix — so
they are read from it rather than copied here; a connector removed from the
lane while still holding ledger rows FAILS rather than falling silent.

Installable MODULES are unmonitored here, and named as such by
`test_the_unmonitored_distributions_are_named`. `ModuleManifest` exposes no
digest and no installation adopts a module by one: a module's published bytes
are held immutable by the released-migration map instead. If a module manifest
ever grows an adopted digest, it enrols here by existing in the ledger — the
reconciler is keyed by distribution, not by "connector".

Stdlib plus the packages themselves: `--check` imports each connector's plugin,
which is the ONE owner of the digest rule (`ConnectorManifest.digest`).
Re-deriving the hash here would make this file a second writer of the contract
identity, which is the failure ADR-0024 names.

    python scripts/released_manifest_sweep.py --check
    python scripts/released_manifest_sweep.py --verify-tags
    python scripts/released_manifest_sweep.py --record --tag <tag>
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib
from typing import Final

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[1]
LANE: Final = REPO_ROOT / ".github" / "release-connectors.json"
LEDGER: Final = REPO_ROOT / "docs" / "inventories" / "released-manifest-digests.json"

#: Printed by the probe subprocess `--verify-tags` runs against tagged source.
#: A subprocess, because the tagged package and the working-tree package claim
#: the SAME import name — importing both into one interpreter would hand
#: whichever won `sys.path` back for both, and the check would compare a
#: manifest to itself.
_PROBE: Final = """
import importlib, json, sys
module = importlib.import_module(sys.argv[1])
plugin = getattr(module, sys.argv[2])
print(json.dumps([
    {
        "version": manifest.version,
        "digest": manifest.digest,
        "spi_range": str(manifest.spi_range),
        "capabilities": sorted(manifest.capability_ids),
    }
    for manifest in (plugin.manifest, *plugin.historical_manifests)
]))
"""

_SHA1: Final = re.compile(r"^[0-9a-f]{40}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


class SweepRefused(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"released-manifest sweep: {message}")


# ── Inputs ──────────────────────────────────────────────────────────────────


def lane(repo_root: pathlib.Path = REPO_ROOT) -> dict[str, dict]:
    """The connector release lane, which OWNS each distribution's coordinates."""
    path = repo_root / LANE.relative_to(REPO_ROOT)
    if not path.is_file():
        raise SweepRefused(f"{LANE.relative_to(REPO_ROOT)} is missing")
    connectors = json.loads(path.read_text(encoding="utf-8"))["connectors"]
    return {
        name: entry for name, entry in connectors.items() if not name.startswith("$")
    }


def ledger(repo_root: pathlib.Path = REPO_ROOT) -> dict[str, dict[str, dict]]:
    path = repo_root / LEDGER.relative_to(REPO_ROOT)
    if not path.is_file():
        raise SweepRefused(f"{LEDGER.relative_to(REPO_ROOT)} is missing")
    return json.loads(path.read_text(encoding="utf-8"))["released"]


def declared_version(distribution: str, repo_root: pathlib.Path = REPO_ROOT) -> str:
    pyproject = repo_root / "packages" / distribution / "pyproject.toml"
    if not pyproject.is_file():
        raise SweepRefused(f"{distribution} has no pyproject.toml under packages/")
    version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["poetry"][
        "version"
    ]
    return str(version)


def tree_manifests(entry: dict) -> list[dict]:
    """Every manifest the WORKING TREE's plugin carries, current one first.

    A list rather than a mapping on purpose: two manifests may claim the same
    version, and that is precisely the defect
    :func:`reconcile` has to be able to see. A mapping would silently keep one.
    """
    module = importlib.import_module(entry["import_name"])
    plugin = getattr(module, entry["plugin_attr"])
    return [
        {
            "version": manifest.version,
            "digest": manifest.digest,
            "spi_range": str(manifest.spi_range),
            "capabilities": sorted(manifest.capability_ids),
        }
        for manifest in (plugin.manifest, *plugin.historical_manifests)
    ]


def survey(repo_root: pathlib.Path = REPO_ROOT) -> dict[str, dict]:
    """Declared version plus every carried manifest, per lane distribution."""
    return {
        distribution: {
            "declared": declared_version(distribution, repo_root),
            "manifests": tree_manifests(entry),
        }
        for distribution, entry in sorted(lane(repo_root).items())
    }


# ── The reconciler (pure, so its sensitivity proofs exercise it directly) ────


def reconcile(
    survey_result: dict[str, dict], recorded: dict[str, dict[str, dict]]
) -> list[str]:
    """Compare the tree's manifests with the digests published under each tag."""
    problems: list[str] = []

    for distribution in sorted(set(recorded) - set(survey_result)):
        problems.append(
            f"{distribution}: has {len(recorded[distribution])} released-manifest "
            "row(s) but is not in .github/release-connectors.json, so its "
            "coordinates cannot be resolved. A distribution leaving the release "
            "lane keeps its published contracts — move the rows deliberately, "
            "never by deleting the lane entry."
        )

    for distribution, finding in sorted(survey_result.items()):
        rows = recorded.get(distribution, {})
        carried = finding["manifests"]
        declared = finding["declared"]

        seen: dict[str, str] = {}
        for manifest in carried:
            version, digest = manifest["version"], manifest["digest"]
            if version in seen and seen[version] != digest:
                problems.append(
                    f"{distribution}: carries TWO manifests both claiming version "
                    f"{version} ({seen[version][:12]}… and {digest[:12]}…). One "
                    "version name cannot mean two contracts — bump the version "
                    "the new manifest declares."
                )
            seen.setdefault(version, digest)

        for version, row in sorted(rows.items()):
            published = row["manifest_digest"]
            match = [m for m in carried if m["version"] == version]
            if not match:
                problems.append(
                    f"{distribution}: {version} was published (tag {row['tag']}, "
                    f"peeled {row['peeled_commit'][:12]}…) and the tree no longer "
                    "carries its manifest. The exact published manifest must stay "
                    "in `historical_manifests`, or every installation adopted "
                    f"against digest {published[:12]}… becomes unidentifiable."
                )
                continue
            found = match[0]
            if found["digest"] != published:
                problems.append(
                    f"{distribution}: {version} is PUBLISHED with manifest digest "
                    f"{published} (tag {row['tag']}) and the tree now computes "
                    f"{found['digest']}. A published version's contract does not "
                    "move: declare a NEW version and preserve the published "
                    "manifest in `historical_manifests`. Never edit the digest "
                    "here to match — the wheel on the registry is the artifact "
                    "consumers hold."
                )
                continue
            for field in ("spi_range", "capabilities"):
                if found[field] != row[field]:
                    problems.append(
                        f"{distribution}: {version} records {field}={row[field]!r} "
                        f"but the tree carries {found[field]!r}. The digest still "
                        "agrees, so this is the ledger's readable half drifting "
                        "from the contract it describes — re-record the row."
                    )

        for manifest in carried:
            version = manifest["version"]
            if version in rows or version == declared:
                continue
            problems.append(
                f"{distribution}: carries a manifest for {version}, which is "
                f"neither a recorded publication nor the declared version "
                f"({declared}). A historical manifest is the record of something "
                "that has a verified publication; inventing one for a version "
                "with no verified publication offers an adoption window onto a "
                "contract whose build or publication status is unknown."
            )

    return problems


def unmonitored(repo_root: pathlib.Path = REPO_ROOT) -> list[str]:
    """Package directories this sweep says nothing about (ADR-0018)."""
    covered = set(lane(repo_root))
    return sorted(
        path.parent.name
        for path in (repo_root / "packages").glob("*/pyproject.toml")
        if path.parent.name not in covered
    )


# ── The tag oracle ──────────────────────────────────────────────────────────


def _git(*args: str, repo_root: pathlib.Path = REPO_ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise SweepRefused(
            f"cannot run `git {args[0]}` in {repo_root}: {exc}"
        ) from None
    if result.returncode != 0:
        raise SweepRefused(f"`git {' '.join(args)}` failed: {result.stderr.strip()}")
    return result.stdout


def refuse_unusable_oracle(repo_root: pathlib.Path = REPO_ROOT) -> set[str]:
    """The tag set, or a refusal — never a quiet pass.

    Identical discipline to `declared_publication_sweep.py`: a shallow or
    tagless checkout makes every publication look absent, and "the oracle was
    unavailable" is not evidence that nothing is wrong.
    """
    if (
        _git("rev-parse", "--is-shallow-repository", repo_root=repo_root).strip()
        == "true"
    ):
        raise SweepRefused(
            "the checkout is SHALLOW, so its tag set cannot be trusted. A missing "
            "tag is indistinguishable from an unreleased version here. Check out "
            "with `fetch-depth: 0` and re-run."
        )
    tags = {
        line.strip()
        for line in _git("tag", "--list", repo_root=repo_root).splitlines()
        if line.strip()
    }
    if not tags:
        raise SweepRefused(
            "the checkout has no tags at all, so every publication would read as "
            "absent. Fetch tags (`git fetch --tags`) and re-run."
        )
    return tags


def tagged_manifests(
    entry: dict, tag: str, repo_root: pathlib.Path = REPO_ROOT
) -> list[dict]:
    """The manifests the TAGGED source declares, measured by today's SPI.

    Deliberately not a claim to reproduce the hash function that ran in 2026-08:
    it is the same measurement `--check` makes on the tree, taken against the
    published source instead. That is what makes the comparison meaningful —
    both sides are computed by the one owner of the digest rule, so a difference
    is a difference in the CONTRACT rather than in the arithmetic.
    """
    archive = subprocess.run(
        ["git", "archive", tag, f"{entry['package_dir']}/src"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        raise SweepRefused(
            f"cannot read {entry['package_dir']}/src at {tag}: "
            f"{archive.stderr.decode('utf-8', 'replace').strip()}"
        )
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run(["tar", "-x", "-C", scratch], input=archive.stdout, check=True)
        source = pathlib.Path(scratch) / entry["package_dir"] / "src"
        # The tagged source FIRST, then this tree's `dotmac-integration`. Both
        # ahead of site-packages, so the comparison is between two manifests
        # measured by one SPI — the working tree's — rather than by whichever
        # copy an editable install happened to point at.
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(source), str(repo_root / "packages/dotmac-integration/src")]
        )
        probe = subprocess.run(
            [sys.executable, "-c", _PROBE, entry["import_name"], entry["plugin_attr"]],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
    if probe.returncode != 0:
        tail = probe.stderr.strip().splitlines()[-1:] or ["no stderr"]
        raise SweepRefused(f"cannot import {entry['import_name']} at {tag}: {tail[0]}")
    parsed = json.loads(probe.stdout)
    return [dict(item) for item in parsed]


def verify_tags(repo_root: pathlib.Path = REPO_ROOT) -> list[str]:
    """The cross-check. Doctoring the ledger must require moving a tag."""
    tags = refuse_unusable_oracle(repo_root)
    entries = lane(repo_root)
    recorded = ledger(repo_root)
    problems: list[str] = []

    for distribution, entry in sorted(entries.items()):
        prefix = entry["tag_prefix"]
        published = {
            tag: tag.removeprefix(prefix) for tag in tags if tag.startswith(prefix)
        }
        rows = recorded.get(distribution, {})

        for tag, version in sorted(published.items()):
            if version not in rows:
                problems.append(
                    f"{distribution}: {tag} is published and has no row in "
                    f"{LEDGER.relative_to(REPO_ROOT)}. Record it with "
                    f"`make manifest-digest-record TAG={tag}` — a published "
                    "contract nobody wrote down cannot be held immutable."
                )
        for version, row in sorted(rows.items()):
            tag = row["tag"]
            if tag not in tags:
                problems.append(
                    f"{distribution}: the ledger records {tag}, which does not "
                    "exist. A row here is a publication claim and needs a real "
                    "peeled tag behind it (AGENTS.md rule 30)."
                )
                continue
            peeled = _git("rev-list", "-n", "1", tag, repo_root=repo_root).strip()
            if peeled != row["peeled_commit"]:
                problems.append(
                    f"{distribution}: {tag} peels to {peeled} but the ledger "
                    f"records {row['peeled_commit']}."
                )
                continue
            carried = tagged_manifests(entry, tag, repo_root)
            current = carried[0]
            if current["version"] != version:
                problems.append(
                    f"{distribution}: {tag} declares manifest version "
                    f"{current['version']}, not {version}."
                )
                continue
            if current["digest"] != row["manifest_digest"]:
                problems.append(
                    f"{distribution}: {tag} publishes manifest digest "
                    f"{current['digest']} but the ledger records "
                    f"{row['manifest_digest']}. The TAG is the artifact."
                )

    return problems


# ── Recording ───────────────────────────────────────────────────────────────


def _row(
    entry: dict, tag: str, repo_root: pathlib.Path, release_run: str
) -> tuple[str, dict]:
    peeled = _git("rev-list", "-n", "1", tag, repo_root=repo_root).strip()
    if not _SHA1.fullmatch(peeled):
        raise SweepRefused(f"{tag} does not peel to a commit")
    current = tagged_manifests(entry, tag, repo_root)[0]
    version = str(current["version"])
    if not _SHA256.fullmatch(str(current["digest"])):
        raise SweepRefused(f"{tag} produced a non-sha256 manifest digest")
    return version, {
        "tag": tag,
        "peeled_commit": peeled,
        "release_run": release_run,
        "manifest_digest": current["digest"],
        "spi_range": current["spi_range"],
        "capabilities": current["capabilities"],
    }


def record(tag: str, release_run: str = "", repo_root: pathlib.Path = REPO_ROOT) -> str:
    """Write one published tag's row, reading the digest from the TAG itself."""
    tags = refuse_unusable_oracle(repo_root)
    if tag not in tags:
        raise SweepRefused(f"{tag} does not exist; a row is written for a real tag")
    entries = lane(repo_root)
    matches = [
        (name, entry)
        for name, entry in entries.items()
        if tag.startswith(entry["tag_prefix"])
    ]
    if len(matches) != 1:
        raise SweepRefused(
            f"{tag} matches {len(matches)} connector tag prefixes; it must match one"
        )
    distribution, entry = matches[0]
    version, row = _row(entry, tag, repo_root, release_run)

    path = repo_root / LEDGER.relative_to(REPO_ROOT)
    document = json.loads(path.read_text(encoding="utf-8"))
    existing = document["released"].setdefault(distribution, {}).get(version)
    if existing is not None and existing != row:
        raise SweepRefused(
            f"{distribution} {version} is already recorded with different "
            "coordinates. A published contract is written once; investigate "
            "rather than overwrite."
        )
    document["released"][distribution][version] = row
    document["released"][distribution] = dict(
        sorted(document["released"][distribution].items())
    )
    document["released"] = dict(sorted(document["released"].items()))
    # `ensure_ascii=False`, deliberately. The default rewrites every em-dash in
    # the `$comment` prose to a `\uXXXX` escape, turning a six-line addition
    # into a hundred-line diff touching paragraphs the change has nothing to do
    # with — the same trap `write_release_record.py` avoids by editing as text.
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return f"recorded {distribution} {version} -> {row['manifest_digest'][:12]}…"


# ── CLI ─────────────────────────────────────────────────────────────────────


def render(survey_result: dict[str, dict], recorded: dict[str, dict]) -> str:
    lines = ["Published manifest digests vs. the working tree:"]
    for distribution, finding in sorted(survey_result.items()):
        rows = recorded.get(distribution, {})
        lines.append(
            f"  {distribution:<34} declares {finding['declared']:<9} "
            f"{len(rows)} published, {len(finding['manifests'])} carried"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify-tags", action="store_true")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--tag", default="")
    parser.add_argument("--release-run", default="")
    args = parser.parse_args()

    if args.record:
        if not args.tag:
            raise SweepRefused("--record needs --tag")
        print(record(args.tag, args.release_run))
        return 0

    if args.verify_tags:
        problems = verify_tags()
        if problems:
            print("\n".join(problems))
            return 1
        print("released-manifest tag cross-check PASS")
        return 0

    survey_result = survey()
    recorded = ledger()
    print(render(survey_result, recorded))
    if not args.check:
        return 0

    problems = reconcile(survey_result, recorded)
    if problems:
        print("\n" + "\n\n".join(problems))
        return 1
    print("\nreleased-manifest sweep PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
