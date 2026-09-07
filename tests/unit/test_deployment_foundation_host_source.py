"""Foundation binds its OWN artifact digest, and refuses source/artifact disagreement.

`_do_verify_revision` is the precedent these tests are written against. It reads
`org.opencontainers.image.revision` off the product image, refuses when it is
ABSENT (*"nothing connects the running bytes to a reviewable commit"*) and
refuses separately when it DISAGREES (*"One of the two is stale, and guessing
which would deploy an unreviewed tree"*). Those are two refusals rather than
one because they send a reader to two different repairs.

Every one of those gates is about the PRODUCT. None asks which Foundation is
performing them. Two digests about Foundation itself already existed —
`launcher._package_digest` hashing the package's `.py` SOURCE, and
`RehearsalReceiptV1.foundation_artifact_digest` naming a built WHEEL — in
incommensurable units, with nothing putting them in one sentence.

## Why every test in this file failed before `host_source.py` existed

Not by argument: at `6147618a` the module is absent. `grep -r HostSource`
over the whole repository returns nothing, and
`packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation/`
contains no `host_source.py`. Each test below fails at COLLECTION with
`ModuleNotFoundError`, which is the strongest form of "this did not pass
before" available — there was no code to pass it.

## The near-misses, and why silence is the right answer for them

Two plants below must produce NOTHING. A rule observed only refusing is
indistinguishable from a rule that refuses everything, and this one has a
specific bound set it must not exceed: the file set the artifact DECLARES.
An mtime cannot move the digest because `RECORD` has no mtime field, and a
file `RECORD` does not list is outside the set by definition. Both are asserted
as admits, not as absences.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.host_source import (
    ABSENT,
    DISAGREES,
    DISTRIBUTION,
    MALFORMED_SOURCE_REVISION,
    NO_RECEIPT,
    WRONG_KIND,
    CandidateReceipt,
    HostSource,
    InstalledArtifact,
    candidate_receipt_from_mapping,
    installed_distribution_version,
    read_installed_artifact,
    require_host_source,
    resolve_committed_candidate_receipt,
)

# ── the genuine article, in the exact shapes measured on a real install ─────
#
# `direct_url.json` below is byte-shaped after the one pip actually wrote for
# `dotmac_deployment_foundation-0.4.0a1-py3-none-any.whl` on this workstation.
# A fixture invented from the PEP text rather than from an installer's output
# is a fixture that tests the PEP.

WHEEL_SHA256 = "3b96b0511647ea5119d5329d4abd35940e05c3c872e9a611c620784a09f1c672"
SOURCE_SHA = "753a004e7f8dbab034d5d6ca565c680d931a5309"
VERSION = "0.4.0a1"

#: A digest that is neither the wheel's nor anything derived from a tree — the
#: "other candidate" a disagreement is about.
OTHER_WHEEL_SHA256 = hashlib.sha256(b"a different candidate wheel").hexdigest()


def _record_line(path: str, payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return f"{path},sha256={encoded.decode()},{len(payload)}"


#: Shaped after the real `RECORD`: a console script outside site-packages, the
#: dist-info files, and the package's own modules. The `RECORD` line carries an
#: empty hash, exactly as an installer writes it.
RECORD_ENTRIES: tuple[tuple[str, bytes], ...] = (
    ("../../../bin/dotmac-deploy", b"#!/usr/bin/env python\n"),
    (f"dotmac_deployment_foundation-{VERSION}.dist-info/METADATA", b"Name: x\n"),
    (
        f"dotmac_deployment_foundation-{VERSION}.dist-info/WHEEL",
        b"Wheel-Version: 1.0\n",
    ),
    ("dotmac_deployment_foundation/__init__.py", b"VERSION = 'x'\n"),
    ("dotmac_deployment_foundation/authorization.py", b"OPERATIONS = ()\n"),
)


def _record(order: tuple[int, ...] | None = None, extra: str = "") -> str:
    indices = order if order is not None else tuple(range(len(RECORD_ENTRIES)))
    lines = [_record_line(*RECORD_ENTRIES[index]) for index in indices]
    lines.append(f"dotmac_deployment_foundation-{VERSION}.dist-info/RECORD,,")
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def _direct_url(sha256: str = WHEEL_SHA256) -> str:
    return json.dumps(
        {
            "archive_info": {
                "hash": f"sha256={sha256}",
                "hashes": {"sha256": sha256},
            },
            "url": (
                "file:///tmp/build/dotmac_deployment_foundation-"
                f"{VERSION}-py3-none-any.whl"
            ),
        }
    )


class FakeInstall:
    """A fake INSTALLATION, not a fake reader.

    Every planted defect below is a state a real `.dist-info` directory can be
    in. Nothing here stubs out a decision — `read_installed_artifact` runs its
    real logic over real-shaped file contents.
    """

    def __init__(
        self,
        *,
        version: str = VERSION,
        files: Mapping[str, str] | None = None,
        installed: bool = True,
    ) -> None:
        self._version = version
        self._installed = installed
        self._files: dict[str, str] = dict(
            files
            if files is not None
            else {"RECORD": _record(), "direct_url.json": _direct_url()}
        )

    def version(self, distribution: str) -> str:
        if not self._installed:
            raise LookupError(f"No package metadata was found for {distribution}")
        return self._version

    def read_text(self, distribution: str, filename: str) -> str | None:
        if not self._installed:
            raise LookupError(f"No package metadata was found for {distribution}")
        return self._files.get(filename)


def _receipt(
    *, sha256: str = WHEEL_SHA256, source_sha: str = SOURCE_SHA
) -> CandidateReceipt:
    return candidate_receipt_from_mapping(_receipt_document(sha256, source_sha))


def _receipt_document(
    sha256: str = WHEEL_SHA256, source_sha: str = SOURCE_SHA
) -> dict[str, Any]:
    """The `CandidateArtifact.v1` field set, as committed under
    `docs/inventories/foundation-candidate-0.3.0a5.json`."""
    return {
        "schema": "CandidateArtifact.v1",
        "facility": DISTRIBUTION,
        "version": VERSION,
        "sha256": sha256,
        "source_sha": source_sha,
        "repository": "michaelayoade/dotmac_starter_mt",
        "artifact_id": "9954731961",
        "run_id": "33920058598",
    }


#: A source-tree digest, in `launcher._package_digest`'s exact algorithm and
#: exact output spelling: sha256 over sorted `relpath\0bytes\0`, prefixed.
def _source_tree_digest() -> str:
    running = hashlib.sha256()
    for path, payload in sorted(RECORD_ENTRIES):
        running.update(path.encode("utf-8"))
        running.update(b"\0")
        running.update(payload)
        running.update(b"\0")
    return f"sha256:{running.hexdigest()}"


# ── the admit control ───────────────────────────────────────────────────────


def test_a_genuine_artifact_matching_its_receipt_is_accepted() -> None:
    """THE ADMIT CONTROL. A rule observed only refusing is indistinguishable
    from one that refuses everything, and every other test in this file is a
    refusal."""
    bound = require_host_source(
        receipt=_receipt(),
        metadata=FakeInstall(),
        source_tree_digest=_source_tree_digest,
    )

    assert isinstance(bound, HostSource)
    assert bound.artifact_digest == Digest.parse(WHEEL_SHA256)
    assert bound.distribution == DISTRIBUTION
    assert bound.version == VERSION
    # The transitive link: no field on the HOST says this. It is reached only
    # through the receipt the digest identifies.
    assert bound.source_revision == SOURCE_SHA
    assert "direct_url.json" in bound.read_from


# ── planted defect 1: disagreement ──────────────────────────────────────────


def test_an_artifact_digest_disagreeing_with_the_receipt_is_refused() -> None:
    """PLANTED. The installer recorded one wheel; the receipt binds another.
    `_do_verify_revision`'s second refusal, about Foundation instead of the
    product image."""
    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(
            receipt=_receipt(sha256=OTHER_WHEEL_SHA256),
            metadata=FakeInstall(),
            source_tree_digest=_source_tree_digest,
        )

    assert refusal.value.code == DISAGREES
    message = str(refusal.value)
    # NAMING BOTH is the requirement. A refusal reporting one digest cannot be
    # acted on: the reader has no way to tell which side to go and look at.
    assert WHEEL_SHA256 in message, message
    assert OTHER_WHEEL_SHA256 in message, message
    assert SOURCE_SHA in message, message


def test_a_receipt_about_another_facility_does_not_bind_these_bytes() -> None:
    """A receipt is not made relevant by being present. One about
    `dotmac-deployment-control` describes other bytes entirely."""
    document = _receipt_document()
    document["facility"] = "dotmac-deployment-control"

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(
            receipt=candidate_receipt_from_mapping(document),
            metadata=FakeInstall(),
            source_tree_digest=_source_tree_digest,
        )

    assert refusal.value.code == DISAGREES
    assert "dotmac-deployment-control" in str(refusal.value)


def test_a_matching_digest_with_the_wrong_version_is_refused() -> None:
    """PLANTED — the exact admitted defect this finding closes: a matching
    digest previously bound regardless of `version`. `version="9.9.9"` here
    would, before this fix, sail through unopposed because only facility and
    digest were compared."""
    document = _receipt_document()
    document["version"] = "9.9.9"

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(
            receipt=candidate_receipt_from_mapping(document),
            metadata=FakeInstall(),
            source_tree_digest=_source_tree_digest,
        )

    assert refusal.value.code == DISAGREES
    message = str(refusal.value)
    # NAMING BOTH — which term failed, and against what.
    assert "9.9.9" in message, message
    assert VERSION in message, message


def test_a_matching_version_stays_silent_the_near_miss() -> None:
    """NEAR-MISS, MUST BE SILENT. A version that legitimately agrees with the
    installed distribution must not be refused — without this, a check that
    refused every version would pass the mismatch test above for the wrong
    reason."""
    bound = require_host_source(
        receipt=_receipt(),
        metadata=FakeInstall(version=VERSION),
        source_tree_digest=_source_tree_digest,
    )
    assert bound.version == VERSION


# ── planted defect 2: no digest at all, and it is NOT a disagreement ────────


def test_an_editable_install_has_no_artifact_digest_and_says_so() -> None:
    """PLANTED, and MEASURED rather than imagined. This is the exact
    `direct_url.json` an editable `dotmac_observability` carries on this
    workstation, and its real `RECORD` likewise lists a `.pth`, a console
    script and metadata — not one line of importable source."""
    install = FakeInstall(
        files={
            "RECORD": (
                "dotmac_deployment_foundation.pth,sha256=AAAA,93\n"
                f"dotmac_deployment_foundation-{VERSION}.dist-info/RECORD,,\n"
            ),
            "direct_url.json": json.dumps(
                {
                    "dir_info": {"editable": True},
                    "url": "file:///Users/x/management/dotmac_starter_mt",
                }
            ),
        }
    )

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(receipt=_receipt(), metadata=install)

    assert refusal.value.code == ABSENT
    assert "EDITABLE" in str(refusal.value)


def test_an_index_install_records_no_digest_and_that_is_its_own_refusal() -> None:
    """PLANTED. No `direct_url.json` at all — the ordinary shape of an install
    resolved from an index, and measured on the a5 wheel's real dist-info."""
    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(
            receipt=_receipt(), metadata=FakeInstall(files={"RECORD": _record()})
        )

    assert refusal.value.code == ABSENT
    assert "INDEX" in str(refusal.value)


