"""The Foundation wheel observer measures local bytes; it does not trust inputs."""

from __future__ import annotations

import hashlib
import inspect
import zipfile
from pathlib import Path

import pytest
from dotmac_deployment_foundation.artifact_identity import (
    ARTIFACT_IDENTITY_SCHEMA,
    ArtifactIdentityError,
    observe_artifact_identity,
)


def _wheel(
    path: Path,
    *,
    payload: bytes = b"print('foundation')\n",
    metadata_name: str = "dotmac-deployment-foundation",
    metadata_version: str = "1.2.3",
    dist_info: str = "dotmac_deployment_foundation-1.2.3.dist-info",
    compression: int = zipfile.ZIP_DEFLATED,
    extra: tuple[tuple[str, bytes], ...] = (),
    core_metadata_version: str | None = "2.1",
    wheel_metadata: str = "Wheel-Version: 1.0\n",
    record: str = "untrusted,sha256=not-a-proof,1\n",
) -> Path:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        archive.writestr("dotmac_deployment_foundation/__init__.py", payload)
        for name, content in extra:
            archive.writestr(name, content)
        archive.writestr(
            f"{dist_info}/METADATA",
            (
                ""
                if core_metadata_version is None
                else f"Metadata-Version: {core_metadata_version}\n"
            )
            + f"Name: {metadata_name}\nVersion: {metadata_version}\n",
        )
        archive.writestr(f"{dist_info}/WHEEL", wheel_metadata)
        # RECORD has no integrity authority in this observer and packing it
        # differently must not change semantic evidence.
        archive.writestr(f"{dist_info}/RECORD", record)
    return path


def test_observation_is_deterministic_and_derived_from_wheel_bytes(
    tmp_path: Path,
) -> None:
    wheel = _wheel(tmp_path / "foundation-1.2.3-py3-none-any.whl")

    first = observe_artifact_identity(wheel)
    second = observe_artifact_identity(wheel)

    assert first == second
    assert first.schema == ARTIFACT_IDENTITY_SCHEMA
    assert first.size_bytes == wheel.stat().st_size
    assert first.distribution_name == "dotmac-deployment-foundation"
    assert first.distribution_version == "1.2.3"
    assert first.wheel_sha256 == (
        f"sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}"
    )


def test_content_change_moves_wheel_and_semantic_digests(tmp_path: Path) -> None:
    before = observe_artifact_identity(_wheel(tmp_path / "before.whl"))
    after = observe_artifact_identity(
        _wheel(tmp_path / "after.whl", payload=b"changed\n")
    )

    assert after.wheel_sha256 != before.wheel_sha256
    assert after.semantic_manifest_sha256 != before.semantic_manifest_sha256


def test_selected_record_changes_wheel_bytes_but_not_semantic_evidence(
    tmp_path: Path,
) -> None:
    first = observe_artifact_identity(
        _wheel(tmp_path / "first.whl", record="package,,\n")
    )
    second = observe_artifact_identity(
        _wheel(tmp_path / "second.whl", record="package,sha256=changed,7\n")
    )

    assert first.wheel_sha256 != second.wheel_sha256
    assert first.semantic_manifest_sha256 == second.semantic_manifest_sha256


def test_path_subclasses_are_refused_before_they_can_inject_bytes(
    tmp_path: Path,
) -> None:
    class MaliciousPath(type(Path())):
        def read_bytes(self) -> bytes:
            raise AssertionError("subclass bytes must never be read")

    path = MaliciousPath(tmp_path / "not-a-wheel.whl")
    with pytest.raises(ArtifactIdentityError, match="concrete platform Path"):
        observe_artifact_identity(path)


