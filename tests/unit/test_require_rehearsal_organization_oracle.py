"""`require_rehearsal.main` end to end, with the network replaced and a real
`RehearsalReceipt.v2`.

The pure halves are covered in `tests/architecture/test_lane3_execution_oracle.py`.
This file proves the wiring:
- the oracle selects a run, reads that run's receipt, and binds the two;
- a receipt from another attempt is refused;
- the checked-in, unprovisioned topology refuses before any network call.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import pathlib
import sys
import zipfile
from typing import Any

import pytest
from dotmac_deployment_foundation.rehearsal import ExecutionRunBindingV1

from tests.unit.foundation_v3_support import WHEEL
from tests.unit.lane3_evidence_support import REFERENCE, write_topology
from tests.unit.test_deployment_foundation_rehearsal_receipt_v2 import (
    REVISION,
    Subject,
    subject,  # noqa: F401 - pytest fixture, used by name
)
from tests.unit.test_deployment_foundation_rehearsal_receipt_v2 import (
    _build as _build_receipt,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import require_rehearsal  # noqa: E402

LAUNCHER = "e" * 40
REPO_ID = 1397614141
GROUP_ID = 4
ENV_ID = 23103080999
RUN_ID = 4242
CIPHERTEXT = b"age-encryption.org/v1\nsynthetic-ciphertext"


def _build(held_subject, **overrides):
    return _build_receipt(
        held_subject,
        evidence_bundle_digest="sha256:" + hashlib.sha256(CIPHERTEXT).hexdigest(),
        **overrides,
    )


def _archive(receipt, ciphertext=CIPHERTEXT):
    storage = io.BytesIO()
    with zipfile.ZipFile(storage, "w") as archive:
        archive.writestr("receipt.json", receipt)
        archive.writestr("evidence-bundle.age", ciphertext)
        archive.writestr(
            "docs/inventories/deployment-exposure-rehearsal-status.md",
            b"synthetic status\n",
        )
    return storage.getvalue()


TOPOLOGY: dict[str, Any] = {
    "schema": "Lane3ExecutionTopology.v1",
    "starter_repository": "michaelayoade/dotmac_starter_mt",
    "execution_repository": "dotmac-tech/lane3-exposure-execution",
    "execution_repository_id": REPO_ID,
    "execution_owner_id": 335992433,
    "workflow_path": ".github/workflows/lane3-exposure-rehearsal.yml",
    "environment": {
        "name": "lane3-rehearsal-protected",
        "id": ENV_ID,
        "reviewer": "michaelayoade",
    },
    "runner_group": {"name": "lane3-exposure-protected", "id": GROUP_ID},
    "admitted_launcher_revisions": [LAUNCHER],
    "receipt_artifact": "lane3-rehearsal-receipt",
    "admission_evidence": REFERENCE,
}

RUN: dict[str, Any] = {
    "id": RUN_ID,
    "run_attempt": 1,
    "repository": {"id": REPO_ID},
    "path": TOPOLOGY["workflow_path"],
    "event": "workflow_dispatch",
    "head_branch": "main",
    "head_sha": LAUNCHER,
    "display_title": f"lane3 starter={REVISION} candidate=0.4.0a2",
    "status": "completed",
    "conclusion": "success",
    "run_started_at": "2026-10-05T10:00:00Z",
    "html_url": "https://example.invalid/run/4242",
}


def _network(
    monkeypatch: pytest.MonkeyPatch, receipt: bytes, *, run: dict[str, Any] = RUN
) -> list[str]:
    calls: list[str] = []

    def fake_list(url: str, key: str, token: str) -> list[dict[str, Any]]:
        calls.append(url)
        return {
            "workflow_runs": [run],
            "jobs": [{"id": 1, "runner_group_id": GROUP_ID}],
            "artifacts": [
                {
                    "id": 9,
                    "name": "lane3-rehearsal-receipt",
                    "expired": False,
                    "size_in_bytes": len(_archive(receipt)),
                    "expires_at": "2099-01-01T00:00:00Z",
                    "workflow_run": {"id": run["id"]},
                }
            ],
        }[key]

    def fake_fetch(url: str, token: str) -> Any:
        calls.append(url)
        assert url.endswith("/approvals")
        return [
            {
                "state": "approved",
                "user": {"login": "michaelayoade"},
                "environments": [{"id": ENV_ID}],
            }
        ]

    monkeypatch.setattr(require_rehearsal, "_list", fake_list)
    monkeypatch.setattr(require_rehearsal, "_fetch", fake_fetch)
    monkeypatch.setattr(
        require_rehearsal, "_download", lambda *a, **k: _archive(receipt)
    )
    return calls


def _main(tmp_path: pathlib.Path, topology: dict[str, Any] | None) -> int:
    path = pathlib.Path(
        write_topology(tmp_path, topology if topology is not None else {})
    )
    return require_rehearsal.main(
        [
            REVISION,
            "--repo",
            "michaelayoade/dotmac_starter_mt",
            "--topology",
            str(path),
            "--receipt-out",
            str(tmp_path / "receipt.json"),
            "--artifact-digest",
            WHEEL,
        ]
    )


def test_a_green_receipt_cannot_replace_an_authenticated_offline_result(
    subject: Subject,  # noqa: F811
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipt = _build(
        subject,
        execution_run=ExecutionRunBindingV1(
            repository_id=REPO_ID, run_id=RUN_ID, run_attempt=1
        ),
    )
    _network(monkeypatch, receipt.canonical_bytes())
    assert _main(tmp_path, TOPOLOGY) == require_rehearsal.EXIT_USAGE
    captured = capsys.readouterr()
    assert "authenticated_offline_result_unavailable" in captured.err
    assert captured.out == ""
    assert (tmp_path / "receipt.json").read_bytes() == receipt.canonical_bytes()


def test_a_receipt_from_another_attempt_of_the_selected_run_is_refused(
    subject: Subject,  # noqa: F811
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The selected run is attempt 2; the receipt is attempt 1's, which passed."""
    receipt = _build(
        subject,
        execution_run=ExecutionRunBindingV1(
            repository_id=REPO_ID, run_id=RUN_ID, run_attempt=1
        ),
    )
    _network(monkeypatch, receipt.canonical_bytes(), run={**RUN, "run_attempt": 2})
    assert _main(tmp_path, TOPOLOGY) == require_rehearsal.EXIT_REFUSED
    assert "from another" in capsys.readouterr().err


