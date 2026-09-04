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


def cmd_resolve_candidate(args: argparse.Namespace) -> None:
    """Emit the recorded candidate's coordinates for the workflow to fetch."""
    resolve(args.distribution)
    path, receipt = candidate_receipt(args.distribution, args.version)
    for key in ("repository", "run_id", "artifact_id", "filename", "sha256"):
        print(f"candidate_{key}={receipt[key]}")
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


#: The probe BINDINGS DISTRIBUTION, installed as a real wheel for the ADMIT.
#:
#: Item 11's second half. The unit tests drive discovery through an injectable
#: `entries` parameter, which proves every refusal shape and cannot prove the
#: one thing a4 died on: that a REAL installed distribution's metadata reaches
#: the installed console script. So the smoke builds this module into a wheel
#: (a wheel is a zip with dist-info; no build backend, no index), pip-installs
#: it, drives `dotmac-deploy deploy --execute` END TO END through discovery to
#: a successful outcome — then UNINSTALLS it and drives the same command to the
#: refusal that names the entry-point group. One mechanism, shown admitting and
#: refusing, on the installed artifact.
#:
#: The fake Effects here derives every healthy answer FROM THE SPEC it is
#: handed, so the probe cannot drift from the descriptor: labels carry the
#: spec's revision, the manifest digest is the spec's, heads are the declared
#: heads, and `switch` flips the observed roles to the digest it was given.
#: Evidence is written content-addressed into the deploy dir so the outer
#: smoke can assert on a real artefact.
_PROBE_BINDINGS_SOURCE: Final = """\
import hashlib
import json
import os
from pathlib import Path

from dotmac_deployment_foundation.engine.run import (
    BackupResult,
    CommandResult,
    RoleObservation,
)
from dotmac_deployment_foundation.evidence import (
    ReleaseEvidenceV1,
    SignedEvidenceEnvelope,
    TrustPolicy,
)
from dotmac_deployment_foundation.execution_bindings import ExecutionBindings

PREVIOUS_DIGEST = "sha256:" + "b" * 64
KEY_ID = "probe-signer"


class _Verifier:
    def attest(self, material):
        return dict(material)


class _Signatures:
    def verify(self, *, key_id, message, signature):
        return signature == "probe-valid" and key_id == KEY_ID


class ProbeEffects:
    \"\"\"Every healthy answer derived from the spec; every mutation in memory,
    except evidence, which lands on disk so the smoke can assert on it.\"\"\"

    def __init__(self, spec, deploy_dir):
        self.spec = spec
        self.deploy_dir = Path(deploy_dir)
        self.roles = {
            role.code: RoleObservation(role.code, True, PREVIOUS_DIGEST, 0)
            for role in spec.roles
            if role.replicas > 0
        }

    # ── gates ──
    def image_present(self, reference):
        return True

    def image_labels(self, reference):
        return {"org.opencontainers.image.revision": self.spec.source_revision}

    def release_evidence(self, revision):
        document = ReleaseEvidenceV1(
            revision=revision,
            repository="michaelayoade/dotmac_starter_mt",
            repository_id="1001",
            head_repository_id="1001",
            ref="refs/heads/main",
            run_id="1",
            workflow="ci.yml",
            conclusion="success",
        ).as_document()
        return SignedEvidenceEnvelope(
            document=document, signature="probe-valid", key_id=KEY_ID
        )

    def manifest_digest(self, manifest_path):
        return self.spec.manifest_digest

    def observe_roles(self):
        return list(self.roles.values())

    def working_tree_dirty(self):
        return False

    def untracked_compose_overrides(self):
        return []

    def resolved_materials(self):
        names = set(self.spec.runtime_materials)
        for role in self.spec.roles:
            names.update(role.materials)
        if self.spec.migration is not None:
            names.add(self.spec.migration.owner_material)
        return sorted(names)

    # ── mutations ──
    def run_command(self, command, *, timeout_seconds, materials=()):
        return CommandResult(exit_code=0, stdout="", stderr="")

    def run_migration_command(
        self, command, *, timeout_seconds, materials=(), image
    ):
        assert image, "the engine must state the candidate image"
        return CommandResult(exit_code=0, stdout="", stderr="")

    def backup(self, dataset_code, *, timeout_seconds):
        return BackupResult(
            dataset=dataset_code,
            path="probe://backup",
            size_bytes=1,
            checksum="probe-checksum",
            checksum_algorithm="sha256",
        )

    def verify_backup(self, result):
        return True

    def migration_heads(self, *, image):
        assert image, "the engine must state the candidate image"
        return list(self.spec.migration.expected_heads)

    def stop_roles(self, roles, *, timeout_seconds):
        for code in roles:
            seen = self.roles[code]
            self.roles[code] = RoleObservation(code, False, seen.image_digest, 0)

    def start_candidate(self, role, *, timeout_seconds, image):
        return image.rsplit("@", 1)[1] if "@" in image else image

    def candidate_ready(self, role):
        return True

    def role_ready(self, role):
        return True

    def switch(self, *, timeout_seconds, image):
        digest = image.rsplit("@", 1)[1] if "@" in image else image
        for code in self.roles:
            self.roles[code] = RoleObservation(code, True, digest, 0)

    def worker_responds(self, role):
        return True

    def scheduler_last_tick_age_seconds(self, role):
        return 0

    def write_evidence(self, evidence):
        canonical = (
            json.dumps(dict(evidence), indent=2, sort_keys=True, default=str) + "\\n"
        ).encode("utf-8")
        records = self.deploy_dir / "evidence-records"
        records.mkdir(parents=True, exist_ok=True)
        record = records / (hashlib.sha256(canonical).hexdigest() + ".json")
        if not record.exists():
            record.write_bytes(canonical)
        return str(record)

    def read_evidence(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def bootstrap_principal_credential(self, bootstrap):
        # The probe wheel must conform to the WIDENED protocol or the installed
        # ADMIT below stops proving anything: a non-conforming implementation
        # fails at the seam rather than at the gate it is meant to exercise.
        # It reports `installed`, which is what a real provider reports for an
        # absent-to-present transition.
        from dotmac_deployment_foundation.deployment_evidence import StepStanding

        return StepStanding.INSTALLED

    def prune_images(self, *, retain):
        return None

    def emit_annotation(self, annotation):
        return None


def build():
    return ExecutionBindings(
        provider="probe-host",
        build_effects=lambda spec, deploy_dir: ProbeEffects(spec, deploy_dir),
        authorization_verifier=_Verifier(),
        evidence_policy=TrustPolicy(
            repository="michaelayoade/dotmac_starter_mt",
            accepted_key_ids=frozenset({KEY_ID}),
        ),
        evidence_verifier=_Signatures(),
        recovery_verifier=_Signatures(),
    )
"""