def test_observation_uses_one_snapshot_after_reading_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = _wheel(tmp_path / "foundation.whl")
    snapshot = wheel.read_bytes()
    replacement = _wheel(
        tmp_path / "replacement.whl", payload=b"replacement\n"
    ).read_bytes()

    def read_then_replace(path: Path) -> bytes:
        assert path == wheel
        wheel.write_bytes(replacement)
        return snapshot

    monkeypatch.setattr(Path, "read_bytes", read_then_replace)
    observed = observe_artifact_identity(wheel)

    assert observed.size_bytes == len(snapshot)
    assert observed.wheel_sha256 == f"sha256:{hashlib.sha256(snapshot).hexdigest()}"
    assert observed.wheel_sha256 != f"sha256:{hashlib.sha256(replacement).hexdigest()}"


def test_same_semantic_members_with_different_zip_packing_keep_semantic_digest(
    tmp_path: Path,
) -> None:
    stored = observe_artifact_identity(
        _wheel(tmp_path / "stored.whl", compression=zipfile.ZIP_STORED)
    )
    compressed = observe_artifact_identity(
        _wheel(tmp_path / "compressed.whl", compression=zipfile.ZIP_DEFLATED)
    )

    assert stored.semantic_manifest_sha256 == compressed.semantic_manifest_sha256
    assert stored.wheel_sha256 != compressed.wheel_sha256


def test_nested_record_is_semantic_content_not_the_selected_dist_info_record(
    tmp_path: Path,
) -> None:
    before = observe_artifact_identity(
        _wheel(
            tmp_path / "before.whl",
            extra=(("package/private.dist-info/RECORD", b"first"),),
        )
    )
    after = observe_artifact_identity(
        _wheel(
            tmp_path / "after.whl",
            extra=(("package/private.dist-info/RECORD", b"second"),),
        )
    )

    assert before.semantic_manifest_sha256 != after.semantic_manifest_sha256


@pytest.mark.parametrize(
    ("kwargs", "match"),
    (
        ({"wheel_metadata": "Wheel-Version: unsupported\n"}, "invalid Wheel-Version"),
        ({"wheel_metadata": "Wheel-Version: 2.0\n"}, "unsupported Wheel-Version"),
        ({"record": "only,two\n"}, "three-column CSV"),
    ),
)
def test_wheel_and_record_metadata_must_be_structurally_readable(
    tmp_path: Path, kwargs: dict[str, str], match: str
) -> None:
    wheel = _wheel(tmp_path / "malformed-metadata.whl", **kwargs)

    with pytest.raises(ArtifactIdentityError, match=match):
        observe_artifact_identity(wheel)


@pytest.mark.parametrize("core_metadata_version", (None, "not-a-version"))
def test_core_metadata_requires_one_sane_metadata_version(
    tmp_path: Path, core_metadata_version: str | None
) -> None:
    wheel = _wheel(
        tmp_path / "malformed-core-metadata.whl",
        core_metadata_version=core_metadata_version,
    )

    with pytest.raises(ArtifactIdentityError, match="sane Metadata-Version"):
        observe_artifact_identity(wheel)


@pytest.mark.parametrize("missing", ("METADATA", "WHEEL", "RECORD"))
def test_missing_required_dist_info_member_is_refused(
    tmp_path: Path, missing: str
) -> None:
    wheel = tmp_path / f"missing-{missing.lower()}-member.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("package/__init__.py", b"")
        members = {
            "METADATA": "Name: dotmac-deployment-foundation\nVersion: 1.2.3\n",
            "WHEEL": "Wheel-Version: 1.0\n",
            "RECORD": "package,,\n",
        }
        for name, content in members.items():
            if name != missing:
                archive.writestr(
                    f"dotmac_deployment_foundation-1.2.3.dist-info/{name}", content
                )

    with pytest.raises(ArtifactIdentityError, match=missing):
        observe_artifact_identity(wheel)