def test_a_recorded_url_with_no_hash_is_absent_not_a_mismatch() -> None:
    """PEP 610 permits `archive_info` with no hashes. The one comparable value
    was never written down, and it cannot be recovered by hashing the tree."""
    install = FakeInstall(
        files={
            "RECORD": _record(),
            "direct_url.json": json.dumps(
                {"archive_info": {}, "url": "https://example.invalid/x.whl"}
            ),
        }
    )

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(receipt=_receipt(), metadata=install)

    assert refusal.value.code == ABSENT


def test_a_directory_install_is_absent_and_names_itself_a_checkout() -> None:
    """PLANTED. `pip install -e` is the EDITABLE branch tested above; a plain
    `pip install /path/to/checkout` (no `editable` key at all) is the sibling
    branch — a directory with no artifact and no digest any receipt binds."""
    install = FakeInstall(
        files={
            "RECORD": _record(),
            "direct_url.json": json.dumps(
                {"dir_info": {}, "url": "file:///Users/x/checkout"}
            ),
        }
    )

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(receipt=_receipt(), metadata=install)

    assert refusal.value.code == ABSENT
    assert "DIRECTORY" in str(refusal.value)


def test_a_missing_record_is_absent() -> None:
    """PLANTED. No `RECORD` at all — an installation whose own manifest is
    missing has no declared file set and nothing to call the bound set,
    independent of whatever `direct_url.json` says."""
    install = FakeInstall(files={"direct_url.json": _direct_url()})

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(receipt=_receipt(), metadata=install)

    assert refusal.value.code == ABSENT
    assert "RECORD" in str(refusal.value)


