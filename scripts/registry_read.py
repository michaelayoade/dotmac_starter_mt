#!/usr/bin/env python3
"""Fetch an ENUMERATED set of files from a private simple index BY NAME.

One owner for the by-name registry read, extracted unchanged from the kernel
lane's `collect_private_registry_files.py` so a second caller does not become a
second implementation of it.

## Why "by name" is the whole point

A resolver-mediated fetch answers a different question from the one a release
verification asks. `pip download` — with or without `--only-binary` — resolves
a requirement and retrieves whatever satisfies it. That is correct pip
behaviour and it is no proof at all about the files it did not choose: a
project publishes a wheel AND an sdist, `pip download` takes the wheel, and the
sdist's bytes on the index are never compared with anything.

`dotmac-deployment-control` 0.1.0a3 is the recorded precedent. Its first
verifier asked "did the consumer retrieve everything the run built?" of a
`pip download`, the sdist was on the index the whole time, nothing had ever
compared its bytes, and the version was ruled unprovable. The repair was not to
narrow the question to whatever the resolver happened to return; it was to
request every expected filename EXPLICITLY.

So the caller states the exact filename set it expects, this module refuses
unless the index lists each of them exactly once, and every one is retrieved
individually. What the caller then does with the bytes — hashing them against a
build manifest or a candidate receipt — is the caller's decision, not this
module's.

## Credential discipline

The credential arrives as an argument that came from an environment variable;
it never appears in the index URL, in `argv`, or in a redirect target. The
index must be credential-free HTTPS, redirects may not leave the index's own
origin, and an artifact served from any other origin is refused rather than
downloaded.
"""

from __future__ import annotations

import base64
import html.parser
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class RegistryReadRefused(SystemExit):
    """The index could not be read the way an enumerated fetch requires."""


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


class RegistryReader:
    """A read-only client for ONE private simple index.

    `index` is the project's simple-index URL and must carry no credential.
    """

    def __init__(self, index: str, login: str, password: str) -> None:
        parsed = urllib.parse.urlsplit(index)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise RegistryReadRefused(
                "registry read refused: index must be credential-free HTTPS"
            )
        if not login or not password:
            raise RegistryReadRefused(
                "registry read refused: a read login and credential are required"
            )
        self.index = index
        self._netloc = parsed.netloc
        self._authorization = (
            "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()
        )
        self._opener = urllib.request.build_opener(SameOriginRedirect(self._netloc))

    def get(self, url: str) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != self._netloc:
            raise RegistryReadRefused(
                "registry read refused an unexpected artifact origin"
            )
        request = urllib.request.Request(  # noqa: S310 -- origin constrained above.
            url, headers={"Authorization": self._authorization}
        )
        with self._opener.open(request, timeout=60) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if (
                final.scheme != "https"
                or final.netloc != self._netloc
                or final.username
                or final.password
            ):
                raise RegistryReadRefused("registry read refused an unsafe redirect")
            return response.read()

    def listed_filenames(self) -> dict[str, list[str]]:
        """Every artifact filename the index lists, mapped to its URLs."""
        parser = Links()
        parser.feed(self.get(self.index).decode("utf-8"))
        listed: dict[str, list[str]] = {}
        for href in parser.hrefs:
            url = urllib.parse.urljoin(self.index, href)
            name = Path(urllib.parse.urlsplit(url).path).name
            listed.setdefault(name, []).append(url)
        return listed

    def collect(self, expected: frozenset[str], output: Path) -> dict[str, Path]:
        """Retrieve EVERY expected filename, each requested by its own name.

        Refuses unless the index lists each expected name exactly once — zero
        means the file the caller is verifying is not there, and more than one
        means the name does not identify a single set of bytes.
        """
        if not expected:
            raise RegistryReadRefused(
                "registry read refused: an empty filename set proves nothing"
            )
        listed = self.listed_filenames()
        matches = {name: listed.get(name, []) for name in expected}
        wrong = {name: len(urls) for name, urls in matches.items() if len(urls) != 1}
        if wrong:
            raise RegistryReadRefused(
                f"registry read refused artifact cardinality: {wrong}"
            )
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        collected: dict[str, Path] = {}
        for name, urls in sorted(matches.items()):
            destination = output / name
            destination.write_bytes(self.get(urls[0]))
            destination.chmod(0o600)
            collected[name] = destination
        return collected
