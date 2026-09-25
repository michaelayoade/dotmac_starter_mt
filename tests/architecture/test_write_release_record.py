"""The post-release record is written by a script, not by remembering.

`scripts/write_release_record.py` exists because between 2026-08-21 and
2026-08-22 a publication left `main` red FOUR times — twice for the same
distribution, one version apart. The rule is stated verbatim in the tests that
catch it (`test_the_ledger_holds_no_stale_absolution`,
`test_the_tag_oracle_is_usable_and_complete`) and nothing enforced it, so the
only thing between a release and a red `main` was somebody remembering.

These tests hold the writer to the properties that make it safe to run from a
release workflow immediately after tagging:

- it never writes a record for a tag that does not exist;
- it never removes a row describing a DIFFERENT version;
- it is idempotent, so a re-run after a partial repair converges;
- it edits the JSON as text, so the ledger's prose survives untouched.
- it enrols every migration-history map on a distribution's first release;
- it moves incremental migrations from ``UNRELEASED`` into the immutable tag;
- it validates both record files before writing either one.

The digest half is proven against a real published tag rather than a fixture:
a hand-built record already in `RELEASED_TAGS` is the oracle, and the writer
has to reproduce it exactly.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "write_release_record.py"
LEGACY_MODULE_BASELINE_SHA256 = (
    "fd5568c0f8f7eb511534a10e692de7cb43bcadb5a451c8f534973357f9534bd9"
)

#: A real, published, already-recorded release — the oracle for the digest path.
KNOWN_TAG = "dotmac-integration-v0.1.0a12"
KNOWN_DISTRIBUTION = "dotmac-integration"
KNOWN_PACKAGE_DIR = "packages/dotmac-integration"
KNOWN_IMPORT_NAME = "dotmac_integration"

AGREEMENTS_TAG = "dotmac-commercial-agreements-v0.1.0a1"
AGREEMENTS_DISTRIBUTION = "dotmac-commercial-agreements"
AGREEMENTS_PACKAGE_DIR = "packages/dotmac-commercial-agreements"
AGREEMENTS_IMPORT_NAME = "dotmac_commercial_agreements"


def _writer():
    spec = importlib.util.spec_from_file_location("write_release_record", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recorded_entry(writer, tag: str):
    """The committed `RELEASED_TAGS` entry for `tag`, as data."""
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    block = re.search(r"RELEASED_TAGS: [^=]+= (\{.*?\n\})\n\n", text, re.DOTALL)
    assert block is not None, "RELEASED_TAGS is no longer a literal this can read"
    return ast.literal_eval(block.group(1))[tag]


def _ledger_text(writer) -> str:
    return writer.LEDGER.read_text(encoding="utf-8")


def _module_release_verification_text(writer) -> str:
    return writer.MODULE_RELEASE_VERIFICATIONS.read_text(encoding="utf-8")


def _legacy_module_text(writer) -> str:
    return writer.MODULE_RELEASE_LEGACY.read_text(encoding="utf-8")


# ── Module artifact coordinates ─────────────────────────────────────────────────────


def test_module_release_verification_appends_exact_immutable_coordinates() -> None:
    writer = _writer()
    before = _module_release_verification_text(writer)
    after, added = writer.add_module_release_verification(
        before,
        distribution="dotmac-approvals",
        version="0.1.0a7",
        tag="dotmac-approvals-v0.1.0a7",
        tag_object="1" * 40,
        peeled_commit="2" * 40,
        wheel_filename="dotmac_approvals-0.1.0a7-py3-none-any.whl",
        wheel_sha256="3" * 64,
    )
    assert added
    document = json.loads(after)
    assert document["schema"] == "ModuleReleaseVerifications.v1"
    assert document["releases"] == [
        {
            "distribution": "dotmac-approvals",
            "version": "0.1.0a7",
            "tag": "dotmac-approvals-v0.1.0a7",
            "tag_object": "1" * 40,
            "peeled_commit": "2" * 40,
            "status": "released",
            "pinnable": True,
            "sha256": {"dotmac_approvals-0.1.0a7-py3-none-any.whl": "3" * 64},
        }
    ]

    unchanged, added_again = writer.add_module_release_verification(
        after,
        distribution="dotmac-approvals",
        version="0.1.0a7",
        tag="dotmac-approvals-v0.1.0a7",
        tag_object="1" * 40,
        peeled_commit="2" * 40,
        wheel_filename="dotmac_approvals-0.1.0a7-py3-none-any.whl",
        wheel_sha256="3" * 64,
    )
    assert not added_again
    assert unchanged == after


def test_module_release_verification_refuses_coordinate_rewrite() -> None:
    writer = _writer()
    after, _ = writer.add_module_release_verification(
        _module_release_verification_text(writer),
        distribution="dotmac-approvals",
        version="0.1.0a7",
        tag="dotmac-approvals-v0.1.0a7",
        tag_object="1" * 40,
        peeled_commit="2" * 40,
        wheel_filename="dotmac_approvals-0.1.0a7-py3-none-any.whl",
        wheel_sha256="3" * 64,
    )
    with pytest.raises(writer.ReleaseRecordError, match="different coordinates"):
        writer.add_module_release_verification(
            after,
            distribution="dotmac-approvals",
            version="0.1.0a7",
            tag="dotmac-approvals-v0.1.0a7",
            tag_object="1" * 40,
            peeled_commit="2" * 40,
            wheel_filename="dotmac_approvals-0.1.0a7-py3-none-any.whl",
            wheel_sha256="4" * 64,
        )


def test_module_wheel_digest_requires_the_exact_single_build_once_wheel(
    tmp_path: Path,
) -> None:
    writer = _writer()
    wheel = tmp_path / "dotmac_approvals-0.1.0a7-py3-none-any.whl"
    wheel.write_bytes(b"exact build once bytes")
    assert writer.module_wheel_digest(
        str(tmp_path), distribution="dotmac-approvals", version="0.1.0a7"
    ) == (
        wheel.name,
        "554a5f552ff0125cc889363a77f316bd6f0234f47238916516cc6a4e1191ba46",
    )
    (tmp_path / "other-1.0-py3-none-any.whl").write_bytes(b"other")
    with pytest.raises(writer.ReleaseRecordError, match="exactly one wheel"):
        writer.module_wheel_digest(
            str(tmp_path), distribution="dotmac-approvals", version="0.1.0a7"
        )


def test_only_the_governed_module_lane_can_append_module_release_evidence() -> None:
    writer = _writer()
    writer.require_module_release_target("dotmac-approvals")
    with pytest.raises(writer.ReleaseRecordError, match="not governed"):
        writer.require_module_release_target("dotmac-kernel")


def test_module_release_workflow_carries_exact_wheel_into_the_recorder() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/release-module.yml").read_text()
    verify = workflow.split("  verify:", 1)[1]
    assert "Download exact built bytes for the publication record" in verify
    assert "name: ${{ inputs.module }}-dist" in verify
    assert "path: ${{ runner.temp }}/module-release-dist" in verify
    # The verify job's env block is the single place `runner.temp` is
    # evaluated; every run: body below references it as $ARTIFACT_DIR.
    assert "ARTIFACT_DIR: ${{ runner.temp }}/module-release-dist" in verify
    assert '--artifact-dir "${ARTIFACT_DIR}"' in verify
    compare = verify.index("Prove published bytes match the retained build artifact")
    tag = verify.index("Tag the verified release")
    assert compare < tag
    assert 'release_module.py compare-published "$MODULE"' in verify
    assert '--dist "${ARTIFACT_DIR}"' in verify
    assert "python scripts/tag_module_release.py" in verify

    wrapper = (PROJECT_ROOT / "scripts/open_release_record_pr.sh").read_text()
    assert '--artifact-dir) ARTIFACT_DIR="$2"' in wrapper
    assert 'ARGS+=(--artifact-dir "${ARTIFACT_DIR}")' in wrapper
    assert '[ "${GOVERNED_MODULE}" = "1" ] && [ -z "${ARTIFACT_DIR}" ]' in wrapper

    recovery = (
        PROJECT_ROOT / ".github/workflows/recover-module-release.yml"
    ).read_text()
    assert "RECOVERED_DIST: ${{ runner.temp }}/recovered-dist" in recovery
    recorder = recovery.split("- name: Open the post-release record", 1)[1]
    assert '--artifact-dir "$RECOVERED_DIST"' in recorder
    assert "release_module.py compare-published" in recovery
    assert "python scripts/tag_module_release.py" in recovery


def test_normal_compare_and_recovery_forwarding_detectors_are_sensitive() -> None:
    normal = (PROJECT_ROOT / ".github/workflows/release-module.yml").read_text()
    recovery = (
        PROJECT_ROOT / ".github/workflows/recover-module-release.yml"
    ).read_text()

    def compares_before_tag(source: str) -> bool:
        verify = source.split("  verify:", 1)[1]
        return (
            "release_module.py compare-published" in verify
            and '--dist "${ARTIFACT_DIR}"' in verify
            and verify.index("release_module.py compare-published")
            < verify.index("Tag the verified release")
        )

    def forwards_recovery_artifact(source: str) -> bool:
        return (
            '--artifact-dir "$RECOVERED_DIST"'
            in source.split("- name: Open the post-release record", 1)[1]
        )

    assert compares_before_tag(normal)
    assert not compares_before_tag(
        normal.replace("release_module.py compare-published", "verify-registry")
    )
    assert forwards_recovery_artifact(recovery)
    assert not forwards_recovery_artifact(
        recovery.replace('--artifact-dir "$RECOVERED_DIST"', "")
    )


def test_every_governed_module_tag_has_one_typed_evidence_class() -> None:
    """Tag-backed CI gate: deleting a new verification row is not a pass."""
    writer = _writer()
    writer.validate_module_release_inventory(
        _module_release_verification_text(writer), _legacy_module_text(writer)
    )


def test_pre_cutover_unverified_baseline_cannot_silently_grow() -> None:
    """Adding a future tag as 'legacy' requires an explicit guard change."""
    writer = _writer()
    source = writer.MODULE_RELEASE_LEGACY.read_bytes()
    assert hashlib.sha256(source).hexdigest() == LEGACY_MODULE_BASELINE_SHA256


def test_append_only_guard_refuses_valid_hash_mutation_deletion_and_reordering() -> (
    None
):
    writer = _writer()
    _, first, base, _ = _synthetic_module_inventory()
    second = dict(first)
    second.update(
        version="0.1.0a100",
        tag="dotmac-approvals-v0.1.0a100",
        sha256={"dotmac_approvals-0.1.0a100-py3-none-any.whl": "d" * 64},
    )
    base["releases"] = [first, second]
    before = json.dumps(base)

    appended = json.loads(before)
    third = dict(first)
    third.update(
        version="0.1.0a101",
        tag="dotmac-approvals-v0.1.0a101",
        sha256={"dotmac_approvals-0.1.0a101-py3-none-any.whl": "e" * 64},
    )
    appended["releases"].append(third)
    writer.require_module_release_verifications_append_only(
        before, json.dumps(appended), targets={"dotmac-approvals"}
    )

    changed_hash = json.loads(before)
    changed_hash["releases"][0]["sha256"] = {
        "dotmac_approvals-0.1.0a99-py3-none-any.whl": "f" * 64
    }
    for damaged in (
        changed_hash,
        {**base, "releases": [second]},
        {**base, "releases": [second, first]},
    ):
        with pytest.raises(
            writer.ReleaseRecordError, match="deleted, reordered or mutated"
        ):
            writer.require_module_release_verifications_append_only(
                before, json.dumps(damaged), targets={"dotmac-approvals"}
            )


def test_append_only_ci_uses_immutable_event_shas_and_full_history() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("  module-release-append-only:", 1)[1].split("\n  unit:", 1)[0]
    assert "fetch-depth: 0" in job
    assert "github.event.pull_request.base.sha" in job
    assert "github.event.before" in job
    assert job.count("check_module_release_verification_append_only.py --base") == 2
    assert "origin/main" not in job


def test_append_only_gate_refuses_missing_base_shallow_and_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    path = PROJECT_ROOT / "scripts/check_module_release_verification_append_only.py"
    spec = importlib.util.spec_from_file_location("module_release_append_only", path)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    base = "a" * 40

    def git_response(*args: str, fault: str):
        if args == ("rev-parse", "--is-shallow-repository"):
            return subprocess.CompletedProcess(
                args, 0, "true\n" if fault == "shallow" else "false\n", ""
            )
        if args[0:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(
                args, 128 if fault == "unresolvable" else 0, base + "\n", ""
            )
        if args[0:2] == ("merge-base", "--is-ancestor"):
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(
            args, 128 if fault == "missing" else 0, "", ""
        )

    for fault, refusal in (
        ("shallow", "shallow/unavailable"),
        ("unresolvable", "unresolvable"),
        ("missing", "no accepted"),
    ):
        monkeypatch.setattr(
            checker, "_git", lambda *args, fault=fault: git_response(*args, fault=fault)
        )
        with pytest.raises(checker.ReleaseRecordError, match=refusal):
            checker.check_append_only_base(base)


def _synthetic_module_inventory():
    tag = "dotmac-approvals-v0.1.0a99"
    row = {
        "distribution": "dotmac-approvals",
        "version": "0.1.0a99",
        "tag": tag,
        "tag_object": "a" * 40,
        "peeled_commit": "b" * 40,
        "status": "released",
        "pinnable": True,
        "sha256": {"dotmac_approvals-0.1.0a99-py3-none-any.whl": "c" * 64},
    }
    verified = {
        "$comment": "verified",
        "schema": "ModuleReleaseVerifications.v1",
        "releases": [row],
    }
    legacy = {
        "$comment": "historical, not verified",
        "schema": "ModuleReleaseLegacyUnverified.v1",
        "cutover_source_commit": "d" * 40,
        "tags": [],
    }
    return tag, row, verified, legacy


def test_missing_or_deleted_verified_row_is_refused_by_live_tag() -> None:
    writer = _writer()
    tag, row, verified, legacy = _synthetic_module_inventory()
    live = {tag: (row["tag_object"], row["peeled_commit"])}
    writer.validate_module_release_inventory(
        json.dumps(verified),
        json.dumps(legacy),
        live=live,
        targets={"dotmac-approvals"},
    )
    verified["releases"] = []
    with pytest.raises(writer.ReleaseRecordError, match="missing"):
        writer.validate_module_release_inventory(
            json.dumps(verified),
            json.dumps(legacy),
            live=live,
            targets={"dotmac-approvals"},
        )


def test_corrupt_duplicate_and_overlapping_module_rows_are_refused() -> None:
    writer = _writer()
    tag, row, verified, legacy = _synthetic_module_inventory()
    live = {tag: (row["tag_object"], row["peeled_commit"])}
    verified["releases"].append(dict(row))
    with pytest.raises(writer.ReleaseRecordError, match="duplicate"):
        writer.validate_module_release_inventory(
            json.dumps(verified),
            json.dumps(legacy),
            live=live,
            targets={"dotmac-approvals"},
        )
    verified["releases"] = [dict(row)]
    verified["releases"][0]["unknown"] = "silently dropped?"
    with pytest.raises(writer.ReleaseRecordError, match="wrong keys"):
        writer.parse_module_release_verifications(
            json.dumps(verified), targets={"dotmac-approvals"}
        )
    verified["releases"] = [row]
    legacy["tags"] = [
        {
            "tag": tag,
            "tag_object": row["tag_object"],
            "peeled_commit": row["peeled_commit"],
        }
    ]
    with pytest.raises(writer.ReleaseRecordError, match="overlap"):
        writer.validate_module_release_inventory(
            json.dumps(verified),
            json.dumps(legacy),
            live=live,
            targets={"dotmac-approvals"},
        )


def test_module_schema_refuses_duplicate_json_keys_and_noncanonical_wheel() -> None:
    writer = _writer()
    _, row, verified, _ = _synthetic_module_inventory()
    with pytest.raises(writer.ReleaseRecordError, match="repeats JSON key"):
        writer.parse_module_release_verifications(
            '{"schema":"ModuleReleaseVerifications.v1",'
            '"schema":"ModuleReleaseVerifications.v1","releases":[],"$comment":"x"}',
            targets={"dotmac-approvals"},
        )
    row["sha256"] = {"wrong-0.1.0a99-py3-none-any.whl": "c" * 64}
    with pytest.raises(writer.ReleaseRecordError, match="invalid wheel digest"):
        writer.parse_module_release_verifications(
            json.dumps(verified), targets={"dotmac-approvals"}
        )
    row["sha256"] = {"dotmac_approvals-0.1.0a99-py3-none-any.whl": "c" * 64}
    row["tag"] = "dotmac-approvals-v0.01.0a99"
    row["version"] = "0.01.0a99"
    with pytest.raises(writer.ReleaseRecordError, match="noncanonical"):
        writer.parse_module_release_verifications(
            json.dumps(verified), targets={"dotmac-approvals"}
        )


def test_lightweight_module_tag_is_not_accepted_as_annotated_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _writer()

    def fake_git(*args: str) -> str:
        if args[0] == "rev-parse":
            return "false\n"
        return "dotmac-approvals-v0.1.0a99|commit|" + "a" * 40 + "|\n"

    monkeypatch.setattr(writer, "_git", fake_git)
    with pytest.raises(writer.ReleaseRecordError, match="not an annotated"):
        writer.live_module_tag_refs(targets={"dotmac-approvals"})


def test_annotated_tag_object_or_peeled_commit_mutation_is_refused() -> None:
    writer = _writer()
    tag, row, verified, legacy = _synthetic_module_inventory()
    for live in (
        {tag: ("e" * 40, row["peeled_commit"])},
        {tag: (row["tag_object"], "f" * 40)},
    ):
        with pytest.raises(writer.ReleaseRecordError, match="object or peeled"):
            writer.validate_module_release_inventory(
                json.dumps(verified),
                json.dumps(legacy),
                live=live,
                targets={"dotmac-approvals"},
            )


def test_manual_governed_module_record_requires_retained_artifact() -> None:
    source = SCRIPT.read_text()
    assert "distribution in governed_modules and artifact_dir is None" in source
    assert "--artifact-dir with the" in source
    wrapper = (PROJECT_ROOT / "scripts/open_release_record_pr.sh").read_text()
    assert "--artifact-dir is required for governed module" in wrapper
    writer = _writer()
    with pytest.raises(writer.ReleaseRecordError, match="--artifact-dir"):
        writer.write_record(
            distribution=KNOWN_DISTRIBUTION,
            version="0.1.0a12",
            tag=KNOWN_TAG,
            package_dir=KNOWN_PACKAGE_DIR,
            import_name=KNOWN_IMPORT_NAME,
        )


def _mapping_keys(source: str, name: str) -> set[str]:
    """Literal string keys from one top-level dictionary assignment."""
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
    )
    assert isinstance(assignment.value, ast.Dict)
    return {ast.literal_eval(key) for key in assignment.value.keys if key is not None}


# ── The digest path, against a real tag ─────────────────────────────────────


def test_digests_read_from_the_tag_reproduce_a_hand_built_record() -> None:
    """The writer must agree with a record a human built and CI accepted.

    This is the property that matters: the digests are read from the TAG, not
    from the working tree. A digest taken from the tree would agree with an
    edit made after publication, which is precisely what the immutability gate
    exists to refuse — so a writer that reads the tree would pass its own tests
    while defeating the thing it feeds.
    """
    writer = _writer()
    distribution, commit, digests = _recorded_entry(writer, KNOWN_TAG)
    assert distribution == KNOWN_DISTRIBUTION

    assert writer.tag_commit(KNOWN_TAG) == commit
    assert (
        writer.migration_digests(KNOWN_TAG, KNOWN_PACKAGE_DIR, KNOWN_IMPORT_NAME)
        == digests
    )


def test_first_enrolment_history_reads_every_published_matching_tag() -> None:
    """The Commercial Agreements regression: a1 shipped before enrolment.

    The next release must not pretend its requested tag is the first tag. The
    repository tag set is the oracle, and a1's peeled coordinates and bytes
    must be part of the history the recorder supplies to first enrolment.
    """
    writer = _writer()
    history = writer.published_release_history(
        AGREEMENTS_DISTRIBUTION,
        AGREEMENTS_PACKAGE_DIR,
        AGREEMENTS_IMPORT_NAME,
    )

    assert history[AGREEMENTS_TAG] == (
        "fead57bc93d6551450f5e6ae1c9de1296e27b0ae",
        {
            "cg_0001_agreements.py": (
                "ac9e5f698f1814381a5987274131b186e9b0c0237b03314164cd69aa3806ec38"
            )
        },
    )


def test_published_history_discovers_every_matching_tag_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sensitivity: discovery itself owns completeness and version ordering."""
    writer = _writer()
    newest = "dotmac-nonesuch-v0.1.0a2"
    oldest = "dotmac-nonesuch-v0.1.0a1"
    monkeypatch.setattr(writer, "_git", lambda *_args: f"{newest}\n{oldest}\n")
    monkeypatch.setattr(writer, "tag_commit", lambda tag: f"commit:{tag}")
    monkeypatch.setattr(writer, "migration_digests", lambda tag, *_args: {tag: tag})

    history = writer.published_release_history(
        "dotmac-nonesuch", "packages/dotmac-nonesuch", "dotmac_nonesuch"
    )

    assert list(history) == [newest, oldest]
    assert history == {
        newest: (f"commit:{newest}", {newest: newest}),
        oldest: (f"commit:{oldest}", {oldest: oldest}),
    }