def test_a_direct_url_that_is_not_json_is_absent_not_an_uncoded_crash() -> None:
    """PLANTED. Corrupt `direct_url.json` bytes — a different fact from an
    absent file, and refused with the same code but a distinguishing
    message rather than an uncaught `json.JSONDecodeError`."""
    install = FakeInstall(
        files={"RECORD": _record(), "direct_url.json": "{not json at all"}
    )

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(receipt=_receipt(), metadata=install)

    assert refusal.value.code == ABSENT
    assert "not JSON" in str(refusal.value)


def test_a_direct_url_holding_a_non_object_is_absent() -> None:
    """PLANTED. Valid JSON, wrong shape — a bare list rather than the object
    PEP 610 defines."""
    install = FakeInstall(
        files={"RECORD": _record(), "direct_url.json": json.dumps([1, 2, 3])}
    )

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(receipt=_receipt(), metadata=install)

    assert refusal.value.code == ABSENT
    assert "not an object" in str(refusal.value)


def test_a_malformed_archive_hash_is_refused_not_an_uncoded_spec_error() -> None:
    """PLANTED — the second half of finding 2. `Digest.parse` raises a bare
    `SpecError` for an unparsable hash; this module promises `PreconditionFailed`
    with a coded refusal and classifies unreadable provenance under `ABSENT`.
    `pytest.raises(PreconditionFailed)` is itself the proof the conversion
    happened: before it, this exact input propagated an uncaught `SpecError`
    that this assertion could not have caught."""
    install = FakeInstall(
        files={
            "RECORD": _record(),
            "direct_url.json": json.dumps(
                {
                    "archive_info": {"hashes": {"sha256": "not-a-digest"}},
                    "url": "https://example.invalid/x.whl",
                }
            ),
        }
    )

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(receipt=_receipt(), metadata=install)

    assert refusal.value.code == ABSENT
    assert isinstance(refusal.value, PreconditionFailed)
    assert not isinstance(refusal.value, SpecError)


