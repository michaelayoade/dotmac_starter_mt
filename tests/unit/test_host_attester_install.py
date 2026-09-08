"""`scripts/host_attester_install.py`: download, verify, install, then attest.

Two plants are required by the brief this lane implements, and both live
here because both properties belong to this file:

1. **No caller-supplied installed digest** — the workload must observe what
   it actually installed, never accept a parameter naming it.
2. **No caller-selected candidate root/receipt** — the authority that decides
   whether a downloaded file's digest is trusted must not arrive as a path, an
   environment variable, or a config value the invoker controls.

Loaded by file location, registered in `sys.modules` before execution — the
same idiom `test_release_facility_candidate_bytes.py` already uses, and the
same fix commit `d2a2e9e0` made after a helper named like a test was collected
by pytest's default `test*` glob and broke on missing fixtures. Every helper
here that is not itself a test keeps a leading underscore for exactly that
reason.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from dotmac_deployment_foundation.digest import Digest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.host_source import CandidateReceipt, InstalledArtifact

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# `host_attester` must be importable under its own name before
# `host_attester_install` executes its `from host_attester import ...`.
_load("host_attester")
INSTALL = _load("host_attester_install")

RECEIPT = CandidateReceipt(
    facility="dotmac-deployment-foundation",
    version="0.4.0a2",
    artifact_digest=Digest.parse("a" * 64, where="test"),
    source_revision="c" * 40,
    repository="dotmac/foundation",
    run_id="123",
    artifact_id="456",
)

CORRECT_BYTES = b"correct candidate wheel bytes"
WRONG_BYTES = b"a substituted wheel with different bytes entirely"


def _receipt_for(payload: bytes) -> CandidateReceipt:
    import hashlib

    return CandidateReceipt(
        facility=RECEIPT.facility,
        version=RECEIPT.version,
        artifact_digest=Digest.parse(hashlib.sha256(payload).hexdigest(), where="test"),
        source_revision=RECEIPT.source_revision,
        repository=RECEIPT.repository,
        run_id=RECEIPT.run_id,
        artifact_id=RECEIPT.artifact_id,
    )


# ── plant 1: no caller-supplied installed digest ────────────────────────────
# (the producer-side half is proven in test_host_attester_producer.py; this
# file proves the INSTALL pipeline never lets a caller substitute a digest
# for the one `sha256_of` actually computes over the downloaded bytes.)


def test_download_verify_install_refuses_before_install_on_digest_mismatch(
    tmp_path: Path,
) -> None:
    receipt = _receipt_for(CORRECT_BYTES)
    installer_calls: list[Path] = []

    def _fake_download(url: str, dest: Path) -> Path:
        dest.write_bytes(WRONG_BYTES)  # a substituted wheel
        return dest

    def _fake_install(wheel: Path, *, python=None) -> None:
        installer_calls.append(wheel)

    with pytest.raises(PreconditionFailed) as raised:
        INSTALL.download_verify_install(
            receipt=receipt,
            download_url="https://example.invalid/candidate.whl",
            workdir=tmp_path,
            downloader=_fake_download,
            installer=_fake_install,
        )
    assert raised.value.code == INSTALL.DOWNLOAD_DIGEST_MISMATCH
    assert installer_calls == [], (
        "install must never run once the digest disagrees — the whole point "
        "of decision 3 is refusing BEFORE install, not after"
    )


def test_download_verify_install_installs_on_a_genuine_match(tmp_path: Path) -> None:
    receipt = _receipt_for(CORRECT_BYTES)
    installer_calls: list[Path] = []

    def _fake_download(url: str, dest: Path) -> Path:
        dest.write_bytes(CORRECT_BYTES)
        return dest

    def _fake_install(wheel: Path, *, python=None) -> None:
        installer_calls.append(wheel)

    class _FakeMetadata:
        def version(self, distribution: str) -> str:
            return receipt.version

        def read_text(self, distribution: str, filename: str) -> str | None:
            if filename == "RECORD":
                return "a,sha256=x,1\n"
            if filename == "direct_url.json":
                hex_digest = str(receipt.artifact_digest).split(":")[1]
                return (
                    '{"url": "file:///tmp/x.whl", "archive_info": '
                    f'{{"hashes": {{"sha256": "{hex_digest}"}}}}}}'
                )
            return None

    installed = INSTALL.download_verify_install(
        receipt=receipt,
        download_url="https://example.invalid/candidate.whl",
        workdir=tmp_path,
        downloader=_fake_download,
        installer=_fake_install,
        metadata=_FakeMetadata(),
    )
    assert installer_calls, "install must run once the digest genuinely matches"
    assert isinstance(installed, InstalledArtifact)
    assert installed.artifact_digest == receipt.artifact_digest


# ── plant 2: no caller-selected candidate root ──────────────────────────────


@pytest.mark.parametrize(
    "bad_receipt",
    [
        "docs/inventories/foundation-candidate-0.4.0a2.json",  # a path string
        {"facility": "dotmac-deployment-foundation", "sha256": "a" * 64},  # a dict
        None,
    ],
    ids=["path-string", "dict", "none"],
)
def test_a_non_candidate_receipt_value_is_refused_independent_of_other_components(
    bad_receipt: Any, tmp_path: Path
) -> None:
    """PLANT: the caller offers something OTHER than a `CandidateReceipt`
    value — a path, a bare dict, nothing at all — standing in for the trust
    root. The refusal must fire even when EVERY other component (the
    downloader, the installer) is perfectly healthy, proving this gate does
    not depend on some other check catching the substitution instead."""

    def _never_called_download(url: str, dest: Path) -> Path:
        raise AssertionError("download must not run before the receipt is validated")

    def _never_called_install(wheel: Path, *, python=None) -> None:
        raise AssertionError("install must not run before the receipt is validated")

    with pytest.raises(PreconditionFailed) as raised:
        INSTALL.download_verify_install(
            receipt=bad_receipt,
            download_url="https://example.invalid/candidate.whl",
            workdir=tmp_path,
            downloader=_never_called_download,
            installer=_never_called_install,
        )
    assert raised.value.code == INSTALL.NOT_A_CANDIDATE_RECEIPT


def test_verify_downloaded_digest_alone_refuses_a_non_receipt(tmp_path: Path) -> None:
    target = tmp_path / "whatever.whl"
    target.write_bytes(CORRECT_BYTES)
    with pytest.raises(PreconditionFailed) as raised:
        INSTALL.verify_downloaded_digest(target, {"sha256": "a" * 64})
    assert raised.value.code == INSTALL.NOT_A_CANDIDATE_RECEIPT


# ── sensitivity proof: no path/env/CLI can select the trust root ───────────


def _forbidden_trust_selectors(source: str) -> list[str]:
    """Every way a caller could be given a say in WHICH receipt is trusted.

    Returns the list of findings (empty means clean). Checked structurally
    over the AST plus two textual greps ast cannot express as cleanly
    (module-level `__main__` guard, `os.environ` attribute access).
    """
    findings: list[str] = []
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            findings.append(f"reads an environment variable ({node.attr})")
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name == "add_argument":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        lowered = arg.value.lower()
                        if any(
                            token in lowered
                            for token in ("receipt", "candidate", "root", "trust")
                        ):
                            findings.append(
                                "defines a CLI option naming a trust input: "
                                f"{arg.value!r}"
                            )
        if isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
            ):
                findings.append(
                    'defines an `if __name__ == "__main__":` CLI entry point'
                )
    return findings


def test_host_attester_install_offers_no_caller_selected_trust_root() -> None:
    source = (SCRIPTS / "host_attester_install.py").read_text(encoding="utf-8")
    assert _forbidden_trust_selectors(source) == []


def test_the_trust_selector_detector_flags_an_environment_variable_plant() -> None:
    """SENSITIVITY PLANT: a decoy source that reads
    `os.environ["CANDIDATE_RECEIPT_PATH"]` must be caught, or the clean result
    above proves nothing."""
    decoy = (
        "import os\n"
        "def resolve_receipt():\n"
        "    return os.environ['CANDIDATE_RECEIPT_PATH']\n"
    )
    assert _forbidden_trust_selectors(decoy) != []


def test_the_trust_selector_detector_flags_a_cli_receipt_path_plant() -> None:
    """SENSITIVITY PLANT: a decoy source with an `argparse` option naming a
    receipt path must be caught."""
    decoy = (
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--receipt-path')\n"
    )
    assert _forbidden_trust_selectors(decoy) != []


def test_the_trust_selector_detector_flags_a_bare_main_entry_point_plant() -> None:
    """SENSITIVITY PLANT: a decoy `if __name__ == "__main__":` block must be
    caught, even with no argument naming a trust input directly — a CLI
    entry point is itself the shape this module refuses to offer."""
    decoy = 'if __name__ == "__main__":\n    pass\n'
    assert _forbidden_trust_selectors(decoy) != []
