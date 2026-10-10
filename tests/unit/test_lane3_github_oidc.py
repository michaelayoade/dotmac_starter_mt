"""Actions OIDC supplier sensitivity cases; hosted CI only, synthetic tokens."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "lane3_github_oidc", ROOT / "scripts/lane3_github_oidc.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
ORIGIN = "https://fixture.actions.githubusercontent.com"
URL = ORIGIN + "/synthetic/jobs/job/idtoken?api-version=2.0"


def supplier(**kwargs: Any) -> Any:
    args = {
        "request_url": URL,
        "approved_origin": ORIGIN,
        "request_token": "synthetic-request-token",
        "fetch": lambda u, t: {"value": "a.b.c"},
    }
    args.update(kwargs)
    return m.GithubOidcSupplier(**args)


def test_exact_broker_fixed_audience_and_single_use() -> None:
    calls = []
    s = supplier(fetch=lambda u, t: calls.append((u, t)) or {"value": "a.b.c"})
    assert s(m.AUDIENCE) == "a.b.c"
    assert parse_qs(urlsplit(calls[0][0]).query)["audience"] == [m.AUDIENCE]
    assert calls[0][1] == "synthetic-request-token"
    assert "synthetic-request-token" not in repr(s)
    with pytest.raises(m.TopologySourceUnavailable):
        s(m.AUDIENCE)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://fixture.actions.githubusercontent.com/idtoken?api-version=2.0",
        "https://other.actions.githubusercontent.com/idtoken?api-version=2.0",
        "https://fixture.actions.githubusercontent.com.evil.invalid/idtoken?api-version=2.0",
        "https://user@fixture.actions.githubusercontent.com/idtoken?api-version=2.0",
        URL + "&audience=other",
        URL + "&api-version=3",
        URL + "#fragment",
        ORIGIN + "/other?api-version=2.0",
        ORIGIN + "/idtoken",
    ],
)
def test_unapproved_destination_or_query_refuses_before_fetch(url: str) -> None:
    calls = []
    with pytest.raises(m.TopologySourceUnavailable):
        supplier(request_url=url, fetch=lambda *a: calls.append(a))
    assert calls == []


def test_wrong_audience_never_fetches_and_consumes_credential() -> None:
    calls = []
    s = supplier(fetch=lambda *a: calls.append(a))
    with pytest.raises(m.TopologySourceUnavailable):
        s("other")
    with pytest.raises(m.TopologySourceUnavailable):
        s(m.AUDIENCE)
    assert calls == []


@pytest.mark.parametrize(
    "reply",
    [{}, {"value": ""}, {"value": "not-jwt"}, {"value": 1}, {"value": "a.b.c\n"}],
)
def test_bad_token_response_refuses(reply: Any) -> None:
    with pytest.raises(m.TopologySourceUnavailable):
        supplier(fetch=lambda *a: reply)(m.AUDIENCE)


def test_fetch_failure_redacted_and_not_retried() -> None:
    def fail(*args: Any) -> Any:
        raise RuntimeError("PRIVATE-TOKEN")

    s = supplier(fetch=fail)
    with pytest.raises(m.TopologySourceUnavailable) as e:
        s(m.AUDIENCE)
    assert "PRIVATE-TOKEN" not in str(e.value)
    with pytest.raises(m.TopologySourceUnavailable):
        s(m.AUDIENCE)