def test_a_distribution_that_is_not_installed_is_absent() -> None:
    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(receipt=_receipt(), metadata=FakeInstall(installed=False))

    assert refusal.value.code == ABSENT


def test_absent_and_disagreeing_are_different_refusals() -> None:
    """The property the two plants above exist to establish, asserted directly
    rather than left to be inferred from two separate tests.

    `_do_verify_revision` draws this line and conflating the two is the defect:
    'no digest' is repaired by installing from an artifact, 'wrong digest' by
    working out which of two records is stale. One code for both would send
    every reader to one of those and be wrong half the time."""
    codes = {ABSENT, WRONG_KIND, DISAGREES, NO_RECEIPT}
    assert len(codes) == 4, (
        "two refusal codes collided. Distinct causes sharing a code is exactly "
        "the conflation this file was written to prevent"
    )

    with pytest.raises(PreconditionFailed) as absent:
        require_host_source(
            receipt=_receipt(), metadata=FakeInstall(files={"RECORD": _record()})
        )
    with pytest.raises(PreconditionFailed) as disagreeing:
        require_host_source(
            receipt=_receipt(sha256=OTHER_WHEEL_SHA256),
            metadata=FakeInstall(),
            source_tree_digest=_source_tree_digest,
        )

    assert absent.value.code != disagreeing.value.code
    assert str(absent.value) != str(disagreeing.value)


