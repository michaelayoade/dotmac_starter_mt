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
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

import version_binding_guard
from registry_read import RegistryReader
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
    # A FROZEN CANDIDATE'S IDENTITY COMES FROM ITS RECEIPT, NOT FROM SOURCE.
    #
    # This used to compare the dispatched version against `pyproject.toml`
    # unconditionally, and that is the `[image]` circularity in a new coat: a
    # candidate whose identity is re-derived from the CURRENT tree is not
    # frozen. Once `foundation-candidate.yml` has built a version, its bytes
    # and its version are one immutable fact recorded in
    # `CandidateArtifact.v1`; the tree then moves on, and the moment it
    # declares a successor the already-built candidate became unreleasable —
    # not because anything was wrong with it, but because the lane was asking
    # the wrong document who it was.
    #
    # It also refused for the WRONG REASON, which is the sharper defect. A
    # frozen candidate that must not ship has a record that says so — a
    # `CandidateDisposition.v1` — and the version-binding guard reads it. A
    # source-version mismatch that happens to block the same release is a
    # coincidence standing where a reason belongs, and it stops holding the
    # instant somebody bumps a version for an unrelated purpose.
    #
    # So: if a receipt names this version, the receipt is the identity and no
    # source comparison happens at all. If none does — the version has never
    # been built — the tree is the only identity there is, and equality is
    # still required, because a version nobody has built must be the version
    # this tree declares or it names nothing.
    version = args.version
    if version:
        frozen = find_candidate_receipt(args.distribution, version)
        if frozen is None and manifest["version"] != version:
            raise ReleaseRefused(
                f"{args.distribution}: dispatched version {version!r} != "
                f"package version {manifest['version']!r}, and no committed "
                f"{version_binding_guard.CANDIDATE_SCHEMA} receipt names "
                f"{version!r}. An unbuilt version is identified by this tree "
                "alone, so the two must agree; fix one of them."
            )
    else:
        version = manifest["version"]

    # Consumed by the workflow via $GITHUB_OUTPUT. Deliberately no db_schema,
    # manifest_attr or kernel_floor — a facility has none, and emitting an
    # empty value would let a later step read it as "unknown" rather than
    # "absent".
    for key in ("package_dir", "entry_point", "tag_prefix"):
        print(f"{key}={entry[key]}")
    print(f"version={version}")
    print(f"tag={entry['tag_prefix']}{version}")


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
    wheel = _sole_wheel(Path(args.dist))

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


def find_candidate_receipt(
    distribution: str, version: str
) -> tuple[Path, dict[str, Any]] | None:
    """The committed `CandidateArtifact.v1` for this facility+version, or None.

    ABSENCE IS A DIFFERENT ANSWER FROM A DEFECT, and separating them is what
    lets `cmd_resolve` ask "is this version frozen?" without swallowing the
    refusals that must still bite. Returns None only when NO receipt names the
    version; a duplicate, an unresolvable or an already-published receipt still
    raises, because each of those is a reason to stop rather than a reason to
    fall back to the source tree.

    THE RECEIPT IS THE ONLY SOURCE OF THE CANDIDATE'S COORDINATES — repository
    included. Nothing here takes an owning repository, a run, an artifact or a
    digest from a workflow input or from a constant, and that is a correctness
    property rather than tidiness:

      * a dispatch input lets someone name a version whose receipt says
        something else, and the two would disagree silently. Reading digest,
        repository, run and artifact from ONE already-validated record makes
        that unrepresentable;
      * `michaelayoade/dotmac_starter_mt` as a literal becomes wrong the day
        the Foundation's lanes move to their own repository. The receipt
        travels with the artifact and names its own home, so that migration
        edits a record, not this lane.

    Discovered by SCHEMA rather than by filename, and by the SAME schema
    constant `version_binding_guard` binds versions with — imported rather
    than respelled, because two copies of a schema name drift silently and
    this one decides whether a receipt is seen at all.
    """
    matches: list[tuple[Path, dict[str, Any]]] = []
    directory = REPO_ROOT / version_binding_guard.INVENTORIES
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(document, dict):
            continue
        if document.get("schema") != version_binding_guard.CANDIDATE_SCHEMA:
            continue
        if document.get("facility") != distribution:
            continue
        if str(document.get("version")) != version:
            continue
        matches.append((path, document))

    if not matches:
        return None
    if len(matches) > 1:
        listed = ", ".join(str(path.relative_to(REPO_ROOT)) for path, _ in matches)
        raise ReleaseRefused(
            f"{distribution} {version}: {len(matches)} candidate receipts name "
            f"this version ({listed}). One version, one artifact — refusing "
            "rather than choosing one."
        )

    path, receipt = matches[0]
    missing = [
        field
        for field in ("repository", "run_id", "artifact_id", "filename", "sha256")
        if not receipt.get(field)
    ]
    if missing:
        raise ReleaseRefused(
            f"{path.relative_to(REPO_ROOT)} is missing {', '.join(missing)}. A "
            "receipt that cannot be resolved back to bytes is not a coordinate."
        )
    if receipt.get("published"):
        raise ReleaseRefused(
            f"{path.relative_to(REPO_ROOT)} already records published=true for "
            f"{distribution} {version}. Republishing a version cannot produce "
            "the same identity twice."
        )
    return path, receipt


