"""Qualification scope and hosted-only custody; workload executes in Docker CI."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.verify_reference_upgrade import (
    Qualification,
    hosted_identity,
    load_baseline,
)


def test_hosted_guard_accepts_github_and_refuses_each_missing_premise():
    valid = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
    }
    assert hosted_identity(valid) == "123-2"
    for key in valid:
        invalid = {**valid, key: ""}
        with pytest.raises(ValueError, match="GitHub-hosted"):
            hosted_identity(invalid)
    with pytest.raises(ValueError, match="GitHub-hosted"):
        hosted_identity({**valid, "RUNNER_ENVIRONMENT": "self-hosted"})


def test_baseline_requires_immutable_source_and_never_claims_a_release(tmp_path):
    path = tmp_path / "baseline.json"
    valid = {"kind": "source-compatibility", "source_sha": "a" * 40}
    path.write_text(json.dumps(valid))
    assert load_baseline(path) == valid["source_sha"]
    for bad in (
        {**valid, "source_sha": "main"},
        {**valid, "source_sha": "a" * 7},
        {**valid, "kind": "supported-release"},
    ):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="immutable source"):
            load_baseline(path)


@pytest.mark.parametrize(
    "defect", [None, "data", "read", "delete", "token", "listing", "rls"]
)
def test_upgrade_canaries_accept_retained_data_and_detect_each_isolation_failure(
    monkeypatch, defect
):
    qualification = Qualification("candidate", Path.cwd(), "123-2")
    qualification.database_name = "disposable"
    subjects = {
        slug: {
            "host": slug,
            "email": slug,
            "token": slug,
            "tenant_id": str(uuid4()),
            "party": {"id": str(uuid4()), "email": slug},
        }
        for slug in ("alpha", "beta")
    }
    monkeypatch.setattr(qualification, "login", lambda host, email: host)
    monkeypatch.setattr(
        qualification, "sql", lambda *a, **kw: "f" if defect == "rls" else "t"
    )

    def request(method, path, *, host, token, **kwargs):
        own = subjects[host]["party"]
        other = subjects["beta" if host == "alpha" else "alpha"]["party"]
        if token != host:
            return (200 if defect == "token" else 401), []
        if path == "/parties":
            return 200, [own, other] if defect == "listing" else [own]
        if path == "/parties/" + own["id"]:
            return 200, {**own, "email": "lost"} if defect == "data" else own
        if path == "/parties/" + other["id"]:
            permissive = defect == ("delete" if method == "DELETE" else "read")
            return (200 if permissive else 404), other
        raise AssertionError("Unexpected canary request")

    monkeypatch.setattr(qualification, "request", request)
    if defect:
        with pytest.raises(RuntimeError, match="Qualification assertion failed"):
            qualification.verify(subjects, retained_sessions=True)
    else:
        qualification.verify(subjects, retained_sessions=True)