# ── planted defect 3: the WRONG KIND of digest ──────────────────────────────


def test_a_source_tree_digest_is_refused_as_the_wrong_kind_not_a_mismatch() -> None:
    """PLANTED, and the assertion that matters is the NEGATIVE one.

    `launcher.identify_launcher().digest` is one attribute away from what this
    gate wants, in a different unit, and can never equal a wheel hash. Reported
    as a mismatch it would send an operator to rebuild a candidate that is
    perfectly fine. A refusal that misdescribes its cause is worse than none.
    """
    tree = _source_tree_digest()
    planted = InstalledArtifact(
        distribution=DISTRIBUTION,
        version=VERSION,
        artifact_digest=Digest.parse(tree),
        installed_content_digest=Digest.of(b"unrelated"),
        read_from="a caller that reached for the launcher digest",
    )

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(
            receipt=_receipt(),
            installed=planted,
            source_tree_digest=lambda: tree,
        )

    assert refusal.value.code == WRONG_KIND
    assert refusal.value.code != DISAGREES
    message = str(refusal.value)
    assert "SOURCE-TREE" in message, message
    assert "rebuilding will not fix it" in message, message


def test_an_installed_content_digest_is_also_the_wrong_kind() -> None:
    """The second wrong kind, and it exists because this module PRODUCES it.
    Same subject as the artifact, different unit — comparing it with a receipt
    would refuse every correct install."""
    reading = read_installed_artifact(DISTRIBUTION, metadata=FakeInstall())
    planted = InstalledArtifact(
        distribution=reading.distribution,
        version=reading.version,
        artifact_digest=reading.installed_content_digest,
        installed_content_digest=reading.installed_content_digest,
        read_from=reading.read_from,
    )

    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(
            receipt=_receipt(),
            installed=planted,
            source_tree_digest=_source_tree_digest,
        )

    assert refusal.value.code == WRONG_KIND
    assert "different unit" in str(refusal.value)


def test_a_probe_that_cannot_answer_does_not_admit() -> None:
    """The wrong-kind discrimination needs a probe, and a probe can fail. When
    it does, the check is disabled and NOTHING ELSE IS: a broken probe must not
    become a way past the disagreement refusal."""
    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(
            receipt=_receipt(sha256=OTHER_WHEEL_SHA256),
            metadata=FakeInstall(),
            source_tree_digest=lambda: "not a digest at all",
        )

    assert refusal.value.code == DISAGREES


# ── the transitive link ─────────────────────────────────────────────────────


def test_a_digest_with_no_receipt_behind_it_establishes_nothing() -> None:
    """The digest is not the claim; the LINK is. Admitting a digest that
    reaches no source revision reduces this gate to a formatting check."""
    with pytest.raises(PreconditionFailed) as refusal:
        require_host_source(
            receipt=None,
            metadata=FakeInstall(),
            source_tree_digest=_source_tree_digest,
        )

    assert refusal.value.code == NO_RECEIPT
    assert refusal.value.code not in {ABSENT, DISAGREES, WRONG_KIND}


def test_a_mapping_that_is_not_a_candidate_receipt_is_refused() -> None:
    """A gate satisfiable by any JSON file whose keys line up is not a gate.
    `SpecError`, not `PreconditionFailed`: a malformed receipt is a file a
    human edits, which is the split `errors.py` draws."""
    document = _receipt_document()
    document["schema"] = "RehearsalReceipt.v1"

    with pytest.raises(SpecError, match="CandidateArtifact.v1"):
        candidate_receipt_from_mapping(document)


@pytest.mark.parametrize(
    "field",
    [
        "facility",
        "version",
        "sha256",
        "source_sha",
        "repository",
        "run_id",
        "artifact_id",
    ],
)
def test_every_receipt_field_the_binding_needs_is_required(field: str) -> None:
    """PLANTED — extended to the candidate LOCATION coordinate. Before this,
    `repository`/`run_id`/`artifact_id` were read by nothing: a receipt
    missing all three parsed cleanly, so a `HostSource` could be bound with no
    way to trace which workflow run produced it."""
    document = _receipt_document()
    document[field] = ""

    with pytest.raises(SpecError, match=field):
        candidate_receipt_from_mapping(document)