def candidate_receipt(distribution: str, version: str) -> tuple[Path, dict[str, Any]]:
    """As :func:`find_candidate_receipt`, but absence is a refusal.

    The release path proper needs the receipt to EXIST — it publishes bytes
    somebody already built and does not build them.
    """
    found = find_candidate_receipt(distribution, version)
    if found is None:
        raise ReleaseRefused(
            f"{distribution} {version}: no committed "
            f"{version_binding_guard.CANDIDATE_SCHEMA} receipt. This lane "
            "publishes the bytes `foundation-candidate.yml` already built; it "
            "does not build them. Build the candidate, then commit its "
            "receipt, then release."
        )
    return found


def _sole_wheel(dist: Path) -> Path:
    wheels = sorted(Path(dist).glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseRefused(
            f"expected exactly one wheel in {dist}, found {len(wheels)}"
        )
    return wheels[0]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_artifacts(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every file the receipt binds, keyed by filename.

    BOTH distribution forms, and the sdist is not optional. `twine upload
    dist/*` publishes the sdist beside the wheel, so a receipt that names only
    the wheel binds half of what a release puts on the index — and the half it
    leaves loose is the one a resolver never fetches, which is precisely how
    `dotmac-deployment-control` 0.1.0a3 became permanently unprovable. A
    receipt that cannot name the sdist cannot authorise the upload, so this
    refuses rather than silently verifying one file out of two.
    """
    wheel_name = receipt.get("filename")
    wheel_digest = receipt.get("sha256")
    if not wheel_name or not wheel_digest:
        raise ReleaseRefused(
            "the candidate receipt names no wheel filename/sha256; it cannot "
            "bind the bytes a release publishes"
        )
    sdist = receipt.get("sdist")
    if (
        not isinstance(sdist, dict)
        or not sdist.get("filename")
        or not sdist.get("sha256")
    ):
        raise ReleaseRefused(
            "the candidate receipt carries no sdist filename/sha256. `twine "
            "upload dist/*` publishes the sdist too, so an unrecorded sdist "
            "reaches the index bound to nothing. Record it, or do not publish."
        )
    return {
        str(wheel_name): {
            "sha256": str(wheel_digest),
            "size_bytes": receipt.get("size_bytes"),
        },
        str(sdist["filename"]): {
            "sha256": str(sdist["sha256"]),
            "size_bytes": sdist.get("size_bytes"),
        },
    }


def candidate_filenames(receipt: dict[str, Any]) -> frozenset[str]:
    """The exact filename set an enumerated by-name fetch must request."""
    return frozenset(candidate_artifacts(receipt))


def require_candidate_bytes(receipt: dict[str, Any], dist: Path) -> None:
    """EVERY file in hand must BE the recorded candidate. Digest first.

    Both directions, and the second one matters as much as the first: a file
    the receipt does not name is refused too, because `publish` uploads the
    whole directory and an unrecorded file in it would reach the index having
    been compared with nothing.
    """
    dist = Path(dist)
    expected = candidate_artifacts(receipt)
    present = {
        path.name: path
        for path in sorted(dist.iterdir())
        if path.is_file()
        and (path.name.endswith(".whl") or path.name.endswith(".tar.gz"))
    }

    problems: list[str] = []
    for name in sorted(set(expected) - set(present)):
        problems.append(f"{name}: recorded by the receipt, absent here")
    for name in sorted(set(present) - set(expected)):
        problems.append(f"{name}: present here, named by no receipt entry")

    for name in sorted(set(expected) & set(present)):
        path = present[name]
        actual = sha256_of(path)
        if actual != expected[name]["sha256"]:
            problems.append(
                f"{name}: sha256 {actual} != the receipt's {expected[name]['sha256']}"
            )
        expected_size = expected[name]["size_bytes"]
        if isinstance(expected_size, int) and path.stat().st_size != expected_size:
            problems.append(
                f"{name}: size {path.stat().st_size} != the receipt's {expected_size}"
            )

    if problems:
        raise ReleaseRefused(
            "the artifacts in hand are NOT the recorded candidate:\n  - "
            + "\n  - ".join(problems)
            + "\nRebuilding is not the repair. The downstream receipts name "
            "these bytes; bytes that merely resemble them are a claim."
        )


def candidate_source_revision(receipt: dict[str, Any]) -> str:
    """The commit the recorded candidate was BUILT FROM. A third revision.

    Three different commits are in play across the build/rehearse/publish
    sequence and they are three different questions:

      * the CANDIDATE SOURCE revision — what the artifact was built from. This
        one. It lives here, in the committed ``CandidateArtifact.v1``, and
        nowhere else;
      * the LANE 3 RUNNER revision — whose runner drove the rehearsal. That is
        the rehearsal run's own head SHA, and it is what
        ``RehearsalReceipt.v1.foundation_revision`` records;
      * the RELEASE/TAG revision — what publishes. The release run's head SHA,
        which ``verify_publication`` already compares against the receipt's.

    They were not distinguishable from one another because only two of them
    were ever named: the candidate's was never emitted, so nothing downstream
    could refer to it, let alone compare it. Emitting it is what makes a
    binding possible at all — "build once, run those exact bytes, publish
    unchanged" is a claim about three commits agreeing in a stated way, and a
    value nobody can name cannot be shown to agree with anything.

    Refused rather than defaulted when absent or not a full commit: a receipt
    that cannot say what it was built from is a receipt that binds nothing, and
    a short or empty revision compared against a full one silently never
    matches.
    """
    value = str(receipt.get("source_sha", "")).strip().lower()
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise ReleaseRefused(
            f"the candidate receipt records source_sha {receipt.get('source_sha')!r}, "
            "which is not a full 40-character commit. The revision an artifact "
            "was built from is one of the three this sequence binds; an "
            "unusable one cannot be compared and must not be passed on as "
            "though it could"
        )
    return value


#: Stable identifiers for the four relationship refusals, so a caller asserts a
#: code rather than prose.
REVISION_NOT_ANCESTOR: Final = "revisions.candidate_source_not_ancestor"
REVISION_UNKNOWN_COMMIT: Final = "revisions.commit_unknown"
REVISION_TAG_MISPEELED: Final = "revisions.tag_does_not_peel_to_candidate_source"


def _git(*args: str, repo_root: Path = REPO_ROOT) -> str:
    """git, refusing rather than answering when it cannot.

    An unavailable oracle is not a pass — the same rule
    `version_binding_guard.tag_bindings` states for a checkout with no tags. A
    shallow clone is the concrete way this bites: `merge-base --is-ancestor`
    against history that was never fetched answers "not an ancestor" for two
    commits that are related, so the failure would look like the defect.
    """
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise ReleaseRefused(
            f"cannot run `git {args[0]}` in {repo_root}: {exc}"
        ) from None
    if result.returncode != 0:
        raise ReleaseRefused(f"`git {' '.join(args)}` failed: {result.stderr.strip()}")
    return result.stdout


def _require_known_commit(revision: str, *, what: str, repo_root: Path) -> str:
    """The commit exists in THIS checkout, so a comparison against it means
    something.

    Separate from the ancestry question and checked first, because the two fail
    for opposite reasons and demand opposite repairs: an unknown commit is a
    FETCH problem (`fetch-depth: 0`), a known non-ancestor is a BRANCH problem.
    Collapsing them would report a shallow clone as a divergent branch, which is
    the failure mode most likely to get a real guard disabled.
    """
    text = str(revision).strip().lower()
    if len(text) != 40 or any(c not in "0123456789abcdef" for c in text):
        raise ReleaseRefused(f"{what} {revision!r} is not a full 40-character commit")
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{text}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseRefused(
            f"{what} {text} is not a commit in this checkout. Ancestry cannot be "
            "decided against history that was never fetched, and a shallow clone "
            "would answer 'not an ancestor' for two commits that are related — "
            "which reads exactly like the defect this check exists to find. "
            "Check out with `fetch-depth: 0`"
        )
    return text


def require_revision_relationships(
    receipt: dict[str, Any],
    *,
    runner_revision: str,
    release_revision: str,
    repo_root: Path = REPO_ROOT,
) -> dict[str, str]:
    """The relationship between the three revisions. Neither equality nor nothing.

    ## Why not equality, and why not nothing

    Requiring the candidate source to EQUAL the runner and release revisions
    recreates the bootstrap loop `foundation-candidate.yml` exists to break: a
    candidate must be buildable BEFORE the commit that rehearses it, so any
    commit landing afterwards — the rehearsal repairs above all — would
    invalidate it and the release could never be satisfied. Every one of the five
    recorded candidates was built at a commit that is an ancestor of `main` and
    not its head, so equality is not a stricter rule; it is an unsatisfiable one,
    and unsatisfiable gates get waived rather than met.

    Requiring NO relationship is the other failure: a candidate built on a
    divergent branch could then be rehearsed and published by a protected-main
    run, and nothing would say the bytes came from code that was never on main.
    That is substitution, and it is the whole reason the three are named apart.

    **ANCESTRY is the requirement.** The candidate source must be reachable from
    both protected revisions: the artifact was built from code that is now part
    of the history being rehearsed and published, and no other branch can supply
    it. Equality satisfies ancestry, so a candidate built at the tip is still
    allowed — the rule admits the strict case without demanding it.

    Both protected revisions are checked, not just one. Checking only the release
    revision would let a rehearsal run on a branch that never contained the
    candidate source; checking only the runner's would let publication happen
    from one.
    """
    source = candidate_source_revision(receipt)
    runner = _require_known_commit(
        runner_revision, what="the Lane 3 runner revision", repo_root=repo_root
    )
    release = _require_known_commit(
        release_revision, what="the release revision", repo_root=repo_root
    )
    _require_known_commit(
        source, what="the candidate source revision", repo_root=repo_root
    )
    for name, protected in (("Lane 3 runner", runner), ("release", release)):
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", source, protected],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 1:
            raise ReleaseRefused(
                f"the candidate was built from {source}, which is NOT an "
                f"ancestor of the {name} revision {protected}. The bytes about "
                "to be rehearsed or published came from code that is not in "
                "this history — a candidate from a divergent branch, which is "
                "the substitution these three revisions are named apart to make "
                "visible",
            )
        if proc.returncode != 0:
            raise ReleaseRefused(
                f"`git merge-base --is-ancestor` could not answer for {source} "
                f"and {protected}: {proc.stderr.strip()}. An oracle that cannot "
                "answer is not a pass"
            )
    return {
        "candidate_source_revision": source,
        "runner_revision": runner,
        "release_revision": release,
    }


def require_tag_peels_to(
    tag: str, *, expected_commit: str, repo_root: Path = REPO_ROOT
) -> str:
    """The version tag PEELS to the candidate source commit.

    ## Peels, in the strict sense

    An annotated tag is an object of its own. ``git rev-parse <tag>`` returns
    that TAG OBJECT's sha; ``git rev-list -n 1 <tag>`` returns the COMMIT it
    ultimately points at. Only the second answers "which source is this version".
    This repository has already had a gate turn on exactly that distinction —
    `released_manifest_sweep` records `peeled_commit` and re-derives it the same
    way — so the idiom is reused rather than re-invented.

    ## Why the candidate source and not the release run's own SHA

    A version tag names WHERE THIS VERSION'S SOURCE IS. The bytes were built from
    the candidate source commit; the release run merely publishes them. Tagging
    the release SHA makes the tag point at a tree that was never built, and every
    consumer who checks out the tag to inspect what they installed gets code that
    is not what they installed. The release revision is not lost by this — it is
    recorded separately, which is what "bound independently" means.
    """
    expected = str(expected_commit).strip().lower()
    peeled = _git("rev-list", "-n", "1", tag, repo_root=repo_root).strip()
    if peeled != expected:
        raise ReleaseRefused(
            f"{tag} peels to {peeled} and the candidate was built from "
            f"{expected}. A version tag names where this version's SOURCE is; "
            "one pointing anywhere else sends a consumer inspecting what they "
            "installed to a tree that was never built"
        )
    return peeled


def cmd_verify_revisions(args: argparse.Namespace) -> None:
    """Refuse unless the three revisions stand in the ruled relationship.

    The exact-digest half is NOT repeated here: `verify-candidate` compares the
    fetched bytes with the receipt and `require_rehearsal.py --artifact-digest`
    compares the receipt with those bytes. Re-deriving it in a third place would
    be a third answer to one question.
    """
    resolve(args.distribution)
    _, receipt = candidate_receipt(args.distribution, args.version)
    bound = require_revision_relationships(
        receipt,
        runner_revision=args.runner_revision,
        release_revision=args.release_revision,
    )
    for key, value in bound.items():
        print(f"{key}={value}")
    print(f"candidate_artifact_digest={receipt['sha256']}")


def cmd_verify_tag(args: argparse.Namespace) -> None:
    """Refuse unless the pushed tag peels to the candidate source commit."""
    resolve(args.distribution)
    _, receipt = candidate_receipt(args.distribution, args.version)
    peeled = require_tag_peels_to(
        args.tag, expected_commit=candidate_source_revision(receipt)
    )
    print(f"tag_peeled_commit={peeled}")


def cmd_resolve_candidate(args: argparse.Namespace) -> None:
    """Emit the recorded candidate's coordinates for the workflow to fetch."""
    resolve(args.distribution)
    path, receipt = candidate_receipt(args.distribution, args.version)
    for key in ("repository", "run_id", "artifact_id", "filename", "sha256"):
        print(f"candidate_{key}={receipt[key]}")
    # Named separately from `candidate_sha256`, and from the workflow's own
    # `GITHUB_SHA`, because those are three answers to three questions. See
    # `candidate_source_revision`.
    print(f"candidate_source_sha={candidate_source_revision(receipt)}")
    print(f"candidate_receipt={path.relative_to(REPO_ROOT)}")


def cmd_verify_candidate(args: argparse.Namespace) -> None:
    """Refuse unless the fetched bytes are the recorded candidate.

    Runs in the job that has NO access to the publish credential, so a
    mismatched artifact fails before the token is reachable. The precedent is
    `dotmac-deployment-control` 0.1.0a3: a run that published and then failed
    its own verification, leaving bytes on an index that are permanently
    unprovable. Verification after upload is not verification.
    """
    resolve(args.distribution)
    _, receipt = candidate_receipt(args.distribution, args.version)
    require_candidate_bytes(receipt, Path(args.dist))
    for name in sorted(candidate_filenames(receipt)):
        print(f"{name}: matches the recorded candidate")


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

#: Historical V1 parse/digest probe, retained only as a reference and not run
#: by the successor release gate. The active installed-wheel proof is V3 below.
#: Formerly this proof was executed by the INSTALLED interpreter against the
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
    HostPrestateV1,
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
    host_prestate=HostPrestateV1.from_document(document["host_prestate"]),
    application_profile_digest=document["application_profile_digest"],
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


# V1 probe above remains historical parsing evidence. Only this successor
# probe checks the version that may enter execution authority.
_EXECUTION_PLAN_V3_PROBE: Final = """\
import dataclasses
from dotmac_deployment_foundation.execution_plan import HostPrestateV1
from dotmac_deployment_foundation.execution_plan_v2 import FoundationExecutionPlanV2
from dotmac_deployment_foundation.execution_plan_v3 import (
    FoundationExecutionPlanV3, canonical_execution_plan_v3_bytes,
    require_execution_plan_v3_digest,
)
from dotmac_deployment_foundation.version import VERSION

base = FoundationExecutionPlanV2(
    product="release-smoke", target="release-smoke-host", operation="deploy",
    foundation_version=VERSION, image_reference="example@sha256:" + "b" * 64,
    image_digest="sha256:" + "b" * 64, source_revision="c" * 40,
    manifest_digest="sha256:" + "d" * 64,
    descriptor_digest="sha256:" + "e" * 64, strategy="warm_candidate",
    environment_inventory=(), host_prestate=HostPrestateV1(roles=()),
    application_profile_digest="", steps=(),
)
plan = FoundationExecutionPlanV3(
    base=base, candidate_wheel_digest="sha256:" + "a" * 64,
    target_id="target-1", controller_ssh_fingerprint="SHA256:controller",
    host_id="fleet-host-1", host_incarnation="sha256:host-key",
    host_enrolment_ref="00000000-0000-4000-8000-000000000001",
)
assert canonical_execution_plan_v3_bytes(plan.as_document()) == plan.canonical_bytes()
assert require_execution_plan_v3_digest(plan, authorized=plan.digest()) == plan.digest()
for field in ("candidate_wheel_digest", "target_id", "controller_ssh_fingerprint",
              "host_id", "host_incarnation", "host_enrolment_ref"):
    value = (
        "sha256:" + "f" * 64 if field == "candidate_wheel_digest" else
        "00000000-0000-4000-8000-000000000002" if field == "host_enrolment_ref" else
        getattr(plan, field) + "-drift"
    )
    changed = dataclasses.replace(plan, **{field: value})
    assert changed.digest() != plan.digest(), field
print(plan.digest())
"""


#: THE NINTH PROPERTY, and it is here rather than in `tests/unit/` on purpose.
#:
#: Eight tests in `test_deployment_foundation_execution_binding.py` run against
#: the source tree, and the source tree is not what anyone installs. A wheel can
#: be built from a repaired tree and still ship an older module, or ship the
#: right module under a broken entry point — `0.3.0a2` shipped a wheel whose
#: `__version__` disagreed with its own metadata, and every source-side gate was
#: green. So the binding is exercised against the INSTALLED distribution, by the
#: INSTALLED interpreter, in the candidate lane and again against the bytes the
#: registry served.
#:
#: Behaviour, never spelling. Nothing here asks whether a name exists: each
#: check drives a refusal and fails if the refusal does not happen.
_EXECUTION_BINDING_PROBE: Final = """\
import inspect
import sys

from dotmac_deployment_foundation.authorization import authorize
from dotmac_deployment_foundation.engine.run import Executor
from dotmac_deployment_foundation.provenance import (
    AuthorizationReceipt,
    VerifiedAuthorization,
    verify_authorization,
)

problems = []

RECEIPT = {
    "plan_id": "00000000-0000-4000-8000-00000000beef",
    "target_ref": "installed-artifact-probe",
    "descriptor_digest": "sha256:" + "a" * 64,
    "execution_plan_digest": "sha256:" + "e" * 64,
    "control_plan_digest": "f" * 64,
    "execution_sequence": 7,
    "attempt_no": 1,
    "policy_code": "deployment.production",
    "policy_version": 1,
    "decision_ref": "approvals:decision:1",
    "approved_at": "2026-08-30T00:00:00Z",
    "expires_at": "2026-08-31T00:00:00Z",
    "control_version": "0.0.0",
    "operation": "deploy",
}


class _Stub:
    def attest(self, material):
        return dict(material)


# 1. An UNBOUND executor must be unconstructable. Checked on the installed
#    signature: `execution_plan` required, and no way to hand the authorized
#    digest in beside it.
parameters = inspect.signature(Executor.__init__).parameters
if "execution_plan" not in parameters:
    problems.append("the installed Executor takes no execution_plan at all")
elif parameters["execution_plan"].default is not inspect.Parameter.empty:
    problems.append(
        "the installed Executor defaults execution_plan to %r, so an unbound "
        "executor is constructable" % (parameters["execution_plan"].default,)
    )
if "authorized_execution_plan_digest" in parameters:
    problems.append(
        "the installed Executor still accepts authorized_execution_plan_digest, "
        "so an authorized digest can arrive without passing through attestation"
    )

# 2. Verified terms cannot be hand-built.
try:
    VerifiedAuthorization(object(), receipt=AuthorizationReceipt(**RECEIPT))
    problems.append("the installed VerifiedAuthorization accepted a hand-built witness")
except Exception:
    pass

# 3. A receipt that names no frozen plan is refused.
try:
    bare = dict(RECEIPT)
    del bare["execution_plan_digest"]
    verify_authorization(bare, verifier=_Stub())
    problems.append(
        "the installed receipt accepted a document with no execution_plan_digest"
    )
except Exception:
    pass

# 4. An expired approval is refused, with time supplied by the caller.
import datetime as _dt

verified = verify_authorization(dict(RECEIPT), verifier=_Stub())
try:
    authorize(
        verified=verified,
        operation="deploy",
        descriptor_digest="sha256:" + "a" * 64,
        target="installed-artifact-probe",
        now=_dt.datetime(2026, 8, 30, 12, tzinfo=_dt.UTC),
    )
    problems.append("the installed authorize() still granted a live V1 receipt")
except Exception:
    pass
try:
    authorize(
        verified=verified,
        operation="deploy",
        descriptor_digest="sha256:" + "a" * 64,
        target="installed-artifact-probe",
        now=_dt.datetime(2026, 9, 1, tzinfo=_dt.UTC),
    )
    problems.append("the installed authorize() accepted an expired approval")
except Exception:
    pass

# 5. Control's plan digest arriving as the descriptor digest is refused.
substituted = dict(RECEIPT)
substituted["descriptor_digest"] = RECEIPT["control_plan_digest"]
try:
    authorize(
        verified=verify_authorization(substituted, verifier=_Stub()),
        operation="deploy",
        descriptor_digest="sha256:" + "a" * 64,
        target="installed-artifact-probe",
        now=_dt.datetime(2026, 8, 30, 12, tzinfo=_dt.UTC),
    )
    problems.append(
        "the installed authorize() accepted Control's plan digest as the "
        "descriptor digest"
    )
except Exception:
    pass

if problems:
    sys.stderr.write("\\n".join(problems) + "\\n")
    raise SystemExit(1)

sys.stdout.write("execution binding OK\\n")
"""


_PRESTATE_DIGEST_PROBE: Final = """\
import sys

from dotmac_deployment_foundation import (
    PRESTATE_DIGEST_SCHEMA,
    PRESTATE_DISCRIMINATOR,
    PRESTATE_SCHEMA,
)
from dotmac_deployment_foundation.execution_plan import HostPrestateV1
from dotmac_deployment_foundation.recovery_plan import FailedSystemObservationV1

FROZEN = "sha256:bb81b4a47e5c8f3deff5fe9a94db5a910353381dacb7068d30ae47bb43387068"

problems = []

observation = FailedSystemObservationV1(
    target="installed-artifact-probe",
    roles=HostPrestateV1(roles=(("app", "sha256:" + "c" * 64),)),
    observed_descriptor_digest="sha256:" + "a" * 64,
)

if observation.digest() != FROZEN:
    problems.append(
        "the installed wheel produces %s for the frozen observation, not %s. "
        "This value is signed by another repository and computed only here"
        % (observation.digest(), FROZEN)
    )
if PRESTATE_SCHEMA != "FailedSystemObservationV1":
    problems.append("the document schema moved: %s" % PRESTATE_SCHEMA)
if PRESTATE_DIGEST_SCHEMA != "FailedSystemObservationDigestV1":
    problems.append("the value schema moved: %s" % PRESTATE_DIGEST_SCHEMA)
if PRESTATE_DISCRIMINATOR != (
    "dotmac.deployment_foundation.failed_system_observation.v1"
):
    problems.append("the discriminator moved: %s" % PRESTATE_DISCRIMINATOR)

namespace = {}
exec("from dotmac_deployment_foundation import *", namespace)
for name in (
    "PRESTATE_SCHEMA",
    "PRESTATE_DIGEST_SCHEMA",
    "PRESTATE_DISCRIMINATOR",
    "canonical_prestate_bytes",
    "failed_system_observation_digest",
):
    if name not in namespace:
        problems.append("%s is not on the installed public surface" % name)

if problems:
    print(chr(10).join(problems), file=sys.stderr)
    sys.exit(1)
print("installed prestate digest canary: %s" % FROZEN)
"""


def _prestate_digest_smoke(venv_python: Path, workdir: Path) -> None:
    """Run the prestate canary with the INSTALLED interpreter."""
    probe = workdir / "prestate_digest_probe.py"
    probe.write_text(_PRESTATE_DIGEST_PROBE, encoding="utf-8")
    checked = subprocess.run(
        [str(venv_python), str(probe)], check=False, capture_output=True, text=True
    )
    if checked.returncode != 0:
        raise ReleaseRefused(
            "the installed bytes do not produce the frozen prestate digest, or "
            "do not publish its identity:\n  - "
            + "\n  - ".join(checked.stderr.strip().splitlines())
        )


def _execution_binding_smoke(venv_python: Path, workdir: Path) -> None:
    """Run the binding probe with the INSTALLED interpreter."""
    probe = workdir / "execution_binding_probe.py"
    probe.write_text(_EXECUTION_BINDING_PROBE, encoding="utf-8")
    checked = subprocess.run(
        [str(venv_python), str(probe)], check=False, capture_output=True, text=True
    )
    if checked.returncode != 0:
        raise ReleaseRefused(
            "the installed bytes do not enforce the execution binding:\n  - "
            + "\n  - ".join(checked.stderr.strip().splitlines())
        )


def _execution_plan_smoke(
    venv_python: Path, script: Path, descriptor: Path, workdir: Path
) -> str:
    """Prove installed V3 rendering and no unauthenticated CLI plan output."""
    prestate_path = workdir / "prestate.json"
    prestate_path.write_text('{"roles": []}\n', encoding="ascii")
    refused = subprocess.run(
        [
            str(script),
            "-f",
            str(descriptor),
            "execution-plan",
            "--target",
            SMOKE_TARGET,
            "--operation",
            "deploy",
            "--prestate",
            str(prestate_path),
            "--format",
            "digest",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if refused.returncode == 0 or "V3 authority" not in refused.stderr:
        raise ReleaseRefused(
            "the installed CLI produced an execution-plan digest without a "
            "startup-fixed V3 provider; a historical V1 plan cannot authorize"
        )
    probe = workdir / "execution_plan_v3_probe.py"
    probe.write_text(_EXECUTION_PLAN_V3_PROBE, encoding="utf-8")
    checked = subprocess.run(
        [str(venv_python), str(probe)], check=False, capture_output=True, text=True
    )
    if checked.returncode != 0:
        raise ReleaseRefused(
            "the installed bytes do not render/digest FoundationExecutionPlanV3:\n  - "
            + "\n  - ".join(checked.stderr.strip().splitlines())
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
        # The binding, on the same installed bytes. A wheel that renders a plan
        # and then executes without one is exactly the state this release
        # repairs, and only the artifact can say whether it still does.
        _execution_binding_smoke(venv_python, Path(tmp))
        _prestate_digest_smoke(venv_python, Path(tmp))
        # Publication checks these bytes and an honest standalone refusal;
        # it does not claim CP adoption or a successful Gate-3 execution.
    print(f"{entry_point}: execution-plan contract OK ({digest})")
    print(f"{entry_point}: execution binding enforced on the installed artifact")


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
    """Post-publish: prove the index holds EVERY recorded byte, then install
    those exact bytes and re-run the CLI smoke.

    `--index` is the CREDENTIAL-FREE simple-index URL; the read credential
    arrives in `REGISTRY_PASSWORD` and never appears in `argv` or in a URL. An
    exact pin only: a range would let this pass against a version nobody
    published in this run.

    ## Why the fetch is BY NAME and not a resolver's choice

    This step used to fetch with `pip download --no-deps --only-binary :all:`
    and compare the one wheel that came back. That asks the resolver's
    question, not the release's: `publish` runs `twine upload dist/*`, so the
    index ends up holding a wheel AND an sdist, and a resolver has no reason to
    retrieve the second one. The receipt records the sdist's digest and nothing
    read it — so the sdist's published bytes were compared with nothing, from
    the candidate fetch all the way to the index.

    That is the exact gap that made `dotmac-deployment-control` 0.1.0a3
    unprovable, and it is recorded there in those terms: "The sdist was on the
    index the whole time; nothing had ever compared its bytes." The repair is
    not to narrow the claim to whatever pip retrieves. Every filename the
    receipt binds is requested from the index BY NAME, the index must list each
    exactly once, and every one is compared.

    The comparison is against the RECEIPT, never against what `publish`
    uploaded — comparing a download with the upload compares an upload with
    itself and passes however wrong the upload was.
    """
    distribution, _, version = args.pin.partition("==")
    if not version:
        raise ReleaseRefused(f"{args.pin!r} is not an exact pin (name==version)")
    entry = resolve(distribution)
    _, receipt = candidate_receipt(distribution, version)
    expected = candidate_filenames(receipt)

    password = os.environ.get("REGISTRY_PASSWORD", "")
    if not password:
        raise ReleaseRefused(
            "REGISTRY_PASSWORD is required. The index read is authenticated as "
            f"{args.login!r}, and the credential is passed in the environment "
            "rather than in the index URL or in argv."
        )
    project_index = args.index.rstrip("/") + f"/{distribution}/"
    reader = RegistryReader(project_index, args.login, password)
    password = ""

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        python, pip = _venv(Path(tmp) / "venv")

        fetched = Path(tmp) / "fetched"
        reader.collect(expected, fetched)
        require_candidate_bytes(receipt, fetched)
        for name in sorted(expected):
            print(f"the index serves the recorded candidate: {name}")

        # Install THOSE bytes, not a second resolution of the same pin.
        served = _sole_wheel(fetched)
        subprocess.run(
            [str(pip), "install", "--quiet", "--no-index", str(served)],
            check=True,
        )
        _cli_smoke(python, entry["entry_point"], Path(args.descriptor))
    print(f"registry verification OK for {args.pin} ({len(expected)} artifacts)")


def _load_guard(entry: dict) -> object:
    """Import the guard FROM THE REPOSITORY under test, not from site-packages.

    The scanner is part of the package it scans, which is the arrangement that
    keeps one policy rather than two. Importing an installed copy would let a
    stale wheel on the runner decide whether a fresh wheel is clean.
    """
    src = Path(entry["package_dir"]) / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    module = importlib.import_module(f"{entry['import_name']}.target_identity_guard")
    return module


def cmd_scan_targets(args: argparse.Namespace) -> None:
    """Refuse an artifact carrying a target or vantage identity.

    Runs over the COMPLETED wheel AND sdist -- both, because an sdist is a
    perfectly good way to ship a literal that never reaches a wheel, and
    because the publication job re-runs this same command over the same bytes.
    One scanner, two stages, and the second never rebuilds.

    Every member is inspected, not `*.py`: packaged templates, rendered
    configuration and plain package data ship and are read on the host exactly
    as a module is.
    """
    entry = resolve(args.distribution)
    guard = _load_guard(entry)

    artifacts = sorted(
        [*Path(args.dist).glob("*.whl"), *Path(args.dist).glob("*.tar.gz")]
    )
    if not artifacts:
        raise ReleaseRefused(
            f"no wheel or sdist in {args.dist}. An artifact scan that found "
            "nothing to scan has not passed; it has not run."
        )

    problems: list[str] = []
    for artifact in artifacts:
        findings = guard.scan_archive(artifact)  # type: ignore[attr-defined]
        inspected = {guard.ledger_key(f.where) for f in findings}  # type: ignore[attr-defined]
        complaints = guard.check_debt(findings, inspected=inspected)  # type: ignore[attr-defined]
        problems.extend(f"{artifact.name}: {complaint}" for complaint in complaints)
        print(f"{artifact.name}: scanned {len(findings)} finding(s)")

    if problems:
        raise ReleaseRefused(
            "artifact carries a target identity:\n  - " + "\n  - ".join(problems)
        )
    print(f"target-identity scan OK across {len(artifacts)} artifact(s)")


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
        "resolve-candidate",
        help="emit the recorded candidate's coordinates (repo, run, artifact)",
    )
    p.add_argument("distribution")
    p.add_argument("--version", required=True)
    p.set_defaults(func=cmd_resolve_candidate)

    p = sub.add_parser(
        "verify-candidate",
        help="refuse unless the fetched bytes ARE the recorded candidate",
    )
    p.add_argument("distribution")
    p.add_argument("--version", required=True)
    p.add_argument("--dist", required=True)
    p.set_defaults(func=cmd_verify_candidate)

    p = sub.add_parser(
        "verify-revisions",
        help=(
            "refuse unless the candidate source commit is an ancestor of both "
            "the Lane 3 runner and the release revisions"
        ),
    )
    p.add_argument("distribution")
    p.add_argument("--version", required=True)
    p.add_argument("--runner-revision", required=True)
    p.add_argument("--release-revision", required=True)
    p.set_defaults(func=cmd_verify_revisions)

    p = sub.add_parser(
        "verify-tag",
        help="refuse unless the tag PEELS to the candidate source commit",
    )
    p.add_argument("distribution")
    p.add_argument("--version", required=True)
    p.add_argument("--tag", required=True)
    p.set_defaults(func=cmd_verify_tag)

    p = sub.add_parser(
        "scan-targets",
        help="refuse a wheel or sdist carrying a target/vantage identity",
    )
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.set_defaults(func=cmd_scan_targets)

    p = sub.add_parser("verify-wheel", help="install the built wheel and smoke its CLI")
    p.add_argument("distribution")
    p.add_argument("--dist", required=True)
    p.add_argument("--descriptor", required=True)
    p.set_defaults(func=cmd_verify_wheel)

    p = sub.add_parser(
        "verify-registry",
        help="fetch every published artifact by name, compare, install, smoke",
    )
    p.add_argument(
        "--index",
        required=True,
        help="credential-free simple-index root (no project path, no userinfo)",
    )
    p.add_argument(
        "--login",
        default="ci-reader",
        help="the READ-only registry identity; the credential is REGISTRY_PASSWORD",
    )
    p.add_argument("--pin", required=True)
    p.add_argument("--descriptor", required=True)
    p.set_defaults(func=cmd_verify_registry)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
