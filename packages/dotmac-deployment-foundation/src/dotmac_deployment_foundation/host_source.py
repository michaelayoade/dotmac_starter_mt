"""What Foundation itself is, on the host it is about to change.

Every other identity gate in this facility is about the PRODUCT: `verify_image`
asks whether the product image is present, `_do_verify_revision` asks whether it
carries a revision label agreeing with the descriptor, `_do_verify_manifest`
asks the same of the module set. Not one of them asks the question that comes
before all of them — *which Foundation is asking?*

`launcher.py` gets closest and stops deliberately short. It refuses a facility
loaded from inside the tree being deployed, and it exposes a
`LauncherIdentity.digest`, but that digest is a hash over the package's own
`.py` files: a SOURCE-TREE digest. Its own docstring says so plainly — the
strong version of the property "lives outside the process: the launcher digest
recorded in the release receipt, compared by whoever starts the run". Nothing
in this package was that "whoever", and nothing performed that comparison.

Meanwhile `RehearsalReceiptV1.foundation_artifact_digest` names a BUILT WHEEL —
`sha256_of(candidate-dist/*.whl)`, computed once in CI. So two digests existed
about the same facility, in different units, and no code path put them in the
same sentence. A source-tree hash cannot be compared with a wheel hash, and a
comparison nobody can make is a control nobody has.

## The subject, stated so it cannot drift

`HostSource` binds **the digest of the installed
`dotmac-deployment-foundation` distribution artifact.**

Not an OCI image. Not a checkout. Not a directory. Not a version string. A
version string is the thing this package has already paid for twice — read
`version.py` — because a name can cover two contracts and a digest cannot.

## Where the digest is READ FROM, and what that proves

From **PEP 610 `direct_url.json`**, key ``archive_info.hashes.sha256`` (with the
legacy ``archive_info.hash`` spelling ``sha256=…`` accepted), written into the
`.dist-info` directory by the installer at install time. That value is the
sha256 of the wheel FILE, which is exactly the unit
`foundation_artifact_digest` is in, which is what makes the comparison possible
at all.

It is read rather than recomputed because **it cannot be recomputed.** A wheel
is a zip; installing it unpacks it and the archive is gone. No amount of
hashing site-packages reproduces the hash of a file that no longer exists.
Hashing the unpacked tree instead is precisely `launcher._package_digest`'s
mistake repeated one directory to the left, and it is the mistake this module
exists to stop, so it is refused explicitly rather than avoided by convention
(see :data:`WRONG_KIND`).

**What it proves:** these bytes were installed FROM an artifact whose digest was
recorded by the installer, and that digest identifies exactly one
`CandidateArtifact.v1`, which names exactly one ``source_sha``. That is the
transitive link, and it is the same one `rehearsal.require_rehearsed_artifact`
already relies on rather than a second theory of provenance.

**What it does NOT prove**, stated because a provenance record read as an
integrity check is worse than no check:

* **Not that the files on disk still match the wheel.** PEP 610 records where
  the distribution came FROM. It is written once and never revisited; an
  editor that changed `authorization.py` in site-packages afterwards leaves it
  untouched. :attr:`InstalledArtifact.installed_content_digest` is the separate,
  host-recomputable reading for that question, and it is deliberately NOT the
  value compared with the receipt, because it is in a different unit.
* **Not that the installer was honest.** A hostile installer writes any string
  it likes. This is the ordinary-accident and stale-install guard `launcher.py`
  describes itself as, held to the same honesty.

## The editable install, which genuinely cannot answer

An editable install has **no artifact**, so it has no artifact digest, and this
module refuses rather than inventing one. Measured, not assumed — on this
workstation, an editable `dotmac_observability` carries::

    {"dir_info": {"editable": true}, "url": "file:///…/observability_fleet_worktree"}

no ``archive_info`` and no hash of anything; and its `RECORD` lists a `.pth`
file, a console script and the `.dist-info` metadata — **not one line of the
importable source**. So neither reading is available: there is no recorded
archive hash, and the file set the artifact declares does not contain the code.
The refusal names the editable install specifically, because "no digest" and
"you are running an editable checkout" send a reader to different repairs.

## The three refusals, in `_do_verify_revision`'s shape

That method is the precedent and the ordering is taken from it: it separates
*absent* (``"nothing connects the running bytes to a reviewable commit"``) from
*disagreeing* (``"One of the two is stale, and guessing which would deploy an
unreviewed tree"``) because those two send a reader to different repairs, and
conflating them is the defect. Here:

* :data:`ABSENT` — no artifact digest is available at all.
* :data:`WRONG_KIND` — a digest was supplied, and it is a digest of the wrong
  SUBJECT. Reported before disagreement and never as one: a source-tree hash
  will never equal a wheel hash, so calling it a mismatch would send an
  operator to rebuild a candidate when the actual repair is to pass a different
  value. A refusal that misdescribes its cause is worse than none.
* :data:`DISAGREES` — a genuine artifact digest that is not the one the
  candidate receipt binds.

and one more that is not a variant of any of them:

* :data:`NO_RECEIPT` — a digest with no `CandidateArtifact.v1` behind it. The
  digest is not the claim; the LINK is. An artifact digest nothing can resolve
  to a source revision establishes exactly nothing, and admitting it would make
  the gate a formatting check.

## Verified before the first effect

:func:`require_host_source` raises `PreconditionFailed`, the class whose
contract is "nothing has changed, so the caller may resolve the stated cause
and re-run the identical command". Every refusal here is reachable before any
mutation, because every input is a file already on disk.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from typing import Any, Final, Protocol, runtime_checkable

from .digest import CANONICAL_ALGORITHM, Digest
from .errors import PreconditionFailed, SpecError

__all__ = [
    "ABSENT",
    "DISAGREES",
    "DISTRIBUTION",
    "NO_RECEIPT",
    "WRONG_KIND",
    "CandidateReceipt",
    "HostSource",
    "InstalledArtifact",
    "InstalledMetadata",
    "candidate_receipt_from_mapping",
    "read_installed_artifact",
    "require_host_source",
]

#: The distribution whose artifact this module is about. Its own, by name:
#: a facility that took this as a parameter with no default would be a generic
#: digest comparator, and the question "which Foundation is asking?" has one
#: correct subject.
DISTRIBUTION: Final = "dotmac-deployment-foundation"

#: The schema of the document that carries the transitive link. Checked rather
#: than assumed — a mapping that merely happens to have a ``sha256`` key is not
#: a candidate receipt, and treating it as one is how a gate comes to be
#: satisfiable by any JSON file with the right-shaped keys.
CANDIDATE_SCHEMA: Final = "CandidateArtifact.v1"

# ── stable refusal codes ────────────────────────────────────────────────────
#
# `errors.py`: "Assert the code; read the prose." Four codes because there are
# four distinct repairs, and a test that can only match on prose cannot tell
# two refusals apart once somebody improves a sentence.

#: No artifact digest is available. The repair is to install from an artifact.
ABSENT: Final = "host-source-artifact-digest-absent"

#: A digest of the wrong subject was supplied. The repair is to pass a
#: different value — NOT to rebuild anything.
WRONG_KIND: Final = "host-source-artifact-digest-wrong-kind"

#: A genuine artifact digest that is not the one the receipt binds. The repair
#: is to work out which of the two is stale.
DISAGREES: Final = "host-source-artifact-digest-disagrees"

#: A digest with no candidate receipt behind it. The repair is to commit the
#: receipt for the build that produced these bytes.
NO_RECEIPT: Final = "host-source-candidate-receipt-absent"


# ── what the installed distribution can be asked ────────────────────────────


@runtime_checkable
class InstalledMetadata(Protocol):
    """The two questions this module asks of an installed distribution.

    A protocol rather than a direct `importlib.metadata` call, for the reason
    `launcher.py` gives about its own subject: the thing being checked must not
    also be the thing supplying the answer to a test. A fake here is a fake
    INSTALLATION, which is what lets the editable case, the index case and the
    hostile case be exercised without installing four distributions.
    """

    def version(self, distribution: str) -> str:
        """The installed version, or raise if it is not installed."""

    def read_text(self, distribution: str, filename: str) -> str | None:
        """A file from the `.dist-info` directory, or `None` if absent."""


class _ImportlibMetadata:
    """The real reader. Imports `importlib.metadata` lazily so that a caller
    supplying its own never pays for, or is affected by, the import."""

    def version(self, distribution: str) -> str:
        import importlib.metadata as md

        try:
            return md.version(distribution)
        except md.PackageNotFoundError as exc:
            raise LookupError(str(exc)) from exc

    def read_text(self, distribution: str, filename: str) -> str | None:
        import importlib.metadata as md

        try:
            return md.distribution(distribution).read_text(filename)
        except md.PackageNotFoundError as exc:
            raise LookupError(str(exc)) from exc


@dataclasses.dataclass(frozen=True, slots=True)
class InstalledArtifact:
    """One reading of the installed distribution artifact.

    Two digests, in two units, and the separation is the point rather than an
    inconvenience — the whole defect this module repairs was two digests about
    one facility with nothing saying which was which.
    """

    #: The distribution name, as installed.
    distribution: str
    #: The installed version. Carried for the REFUSAL MESSAGE, never compared:
    #: `version.py` is a hundred lines of why a version name is not an
    #: identity. It tells an operator which install they are looking at.
    version: str
    #: sha256 of the wheel FILE, as recorded by the installer. This is the one
    #: compared with the receipt, because it is the one in the receipt's unit.
    artifact_digest: Digest
    #: A canonical digest over the file set the artifact DECLARES — the sorted
    #: ``path,hash,size`` triples of `RECORD`. Recomputable on the host, and in
    #: a different unit from :attr:`artifact_digest`, so it is never compared
    #: with a receipt. It is what makes "the bound set" a real set: a file that
    #: `RECORD` does not list is outside it and cannot move this value, and
    #: neither can an mtime, which appears nowhere in `RECORD`.
    installed_content_digest: Digest
    #: Exactly which key of which file the artifact digest came from, so a
    #: refusal can be traced to a line rather than to "the metadata".
    read_from: str


@dataclasses.dataclass(frozen=True, slots=True)
class CandidateReceipt:
    """The `CandidateArtifact.v1` facts that make the link, and only those.

    Deliberately not the whole document and deliberately not an import of
    `rehearsal`: this needs three fields, and a type that carried the rest
    would invite a later comparison against a field that is not part of the
    binding.
    """

    facility: str
    version: str
    artifact_digest: Digest
    source_revision: str


def candidate_receipt_from_mapping(
    document: Mapping[str, Any], *, where: str = "candidate receipt"
) -> CandidateReceipt:
    """Read a committed `CandidateArtifact.v1`, refusing anything else.

    `SpecError` rather than `PreconditionFailed`: a malformed receipt is a file
    a human edits, which is exactly the split `errors.py` draws.
    """
    schema = str(document.get("schema", ""))
    if schema != CANDIDATE_SCHEMA:
        raise SpecError(
            f"{where}: declares schema {schema!r}, expected "
            f"{CANDIDATE_SCHEMA!r}. A mapping with a 'sha256' key is not a "
            "candidate receipt; accepting one would make this gate satisfiable "
            "by any JSON file whose keys happen to line up",
            where=where,
        )
    for field in ("facility", "version", "sha256", "source_sha"):
        if not str(document.get(field, "")).strip():
            raise SpecError(
                f"{where}: carries no {field!r}. Every one of the four is "
                "load-bearing — without it the receipt cannot say which "
                "facility, which bytes, or which tree",
                where=where,
            )
    return CandidateReceipt(
        facility=str(document["facility"]),
        version=str(document["version"]),
        artifact_digest=Digest.parse(str(document["sha256"]), where=f"{where}.sha256"),
        source_revision=str(document["source_sha"]),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class HostSource:
    """Foundation's own identity on this host, bound to a reviewable tree.

    Constructible only through :func:`require_host_source`, in the sense that
    every check lives there; a dataclass cannot forbid its own constructor, and
    pretending otherwise with a private sentinel would be theatre. What it CAN
    do is carry no field that was not checked, so that holding one of these is
    not a claim about anything unverified.
    """

    distribution: str
    version: str
    #: The digest that was verified. One value, agreed by both sides.
    artifact_digest: Digest
    #: The revision the receipt names for that digest. Reached transitively —
    #: no field on the host says this, and no field on the host should.
    source_revision: str
    #: Where the host's half came from, for a log line that can be traced.
    read_from: str

    def __str__(self) -> str:
        return (
            f"{self.distribution} {self.version} "
            f"artifact {self.artifact_digest} built from {self.source_revision}"
        )


# ── reading the host's half ─────────────────────────────────────────────────


def _record_digest(record: str) -> Digest:
    """A canonical digest over `RECORD`'s declared file set.

    Sorted, so the installer's write order cannot move it. Only the three
    fields `RECORD` actually has, so there is nothing here an mtime could reach.
    `RECORD`'s own line carries an empty hash by construction and is dropped
    rather than hashed as empty, which would make the value depend on the
    distribution's own directory name.
    """
    rows = []
    for line in record.splitlines():
        text = line.strip()
        if not text:
            continue
        path, _, rest = text.partition(",")
        digest_field, _, size = rest.partition(",")
        if not digest_field:
            # The RECORD line itself, and any other unhashed entry.
            continue
        rows.append(f"{path},{digest_field},{size}")
    payload = "\n".join(sorted(rows)).encode("utf-8")
    return Digest.of(payload)


def _archive_hash(archive: Mapping[str, Any]) -> str:
    """The sha256 out of `archive_info`, in either spelling PEP 610 allows."""
    hashes = archive.get("hashes")
    if isinstance(hashes, Mapping):
        value = hashes.get(CANONICAL_ALGORITHM)
        if isinstance(value, str) and value.strip():
            return value.strip()
    legacy = archive.get("hash")
    if isinstance(legacy, str) and legacy.strip():
        algorithm, separator, body = legacy.strip().partition("=")
        if separator and algorithm.lower() == CANONICAL_ALGORITHM:
            return body
    return ""


def read_installed_artifact(
    distribution: str = DISTRIBUTION,
    *,
    metadata: InstalledMetadata | None = None,
) -> InstalledArtifact:
    """Read the installed artifact's digest, or refuse saying which way it failed.

    Every refusal is :data:`ABSENT` — there is exactly one repair family here
    ("install from an artifact whose digest the installer records") — but the
    prose distinguishes not-installed, editable, directory, index and
    hash-less, because those are five different things to go and look at.
    """
    reader = metadata if metadata is not None else _ImportlibMetadata()

    try:
        version = reader.version(distribution)
    except LookupError as exc:
        raise PreconditionFailed(
            f"{distribution} is not installed in this interpreter, so nothing "
            "identifies the facility performing this run. Foundation cannot "
            f"read its own artifact digest from a distribution that is absent "
            f"({exc})",
            code=ABSENT,
        ) from exc

    record = reader.read_text(distribution, "RECORD")
    if not record or not record.strip():
        raise PreconditionFailed(
            f"{distribution} {version} has no readable RECORD, so the artifact "
            "declares no file set and there is nothing to call the bound set. "
            "An installation whose own manifest is missing cannot be described",
            code=ABSENT,
        )

    raw = reader.read_text(distribution, "direct_url.json")
    if raw is None:
        raise PreconditionFailed(
            f"{distribution} {version} carries no direct_url.json, so the "
            "installer recorded no artifact digest. This is the ordinary shape "
            "of an install resolved from an INDEX: pip writes PEP 610 metadata "
            "only for a direct URL or path. Nothing connects these bytes to a "
            "candidate receipt, and a wheel hash cannot be recomputed from an "
            "unpacked tree — the archive no longer exists",
            code=ABSENT,
        )

    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise PreconditionFailed(
            f"{distribution} {version} has a direct_url.json that is not JSON "
            f"({exc}). Refusing rather than falling back to 'no digest': a "
            "corrupt provenance record and an absent one are different facts, "
            "and reporting the second would hide the first",
            code=ABSENT,
        ) from exc

    if not isinstance(document, Mapping):
        raise PreconditionFailed(
            f"{distribution} {version} has a direct_url.json holding "
            f"{type(document).__name__}, not an object. PEP 610 defines one "
            "shape and this is not it",
            code=ABSENT,
        )

    directory = document.get("dir_info")
    if isinstance(directory, Mapping):
        if directory.get("editable"):
            raise PreconditionFailed(
                f"{distribution} {version} is an EDITABLE install from "
                f"{document.get('url', 'an unrecorded path')}. There is no "
                "artifact, so there is no artifact digest: the wheel was never "
                "built, and the RECORD lists a .pth file and metadata rather "
                "than the importable source, so even the declared file set does "
                "not contain the code. This is a development installation being "
                "asked to prove a release property. Install the built "
                "distribution",
                code=ABSENT,
            )
        raise PreconditionFailed(
            f"{distribution} {version} was installed from the DIRECTORY "
            f"{document.get('url', 'an unrecorded path')} rather than from an "
            "artifact. A directory has no digest that any receipt binds — that "
            "is a checkout, and a checkout is expressly not this module's "
            "subject",
            code=ABSENT,
        )

    archive = document.get("archive_info")
    if not isinstance(archive, Mapping):
        raise PreconditionFailed(
            f"{distribution} {version} has a direct_url.json with neither "
            "archive_info nor dir_info, so it records no provenance of any "
            "kind. There is nothing here to read a digest out of",
            code=ABSENT,
        )

    recorded = _archive_hash(archive)
    if not recorded:
        raise PreconditionFailed(
            f"{distribution} {version} was installed from "
            f"{document.get('url', 'an unrecorded URL')} but the installer "
            "recorded no sha256 for it. PEP 610 permits that, and it means the "
            "one value that could be compared with a candidate receipt was "
            "never written down. It cannot be recovered by hashing the "
            "installed tree: that is a different subject, not a cheaper route "
            "to this one",
            code=ABSENT,
        )

    return InstalledArtifact(
        distribution=distribution,
        version=version,
        artifact_digest=Digest.parse(
            recorded, where=f"{distribution} direct_url.json archive_info"
        ),
        installed_content_digest=_record_digest(record),
        read_from="direct_url.json archive_info.hashes.sha256",
    )


# ── the binding ─────────────────────────────────────────────────────────────


def _default_source_tree_digest() -> str:
    """The source-tree digest of the facility loaded in THIS process.

    Not a general theory of what a source-tree digest looks like — there is
    none, since every sha256 has the same shape. It is the one specific wrong
    value a caller in this codebase can actually reach for: `launcher.py`
    publishes it as `LauncherIdentity.digest`, one attribute away from the
    thing wanted here, and the whole reason this module exists is that the two
    were never distinguishable at a call site.
    """
    from .launcher import identify_launcher

    return identify_launcher().digest


def require_host_source(
    *,
    receipt: CandidateReceipt | None,
    installed: InstalledArtifact | None = None,
    distribution: str = DISTRIBUTION,
    metadata: InstalledMetadata | None = None,
    source_tree_digest: Callable[[], str] | None = None,
) -> HostSource:
    """Bind the installed artifact to the revision it was built from, or refuse.

    Called before the first effect. Every refusal is `PreconditionFailed`, so
    nothing has changed and the identical command can be re-run once the stated
    cause is resolved.

    ``installed`` is read from the interpreter when not supplied; passing one is
    how a caller that already read it avoids a second read, and how a test
    plants a digest that no real installation would produce.
    """
    reading = (
        installed
        if installed is not None
        else read_installed_artifact(distribution, metadata=metadata)
    )
    offered = reading.artifact_digest

    # WRONG KIND, before disagreement and never reported as one. Ordering is
    # the whole point: both of these values are 64 hex characters and neither
    # will ever equal a wheel hash, so a mismatch message would send an
    # operator to rebuild a candidate that is perfectly fine.
    probe = (
        source_tree_digest
        if source_tree_digest is not None
        else _default_source_tree_digest
    )
    try:
        tree = Digest.parse(probe(), where="loaded facility source tree")
    except SpecError:
        # A probe that cannot answer must not be able to ADMIT. It disables
        # this one discrimination and nothing else; the remaining refusals are
        # unaffected, which is why this is a pass rather than a failure.
        tree = None
    if tree is not None and offered == tree:
        raise PreconditionFailed(
            f"the digest offered as {reading.distribution}'s artifact digest "
            f"({offered}) is the SOURCE-TREE digest of the facility loaded in "
            "this process — `launcher.identify_launcher().digest`, a sha256 "
            "over the package's own .py files. That is a different subject, "
            "not a wrong value: it hashes an unpacked directory, and a "
            "candidate receipt records the sha256 of a wheel FILE. The two can "
            "never be equal, so this is not a mismatch and rebuilding will not "
            "fix it. Read the artifact digest from the installer's PEP 610 "
            "direct_url.json",
            code=WRONG_KIND,
        )
    if offered == reading.installed_content_digest:
        raise PreconditionFailed(
            f"the digest offered as {reading.distribution}'s artifact digest "
            f"({offered}) is its INSTALLED-CONTENT digest — the canonical hash "
            "over RECORD's declared file set. Same subject, different unit: it "
            "describes the unpacked installation, while a candidate receipt "
            "records the sha256 of the wheel that was unpacked. Comparing them "
            "would refuse every correct install",
            code=WRONG_KIND,
        )

    # THE TRANSITIVE LINK. Checked after the kind and before the value, because
    # a receipt is what makes the value mean anything at all.
    if receipt is None:
        raise PreconditionFailed(
            f"{reading.distribution} {reading.version} presents artifact "
            f"{offered} and no CandidateArtifact.v1 stands behind it. The "
            "digest is not the claim — the LINK is. Nothing here reaches a "
            "source revision, so nothing establishes that these bytes were "
            "built from a reviewable tree, and admitting the digest alone "
            "would reduce this gate to a formatting check",
            code=NO_RECEIPT,
        )

    if receipt.facility != reading.distribution:
        raise PreconditionFailed(
            f"the receipt is about {receipt.facility!r} and the installed "
            f"distribution is {reading.distribution!r}. A receipt for another "
            "facility binds another facility's bytes; it says nothing about "
            "these",
            code=DISAGREES,
        )

    if offered != receipt.artifact_digest:
        raise PreconditionFailed(
            f"{reading.distribution} {reading.version} was installed from "
            f"artifact {offered}, and the CandidateArtifact.v1 for "
            f"{receipt.facility} {receipt.version} binds "
            f"{receipt.artifact_digest} to source revision "
            f"{receipt.source_revision}. One of the two is stale, and guessing "
            "which would run a Foundation nobody reviewed against a host "
            "nobody expected to be changed by it",
            code=DISAGREES,
        )

    return HostSource(
        distribution=reading.distribution,
        version=reading.version,
        artifact_digest=offered,
        source_revision=receipt.source_revision,
        read_from=reading.read_from,
    )