def test_a_full_candidate_receipt_carries_its_location_coordinate() -> None:
    """NEAR-MISS, MUST BE SILENT — the admit half of the extension above. A
    receipt that legitimately carries all three location fields must parse
    and bind them onto both `CandidateReceipt` and the resulting
    `HostSource`, not merely tolerate their presence."""
    receipt = _receipt()
    assert receipt.repository == "michaelayoade/dotmac_starter_mt"
    assert receipt.run_id == "33920058598"
    assert receipt.artifact_id == "9954731961"

    bound = require_host_source(
        receipt=receipt,
        metadata=FakeInstall(),
        source_tree_digest=_source_tree_digest,
    )
    assert bound.repository == receipt.repository
    assert bound.run_id == receipt.run_id
    assert bound.artifact_id == receipt.artifact_id


@pytest.mark.parametrize(
    "source_sha",
    [
        "not-a-commit",
        "753a004",  # abbreviated — a real short SHA a human might paste
        "z" * 40,  # right length, not hex
        SOURCE_SHA + "0",  # one character too long
    ],
)
def test_a_source_revision_that_is_not_a_full_commit_is_refused(
    source_sha: str,
) -> None:
    """PLANTED — the other half of the admitted defect: `source_sha` was only
    checked for non-emptiness, so ``"not-a-commit"`` parsed cleanly. Matches
    `scripts/release_facility.py::candidate_source_revision`'s own 40-hex
    check on the identical field."""
    document = _receipt_document(source_sha=source_sha)

    with pytest.raises(SpecError) as refusal:
        candidate_receipt_from_mapping(document)

    assert refusal.value.code == MALFORMED_SOURCE_REVISION


def test_a_full_forty_hex_source_revision_stays_silent_the_near_miss() -> None:
    """NEAR-MISS, MUST BE SILENT. A genuine full commit — the near-miss that
    proves the check above is a SHAPE check, not a check that refuses every
    `source_sha`."""
    receipt = _receipt()
    assert receipt.source_revision == SOURCE_SHA


def test_the_committed_receipts_on_disk_parse(pytestconfig: pytest.Config) -> None:
    """The reader is pointed at the REAL documents, not only at fixtures.

    A parser proved solely against its own fixture proves that the fixture and
    the parser agree. `foundation-candidate-0.3.0a5.json` is a committed
    `CandidateArtifact.v1` this facility actually built.
    """
    root = pytestconfig.rootpath / "docs" / "inventories"
    receipts = sorted(root.glob("foundation-candidate-0.3.0a*.json"))
    assert receipts, (
        "no committed CandidateArtifact.v1 was found. A check over an empty "
        "set passes for the wrong reason"
    )
    for path in receipts:
        document = json.loads(path.read_text(encoding="utf-8"))
        parsed = candidate_receipt_from_mapping(document, where=path.name)
        assert parsed.facility == DISTRIBUTION
        assert len(parsed.source_revision) == 40


# ── near-miss 1: ordering and mtime, which must be SILENT ───────────────────


def test_reordered_record_lines_do_not_move_the_content_digest() -> None:
    """NEAR-MISS, MUST BE SILENT. The installer's write order is not a property
    of the artifact. A digest that moved with it would fire on every reinstall
    and be switched off within a week."""
    forward = read_installed_artifact(DISTRIBUTION, metadata=FakeInstall())
    reversed_record = FakeInstall(
        files={
            "RECORD": _record(order=tuple(reversed(range(len(RECORD_ENTRIES))))),
            "direct_url.json": _direct_url(),
        }
    )
    backward = read_installed_artifact(DISTRIBUTION, metadata=reversed_record)

    assert forward.installed_content_digest == backward.installed_content_digest
    assert forward.artifact_digest == backward.artifact_digest

    # And it ADMITS — the half that makes this a near-miss rather than an
    # absence of a check.
    bound = require_host_source(
        receipt=_receipt(),
        metadata=reversed_record,
        source_tree_digest=_source_tree_digest,
    )
    assert bound.source_revision == SOURCE_SHA


