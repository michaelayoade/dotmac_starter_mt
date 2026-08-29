"""Canaries for the independently consumable controller release receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from scripts import release_facility as lane

DISTRIBUTION = "dotmac-deployment-foundation"
VERSION = tomllib.loads(
    (
        lane.REPO_ROOT / "packages" / "dotmac-deployment-foundation" / "pyproject.toml"
    ).read_text(encoding="utf-8")
)["tool"]["poetry"]["version"]
SOURCE_REVISION = "a" * 40
RUN_ID = 60366
TAG = f"{DISTRIBUTION}-v{VERSION}"
RECEIPT_FIELDS = {
    "schema",
    "distribution",
    "exact_version",
    "artifact_sha256",
    "launcher_sha256",
    "source_revision",
    "release_run_id",
    "tag",
}


def _create_bundle(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    wheel = tmp_path / f"dotmac_deployment_foundation-{VERSION}-py3-none-any.whl"
    wheel.write_bytes(b"exact built wheel bytes\n")
    bundle = tmp_path / "controller-release"
    lane.cmd_create_controller_receipt(
        argparse.Namespace(
            distribution=DISTRIBUTION,
            version=VERSION,
            source_revision=SOURCE_REVISION,
            release_run_id=RUN_ID,
            tag=TAG,
            wheel=str(wheel),
            output_dir=str(bundle),
        )
    )
    receipt = bundle / lane.CONTROLLER_RECEIPT_FILENAME
    bundled_wheel = bundle / "wheel" / wheel.name
    launcher = bundle / "launcher" / "run_deployment_controller.py"
    return bundle, receipt, bundled_wheel, launcher


def _verify(
    receipt: Path,
    wheel: Path,
    launcher: Path,
    *,
    release_run_id: int = RUN_ID,
) -> None:
    lane.verify_controller_receipt(
        receipt_path=receipt,
        wheel=wheel,
        launcher=launcher,
        distribution=DISTRIBUTION,
        version=VERSION,
        source_revision=SOURCE_REVISION,
        release_run_id=release_run_id,
        tag=TAG,
    )


def test_receipt_is_the_exact_strict_schema_and_binds_both_artifacts(
    tmp_path: Path,
) -> None:
    _, receipt, wheel, launcher = _create_bundle(tmp_path)

    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert set(document) == RECEIPT_FIELDS
    assert document == {
        "schema": "DeploymentControllerReleaseReceipt.v1",
        "distribution": DISTRIBUTION,
        "exact_version": VERSION,
        "artifact_sha256": f"sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}",
        "launcher_sha256": (
            f"sha256:{hashlib.sha256(launcher.read_bytes()).hexdigest()}"
        ),
        "source_revision": SOURCE_REVISION,
        "release_run_id": RUN_ID,
        "tag": TAG,
    }
    _verify(receipt, wheel, launcher)


@pytest.mark.parametrize("artifact", ["wheel", "launcher"])
def test_each_artifact_digest_is_load_bearing(tmp_path: Path, artifact: str) -> None:
    _, receipt, wheel, launcher = _create_bundle(tmp_path)
    target = wheel if artifact == "wheel" else launcher
    target.write_bytes(target.read_bytes() + b"tampered\n")

    with pytest.raises(lane.ReleaseRefused, match=f"controller {artifact} hashes to"):
        _verify(receipt, wheel, launcher)


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_receipt_refuses_field_drift(tmp_path: Path, mutation: str) -> None:
    _, receipt, wheel, launcher = _create_bundle(tmp_path)
    document = json.loads(receipt.read_text(encoding="utf-8"))
    if mutation == "missing":
        document.pop("launcher_sha256")
    else:
        document["download_url"] = "https://mutable.example.invalid/controller"
    receipt.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(lane.ReleaseRefused, match="fields differ"):
        _verify(receipt, wheel, launcher)


def test_receipt_refuses_duplicate_fields(tmp_path: Path) -> None:
    _, receipt, wheel, launcher = _create_bundle(tmp_path)
    text = receipt.read_text(encoding="utf-8").replace(
        '"artifact_sha256":',
        '"artifact_sha256":"sha256:' + "0" * 64 + '","artifact_sha256":',
        1,
    )
    receipt.write_text(text, encoding="utf-8")

    with pytest.raises(lane.ReleaseRefused, match="duplicate controller receipt"):
        _verify(receipt, wheel, launcher)


def test_release_run_binding_is_load_bearing(tmp_path: Path) -> None:
    _, receipt, wheel, launcher = _create_bundle(tmp_path)

    with pytest.raises(lane.ReleaseRefused, match="release_run_id"):
        _verify(receipt, wheel, launcher, release_run_id=RUN_ID + 1)


def test_resolver_refuses_durable_generic_package_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = lane.load_allowlist()[DISTRIBUTION]
    monkeypatch.setattr(
        lane,
        "load_allowlist",
        lambda: {
            DISTRIBUTION: {
                **entry,
                "controller_generic_package": "mutable-controller-latest",
            }
        },
    )

    with pytest.raises(lane.ReleaseRefused, match="controller_generic_package"):
        lane.resolve(DISTRIBUTION)
