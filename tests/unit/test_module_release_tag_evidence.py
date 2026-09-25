"""A governed module release tag carries canonical evidence, not free text.

`scripts/write_release_record.py` renders and strictly parses one line of
`ModuleReleaseTagEvidence.v1` JSON; `scripts/tag_module_release.py` is the
single, fail-closed writer of the annotated tag that carries it;
`scripts/check_module_release_verification_append_only.py` freezes both the
verified ledger and the pre-cutover legacy baseline against their accepted
base revision; and the recovery workflow must route every reference to its
retained-artifact directory through one env var, never a bare literal.

These tests exercise real `git` subprocesses against throwaway repositories —
no fixture/mock stands in for tag creation, pushing, or reading a tag's own
object body.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WRITE_RECORD_SCRIPT = PROJECT_ROOT / "scripts" / "write_release_record.py"
TAG_SCRIPT = PROJECT_ROOT / "scripts" / "tag_module_release.py"
APPEND_ONLY_SCRIPT = (
    PROJECT_ROOT / "scripts" / "check_module_release_verification_append_only.py"
)
RECOVER_WORKFLOW = PROJECT_ROOT / ".github/workflows/recover-module-release.yml"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github/workflows/release-module.yml"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _writer():
    return _load("write_release_record", WRITE_RECORD_SCRIPT)


def _tagger():
    return _load("tag_module_release", TAG_SCRIPT)


def _append_only_checker():
    # The checker does `from write_release_record import ...` as a top-level
    # import, so `scripts/` must already be importable before it loads.
    scripts_dir = str(PROJECT_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return _load("module_release_append_only", APPEND_ONLY_SCRIPT)


def _run_git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "--initial-branch=main"], cwd=path)
    _run_git(["config", "user.name", "Test Runner"], cwd=path)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=path)


def _commit_all(path: Path, message: str) -> str:
    _run_git(["add", "-A"], cwd=path)
    _run_git(["commit", "-m", message], cwd=path)
    return _run_git(["rev-parse", "HEAD"], cwd=path).strip()


# ── 1. Canonical round trip and strict refusal ──────────────────────────────


def test_canonical_render_round_trips_through_the_strict_parser() -> None:
    writer = _writer()
    message = writer.render_module_release_tag_evidence(
        distribution="dotmac-approvals",
        version="0.1.0a7",
        wheel_filename="dotmac_approvals-0.1.0a7-py3-none-any.whl",
        wheel_sha256="a" * 64,
        verification_run_id="123456789",
    )
    assert message == (
        '{"distribution":"dotmac-approvals","schema":"ModuleReleaseTagEvidence.v1",'
        '"verification_run_id":"123456789",'
        '"version":"0.1.0a7","wheel_filename":"dotmac_approvals-0.1.0a7-py3-none-any.whl",'
        '"wheel_sha256":"' + "a" * 64 + '"}'
    )
    assert writer.parse_module_release_tag_evidence(message) == {
        "distribution": "dotmac-approvals",
        "version": "0.1.0a7",
        "wheel_filename": "dotmac_approvals-0.1.0a7-py3-none-any.whl",
        "wheel_sha256": "a" * 64,
        "verification_run_id": "123456789",
    }


def _canonical_message(writer) -> str:
    return writer.render_module_release_tag_evidence(
        distribution="dotmac-approvals",
        version="0.1.0a7",
        wheel_filename="dotmac_approvals-0.1.0a7-py3-none-any.whl",
        wheel_sha256="a" * 64,
        verification_run_id="123456789",
    )


def test_parser_refuses_reordered_keys() -> None:
    writer = _writer()
    payload = json.loads(_canonical_message(writer))
    reordered = json.dumps(
        {key: payload[key] for key in sorted(payload, reverse=True)},
        sort_keys=False,
        separators=(",", ":"),
    )
    with pytest.raises(writer.ReleaseRecordError, match="not canonical"):
        writer.parse_module_release_tag_evidence(reordered)


def test_parser_refuses_extra_whitespace() -> None:
    writer = _writer()
    message = _canonical_message(writer)
    padded = message.replace('","', '", "')
    with pytest.raises(writer.ReleaseRecordError, match="not canonical"):
        writer.parse_module_release_tag_evidence(padded)


def test_parser_refuses_two_lines() -> None:
    writer = _writer()
    message = _canonical_message(writer)
    with pytest.raises(writer.ReleaseRecordError, match="exactly one line"):
        writer.parse_module_release_tag_evidence(message + "\nextra")


def test_parser_refuses_an_extra_key() -> None:
    writer = _writer()
    payload = json.loads(_canonical_message(writer))
    payload["extra"] = "surprise"
    with pytest.raises(writer.ReleaseRecordError, match="wrong keys"):
        writer.parse_module_release_tag_evidence(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def test_parser_refuses_a_missing_key() -> None:
    writer = _writer()
    payload = json.loads(_canonical_message(writer))
    del payload["verification_run_id"]
    with pytest.raises(writer.ReleaseRecordError, match="wrong keys"):
        writer.parse_module_release_tag_evidence(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def test_parser_refuses_the_wrong_schema() -> None:
    writer = _writer()
    payload = json.loads(_canonical_message(writer))
    payload["schema"] = "ModuleReleaseTagEvidence.v2"
    with pytest.raises(writer.ReleaseRecordError, match="wrong schema"):
        writer.parse_module_release_tag_evidence(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def test_parser_refuses_a_non_decimal_run_id() -> None:
    writer = _writer()
    with pytest.raises(writer.ReleaseRecordError, match="decimal"):
        writer.render_module_release_tag_evidence(
            distribution="dotmac-approvals",
            version="0.1.0a7",
            wheel_filename="dotmac_approvals-0.1.0a7-py3-none-any.whl",
            wheel_sha256="a" * 64,
            verification_run_id="12x",
        )
    payload = json.loads(_canonical_message(writer))
    payload["verification_run_id"] = "12x"
    # The parser re-renders the parsed fields to prove canonical-ness, and
    # `render_module_release_tag_evidence` refuses a non-decimal run id before
    # any byte comparison happens -- so the parser surfaces THAT refusal, not
    # a generic "not canonical" one.
    with pytest.raises(writer.ReleaseRecordError, match="decimal"):
        writer.parse_module_release_tag_evidence(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def test_parser_refuses_an_uppercase_digest() -> None:
    writer = _writer()
    payload = json.loads(_canonical_message(writer))
    payload["wheel_sha256"] = "A" * 64
    with pytest.raises(writer.ReleaseRecordError, match="lowercase 64-hex"):
        writer.parse_module_release_tag_evidence(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def test_parser_refuses_a_short_digest() -> None:
    writer = _writer()
    payload = json.loads(_canonical_message(writer))
    payload["wheel_sha256"] = "a" * 63
    with pytest.raises(writer.ReleaseRecordError, match="lowercase 64-hex"):
        writer.parse_module_release_tag_evidence(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def test_parser_refuses_a_wheel_filename_not_matching_distribution_and_version() -> (
    None
):
    writer = _writer()
    payload = json.loads(_canonical_message(writer))
    payload["wheel_filename"] = "dotmac_other-9.9.9-py3-none-any.whl"
    with pytest.raises(writer.ReleaseRecordError, match="does not bind"):
        writer.parse_module_release_tag_evidence(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


# ── 2 & 3. Full tag-and-read-back path, against real git ───────────────────


_DISTRIBUTION = "dotmac-approvals"
_VERSION = "0.1.0a7"
_TAG = f"{_DISTRIBUTION}-v{_VERSION}"
_WHEEL_NAME = f"dotmac_approvals-{_VERSION}-py3-none-any.whl"


def _minimal_legacy_document() -> str:
    return json.dumps(
        {
            "$comment": "fixture",
            "schema": "ModuleReleaseLegacyUnverified.v1",
            "cutover_source_commit": "d" * 40,
            "tags": [],
        }
    )


def _minimal_verified_document() -> str:
    return json.dumps(
        {
            "$comment": "fixture",
            "schema": "ModuleReleaseVerifications.v1",
            "releases": [],
        }
    )


def _bare_origin_and_work(tmp_path: Path) -> tuple[Path, Path, str]:
    """A bare "origin" and a work clone with one commit, wired together."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _run_git(["init", "--bare", "--initial-branch=main"], cwd=origin)

    work = tmp_path / "work"
    _init_repo(work)
    (work / "README.md").write_text("fixture repo\n", encoding="utf-8")
    commit = _commit_all(work, "initial commit")
    _run_git(["remote", "add", "origin", str(origin)], cwd=work)
    _run_git(["push", "origin", "main"], cwd=work)
    return origin, work, commit