def test_no_mtime_can_reach_the_digest_because_record_has_no_mtime() -> None:
    """The mtime half of the near-miss, proved STRUCTURALLY rather than by
    touching a file. `RECORD` has exactly three fields — path, hash, size — so
    there is no channel an mtime could arrive through. A test that stat'd a
    temporary file would prove the fixture, not the format."""
    line = _record_line(*RECORD_ENTRIES[0])
    assert len(line.split(",")) == 3, line
    path, digest_field, size = line.split(",")
    assert digest_field.startswith("sha256=")
    assert size.isdigit()


# ── near-miss 2: a file outside the bound set, which must be SILENT ─────────


def test_a_file_outside_the_declared_set_does_not_move_either_digest() -> None:
    """NEAR-MISS, MUST BE SILENT, and this one states the boundary's LIMIT.

    The bound set is the file set the artifact DECLARES. A `.pyc`, a log, an
    operator's scratch file, a co-installed distribution — none of them is part
    of this artifact and none may move its digest. Saying so plainly is better
    than a guard that quietly claims more coverage than it has: this does NOT
    detect a file added beside the installation.
    """
    baseline = read_installed_artifact(DISTRIBUTION, metadata=FakeInstall())

    intruder = FakeInstall(
        files={
            "RECORD": _record(),
            "direct_url.json": _direct_url(),
            "UNRELATED.txt": "an operator left this here",
        }
    )
    observed = read_installed_artifact(DISTRIBUTION, metadata=intruder)

    assert observed.installed_content_digest == baseline.installed_content_digest
    assert observed.artifact_digest == baseline.artifact_digest

    bound = require_host_source(
        receipt=_receipt(),
        metadata=intruder,
        source_tree_digest=_source_tree_digest,
    )
    assert bound.source_revision == SOURCE_SHA


def test_the_content_digest_is_sensitive_to_a_file_INSIDE_the_declared_set() -> None:
    """The near-miss above is only meaningful if the digest moves for something.
    A content digest insensitive to everything is not a near-miss survivor, it
    is a constant."""
    baseline = read_installed_artifact(DISTRIBUTION, metadata=FakeInstall())
    tampered = FakeInstall(
        files={
            "RECORD": _record(extra=_record_line("x/injected.py", b"import os\n")),
            "direct_url.json": _direct_url(),
        }
    )
    observed = read_installed_artifact(DISTRIBUTION, metadata=tampered)

    assert observed.installed_content_digest != baseline.installed_content_digest


# ── finding 3: `read_from` names the spelling actually used ─────────────────


def test_read_from_names_the_modern_hashes_spelling_when_that_is_what_was_read() -> (
    None
):
    """The fixture's `direct_url.json` carries BOTH spellings (measured on a
    real install, which writes both); `_archive_hash` prefers `hashes.sha256`,
    so that is the path a truthful `read_from` must name."""
    reading = read_installed_artifact(DISTRIBUTION, metadata=FakeInstall())

    assert reading.read_from == "direct_url.json archive_info.hashes.sha256"


def test_read_from_names_the_legacy_hash_spelling_when_that_is_all_there_is() -> None:
    """PLANTED. `direct_url.json` here carries ONLY the legacy
    ``archive_info.hash`` spelling — no ``hashes`` key at all. Before this fix,
    `read_from` unconditionally reported ``archive_info.hashes.sha256``, which
    is a claim about a key this document does not even have: a reporting lie
    that sends an operator to inspect the wrong line of the file mid-incident."""
    install = FakeInstall(
        files={
            "RECORD": _record(),
            "direct_url.json": json.dumps(
                {
                    "archive_info": {"hash": f"sha256={WHEEL_SHA256}"},
                    "url": "https://example.invalid/x.whl",
                }
            ),
        }
    )

    reading = read_installed_artifact(DISTRIBUTION, metadata=install)

    assert reading.artifact_digest == Digest.parse(WHEEL_SHA256)
    assert reading.read_from == "direct_url.json archive_info.hash"
    assert "hashes.sha256" not in reading.read_from

    # And the full binding still admits through this spelling — the finding is
    # about reporting, not about a second, weaker acceptance path.
    bound = require_host_source(
        receipt=_receipt(),
        metadata=install,
        source_tree_digest=_source_tree_digest,
    )
    assert bound.read_from == "direct_url.json archive_info.hash"