def test_an_entry_renders_in_the_shape_the_map_already_uses() -> None:
    """A generated entry must be indistinguishable from a hand-written one, or
    the map becomes two styles and the next reader cannot tell which is which.
    """
    writer = _writer()
    _, commit, digests = _recorded_entry(writer, KNOWN_TAG)
    rendered = writer._rendered_entry(KNOWN_TAG, KNOWN_DISTRIBUTION, commit, digests)
    # Round-trips: what it renders parses back to what it was given.
    parsed = ast.literal_eval("{" + rendered.rstrip().rstrip(",") + "}")
    assert parsed == {KNOWN_TAG: (KNOWN_DISTRIBUTION, commit, digests)}


def test_a_second_write_of_the_same_tag_changes_nothing() -> None:
    """Idempotence. A workflow may re-run, and a hand repair may race it."""
    writer = _writer()
    _, commit, digests = _recorded_entry(writer, KNOWN_TAG)
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    unchanged, added = writer.add_released_tag(
        text, KNOWN_TAG, KNOWN_DISTRIBUTION, commit, digests
    )
    assert not added
    assert unchanged == text


def _without_tags_after(text: str, distribution: str, tag: str) -> str:
    """Drop every recorded tag of `distribution` newer than `tag`."""
    keep = int(tag.rsplit("a", 1)[1])
    for name in re.findall(rf'"{re.escape(distribution)}-v[^"]+"', text):
        if int(name.rsplit("a", 1)[1].rstrip('"')) <= keep:
            continue
        entry = re.search(rf"\n    {re.escape(name)}: \(.*?\n    \),", text, re.S)
        assert entry is not None, name
        text = text[: entry.start()] + text[entry.end() :]
    return text