def _tag_a_release(
    tmp_path: Path, *, run_id: str, wheel_bytes: bytes
) -> tuple[Path, Path, str, Path]:
    """Build the wheel, create+push the tag via the real script, return coordinates."""
    origin, work, commit = _bare_origin_and_work(tmp_path)
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    (artifact_dir / _WHEEL_NAME).write_bytes(wheel_bytes)

    tagger = _tagger()
    exit_code = tagger.main(
        [
            "--distribution",
            _DISTRIBUTION,
            "--version",
            _VERSION,
            "--tag",
            _TAG,
            "--commit",
            commit,
            "--artifact-dir",
            str(artifact_dir),
            "--run-id",
            run_id,
            "--remote",
            "origin",
            "--repo-root",
            str(work),
        ]
    )
    assert exit_code == 0
    return origin, work, commit, artifact_dir


def test_write_path_then_read_back_produces_evidence_add_and_validate_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    origin, work, commit, artifact_dir = _tag_a_release(
        tmp_path, run_id="555000111", wheel_bytes=b"exact build once bytes"
    )

    writer = _writer()
    monkeypatch.setattr(writer, "REPO_ROOT", work)

    message = writer.annotated_tag_message(_TAG)
    evidence = writer.parse_module_release_tag_evidence(message)
    assert evidence["distribution"] == _DISTRIBUTION
    assert evidence["version"] == _VERSION
    assert evidence["wheel_filename"] == _WHEEL_NAME
    assert evidence["verification_run_id"] == "555000111"

    tag_object = writer.annotated_tag_object(_TAG)
    peeled_commit = writer.tag_commit(_TAG)
    assert peeled_commit == commit

    verified_text, added = writer.add_module_release_verification(
        _minimal_verified_document(),
        distribution=_DISTRIBUTION,
        version=_VERSION,
        tag=_TAG,
        tag_object=tag_object,
        peeled_commit=peeled_commit,
        wheel_filename=evidence["wheel_filename"],
        wheel_sha256=evidence["wheel_sha256"],
        verification_run_id=evidence["verification_run_id"],
    )
    assert added

    writer.validate_module_release_inventory(
        verified_text,
        _minimal_legacy_document(),
        live={_TAG: (tag_object, peeled_commit)},
        evidence={_TAG: evidence},
        targets={_DISTRIBUTION},
    )

    # The digest half of write_record: the wheel actually on disk must
    # reproduce the digest the tag's own evidence already carries.
    wheel_filename, wheel_sha256 = writer.module_wheel_digest(
        str(artifact_dir), distribution=_DISTRIBUTION, version=_VERSION
    )
    assert wheel_filename == evidence["wheel_filename"]
    assert wheel_sha256 == evidence["wheel_sha256"]