@pytest.mark.parametrize(
    ("writer", "match"),
    [
        (lambda path: path.write_bytes(b"not a zip"), "ZIP archive"),
        (
            lambda path: _wheel(
                path,
                dist_info="other-1.2.3.dist-info",
            ),
            "does not match METADATA",
        ),
        (
            lambda path: _wheel(
                path,
                extra=(("../escape.py", b"bad"),),
            ),
            "unsafe member path",
        ),
    ],
)
def test_malformed_or_wrong_subject_wheels_are_refused(
    tmp_path: Path, writer, match: str
) -> None:
    wheel = tmp_path / "bad.whl"
    writer(wheel)

    with pytest.raises(ArtifactIdentityError, match=match):
        observe_artifact_identity(wheel)


def test_missing_nonfile_and_nonwheel_paths_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIdentityError, match="does not exist"):
        observe_artifact_identity(tmp_path / "missing.whl")
    with pytest.raises(ArtifactIdentityError, match="not a regular file"):
        observe_artifact_identity(tmp_path)
    with pytest.raises(ArtifactIdentityError, match="must end in .whl"):
        observe_artifact_identity(_wheel(tmp_path / "wrong.zip"))


def test_duplicate_members_and_multiple_dist_info_directories_are_refused(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.whl"
    _wheel(
        duplicate,
        extra=(
            ("dotmac_deployment_foundation/repeated.py", b"first"),
            ("dotmac_deployment_foundation/repeated.py", b"second"),
        ),
    )
    with pytest.raises(ArtifactIdentityError, match="duplicate member"):
        observe_artifact_identity(duplicate)

    multiple = tmp_path / "multiple.whl"
    _wheel(
        multiple,
        extra=(("another-1.2.3.dist-info/WHEEL", b"Wheel-Version: 1.0\n"),),
    )
    with pytest.raises(ArtifactIdentityError, match="exactly one .dist-info"):
        observe_artifact_identity(multiple)


@pytest.mark.parametrize("member", ("package//module.py", "package/./module.py"))
def test_normalized_path_aliases_are_refused(tmp_path: Path, member: str) -> None:
    wheel = _wheel(tmp_path / "aliased.whl", extra=((member, b"bad"),))

    with pytest.raises(ArtifactIdentityError, match="unsafe member path"):
        observe_artifact_identity(wheel)


@pytest.mark.parametrize(
    "members",
    (
        (("package/item", b"file"), ("package/item/", b"")),
        (("package/item", b"file"), ("package/item/child.py", b"child")),
    ),
)
def test_file_directory_prefix_conflicts_are_refused(
    tmp_path: Path, members: tuple[tuple[str, bytes], ...]
) -> None:
    wheel = _wheel(tmp_path / "ambiguous.whl", extra=members)

    with pytest.raises(ArtifactIdentityError, match="file/directory prefix conflict"):
        observe_artifact_identity(wheel)


@pytest.mark.parametrize(
    "members",
    (
        (("pkg/Foo", b"file"), ("pkg/foo/child.py", b"child")),
        (("pkg/caf\u00e9", b"file"), ("pkg/cafe\u0301/child.py", b"child")),
    ),
)
def test_normalized_file_directory_prefix_conflicts_are_refused(
    tmp_path: Path, members: tuple[tuple[str, bytes], ...]
) -> None:
    wheel = _wheel(tmp_path / "normalized-ambiguous.whl", extra=members)

    with pytest.raises(ArtifactIdentityError, match="file/directory prefix conflict"):
        observe_artifact_identity(wheel)


@pytest.mark.parametrize(
    "members",
    (
        (("package/Foo.py", b"one"), ("package/foo.py", b"two")),
        (("package/caf\u00e9.py", b"one"), ("package/cafe\u0301.py", b"two")),
    ),
)
def test_casefold_and_nfc_path_collisions_are_refused(
    tmp_path: Path, members: tuple[tuple[str, bytes], ...]
) -> None:
    wheel = _wheel(tmp_path / "collision.whl", extra=members)

    with pytest.raises(ArtifactIdentityError, match="casefold/NFC-colliding"):
        observe_artifact_identity(wheel)


def test_public_observer_signature_has_no_injected_evidence_or_reader() -> None:
    assert tuple(inspect.signature(observe_artifact_identity).parameters) == (
        "wheel_path",
    )
