#!/usr/bin/env python3
"""Small, fixed-origin transport for GitHub Actions JSON endpoints."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.github.com"


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    """Allow HTTPS redirects, removing credentials off the API origin."""

    def redirect_request(self, request, fp, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise urllib.error.HTTPError(new_url, code, "unsafe redirect", headers, fp)
        redirected = super().redirect_request(
            request, fp, code, message, headers, new_url
        )
        if redirected is not None and parsed.netloc != "api.github.com":
            redirected.remove_header("Authorization")
        return redirected


class SameOriginJSONRedirect(GitHubRedirect):
    """Reject every JSON redirect that leaves the GitHub API origin."""

    def redirect_request(self, request, fp, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if parsed.scheme != "https" or parsed.netloc != "api.github.com":
            raise urllib.error.HTTPError(
                new_url, code, "unsafe JSON redirect", headers, fp
            )
        return super().redirect_request(request, fp, code, message, headers, new_url)


OPENER = urllib.request.build_opener(GitHubRedirect())
JSON_OPENER = urllib.request.build_opener(SameOriginJSONRedirect())


def url_for_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("GitHub API path must be an absolute path")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or path.startswith("//"):
        raise ValueError("GitHub API path must not contain an origin")
    return API + path


def get_json(path: str, token: str) -> dict[str, Any]:
    """Fetch one JSON object from the fixed GitHub API origin."""

    request = urllib.request.Request(  # noqa: S310 -- URL is fixed HTTPS origin.
        url_for_path(path),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        },
    )
    with JSON_OPENER.open(request, timeout=30) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("GitHub API response must be a JSON object")
    return value