def test_full_write_record_records_the_verified_wheel_from_a_real_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Drive `write_record` itself, not just its collaborators.

    Every file `write_record` touches unconditionally is pointed at a
    throwaway fixture EXCEPT the four it only reads for the (skipped, since
    this distribution is not `dotmac-kernel`) kernel branch — those stay the
    real, read-only repository files, exactly as `test_write_release_record.py`
    already does for its non-kernel refusal tests.
    """
    origin, work, commit, artifact_dir = _tag_a_release(
        tmp_path, run_id="555000222", wheel_bytes=b"a different build's bytes"
    )

    writer = _writer()
    monkeypatch.setattr(writer, "REPO_ROOT", work)

    released_tags_module = tmp_path / "released_tags.py"
    released_tags_module.write_text("RELEASED_TAGS: dict = {\n}\n", encoding="utf-8")
    monkeypatch.setattr(writer, "RELEASED_TAGS_MODULE", released_tags_module)

    module_verifications = tmp_path / "module-release-verifications.json"
    module_verifications.write_text(_minimal_verified_document(), encoding="utf-8")
    monkeypatch.setattr(writer, "MODULE_RELEASE_VERIFICATIONS", module_verifications)

    module_legacy = tmp_path / "module-release-legacy-unverified.json"
    module_legacy.write_text(_minimal_legacy_document(), encoding="utf-8")
    monkeypatch.setattr(writer, "MODULE_RELEASE_LEGACY", module_legacy)

    ledger = tmp_path / "declared-publication-baseline.json"
    ledger.write_text(json.dumps({"unpublished": {}}), encoding="utf-8")
    monkeypatch.setattr(writer, "LEDGER", ledger)

    changed = writer.write_record(
        distribution=_DISTRIBUTION,
        version=_VERSION,
        tag=_TAG,
        package_dir=None,
        import_name=None,
        no_lineage=True,
        artifact_dir=str(artifact_dir),
        expected_run_id="555000222",
        expected_commit=commit,
    )

    assert any("recorded the verified" in line for line in changed)
    recorded = json.loads(module_verifications.read_text(encoding="utf-8"))
    assert recorded["releases"][0]["tag"] == _TAG
    assert recorded["releases"][0]["verification_run_id"] == "555000222"

    # Idempotent re-run converges rather than refusing.
    assert (
        writer.write_record(
            distribution=_DISTRIBUTION,
            version=_VERSION,
            tag=_TAG,
            package_dir=None,
            import_name=None,
            no_lineage=True,
            artifact_dir=str(artifact_dir),
            expected_run_id="555000222",
            expected_commit=commit,
        )
        == []
    )


def test_write_record_requires_expected_run_id_and_commit_for_a_governed_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A governed module's recorder must bind to the tag ITS OWN run created.

    Before this guard, `write_record` accepted any existing tag whose
    evidence matched the wheel — including a tag some OTHER run wrote, if
    the tag step was skipped or refused but the `if: always()` record step
    still ran.
    """
    origin, work, commit, artifact_dir = _tag_a_release(
        tmp_path, run_id="555000333", wheel_bytes=b"bytes for the binding guard"
    )

    writer = _writer()
    monkeypatch.setattr(writer, "REPO_ROOT", work)

    released_tags_module = tmp_path / "released_tags.py"
    released_tags_module.write_text("RELEASED_TAGS: dict = {\n}\n", encoding="utf-8")
    monkeypatch.setattr(writer, "RELEASED_TAGS_MODULE", released_tags_module)

    module_verifications = tmp_path / "module-release-verifications.json"
    module_verifications.write_text(_minimal_verified_document(), encoding="utf-8")
    monkeypatch.setattr(writer, "MODULE_RELEASE_VERIFICATIONS", module_verifications)

    module_legacy = tmp_path / "module-release-legacy-unverified.json"
    module_legacy.write_text(_minimal_legacy_document(), encoding="utf-8")
    monkeypatch.setattr(writer, "MODULE_RELEASE_LEGACY", module_legacy)

    ledger = tmp_path / "declared-publication-baseline.json"
    ledger.write_text(json.dumps({"unpublished": {}}), encoding="utf-8")
    monkeypatch.setattr(writer, "LEDGER", ledger)

    common_kwargs = {
        "distribution": _DISTRIBUTION,
        "version": _VERSION,
        "tag": _TAG,
        "package_dir": None,
        "import_name": None,
        "no_lineage": True,
        "artifact_dir": str(artifact_dir),
    }

    # Neither flag supplied — refused before any file is touched.
    with pytest.raises(writer.ReleaseRecordError, match="expected-run-id"):
        writer.write_record(**common_kwargs)
    assert (
        json.loads(module_verifications.read_text(encoding="utf-8"))["releases"] == []
    )

    # Only one of the two supplied — still refused.
    with pytest.raises(writer.ReleaseRecordError, match="expected-run-id"):
        writer.write_record(**common_kwargs, expected_commit=commit)
    with pytest.raises(writer.ReleaseRecordError, match="expected-run-id"):
        writer.write_record(**common_kwargs, expected_run_id="555000333")

    # A run id that does not name the run that produced this tag's evidence.
    with pytest.raises(writer.ReleaseRecordError, match="verification run"):
        writer.write_record(
            **common_kwargs, expected_run_id="999999999", expected_commit=commit
        )
    assert (
        json.loads(module_verifications.read_text(encoding="utf-8"))["releases"] == []
    )

    # A commit that is not the tag's own peeled commit.
    with pytest.raises(writer.ReleaseRecordError, match="expected"):
        writer.write_record(
            **common_kwargs,
            expected_run_id="555000333",
            expected_commit="0" * 40,
        )
    assert (
        json.loads(module_verifications.read_text(encoding="utf-8"))["releases"] == []
    )

    # Both correct — the matching, accepted case.
    changed = writer.write_record(
        **common_kwargs, expected_run_id="555000333", expected_commit=commit
    )
    assert any("recorded the verified" in line for line in changed)