# ── the units, stated once so they cannot quietly converge ──────────────────


def test_the_artifact_digest_and_the_source_tree_digest_are_never_equal() -> None:
    """The premise the WRONG_KIND refusal rests on. If these two ever could be
    equal, that refusal would be wrong and the disagreement refusal would be
    the correct one — so the premise is asserted rather than assumed."""
    reading = read_installed_artifact(DISTRIBUTION, metadata=FakeInstall())
    tree = Digest.parse(_source_tree_digest())

    assert reading.artifact_digest != tree
    assert reading.installed_content_digest != tree
    assert reading.artifact_digest != reading.installed_content_digest


# ── resolving the receipt from a committed location, not a free path ───────


def test_installed_distribution_version_reads_the_supplied_metadata() -> None:
    assert installed_distribution_version(metadata=FakeInstall()) == VERSION


def test_installed_distribution_version_is_none_when_nothing_is_installed() -> None:
    """NEAR-MISS shape check: this must return `None`, never raise — the
    caller resolving a receipt path has a `read_installed_artifact`/
    `require_host_source` call downstream that raises the AUTHORITATIVE
    refusal; duplicating it here would be a second place that rule could
    drift from the one actually enforced."""
    assert installed_distribution_version(metadata=FakeInstall(installed=False)) is None


def test_resolve_committed_candidate_receipt_reads_the_committed_document(
    tmp_path,
) -> None:
    """PLANTED — the admit control for defect 2's fix. A committed document
    at the DERIVED path parses exactly like one loaded by a caller-chosen
    path used to."""
    (tmp_path / f"foundation-candidate-{VERSION}.json").write_text(
        json.dumps(_receipt_document()), encoding="utf-8"
    )

    receipt = resolve_committed_candidate_receipt(VERSION, receipts_dir=tmp_path)

    assert receipt is not None
    assert receipt.facility == DISTRIBUTION
    assert receipt.version == VERSION
    assert receipt.artifact_digest == Digest.parse(WHEEL_SHA256)


def test_resolve_committed_candidate_receipt_is_none_for_an_uncommitted_version(
    tmp_path,
) -> None:
    """The exact shape a free-path `--host-source-receipt` COULD have named
    any file for. Here, no file at the derived path means no receipt — there
    is no fallback that searches, guesses, or accepts a near-miss filename."""
    (tmp_path / f"foundation-candidate-{VERSION}.json").write_text(
        json.dumps(_receipt_document()), encoding="utf-8"
    )

    assert resolve_committed_candidate_receipt("9.9.9", receipts_dir=tmp_path) is None


def test_resolve_committed_candidate_receipt_cannot_be_pointed_at_an_arbitrary_file(
    tmp_path,
) -> None:
    """PLANTED — the exact bypass this replaces. A caller who authors a
    document for a DIFFERENT version and tries to pass it off as this
    version's receipt (the free-path attack: name any file, any content)
    finds it invisible: the filename is derived from `version`, never taken
    from the document or from a caller-chosen name."""
    forged = _receipt_document()
    forged["version"] = "9.9.9"
    (tmp_path / "anything-i-like.json").write_text(json.dumps(forged), encoding="utf-8")

    assert resolve_committed_candidate_receipt(VERSION, receipts_dir=tmp_path) is None


def test_resolve_committed_candidate_receipt_reads_the_real_committed_directory() -> (
    None
):
    """The reader is pointed at the REAL default directory, not only at a
    fixture — mirroring `test_the_committed_receipts_on_disk_parse` for the
    resolver rather than the parser."""
    receipt = resolve_committed_candidate_receipt(
        "0.3.0a5",
        receipts_dir=Path(__file__).resolve().parents[2] / "docs" / "inventories",
    )
    assert receipt is not None
    assert receipt.facility == DISTRIBUTION
    assert len(receipt.source_revision) == 40
