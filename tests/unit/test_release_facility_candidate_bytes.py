"""Every byte a facility release publishes is bound to the candidate receipt.

`publish` runs `twine upload dist/*`. That uploads a wheel AND an sdist, so
"the artifact matches the receipt" has to mean both of them or it means half a
release. Until this file existed it meant the wheel: `require_candidate_bytes`
took a single `Path` produced by `_sole_wheel`, and the receipt's
`sdist.sha256` — recorded by `foundation_candidate.py record`, committed with
every receipt in the tree — was read by nothing at any seam. The sdist reached
the index having been compared with nothing.

`dotmac-deployment-control` 0.1.0a3 is the recorded precedent and it says this
in terms: the sdist was on the index the whole time, nothing had ever compared
its bytes, and the version was ruled unprovable rather than quietly narrowed to
whatever the resolver had fetched.

## Every check here is shown RED against a planted defect

A comparison that passes over an absence passes for the wrong reason. Each
property below is asserted twice — once on correct input, once on input carrying
exactly the defect it exists to catch — because a guard that has never been
observed refusing is indistinguishable from one that cannot refuse.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"


def _load():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "release_facility", SCRIPTS / "release_facility.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FACILITY = _load()

WHEEL = "dotmac_deployment_foundation-9.9.9a1-py3-none-any.whl"
SDIST = "dotmac_deployment_foundation-9.9.9a1.tar.gz"


def _write(directory: Path, name: str, payload: bytes) -> Path:
    path = directory / name
    path.write_bytes(payload)
    return path


def _receipt(wheel: bytes, sdist: bytes) -> dict[str, Any]:
    return {
        "schema": "CandidateArtifact.v1",
        "filename": WHEEL,
        "sha256": hashlib.sha256(wheel).hexdigest(),
        "size_bytes": len(wheel),
        "sdist": {
            "filename": SDIST,
            "sha256": hashlib.sha256(sdist).hexdigest(),
            "size_bytes": len(sdist),
        },
    }


@pytest.fixture()
def built(tmp_path: Path) -> tuple[Path, dict[str, Any], bytes, bytes]:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel_bytes = b"the candidate wheel"
    sdist_bytes = b"the candidate sdist"
    _write(dist, WHEEL, wheel_bytes)
    _write(dist, SDIST, sdist_bytes)
    return dist, _receipt(wheel_bytes, sdist_bytes), wheel_bytes, sdist_bytes


# ── the positive control ────────────────────────────────────────────────────


def test_both_recorded_artifacts_present_and_matching_is_accepted(built) -> None:
    dist, receipt, _, _ = built
    FACILITY.require_candidate_bytes(receipt, dist)


def test_the_filename_set_names_both_distribution_forms(built) -> None:
    _, receipt, _, _ = built
    assert FACILITY.candidate_filenames(receipt) == frozenset({WHEEL, SDIST})


# ── the defect the repair exists for ────────────────────────────────────────


def test_a_substituted_sdist_is_refused(built) -> None:
    """The one the old wheel-only comparison could not see."""
    dist, receipt, _, _ = built
    _write(dist, SDIST, b"a different sdist entirely")
    with pytest.raises(FACILITY.ReleaseRefused, match=SDIST):
        FACILITY.require_candidate_bytes(receipt, dist)


def test_a_missing_sdist_is_refused(built) -> None:
    dist, receipt, _, _ = built
    (dist / SDIST).unlink()
    with pytest.raises(FACILITY.ReleaseRefused, match="absent here"):
        FACILITY.require_candidate_bytes(receipt, dist)


def test_an_sdist_of_the_right_size_but_wrong_bytes_is_refused(built) -> None:
    """Size equality is not byte equality, and only one of them is the claim."""
    dist, receipt, _, sdist_bytes = built
    swapped = bytes(byte ^ 0x01 for byte in sdist_bytes)
    assert len(swapped) == len(sdist_bytes)
    _write(dist, SDIST, swapped)
    with pytest.raises(FACILITY.ReleaseRefused, match="sha256"):
        FACILITY.require_candidate_bytes(receipt, dist)


# ── the other direction: nothing unrecorded may reach `twine upload dist/*` ──


def test_an_unrecorded_extra_artifact_is_refused(built) -> None:
    dist, receipt, _, _ = built
    _write(dist, "dotmac_deployment_foundation-9.9.9a2.tar.gz", b"a stowaway")
    with pytest.raises(FACILITY.ReleaseRefused, match="named by no receipt entry"):
        FACILITY.require_candidate_bytes(receipt, dist)


# ── the wheel-side properties the repair must not have lost ─────────────────


def test_a_substituted_wheel_is_still_refused(built) -> None:
    dist, receipt, _, _ = built
    _write(dist, WHEEL, b"a different wheel entirely")
    with pytest.raises(FACILITY.ReleaseRefused, match=WHEEL):
        FACILITY.require_candidate_bytes(receipt, dist)


def test_a_renamed_wheel_is_refused(built) -> None:
    dist, receipt, wheel_bytes, _ = built
    (dist / WHEEL).unlink()
    _write(dist, "dotmac_deployment_foundation-9.9.9a2-py3-none-any.whl", wheel_bytes)
    with pytest.raises(FACILITY.ReleaseRefused):
        FACILITY.require_candidate_bytes(receipt, dist)


# ── a receipt that cannot bind both forms cannot authorise an upload ────────


def test_a_receipt_with_no_sdist_block_is_refused(built) -> None:
    dist, receipt, _, _ = built
    receipt.pop("sdist")
    with pytest.raises(FACILITY.ReleaseRefused, match="no sdist"):
        FACILITY.require_candidate_bytes(receipt, dist)


def test_a_receipt_whose_sdist_block_has_no_digest_is_refused(built) -> None:
    dist, receipt, _, _ = built
    receipt["sdist"] = {"filename": SDIST}
    with pytest.raises(FACILITY.ReleaseRefused, match="no sdist"):
        FACILITY.require_candidate_bytes(receipt, dist)


def test_a_receipt_with_no_wheel_digest_is_refused(built) -> None:
    dist, receipt, _, _ = built
    receipt.pop("sha256")
    with pytest.raises(FACILITY.ReleaseRefused, match="no wheel"):
        FACILITY.require_candidate_bytes(receipt, dist)