def test_write_record_refuses_a_wheel_that_disagrees_with_the_tags_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    origin, work, commit, artifact_dir = _tag_a_release(
        tmp_path, run_id="555000333", wheel_bytes=b"the retained wheel bytes"
    )

    writer = _writer()
    monkeypatch.setattr(writer, "REPO_ROOT", work)

    released_tags_module = tmp_path / "released_tags.py"
    released_tags_module.write_text("RELEASED_TAGS: dict = {\n}\n", encoding="utf-8")
    monkeypatch.setattr(writer, "RELEASED_TAGS_MODULE", released_tags_module)

    module_verifications = tmp_path / "module-release-verifications.json"
    module_verifications.write_text(_minimal_verified_document(), encoding="utf-8")
    monkeypatch.setattr(writer, "MODULE_RELEASE_VERIFICATIONS", module_verifications)

    module_legacy = tmp_path / "module-release-legacy-unverified.json"
    module_legacy.write_text(_minimal_legacy_document(), encoding="utf-8")
    monkeypatch.setattr(writer, "MODULE_RELEASE_LEGACY", module_legacy)

    ledger = tmp_path / "declared-publication-baseline.json"
    ledger.write_text(json.dumps({"unpublished": {}}), encoding="utf-8")
    monkeypatch.setattr(writer, "LEDGER", ledger)

    # Swap the retained artifact for one whose bytes disagree with the tag's
    # own embedded evidence -- as if someone rebuilt instead of downloading
    # the exact retained wheel `compare-published` already verified.
    (artifact_dir / _WHEEL_NAME).write_bytes(b"REBUILT, not the retained bytes")

    with pytest.raises(writer.ReleaseRecordError, match="disagrees with the retained"):
        writer.write_record(
            distribution=_DISTRIBUTION,
            version=_VERSION,
            tag=_TAG,
            package_dir=None,
            import_name=None,
            no_lineage=True,
            artifact_dir=str(artifact_dir),
        )
    # Refused before any file is mutated.
    persisted = json.loads(module_verifications.read_text(encoding="utf-8"))
    assert persisted["releases"] == []


