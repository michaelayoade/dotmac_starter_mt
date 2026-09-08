from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_kernel_release_artifacts import (  # noqa: E402
    public_exports_evidence,
)

CATALOGUE = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/public_exports.json"


def _wheel(path: Path, payload: bytes | None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if payload is not None:
            archive.writestr("dotmac_kernel/public_exports.json", payload)


def _sdist(path: Path, payload: bytes | None) -> None:
    with tarfile.open(path, "w:gz") as archive:
        if payload is not None:
            info = tarfile.TarInfo(
                "dotmac-kernel-0.1.0a103/src/dotmac_kernel/public_exports.json"
            )
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _artifacts(
    tmp_path: Path, *, wheel: bytes | None, sdist: bytes | None
) -> tuple[Path, Path]:
    wheel_path = tmp_path / "dotmac_kernel-0.1.0a103-py3-none-any.whl"
    sdist_path = tmp_path / "dotmac_kernel-0.1.0a103.tar.gz"
    _wheel(wheel_path, wheel)
    _sdist(sdist_path, sdist)
    return wheel_path, sdist_path


def test_successor_catalogue_is_bound_to_both_archive_bytes(tmp_path: Path) -> None:
    payload = CATALOGUE.read_bytes()
    wheel, sdist = _artifacts(tmp_path, wheel=payload, sdist=payload)
    evidence = public_exports_evidence(
        version="0.1.0a103", wheel=wheel, sdist=sdist, source=CATALOGUE
    )
    assert evidence == {
        "name": "dotmac_kernel/public_exports.json",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "schema": "dotmac.kernel-public-exports.v1",
    }


@pytest.mark.parametrize(
    ("wheel", "sdist", "message"),
    [
        (None, b"x", "missing"),
        (CATALOGUE.read_bytes(), CATALOGUE.read_bytes(), "differ"),
        (CATALOGUE.read_bytes(), CATALOGUE.read_bytes(), "schema"),
    ],
)
def test_catalogue_archive_mismatches_refuse(
    tmp_path: Path, wheel: bytes | None, sdist: bytes | None, message: str
) -> None:
    if message == "differ":
        assert sdist is not None
        original = sdist
        document = json.loads(sdist)
        exports = document["modules"]["dotmac_kernel.api_documentation"]["exports"]
        exports.remove("audit_api_documentation")
        sdist = json.dumps(document, sort_keys=True, indent=2).encode() + b"\n"
        assert sdist != original
    elif message == "schema":
        assert wheel is not None
        wheel = wheel.replace(
            b'"schema": "dotmac.kernel-public-exports.v1"', b'"schema": "wrong"'
        )
    wheel_path, sdist_path = _artifacts(tmp_path, wheel=wheel, sdist=sdist)
    with pytest.raises(SystemExit, match=message):
        public_exports_evidence(
            version="0.1.0a103",
            wheel=wheel_path,
            sdist=sdist_path,
            source=CATALOGUE,
        )


def test_historical_source_without_catalogue_remains_v1(tmp_path: Path) -> None:
    wheel, sdist = _artifacts(tmp_path, wheel=None, sdist=None)
    assert (
        public_exports_evidence(
            version="0.1.0a102",
            wheel=wheel,
            sdist=sdist,
            source=None,
            source_present=False,
        )
        is None
    )


def test_successor_source_without_catalogue_members_refuses(tmp_path: Path) -> None:
    wheel, sdist = _artifacts(tmp_path, wheel=None, sdist=None)
    with pytest.raises(SystemExit, match="missing"):
        public_exports_evidence(
            version="0.1.0a103",
            wheel=wheel,
            sdist=sdist,
            source=CATALOGUE,
            source_present=True,
        )


def test_missing_catalogue_is_historical_only_for_named_releases(
    tmp_path: Path,
) -> None:
    wheel, sdist = _artifacts(tmp_path, wheel=None, sdist=None)
    with pytest.raises(SystemExit, match="successor source catalogue is absent"):
        public_exports_evidence(
            version="0.1.0a104",
            wheel=wheel,
            sdist=sdist,
            source=None,
            source_present=False,
        )


def test_absolute_sdist_catalogue_member_is_not_accepted(tmp_path: Path) -> None:
    payload = CATALOGUE.read_bytes()
    wheel = tmp_path / "dotmac_kernel-0.1.0a103-py3-none-any.whl"
    sdist = tmp_path / "dotmac_kernel-0.1.0a103.tar.gz"
    _wheel(wheel, payload)
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo("/src/dotmac_kernel/public_exports.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(SystemExit, match="missing"):
        public_exports_evidence(
            version="0.1.0a103", wheel=wheel, sdist=sdist, source=CATALOGUE
        )