def _unreleased_filenames(text: str, distribution: str) -> set[str]:
    row = re.search(
        rf'^    "{re.escape(distribution)}": frozenset\((.*?)\),$',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert row is not None, distribution
    payload = row.group(1).strip()
    return set() if not payload else ast.literal_eval(payload)


def _with_unreleased_filenames(
    text: str, distribution: str, filenames: set[str]
) -> str:
    row = re.search(
        rf'^    "{re.escape(distribution)}": frozenset\((.*?)\),$',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert row is not None, distribution
    values = ", ".join(repr(filename) for filename in sorted(filenames))
    payload = f"{{{values}}}" if values else ""
    replacement = f'    "{distribution}": frozenset({payload}),'
    return text[: row.start()] + replacement + text[row.end() :]


def test_a_partial_incremental_record_retires_the_released_migration() -> None:
    """The a14 failure: adding the digest without clearing UNRELEASED is not a
    record, because the same file then reads as immutable and editable at once.

    Reconstruct that historical partial state from the corrected record: the
    automatic writer had added the a14 tag row while ``ig_0012`` survived in
    the editable set. Re-running the writer must converge by clearing it.
    """
    writer = _writer()
    tag = "dotmac-integration-v0.1.0a14"
    distribution, commit, digests = _recorded_entry(writer, tag)
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")

    current_unreleased = _unreleased_filenames(text, distribution)
    text = _with_unreleased_filenames(
        text,
        distribution,
        current_unreleased | {"ig_0012_delivery_evidence.py"},
    )
    assert "ig_0012_delivery_evidence.py" in _unreleased_filenames(text, distribution)
    # Reconstruct the state AS OF a14. `add_released_tag` treats every other
    # recorded tag of the distribution as "previously released", without regard
    # to order, so a later tag that re-lists ig_0012 — a15 does, having shipped
    # no new migration — would leave nothing for this repair to retire.
    text = _without_tags_after(text, distribution, tag)
    assert '"dotmac-integration-v0.1.0a15"' not in text

    updated, changed = writer.add_released_tag(text, tag, distribution, commit, digests)

    assert changed
    assert _unreleased_filenames(updated, distribution) == current_unreleased

    unchanged, changed_again = writer.add_released_tag(
        updated, tag, distribution, commit, digests
    )
    assert not changed_again
    assert unchanged == updated


def test_a_new_migration_absent_from_unreleased_is_refused() -> None:
    """Sensitivity proof: the retirement check must fail closed, not merely
    rewrite whatever happens to be present in the editable set."""
    writer = _writer()
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    assert "ig_9999_missing_declaration.py" not in _unreleased_filenames(
        text, "dotmac-integration"
    )
    _, _, prior = _recorded_entry(writer, "dotmac-integration-v0.1.0a13")
    digests = {**prior, "ig_9999_missing_declaration.py": "a" * 64}

    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.add_released_tag(
            text,
            "dotmac-integration-v0.1.0a99",
            "dotmac-integration",
            "abcdef12",
            digests,
        )

    assert "not declared in UNRELEASED" in str(refusal.value)
    assert "ig_9999_missing_declaration.py" in str(refusal.value)


# ── The ledger path ─────────────────────────────────────────────────────────


def test_removing_a_row_touches_only_that_row() -> None:
    """The ledger is edited as TEXT.

    A `json.loads`/`json.dumps` round-trip escapes every em-dash in the
    `$comment` prose to `\\u2014`, turning a five-line removal into a fifteen-
    line diff over paragraphs the change has no business touching. That
    happened three times in one session before the rule was written down.
    """
    writer = _writer()
    before = _ledger_text(writer)
    victim = sorted(json.loads(before)["unpublished"])[0]

    after, removed = writer.remove_ledger_row(before, victim)
    assert removed

    parsed = json.loads(after)
    assert victim not in parsed["unpublished"]
    assert parsed["$comment"] == json.loads(before)["$comment"]
    assert set(json.loads(before)["unpublished"]) - {victim} == set(
        parsed["unpublished"]
    )
    # Byte-level: exactly ONE contiguous deletion of the five lines a row
    # occupies, and no other edit anywhere in the file.
    #
    # Expressed as a diff rather than by filtering, because the obvious filter
    # is wrong in a way that looks right: a row's closing `},` and its
    # `"state"` line recur in every other row, so "keep the lines of `before`
    # that appear in `after`" keeps those duplicates and the comparison
    # inflates. The shape of the edit is the claim, so the diff is what states
    # it.
    diff = difflib.SequenceMatcher(
        None, before.splitlines(), after.splitlines(), autojunk=False
    )
    edits = [op for op in diff.get_opcodes() if op[0] != "equal"]
    assert len(edits) == 1, f"expected one edit, got {edits}"
    tag, start, end, _, _ = edits[0]
    assert tag == "delete", f"expected a deletion, got {tag!r}"
    assert end - start == 5, f"a ledger row is five lines; removed {end - start}"


def test_removing_the_last_row_leaves_valid_json() -> None:
    """The final entry carries no trailing comma, so the naive pattern misses
    it and a second pattern has to take the PRECEDING comma instead. Getting
    this wrong produces a file that parses nowhere and fails five gates at
    once."""
    writer = _writer()
    before = _ledger_text(writer)
    last = list(json.loads(before)["unpublished"])[-1]

    after, removed = writer.remove_ledger_row(before, last)
    assert removed, f"{last} is the final row and was not matched"
    parsed = json.loads(after)
    assert last not in parsed["unpublished"]


def test_removing_an_absent_row_is_a_no_op_not_a_failure() -> None:
    """Convergence: re-running the record after a partial repair must settle,
    not refuse. A workflow that fails on its own second run is one nobody
    re-runs."""
    writer = _writer()
    before = _ledger_text(writer)
    after, removed = writer.remove_ledger_row(before, "dotmac-not-a-real-package")
    assert not removed
    assert after == before


# ── The wrapper fails loudly ────────────────────────────────────────────────


def test_an_unopened_record_fails_the_run_rather_than_warning() -> None:
    """The correction that made #357 worth having.

    The first version of `open_release_record_pr.sh` exited 0 and printed a
    `::warning::` when it could not open the record, reasoning that the
    artifact is already published so the run should not report a successful
    publication as failed.

    That reasoning was wrong, and it rebuilt the exact failure class the
    script exists to close: correctness went back to depending on somebody
    READING a warning inside a green run — and a green run with no record
    looks, at a glance, exactly like a green run with one. "Tag exists,
    record missing" has to be visible where people already look.

    Asserted on the source rather than by running the script, because every
    `give_up` path needs a network, a token and a real tag. The properties
    are textual and this is what makes them stay true.
    """
    script = (PROJECT_ROOT / "scripts" / "open_release_record_pr.sh").read_text(
        encoding="utf-8"
    )
    body = script.split("give_up() {", 1)
    assert len(body) == 2, "give_up() is gone; this guard needs rewriting"
    # Split on the closing brace at column 0, NOT the first `}` — the body is
    # full of `${TAG}` and `${MANUAL}`, and splitting on those truncated the
    # handler to nothing while every assertion below still "passed".
    handler = body[1].split("\n}", 1)[0]

    assert "exit 1" in handler, (
        "give_up must FAIL the run. Exiting 0 puts correctness back on somebody "
        "noticing a warning in a green run, which is the failure this closes"
    )
    assert "exit 0" not in handler
    assert "::warning::" not in handler, "a warning is what did not work"

    # It must also say the artifact is fine, or the loud failure invites the
    # one genuinely harmful reaction: re-running the publish.
    assert "DO NOT RE-RUN THE PUBLISH" in handler
    assert "already" in handler and "published" in handler

    # ...and name the manual repair, since that is what the reader must do.
    assert "${MANUAL}" in handler

    # ...and link the pull-request page for the branch it already pushed. The
    # proven instance (kernel a92, run 32617583628) failed at exactly this
    # step: the edits were correct and the branch was pushed, so the gap was
    # one click wide and nothing said where to click.
    assert "${COMPARE_URL}" in handler


def test_the_only_successful_exits_are_a_record_opened_or_already_complete() -> None:
    """Two success paths, both meaning the record EXISTS. Any third would be a
    way for the run to go green with the ledger still stale."""
    script = (PROJECT_ROOT / "scripts" / "open_release_record_pr.sh").read_text(
        encoding="utf-8"
    )
    exits = [
        line.strip() for line in script.splitlines() if line.strip().startswith("exit ")
    ]
    assert (
        exits.count("exit 0") == 1
    ), f"expected exactly one exit 0 (the already-complete path), found {exits}"
    # `exit 2` is argument misuse, which is a failure like any other.
    assert set(exits) <= {"exit 0", "exit 1", "exit 2"}, exits


def test_kernel_evidence_and_general_record_are_staged_before_one_commit() -> None:
    """First run and rerun share one atomic, fail-closed recorder sequence."""
    script = (PROJECT_ROOT / "scripts" / "open_release_record_pr.sh").read_text(
        encoding="utf-8"
    )
    evidence = script.index("write_kernel_release_verification_record.py")
    general = script.index('write_release_record.py "${ARGS[@]}"')
    commit = script.index("git add -A")
    push = script.index("git push --force-with-lease")
    assert evidence < general < commit < push
    before_commit = script[:commit]
    assert 'give_up "the permanent kernel verification writer refused' in before_commit
    assert 'give_up "the record writer refused' in before_commit


def _record_auto_merge_problems(script: str) -> list[str]:
    problems: list[str] = []
    command = 'gh pr merge "${BRANCH}" --auto --squash'
    if command not in script:
        problems.append("the record PR must enable squash auto-merge")
        return problems
    if script.index(command) < script.index("gh pr create"):
        problems.append("auto-merge is enabled before the record PR is opened")
    if 'give_up "could not enable auto-merge for ${BRANCH}"' not in script:
        problems.append("an auto-merge refusal must fail the release loudly")
    return problems


def test_a_record_pr_auto_merges_only_after_protected_ci_is_green() -> None:
    """The recorder removes the bookkeeping gesture, never the CI authority."""
    script = (PROJECT_ROOT / "scripts" / "open_release_record_pr.sh").read_text(
        encoding="utf-8"
    )
    assert _record_auto_merge_problems(script) == []


def test_record_auto_merge_guard_detects_a_manual_merge_regression() -> None:
    """Sensitivity: opening a PR without ``--auto`` is the retired wait."""
    script = (PROJECT_ROOT / "scripts" / "open_release_record_pr.sh").read_text(
        encoding="utf-8"
    )
    planted = script.replace(
        'gh pr merge "${BRANCH}" --auto --squash',
        'gh pr merge "${BRANCH}" --squash',
    )
    assert _record_auto_merge_problems(planted) == [
        "the record PR must enable squash auto-merge"
    ]


# ── Refusals ────────────────────────────────────────────────────────────────


def test_a_record_is_refused_before_its_tag_exists() -> None:
    """The record describes a publication. Writing it first would remove a live
    exemption for a version that may never ship."""
    writer = _writer()
    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.write_record(
            distribution=KNOWN_DISTRIBUTION,
            version="9.9.9",
            tag="dotmac-integration-v9.9.9",
            package_dir=KNOWN_PACKAGE_DIR,
            import_name=KNOWN_IMPORT_NAME,
        )
    assert "does not resolve to a commit" in str(refusal.value)


def test_a_tag_describing_a_different_version_is_refused() -> None:
    """The tag and version are one coordinate, even after ledger repair."""
    writer = _writer()
    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.write_record(
            distribution=KNOWN_DISTRIBUTION,
            version="9.9.9",
            tag=KNOWN_TAG,
            package_dir=None,
            import_name=None,
        )
    assert "tag/version identity mismatch" in str(refusal.value)


def test_a_row_describing_a_different_version_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sharpest refusal. A row excusing `0.1.0a5` is a LIVE exemption when
    `0.1.0a4` is published; deleting it would silently promise a version nobody
    can install — the exact defect the ledger exists to prevent, committed by
    the tool meant to maintain it."""
    writer = _writer()
    ledger = json.loads(_ledger_text(writer))["unpublished"]
    # A distribution with NO migration lineage, so `--no-lineage` is the honest
    # declaration and the ledger refusal below is the one under test. Picking
    # the alphabetically-first row regardless would make this test fail the day
    # that row becomes a lineage-bearing distribution, and it would fail with a
    # lineage message that has nothing to do with what it is checking.
    anchored = writer.anchored_distributions(
        writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    )
    distribution, row = next(
        (name, value) for name, value in sorted(ledger.items()) if name not in anchored
    )
    wrong_version = row["declared"] + "-not-this-one"
    monkeypatch.setattr(writer, "tag_commit", lambda _tag: "a" * 40)
    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.write_record(
            distribution=distribution,
            version=wrong_version,
            tag=f"{distribution}-v{wrong_version}",
            package_dir=None,
            import_name=None,
            no_lineage=True,
        )
    assert "describes a different" in str(refusal.value), (
        "the ledger refusal is shadowed; lineage validation runs first and must "
        "not mask a state refusal the caller has already satisfied"
    )


def test_a_first_enrolment_records_history_and_every_migration_guard_map() -> None:
    """The release workflow must be able to enrol after a prior release.

    Appending only ``RELEASED_TAGS`` is not enough: the guard then rejects the
    unknown owner and still leaves main red. Recording only the requested tag
    is also insufficient when an older tag already exists. First enrolment is
    one mechanical operation over the complete published history, all four
    owner maps and the immutable tag map.
    """
    writer = _writer()
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    distribution = "dotmac-nonesuch"
    tag = "dotmac-nonesuch-v2.0.0"
    historical_tag = "dotmac-nonesuch-v1.0.0"
    digests = {"ns_0001_nonesuch.py": "a" * 64}
    historical_releases = {
        tag: ("abcdef12", digests),
        historical_tag: ("12345678", digests),
    }
    assert distribution not in _mapping_keys(text, "DISTRIBUTIONS")

    updated, added = writer.add_released_tag(
        text,
        tag,
        distribution,
        "abcdef12",
        digests,
        package_dir="packages/dotmac-nonesuch",
        import_name="dotmac_nonesuch",
        historical_releases=historical_releases,
    )

    assert added
    ast.parse(updated)
    for mapping in ("DISTRIBUTIONS", "LINEAGE_GLOBS", "TAG_PREFIXES", "UNRELEASED"):
        assert distribution in _mapping_keys(updated, mapping)
    assert tag in _mapping_keys(updated, "RELEASED_TAGS")
    assert historical_tag in _mapping_keys(updated, "RELEASED_TAGS")
    assert '"dotmac-nonesuch": "ns_*.py"' in updated
    assert '"dotmac-nonesuch": "dotmac-nonesuch-v"' in updated
    assert "packages/dotmac-nonesuch" in updated
    assert "dotmac_nonesuch/migrations/versions" in updated

    unchanged, added_again = writer.add_released_tag(
        updated,
        tag,
        distribution,
        "abcdef12",
        digests,
    )
    assert not added_again
    assert unchanged == updated

    long_entry = writer._first_release_entries(
        distribution="dotmac-operational-escalations",
        tag="dotmac-operational-escalations-v0.1.0a1",
        commit="abcdef12",
        digests={"oe_0001_escalation_policy.py": "b" * 64},
        package_dir="packages/dotmac-operational-escalations",
        import_name="dotmac_operational_escalations",
    )["DISTRIBUTIONS"]
    assert max(map(len, long_entry.splitlines())) <= 88
    assert '/ "packages/dotmac-operational-escalations"' in long_entry
    assert '/ "src/dotmac_operational_escalations/migrations/versions"' in long_entry


def test_an_existing_partial_enrolment_backfills_older_published_tags() -> None:
    """Rerunning the recorder must converge after the old one-tag defect."""
    writer = _writer()
    text = writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8")
    distribution = "dotmac-nonesuch"
    current_tag = "dotmac-nonesuch-v2.0.0"
    older_tag = "dotmac-nonesuch-v1.0.0"
    digests = {"ns_0001_nonesuch.py": "a" * 64}
    partial_history = {current_tag: ("a" * 40, digests)}
    complete_history = {
        current_tag: ("a" * 40, digests),
        older_tag: ("b" * 40, digests),
    }
    partial, _ = writer.add_released_tag(
        text,
        current_tag,
        distribution,
        "a" * 40,
        digests,
        package_dir="packages/dotmac-nonesuch",
        import_name="dotmac_nonesuch",
        historical_releases=partial_history,
    )

    repaired, changed = writer.add_released_tag(
        partial,
        current_tag,
        distribution,
        "a" * 40,
        digests,
        historical_releases=complete_history,
    )

    releases = writer._released_tags(repaired)
    assert changed
    assert releases[older_tag] == (distribution, "b" * 40, digests)
    assert releases[current_tag] == (distribution, "a" * 40, digests)


def test_a_refused_first_enrolment_does_not_partially_remove_the_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both record files change together only after every premise validates."""
    writer = _writer()
    ledger = tmp_path / "declared-publication-baseline.json"
    released = tmp_path / "test_released_migrations.py"
    ledger.write_text(
        json.dumps(
            {
                "unpublished": {
                    "dotmac-nonesuch": {
                        "declared": "1.0.0",
                        "reason": "synthetic first-release atomicity fixture",
                        "state": "never-published",
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    released.write_text(
        writer.RELEASED_TAGS_MODULE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    ledger_before = ledger.read_text(encoding="utf-8")
    released_before = released.read_text(encoding="utf-8")

    monkeypatch.setattr(writer, "LEDGER", ledger)
    monkeypatch.setattr(writer, "RELEASED_TAGS_MODULE", released)
    monkeypatch.setattr(writer, "tag_commit", lambda _tag: "abcdef12")
    synthetic_digests = {
        "ns_0001_nonesuch.py": "a" * 64,
        "wrong_0002_second_lineage.py": "b" * 64,
    }
    monkeypatch.setattr(writer, "migration_digests", lambda *_args: synthetic_digests)
    monkeypatch.setattr(
        writer,
        "published_release_history",
        lambda *_args: {"dotmac-nonesuch-v1.0.0": ("abcdef12", synthetic_digests)},
    )

    with pytest.raises(writer.ReleaseRecordError) as refusal:
        writer.write_record(
            distribution="dotmac-nonesuch",
            version="1.0.0",
            tag="dotmac-nonesuch-v1.0.0",
            package_dir="packages/dotmac-nonesuch",
            import_name="dotmac_nonesuch",
        )

    assert "one migration prefix" in str(refusal.value)
    assert ledger.read_text(encoding="utf-8") == ledger_before
    assert released.read_text(encoding="utf-8") == released_before


def test_kernel_record_consumes_authorization_and_refreshes_source_census(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The a100 post-tag deadlock: the tag adds one released distribution.

    Removing only the publication row leaves main red because the checked-in
    released-source total still describes the pre-tag tree. The same writer
    must consume the one-shot authorization and regenerate that census before
    writing any output.
    """

    writer = _writer()
    ledger = tmp_path / "declared-publication-baseline.json"
    released = tmp_path / "test_released_migrations.py"
    baseline = tmp_path / "published_source_drift_baseline.json"
    authorization_path = tmp_path / "kernel-release-authorization.json"
    # SELF-CONTAINED, deliberately not seeded from the real ledger.
    #
    # This previously read `writer.LEDGER` and rewrote the live kernel row.
    # That made the test depend on the repository being in its PRE-RECORD
    # state: the moment the a100 record removed the dotmac-kernel row, the
    # replacement matched nothing, the fixture ledger had no row to remove,
    # `write_record` correctly reported no removal, and this assertion failed
    # -- on the very pull request whose job is to land that record. A test
    # that cannot survive the change it exists to verify blocks the release
    # instead of guarding it.
    #
    # The row is written here in full, at the 4-space row indentation
    # `remove_ledger_row` matches, so the fixture describes the pre-record
    # state permanently rather than borrowing it from a file that moves.
    ledger.write_text(
        json.dumps(
            {
                "$comment": ["fixture"],
                "unpublished": {
                    "dotmac-kernel": {
                        "declared": "0.1.0a100",
                        "reason": "fixture kernel row awaiting its post-tag record",
                        "state": "declared-unpublished",
                    },
                    "dotmac-fixture-neighbour": {
                        "declared": "0.1.0a1",
                        "reason": "fixture neighbour, so the kernel row is not last",
                        "state": "declared-unpublished",
                    },
                },
            },
            indent=2,
        )
        + "\n"
    )
    released.write_text(writer.RELEASED_TAGS_MODULE.read_text())
    baseline.write_text(
        json.dumps({"released_total": 77, "drifted_total": 4, "drifted": []}) + "\n"
    )
    authorization_path.write_text(
        json.dumps({"$schema": "KernelReleaseAuthorization.v1", "active": {}}) + "\n"
    )

    class FakeAuthorizationError(RuntimeError):
        pass

    class FakeAuthorization:
        KernelReleaseAuthorizationError = FakeAuthorizationError

        @staticmethod
        def load_authorization():
            return object()

        @staticmethod
        def consume_for_release(**coordinates):
            assert coordinates == {
                "version": "0.1.0a100",
                "tag": "dotmac-kernel-v0.1.0a100",
                "commit": "a" * 40,
            }
            return (
                json.dumps(
                    {"$schema": "KernelReleaseAuthorization.v1", "active": None},
                    indent=2,
                )
                + "\n"
            )

    class FakeSourceDrift:
        @staticmethod
        def render_baseline():
            return (
                json.dumps(
                    {"released_total": 78, "drifted_total": 4, "drifted": []},
                    indent=2,
                )
                + "\n"
            )

    monkeypatch.setattr(writer, "LEDGER", ledger)
    monkeypatch.setattr(writer, "RELEASED_TAGS_MODULE", released)
    monkeypatch.setattr(writer, "SOURCE_DRIFT_BASELINE", baseline)
    monkeypatch.setattr(writer, "KERNEL_AUTHORIZATION", authorization_path)
    monkeypatch.setattr(writer, "tag_commit", lambda _tag: "a" * 40)
    monkeypatch.setattr(writer, "require_kernel_evidence", lambda **_values: {})
    # These two tests are about consuming the authorization and refreshing the
    # census, not about lineage. Until `--package-dir` became required they
    # reached that path through the SILENT SKIP -- the same omission that left
    # `main` red after a101 and again after a102, here in a test rather than a
    # release. Declaring a real path would not keep them on their subject: it
    # pulls `published_release_history` and `add_released_tag` -- real git, real
    # map parsing, the tag oracle -- into tests that touched none of it, which
    # widens what "fully reconciled" means in the rerun case. So the collaborator
    # is stubbed, exactly as `tag_commit` and `require_kernel_evidence` are; the
    # refusals themselves are proven in test_recorder_lineage_refusal.py.
    monkeypatch.setattr(
        writer, "resolve_lineage_inputs", lambda **_arguments: (None, None, {})
    )
    monkeypatch.setattr(
        writer,
        "_local_script",
        lambda name: (
            FakeAuthorization
            if name == "kernel_release_authorization"
            else FakeSourceDrift
        ),
    )

    changed = writer.write_record(
        distribution="dotmac-kernel",
        version="0.1.0a100",
        tag="dotmac-kernel-v0.1.0a100",
        package_dir=None,
        import_name=None,
    )

    assert "dotmac-kernel" not in json.loads(ledger.read_text())["unpublished"]
    assert json.loads(authorization_path.read_text())["active"] is None
    assert json.loads(baseline.read_text())["released_total"] == 78
    assert changed == [
        "removed the dotmac-kernel publication-ledger row",
        "consumed the kernel release authorization",
        "recomputed the published-source census from the tagged tree "
        "(78 released, 4 drifted)",
    ]


def test_kernel_record_rerun_accepts_only_the_fully_reconciled_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writer = _writer()
    ledger = tmp_path / "declared-publication-baseline.json"
    released = tmp_path / "test_released_migrations.py"
    baseline = tmp_path / "published_source_drift_baseline.json"
    authorization_path = tmp_path / "kernel-release-authorization.json"
    ledger.write_text(json.dumps({"unpublished": {}}, indent=2) + "\n")
    released.write_text(writer.RELEASED_TAGS_MODULE.read_text())
    baseline_text = (
        json.dumps({"released_total": 78, "drifted_total": 4, "drifted": []}, indent=2)
        + "\n"
    )
    baseline.write_text(baseline_text)
    authorization_path.write_text(
        json.dumps(
            {"$schema": "KernelReleaseAuthorization.v1", "active": None},
            indent=2,
        )
        + "\n"
    )

    class FakeAuthorization:
        @staticmethod
        def load_authorization():
            return None

    class FakeSourceDrift:
        @staticmethod
        def render_baseline():
            return baseline_text

    monkeypatch.setattr(writer, "LEDGER", ledger)
    monkeypatch.setattr(writer, "RELEASED_TAGS_MODULE", released)
    monkeypatch.setattr(writer, "SOURCE_DRIFT_BASELINE", baseline)
    monkeypatch.setattr(writer, "KERNEL_AUTHORIZATION", authorization_path)
    monkeypatch.setattr(writer, "tag_commit", lambda _tag: "a" * 40)
    monkeypatch.setattr(writer, "require_kernel_evidence", lambda **_values: {})
    # These two tests are about consuming the authorization and refreshing the
    # census, not about lineage. Until `--package-dir` became required they
    # reached that path through the SILENT SKIP -- the same omission that left
    # `main` red after a101 and again after a102, here in a test rather than a
    # release. Declaring a real path would not keep them on their subject: it
    # pulls `published_release_history` and `add_released_tag` -- real git, real
    # map parsing, the tag oracle -- into tests that touched none of it, which
    # widens what "fully reconciled" means in the rerun case. So the collaborator
    # is stubbed, exactly as `tag_commit` and `require_kernel_evidence` are; the
    # refusals themselves are proven in test_recorder_lineage_refusal.py.
    monkeypatch.setattr(
        writer, "resolve_lineage_inputs", lambda **_arguments: (None, None, {})
    )
    monkeypatch.setattr(
        writer,
        "_local_script",
        lambda name: FakeAuthorization
        if name == "kernel_release_authorization"
        else FakeSourceDrift,
    )

    assert (
        writer.write_record(
            distribution="dotmac-kernel",
            version="0.1.0a100",
            tag="dotmac-kernel-v0.1.0a100",
            package_dir=None,
            import_name=None,
        )
        == []
    )

    ledger.write_text(
        json.dumps(
            {"unpublished": {"dotmac-kernel": {"declared": "0.1.0a100"}}},
            indent=2,
        )
        + "\n"
    )
    with pytest.raises(writer.ReleaseRecordError, match="ledger remains"):
        writer.write_record(
            distribution="dotmac-kernel",
            version="0.1.0a100",
            tag="dotmac-kernel-v0.1.0a100",
            package_dir=None,
            import_name=None,
        )