def _synthetic_verified_row_and_evidence() -> tuple[dict, dict]:
    row = {
        "distribution": "dotmac-approvals",
        "version": "0.1.0a99",
        "tag": "dotmac-approvals-v0.1.0a99",
        "tag_object": "a" * 40,
        "peeled_commit": "b" * 40,
        "status": "released",
        "pinnable": True,
        "sha256": {"dotmac_approvals-0.1.0a99-py3-none-any.whl": "c" * 64},
        "verification_run_id": "999",
    }
    evidence = {
        "distribution": "dotmac-approvals",
        "version": "0.1.0a99",
        "wheel_filename": "dotmac_approvals-0.1.0a99-py3-none-any.whl",
        "wheel_sha256": "c" * 64,
        "verification_run_id": "999",
    }
    return row, evidence


def _wrap_verified(row: dict) -> str:
    return json.dumps(
        {
            "$comment": "fixture",
            "schema": "ModuleReleaseVerifications.v1",
            "releases": [row],
        }
    )


def test_validate_inventory_refuses_a_row_whose_digest_disagrees_with_evidence() -> (
    None
):
    writer = _writer()
    row, evidence = _synthetic_verified_row_and_evidence()
    tag = row["tag"]
    live = {tag: (row["tag_object"], row["peeled_commit"])}
    evidence["wheel_sha256"] = "f" * 64
    with pytest.raises(writer.ReleaseRecordError, match="evidence digest mismatch"):
        writer.validate_module_release_inventory(
            _wrap_verified(row),
            _minimal_legacy_document(),
            live=live,
            evidence={tag: evidence},
            targets={"dotmac-approvals"},
        )


def test_validate_inventory_refuses_a_row_whose_run_id_disagrees_with_evidence() -> (
    None
):
    writer = _writer()
    row, evidence = _synthetic_verified_row_and_evidence()
    tag = row["tag"]
    live = {tag: (row["tag_object"], row["peeled_commit"])}
    evidence["verification_run_id"] = "111"
    with pytest.raises(writer.ReleaseRecordError, match="evidence run id mismatch"):
        writer.validate_module_release_inventory(
            _wrap_verified(row),
            _minimal_legacy_document(),
            live=live,
            evidence={tag: evidence},
            targets={"dotmac-approvals"},
        )


def test_validate_inventory_refuses_a_row_whose_filename_disagrees_with_evidence() -> (
    None
):
    writer = _writer()
    row, evidence = _synthetic_verified_row_and_evidence()
    tag = row["tag"]
    live = {tag: (row["tag_object"], row["peeled_commit"])}
    evidence["wheel_filename"] = "dotmac_approvals-0.1.0a99-py3-none-linux_x86_64.whl"
    with pytest.raises(writer.ReleaseRecordError, match="evidence digest mismatch"):
        writer.validate_module_release_inventory(
            _wrap_verified(row),
            _minimal_legacy_document(),
            live=live,
            evidence={tag: evidence},
            targets={"dotmac-approvals"},
        )


# ── 4. Existing-tag refusal, local and remote-only ──────────────────────────