def test_a_v1_receipt_is_refused_by_schema(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _network(monkeypatch, json.dumps({"schema": "RehearsalReceipt.v1"}).encode())
    assert _main(tmp_path, TOPOLOGY) == require_rehearsal.EXIT_REFUSED
    assert "history, not publication evidence" in capsys.readouterr().err


def test_a_topology_for_another_repository_is_refused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _network(monkeypatch, b"")
    elsewhere = copy.deepcopy(TOPOLOGY)
    elsewhere["starter_repository"] = "someone/else"
    assert _main(tmp_path, elsewhere) == require_rehearsal.EXIT_REFUSED
    assert calls == []


def test_the_checked_in_topology_refuses_before_any_network_call(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _network(monkeypatch, b"")
    checked_in = json.loads((REPO / ".github" / "lane3-execution.json").read_text())
    assert _main(tmp_path, checked_in) == require_rehearsal.EXIT_REFUSED
    assert calls == []
    assert "REFUSED" in capsys.readouterr().err


def test_offline_stage_receives_exact_public_coordinates(
    subject: Subject,  # noqa: F811
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _build(subject)
    _network(monkeypatch, receipt.canonical_bytes())
    calls = []

    def unavailable(coordinates):
        calls.append(coordinates)
        raise require_rehearsal.OfflineResultIndeterminate("PRIVATE_SENTINEL")

    monkeypatch.setattr(
        require_rehearsal, "_require_authenticated_offline_result", unavailable
    )
    assert _main(tmp_path, TOPOLOGY) == require_rehearsal.EXIT_USAGE
    assert calls == [
        require_rehearsal.OfflineResultCoordinates(
            repository_id=REPO_ID,
            run_id=RUN_ID,
            run_attempt=1,
            receipt_digest=receipt.sha256_digest(),
            ciphertext_digest=receipt.content["evidence_bundle_digest"],
            candidate_artifact_digest=WHEEL,
            release_revision=REVISION,
        )
    ]


@pytest.mark.parametrize(
    "error_type", [RuntimeError, OSError, require_rehearsal.OfflineResultIndeterminate]
)
def test_private_boundary_failures_are_sanitized(
    subject: Subject,  # noqa: F811
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_type,
) -> None:
    _network(monkeypatch, _build(subject).canonical_bytes())

    def unavailable(coordinates):
        raise error_type("PRIVATE_SENTINEL")

    monkeypatch.setattr(
        require_rehearsal, "_require_authenticated_offline_result", unavailable
    )
    assert _main(tmp_path, TOPOLOGY) == require_rehearsal.EXIT_USAGE
    captured = capsys.readouterr()
    assert "PRIVATE_SENTINEL" not in captured.err
    assert "authenticated_offline_result_unavailable" in captured.err
    assert captured.out == ""


def test_deleting_mandatory_stage_exposes_a_false_success(
    subject: Subject,  # noqa: F811
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _network(monkeypatch, _build(subject).canonical_bytes())
    assert _main(tmp_path, TOPOLOGY) == require_rehearsal.EXIT_USAGE
    tree = ast.parse((REPO / "scripts" / "require_rehearsal.py").read_text())
    removed = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_require_authenticated_offline_result"
        ):
            node.value = ast.Constant(None)
            removed += 1
    assert removed == 1
    main_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    mutant = ast.Module(body=[main_node], type_ignores=[])
    namespace = {**vars(require_rehearsal), "__name__": require_rehearsal.__name__}
    exec(  # noqa: S102 - execute only the planted in-tree main function
        compile(ast.fix_missing_locations(mutant), "oracle_sensitivity_mutant", "exec"),
        namespace,
    )
    monkeypatch.setattr(require_rehearsal, "main", namespace["main"])
    with pytest.raises(AssertionError):
        assert _main(tmp_path, TOPOLOGY) == require_rehearsal.EXIT_USAGE
    assert _main(tmp_path, TOPOLOGY) == require_rehearsal.EXIT_OK
