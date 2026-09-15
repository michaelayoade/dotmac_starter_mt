"""Hosted-CI tests for the fixed-origin GitHub Actions JSON transport."""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "github_actions_transport", ROOT / "scripts" / "github_actions_transport.py"
)
assert SPEC and SPEC.loader
transport = importlib.util.module_from_spec(SPEC)
sys.modules["github_actions_transport"] = transport
SPEC.loader.exec_module(transport)


def test_json_request_uses_fixed_origin_and_auth_header(monkeypatch) -> None:
    seen: list[urllib.request.Request] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true}'

    def open_request(request, *, timeout):
        seen.append(request)
        assert timeout == 30
        return Response()

    monkeypatch.setattr(transport.JSON_OPENER, "open", open_request)
    assert transport.get_json("/repos/acme/project/actions/runs/1", "secret") == {
        "ok": True
    }
    assert seen[0].full_url == (
        "https://api.github.com/repos/acme/project/actions/runs/1"
    )
    assert seen[0].get_header("Authorization") == "Bearer secret"
    assert "secret" not in seen[0].full_url


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("https://evil.test/x", "absolute path"),
        ("//evil.test/x", "must not contain an origin"),
        ("repos/x", "absolute path"),
    ],
)
def test_origin_or_relative_path_is_refused(path: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        transport.get_json(path, "secret")


@pytest.mark.parametrize(
    "location",
    [
        "http://evil.test/x",
        "https://user:pass@evil.test/x",
    ],
)
def test_redirect_rejects_insecure_or_userinfo(location: str) -> None:
    request = urllib.request.Request(  # noqa: S310 -- URL is fixed HTTPS origin.
        transport.API + "/x", headers={"Authorization": "Bearer secret"}
    )
    with pytest.raises(urllib.error.HTTPError, match="unsafe redirect"):
        transport.GitHubRedirect().redirect_request(
            request, None, 302, "Found", {}, location
        )


def test_off_origin_https_redirect_strips_authorization() -> None:
    request = urllib.request.Request(  # noqa: S310 -- URL is fixed HTTPS origin.
        transport.API + "/x", headers={"Authorization": "Bearer secret"}
    )
    redirected = transport.GitHubRedirect().redirect_request(
        request, None, 302, "Found", {}, "https://uploads.github.com/x"
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None


def test_json_redirect_rejects_off_origin_but_accepts_same_origin() -> None:
    handler = transport.SameOriginJSONRedirect()
    request = urllib.request.Request(  # noqa: S310 -- URL is fixed HTTPS origin.
        transport.API + "/x"
    )
    with pytest.raises(urllib.error.HTTPError, match="unsafe JSON redirect"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.test/x")
    accepted = handler.redirect_request(
        request, None, 302, "Found", {}, transport.API + "/next"
    )
    assert accepted is not None
    assert accepted.full_url == transport.API + "/next"


def test_collector_byte_fetch_rejects_hostile_paths_before_open(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import collect_github_release_artifact as collector

    calls = []
    monkeypatch.setattr(
        collector.OPENER, "open", lambda *args, **kwargs: calls.append(args)
    )
    for path in ("@evil.test/x", "//evil.test/x"):
        with pytest.raises(ValueError):
            collector.get_bytes(path, "placeholder")
    assert calls == []


def test_collector_byte_fetch_accepts_api_path(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import collect_github_release_artifact as collector

    seen: list[urllib.request.Request] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"archive"

    def open_request(request, *, timeout):
        seen.append(request)
        assert timeout == 60
        return Response()

    monkeypatch.setattr(collector.OPENER, "open", open_request)
    assert collector.get_bytes("/repos/a/b/archive", "placeholder") == b"archive"
    assert seen[0].full_url == "https://api.github.com/repos/a/b/archive"
    assert seen[0].get_header("Authorization") == "Bearer placeholder"