def test_create_and_push_tag_refuses_an_existing_local_tag(tmp_path: Path) -> None:
    origin, work, commit = _bare_origin_and_work(tmp_path)
    _run_git(["tag", "-a", _TAG, "-m", "pre-existing", commit], cwd=work)
    original_object = _run_git(["rev-parse", _TAG], cwd=work).strip()

    tagger = _tagger()
    with pytest.raises(tagger.ReleaseRecordError, match="already exists"):
        tagger.create_and_push_tag(
            tag=_TAG,
            commit=commit,
            message="a different message",
            remote="origin",
            cwd=work,
        )

    assert _run_git(["rev-parse", _TAG], cwd=work).strip() == original_object
    # Never pushed by the refused attempt.
    remote_listing = _run_git(["ls-remote", "--tags", "origin", _TAG], cwd=work)
    assert remote_listing.strip() == ""


def test_create_and_push_tag_refuses_a_tag_that_exists_only_on_the_remote(
    tmp_path: Path,
) -> None:
    origin, work, commit = _bare_origin_and_work(tmp_path)

    # A second clone creates and pushes the tag first, so `work` has never
    # locally seen it -- only the remote check can catch it.
    other = tmp_path / "other"
    _run_git(["clone", str(origin), str(other)], cwd=tmp_path)
    _run_git(["config", "user.name", "Test Runner"], cwd=other)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=other)
    _run_git(["tag", "-a", _TAG, "-m", "created elsewhere", commit], cwd=other)
    _run_git(["push", "origin", f"refs/tags/{_TAG}"], cwd=other)
    original_object = _run_git(["rev-parse", _TAG], cwd=other).strip()

    local_check = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{_TAG}"],  # noqa: S607
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert local_check.returncode != 0, "the tag must be invisible locally in `work`"

    tagger = _tagger()
    with pytest.raises(tagger.ReleaseRecordError, match="already exists"):
        tagger.create_and_push_tag(
            tag=_TAG,
            commit=commit,
            message="a competing message",
            remote="origin",
            cwd=work,
        )

    unchanged_object = _run_git(
        ["ls-remote", "--tags", "origin", _TAG], cwd=work
    ).split()[0]
    assert unchanged_object == original_object


# ── 5. Legacy/verified append-only freeze ───────────────────────────────────

_APPROVALS_LEGACY_TAG = "dotmac-approvals-v0.1.0a1"


def _write_ledger_fixtures(
    repo: Path, *, legacy_tags: list[dict], legacy_indent: int | None = 2
) -> None:
    inventories = repo / "docs" / "inventories"
    inventories.mkdir(parents=True, exist_ok=True)
    (inventories / "module-release-verifications.json").write_text(
        json.dumps(
            {
                "$comment": "fixture",
                "schema": "ModuleReleaseVerifications.v1",
                "releases": [],
            }
        ),
        encoding="utf-8",
    )
    (inventories / "module-release-legacy-unverified.json").write_text(
        json.dumps(
            {
                "$comment": "fixture",
                "schema": "ModuleReleaseLegacyUnverified.v1",
                "cutover_source_commit": "d" * 40,
                "tags": legacy_tags,
            },
            indent=legacy_indent,
        ),
        encoding="utf-8",
    )


def _base_legacy_row() -> dict:
    return {
        "tag": _APPROVALS_LEGACY_TAG,
        "tag_object": "1" * 40,
        "peeled_commit": "2" * 40,
    }


