#!/usr/bin/env python3
"""Download an enumerated private-index file set without credential URLs or argv."""

from __future__ import annotations

import base64
import html.parser
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from release_artifact_verification import canonical_kernel_filenames

REGISTRY_INDEX = (
    "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/dotmac-kernel/"
)
REGISTRY_IDENTITY = "https://registry.dotmac.io/api/v1/user"
REGISTRY_ORIGIN = "https://registry.dotmac.io"
REGISTRY_LOGIN = "ci-reader"


class Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.hrefs.append(value)


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, netloc: str) -> None:
        super().__init__()
        self.netloc = netloc

    def redirect_request(self, request, fp, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != self.netloc
            or parsed.username
            or parsed.password
        ):
            raise urllib.error.HTTPError(new_url, code, "unsafe redirect", headers, fp)
        return super().redirect_request(request, fp, code, message, headers, new_url)


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"registry collection refused: {name} is required")
    return value


def main() -> int:
    index = REGISTRY_INDEX
    username = REGISTRY_LOGIN
    password = required("REGISTRY_PASSWORD")
    version = required("RELEASE_VERSION")
    expected = frozenset(filter(None, required("EXPECTED_FILENAMES").split("\n")))
    if expected != canonical_kernel_filenames(version):
        raise SystemExit("registry collection refused: filenames are not canonical")
    output = Path(required("REGISTRY_OUTPUT_DIR"))
    parsed_index = urllib.parse.urlsplit(index)
    if parsed_index.scheme != "https" or parsed_index.username or parsed_index.password:
        raise SystemExit(
            "registry collection refused: index must be credential-free HTTPS"
        )
    authorization = (
        "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    )
    password = ""
    opener = urllib.request.build_opener(SameOriginRedirect(parsed_index.netloc))

    def fetch(url: str) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != parsed_index.netloc:
            raise SystemExit(
                "registry collection refused an unexpected artifact origin"
            )
        request = urllib.request.Request(  # noqa: S310 -- origin was constrained above.
            url, headers={"Authorization": authorization}
        )
        with opener.open(request, timeout=60) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if (
                final.scheme != "https"
                or final.netloc != parsed_index.netloc
                or final.username
                or final.password
            ):
                raise SystemExit("registry collection refused an unsafe redirect")
            return response.read()

    parser = Links()
    parser.feed(fetch(index).decode("utf-8"))
    matches: dict[str, list[str]] = {name: [] for name in expected}
    for href in parser.hrefs:
        url = urllib.parse.urljoin(index, href)
        name = Path(urllib.parse.urlsplit(url).path).name
        if name in matches:
            matches[name].append(url)
    wrong = {name: len(urls) for name, urls in matches.items() if len(urls) != 1}
    if wrong:
        raise SystemExit(f"registry collection refused artifact cardinality: {wrong}")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, urls in matches.items():
        destination = output / name
        destination.write_bytes(fetch(urls[0]))
        destination.chmod(0o600)
    identity = json.loads(fetch(REGISTRY_IDENTITY))
    if identity.get("login") != username or identity.get("is_admin") is not False:
        raise SystemExit("registry collection refused: credential identity differs")
    observations = []
    for name in sorted(expected):
        path = output / name
        observations.append({"name": name, "size": path.stat().st_size})
    observation_path = Path(required("REGISTRY_OBSERVATION"))
    observation_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    observation_path.write_text(
        json.dumps(
            {
                "schema": "PrivateRegistryReadObservation.v1",
                "index_origin": REGISTRY_ORIGIN,
                "observed_identity": {
                    "login": str(identity["login"]),
                    "is_admin": False,
                },
                "facility_http_methods": ["GET"],
                "files": observations,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    observation_path.chmod(0o600)
    authorization = ""
    print(f"collected {len(expected)} registry files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