def _build_probe_wheel(workdir: Path) -> Path:
    """A real wheel, by hand: a zip with dist-info. No build backend, no index.

    pip installs it exactly as it installs any wheel, which is the point — the
    ADMIT must exercise real distribution metadata reaching
    `importlib.metadata`, not a fixture handed to a parameter.
    """
    import base64
    import zipfile

    name, version = "probe_deploy_bindings", "0.0.1"
    wheel = workdir / f"{name}-{version}-py3-none-any.whl"
    info = f"{name}-{version}.dist-info"
    members = {
        f"{name}.py": _PROBE_BINDINGS_SOURCE,
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name.replace('_', '-')}\n"
            f"Version: {version}\n"
        ),
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: release_facility probe\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{info}/entry_points.txt": (
            "[dotmac_deployment_foundation.execution_bindings]\n"
            f"probe-host = {name}:build\n"
        ),
    }
    record_lines = []
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for member, text in members.items():
            data = text.encode("utf-8")
            archive.writestr(member, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(
                b"="
            )
            record_lines.append(f"{member},sha256={digest.decode()},{len(data)}")
        record_lines.append(f"{info}/RECORD,,")
        archive.writestr(f"{info}/RECORD", "\n".join(record_lines) + "\n")
    return wheel


def _installed_admit_smoke(
    venv_python: Path, pip: Path, entry_point: str, descriptor: Path, workdir: Path
) -> None:
    """Item 11: the installed CLI, end to end — one real ADMIT, one planted
    refusal, through the SAME mechanism.

    a4's inadmissibility was discovered at exactly this seam: every refusal was
    provable on the installed artifact and no admit was representable, so the
    proof of the refusals said nothing about whether the CLI could ever work.
    The ADMIT half installs a real bindings wheel and drives a deploy to a
    successful, evidence-backed outcome; the refusal half UNINSTALLS it and
    drives the identical command to the refusal that names the discovery
    group. A refusal proven only in a world where discovery never worked would
    be the old vacancy back again.
    """
    script = _bin(venv_python, entry_point)
    target = "installed-admit-target"

    subprocess.run(
        [
            str(pip),
            "install",
            "--quiet",
            "--no-index",
            str(_build_probe_wheel(workdir)),
        ],
        check=True,
    )

    lock_dir = workdir / "lock"
    deploy_dir = workdir / "deploy"
    lock_dir.mkdir(exist_ok=True)
    deploy_dir.mkdir(exist_ok=True)

    # The prestate the probe effects will observe: every replicated role on
    # the previous digest. Written as a document because that is how the
    # off-host renderer receives it.
    prestate = subprocess.run(
        [
            str(venv_python),
            "-c",
            (
                "import json,sys\n"
                "from dotmac_deployment_foundation.spec import ProductDeploymentSpec\n"
                "spec = ProductDeploymentSpec.load(sys.argv[1])\n"
                "roles = [\n"
                "    {'role': role.code, 'image_digest': 'sha256:' + 'b' * 64}\n"
                "    for role in sorted(spec.roles, key=lambda r: r.code)\n"
                "    if role.replicas > 0\n"
                "]\n"
                "print(json.dumps({'roles': roles}))\n"
                "print(spec.to_canonical_document().sha256_digest(), "
                "file=sys.stderr)\n"
            ),
            str(descriptor),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    prestate_path = workdir / "admit-prestate.json"
    prestate_path.write_text(prestate.stdout, encoding="utf-8")
    descriptor_digest = prestate.stderr.strip()

    digest_run = subprocess.run(
        [
            str(script),
            "-f",
            str(descriptor),
            "execution-plan",
            "--target",
            target,
            "--operation",
            "deploy",
            "--prestate",
            str(prestate_path),
            "--format",
            "digest",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    plan_digest = digest_run.stdout.strip()

    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    receipt_path = workdir / "admit-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "plan_id": "00000000-0000-4000-8000-00000000ad17",
                "target_ref": target,
                "descriptor_digest": descriptor_digest,
                "execution_plan_digest": plan_digest,
                "control_plan_digest": "f" * 64,
                "execution_sequence": 7,
                "attempt_no": 1,
                "policy_code": "deployment.release-smoke",
                "policy_version": 1,
                "decision_ref": "approvals:decision:release-smoke",
                "approved_at": (now - _dt.timedelta(minutes=5))
                .isoformat()
                .replace("+00:00", "Z"),
                "expires_at": (now + _dt.timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z"),
                "control_version": "0.0.0-probe",
                "operation": "deploy",
            }
        ),
        encoding="utf-8",
    )

    deploy_argv = [
        str(script),
        "-f",
        str(descriptor),
        "deploy",
        "--target",
        target,
        "--authorization",
        str(receipt_path),
        "--provider",
        "probe-host",
        "--lock-dir",
        str(lock_dir),
        "--deploy-dir",
        str(deploy_dir),
        "--execute",
    ]

    admitted = subprocess.run(deploy_argv, capture_output=True, text=True)
    if admitted.returncode != 0:
        raise ReleaseRefused(
            "the installed CLI could not ADMIT through discovered bindings — "
            "the a4 defect, on these bytes:\n"
            + (admitted.stderr or admitted.stdout)[-2000:]
        )
    records = sorted((deploy_dir / "evidence-records").glob("*.json"))
    if not records:
        raise ReleaseRefused(
            "the admitted deploy left no evidence record, so the outcome "
            "cannot be inspected and the admit proves an exit code only"
        )
    outcomes = [json.loads(record.read_text(encoding="utf-8")) for record in records]
    successful = [outcome for outcome in outcomes if outcome.get("succeeded") is True]
    if len(successful) != 1:
        raise ReleaseRefused(
            "the admitted deploy must leave exactly one successful final evidence "
            f"record; observed {len(successful)} across {len(outcomes)} records"
        )
    outcome = successful[0]
    problems = []
    if outcome.get("execution_plan_digest") != plan_digest:
        problems.append("the evidence does not carry the frozen execution plan digest")
    if outcome.get("descriptor_digest") != descriptor_digest:
        problems.append("the evidence does not carry the descriptor digest")
    if outcome.get("control_plan_digest") != "f" * 64:
        problems.append("the evidence does not carry Control's plan digest")
    if problems:
        raise ReleaseRefused(
            "the installed admit ran but its evidence is wrong:\n  - "
            + "\n  - ".join(problems)
        )

    # ── the planted refusal: SAME command, bindings REMOVED ──
    subprocess.run(
        [str(pip), "uninstall", "--quiet", "-y", "probe-deploy-bindings"],
        check=True,
    )
    refused = subprocess.run(deploy_argv, capture_output=True, text=True)
    if refused.returncode == 0:
        raise ReleaseRefused(
            "with the bindings distribution UNINSTALLED, the installed CLI "
            "still deployed. The attestation gate is not holding on these "
            "bytes, and everything the admit proved is now proof of a bypass"
        )
    said = (refused.stderr or "") + (refused.stdout or "")
    if "execution_bindings" not in said:
        raise ReleaseRefused(
            "the no-bindings refusal does not name the discovery group, so an "
            "operator cannot know the fix is to install the assembly's "
            "bindings distribution:\n" + said[-800:]
        )
    print(
        f"{entry_point}: installed end-to-end ADMIT through discovered "
        "bindings, and the same command refused once they were removed"
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

    # The explicit first-deploy claim, as a document — the a5 contract binds
    # every plan to an observed prestate, and the smoke's fictitious target
    # has no containers by definition. Written out rather than defaulted,
    # because the absence of a prestate is exactly what the field refuses.
    prestate_path = workdir / "prestate.json"
    prestate_path.write_text('{"roles": []}\n', encoding="ascii")

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
                "--prestate",
                str(prestate_path),
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
        # The binding, on the same installed bytes. A wheel that renders a plan
        # and then executes without one is exactly the state this release
        # repairs, and only the artifact can say whether it still does.
        _execution_binding_smoke(venv_python, Path(tmp))
        # And the whole journey: install a real bindings wheel, ADMIT end to
        # end through the console script, uninstall it, watch the identical
        # command refuse. Item 11 of the a5 audit.
        _installed_admit_smoke(
            venv_python, venv_python.parent / "pip", entry_point, descriptor, Path(tmp)
        )
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