def test_append_only_base_accepts_a_byte_identical_legacy_and_verified_ledger(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "legacy_repo"
    _init_repo(repo)
    _write_ledger_fixtures(repo, legacy_tags=[_base_legacy_row()])
    base = _commit_all(repo, "base")

    checker = _append_only_checker()
    checker.ROOT = repo
    checker.check_append_only_base(base)  # HEAD == base: must not raise


def test_append_only_base_refuses_a_changed_legacy_row(tmp_path: Path) -> None:
    repo = tmp_path / "legacy_repo"
    _init_repo(repo)
    _write_ledger_fixtures(repo, legacy_tags=[_base_legacy_row()])
    base = _commit_all(repo, "base")

    mutated_row = {**_base_legacy_row(), "tag_object": "9" * 40}
    _write_ledger_fixtures(repo, legacy_tags=[mutated_row])
    _commit_all(repo, "mutate legacy row")

    checker = _append_only_checker()
    checker.ROOT = repo
    with pytest.raises(checker.ReleaseRecordError, match="frozen"):
        checker.check_append_only_base(base)


def test_append_only_base_refuses_a_removed_legacy_row(tmp_path: Path) -> None:
    repo = tmp_path / "legacy_repo"
    _init_repo(repo)
    _write_ledger_fixtures(repo, legacy_tags=[_base_legacy_row()])
    base = _commit_all(repo, "base")

    _write_ledger_fixtures(repo, legacy_tags=[])
    _commit_all(repo, "remove legacy row")

    checker = _append_only_checker()
    checker.ROOT = repo
    with pytest.raises(checker.ReleaseRecordError, match="frozen"):
        checker.check_append_only_base(base)


def test_append_only_base_refuses_whitespace_only_legacy_reformatting(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "legacy_repo"
    _init_repo(repo)
    _write_ledger_fixtures(repo, legacy_tags=[_base_legacy_row()], legacy_indent=2)
    base = _commit_all(repo, "base")

    # Same rows, same order, different (still valid) JSON whitespace.
    _write_ledger_fixtures(repo, legacy_tags=[_base_legacy_row()], legacy_indent=4)
    _commit_all(repo, "reformat legacy whitespace only")

    checker = _append_only_checker()
    checker.ROOT = repo
    with pytest.raises(checker.ReleaseRecordError, match="frozen"):
        checker.check_append_only_base(base)


# ── 6. Recovery workflow routes the dist directory through one env var ──────


def _run_bodies(workflow: dict, job_name: str) -> list[tuple[str, str]]:
    """[(step name, run body), ...] for every `run:` step in one job."""
    job = workflow["jobs"][job_name]
    return [
        (step.get("name", "<unnamed>"), step["run"])
        for step in job["steps"]
        if "run" in step
    ]


def _recovery_dist_problems(text: str) -> list[str]:
    problems: list[str] = []
    workflow = yaml.safe_load(text)
    job = workflow["jobs"]["recover"]
    env = job.get("env", {})
    # `runner.temp` is not legal in `jobs.<id>.env` — GitHub only expands
    # github/needs/strategy/matrix/vars/secrets/inputs there. The directory
    # is instead resolved once, in a `GITHUB_ENV` step, right after checkout.
    if "RECOVERED_DIST" in env:
        problems.append("RECOVERED_DIST must not be a job-level env entry")
    steps = job["steps"]
    resolver_bodies = [
        step.get("run", "")
        for step in steps
        if "RECOVERED_DIST=${RUNNER_TEMP}/recovered-dist" in step.get("run", "")
        and "GITHUB_ENV" in step.get("run", "")
    ]
    if not resolver_bodies:
        problems.append(
            "no step resolves RECOVERED_DIST via RUNNER_TEMP into GITHUB_ENV"
        )
    for name, body in _run_bodies(workflow, "recover"):
        # The one authorized definition site: it legitimately spells the
        # literal path once, on the right-hand side of the GITHUB_ENV write.
        if "GITHUB_ENV" in body and "RUNNER_TEMP" in body:
            continue
        mentions_dist = "recovered-dist" in body or "RECOVERED_DIST" in body
        if not mentions_dist:
            continue
        uses_var = "$RECOVERED_DIST" in body or "${RECOVERED_DIST}" in body
        bare_literal = "recovered-dist" in body
        if bare_literal:
            problems.append(f"{name!r} references the bare literal 'recovered-dist'")
        elif not uses_var:
            problems.append(f"{name!r} mentions the dist directory without the env var")
    return problems


def test_recovery_workflow_routes_every_dist_reference_through_one_env_var() -> None:
    text = RECOVER_WORKFLOW.read_text(encoding="utf-8")
    assert _recovery_dist_problems(text) == []


def test_recovery_dist_detector_is_sensitive_to_a_reintroduced_bare_literal() -> None:
    text = RECOVER_WORKFLOW.read_text(encoding="utf-8")
    # Plant the exact regression: a run body reverting to the bare workspace
    # literal instead of the env var.
    planted = text.replace(
        'mkdir -p "$RECOVERED_DIST"',
        'mkdir -p "recovered-dist"',
        1,
    )
    assert planted != text, "the fixture string was not found; workflow drifted"
    problems = _recovery_dist_problems(planted)
    assert any("bare literal" in problem for problem in problems)


# ── 6b. `runner.*` is never expanded in `jobs.<id>.env` ─────────────────────
#
# GitHub expands `jobs.<id>.env` values from only github/needs/strategy/
# matrix/vars/secrets/inputs — `runner` is a step/job `run:` context object,
# not an env-expression context, and silently resolves to an empty string
# there. `runner.temp` at step level (e.g. `actions/download-artifact`'s
# `with: path:`) is legal and untouched by this check.


def _job_env_runner_context_violations(workflow: dict) -> list[str]:
    violations: list[str] = []
    for job_name, job in workflow.get("jobs", {}).items():
        for key, value in (job.get("env") or {}).items():
            if isinstance(value, str) and "runner." in value:
                violations.append(f"jobs.{job_name}.env.{key} references runner.*")
    return violations


def test_no_job_level_env_value_references_the_runner_context() -> None:
    for workflow_path in (RELEASE_WORKFLOW, RECOVER_WORKFLOW):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        assert _job_env_runner_context_violations(workflow) == []


def test_job_env_runner_context_detector_is_sensitive_to_a_reintroduced_violation() -> (
    None
):
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    # Plant the exact regression this rule exists to catch.
    workflow["jobs"]["verify"]["env"]["ARTIFACT_DIR"] = (
        "${{ runner.temp }}/module-release-dist"
    )
    violations = _job_env_runner_context_violations(workflow)
    assert any("verify.env.ARTIFACT_DIR" in v for v in violations)


def test_job_env_runner_context_detector_does_not_flag_a_step_level_runner_temp() -> (
    None
):
    # Near-miss: `runner.temp` used at STEP level (legal) must not trip the
    # job-level-only detector.
    workflow = {
        "jobs": {
            "build": {
                "env": {"MODULE": "dotmac-example"},
                "steps": [
                    {
                        "uses": "actions/download-artifact@v8",
                        "with": {"path": "${{ runner.temp }}/dist"},
                    }
                ],
            }
        }
    }
    assert _job_env_runner_context_violations(workflow) == []


# ── 7. Tag/compare steps are hardened against silent force/skip ────────────

_FORBIDDEN_SUBSTRINGS = ("git tag -f", "--force", "git tag -d", "push --delete")


def _step(workflow: dict, job_name: str, step_name: str) -> dict:
    job = workflow["jobs"][job_name]
    for step in job["steps"]:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"no step named {step_name!r} in job {job_name!r}")


def _hardening_problems(step: dict) -> list[str]:
    problems: list[str] = []
    if "continue-on-error" in step:
        problems.append("continue-on-error is set")
    if "if" in step:
        problems.append("if: is set")
    body = step.get("run", "")
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        if forbidden in body:
            problems.append(f"run body contains {forbidden!r}")
    return problems


def test_release_module_tag_and_compare_steps_are_unhardened_by_nothing() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    tag_step = _step(workflow, "verify", "Tag the verified release")
    compare_step = _step(
        workflow, "verify", "Prove published bytes match the retained build artifact"
    )
    assert _hardening_problems(tag_step) == []
    assert _hardening_problems(compare_step) == []


def test_recover_module_tag_and_compare_steps_are_unhardened_by_nothing() -> None:
    workflow = yaml.safe_load(RECOVER_WORKFLOW.read_text(encoding="utf-8"))
    tag_step = _step(workflow, "recover", "Tag the recovered release")
    compare_step = _step(
        workflow, "recover", "Prove the published artifact matches the original build"
    )
    assert _hardening_problems(tag_step) == []
    assert _hardening_problems(compare_step) == []


def test_tag_step_hardening_detector_is_sensitive_to_a_planted_continue_on_error() -> (
    None
):
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    tag_step = dict(_step(workflow, "verify", "Tag the verified release"))
    tag_step["continue-on-error"] = True
    assert _hardening_problems(tag_step) == ["continue-on-error is set"]


def test_tag_step_hardening_detector_is_sensitive_to_a_planted_if_guard() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    tag_step = dict(_step(workflow, "verify", "Tag the verified release"))
    tag_step["if"] = "always()"
    assert _hardening_problems(tag_step) == ["if: is set"]


def test_tag_step_hardening_detector_is_sensitive_to_a_planted_force_tag() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    tag_step = dict(_step(workflow, "verify", "Tag the verified release"))
    tag_step["run"] = tag_step["run"] + "\ngit tag -f something\n"
    problems = _hardening_problems(tag_step)
    assert any("git tag -f" in problem for problem in problems)


def test_compare_step_hardening_detector_is_sensitive_to_a_planted_delete() -> None:
    workflow = yaml.safe_load(RECOVER_WORKFLOW.read_text(encoding="utf-8"))
    step_name = "Prove the published artifact matches the original build"
    compare_step = dict(_step(workflow, "recover", step_name))
    compare_step["run"] = compare_step["run"] + "\ngit push --delete origin sometag\n"
    problems = _hardening_problems(compare_step)
    assert any("push --delete" in problem for problem in problems)
